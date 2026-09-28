from flask import Flask, render_template, request, jsonify, redirect, session
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)

# Session ke liye secret key
app.secret_key = os.environ.get("SECRET_KEY", "astrsafe_secret_key_2026")

# ESP32 device authentication. Set these on Render/local environment.
DEVICE_TOKEN = os.environ.get("SAFEBELT_DEVICE_TOKEN", "demo-safebelt-device")
DEVICE_USERNAME = os.environ.get("SAFEBELT_DEVICE_USERNAME", "")

# Optional real SMS through Twilio. If credentials are absent, the app logs a simulation.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")


# =========================
# LOCATION SOURCE LABELS
# =========================
# Current demo uses the browser/device Geolocation API.
# NEO-6M GPS will be added later (via ESP32) without changing
# the database, since lat/long fields already support either source.

LOCATION_SOURCE_LABELS = {
    "Browser/Device": "Approximate Browser/Device Location",
    "GPS": "NEO-6M GPS"
}


# =========================
# DATABASE
# =========================

def create_database():
    conn = sqlite3.connect("safety_belt.db")
    cursor = conn.cursor()

    # USERS TABLE
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    # EMERGENCIES TABLE
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emergencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            latitude REAL,
            longitude REAL,
            status TEXT,
            date_time TEXT
        )
    """)

    # TRUSTED CONTACTS TABLE
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trusted_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            mobile TEXT
        )
    """)

    # LATEST KNOWN LOCATION (updated only when the dashboard requests location)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_locations (
            user_id INTEGER PRIMARY KEY,
            latitude REAL,
            longitude REAL,
            source TEXT,
            updated_at TEXT
        )
    """)

    # Notification audit trail
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emergency_id INTEGER,
            contact_name TEXT,
            mobile TEXT,
            status TEXT,
            sent_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# =========================
# DEVICE + SMS HELPERS
# =========================

def get_device_user():
    """Resolve the demo SafeBelt account used by the physical ESP32."""
    conn = sqlite3.connect("safety_belt.db")
    cursor = conn.cursor()
    user = None
    if DEVICE_USERNAME:
        cursor.execute("SELECT id, name, username FROM users WHERE username = ?", (DEVICE_USERNAME,))
        user = cursor.fetchone()
    if not user:
        cursor.execute("SELECT id, name, username FROM users ORDER BY id LIMIT 1")
        user = cursor.fetchone()
    conn.close()
    return user


def send_sms(to_number, message):
    """Send a real SMS when Twilio credentials are configured; otherwise simulate."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
        print(f"SIMULATED_SMS -> {to_number}: {message}")
        return "SIMULATED"
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=message, from_=TWILIO_FROM_NUMBER, to=to_number)
        return "SENT"
    except Exception as exc:
        print("SMS ERROR:", exc)
        return "FAILED"


def authenticate_device(req):
    token = req.headers.get("X-Device-Token")
    if not token:
        data = req.get_json(silent=True) or {}
        token = data.get("device_token")
    return token == DEVICE_TOKEN


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return render_template("index.html")


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect("safety_belt.db")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, name, username
            FROM users
            WHERE username = ? AND password = ?
        """, (username, password))

        user = cursor.fetchone()

        conn.close()

        if user:

            # Store logged-in user's ID
            session["user_id"] = user[0]
            session["user_name"] = user[1]
            session["username"] = user[2]

            return redirect("/dashboard")

        else:
            return "Invalid Username or Password!"

    return render_template("login.html")


# =========================
# REGISTER / CREATE ACCOUNT
# =========================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form["name"]
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect("safety_belt.db")
        cursor = conn.cursor()

        try:

            cursor.execute("""
                INSERT INTO users (name, username, password)
                VALUES (?, ?, ?)
            """, (name, username, password))

            conn.commit()
            conn.close()

            return redirect("/login")

        except sqlite3.IntegrityError:

            conn.close()

            return "Username already exists. Please choose another username."

    return render_template("register.html")


# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# =========================
# DASHBOARD
# =========================

@app.route("/dashboard")
def dashboard():

    # Check login
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    conn = sqlite3.connect("safety_belt.db")
    cursor = conn.cursor()

    # Count only this user's emergencies
    cursor.execute("""
        SELECT COUNT(*)
        FROM emergencies
        WHERE user_id = ?
    """, (user_id,))

    emergency_count = cursor.fetchone()[0]

    # Recent emergencies of this user
    cursor.execute("""
        SELECT *
        FROM emergencies
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 5
    """, (user_id,))

    recent_emergencies = cursor.fetchall()

    # Latest emergency of this user
    cursor.execute("""
        SELECT status
        FROM emergencies
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))

    latest_emergency = cursor.fetchone()

    conn.close()

    return render_template(
        "dashboard.html",
        emergency_count=emergency_count,
        recent_emergencies=recent_emergencies,
        latest_emergency=latest_emergency
    )


# =========================
# TRUSTED CONTACTS
# =========================

@app.route("/contacts", methods=["GET", "POST"])
def contacts():

    # Check login
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    conn = sqlite3.connect("safety_belt.db")
    cursor = conn.cursor()

    if request.method == "POST":

        name = request.form["name"]
        mobile = request.form["mobile"]

        cursor.execute("""
            INSERT INTO trusted_contacts
            (user_id, name, mobile)
            VALUES (?, ?, ?)
        """, (user_id, name, mobile))

        conn.commit()

    # Get only this user's contacts
    cursor.execute("""
        SELECT *
        FROM trusted_contacts
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,))

    contacts = cursor.fetchall()

    conn.close()

    return render_template(
        "contacts.html",
        contacts=contacts
    )


# =========================
# DELETE CONTACT
# =========================

@app.route("/delete_contact/<int:contact_id>", methods=["POST"])
def delete_contact(contact_id):

    # Check login
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    conn = sqlite3.connect("safety_belt.db")
    cursor = conn.cursor()

    # Delete only if contact belongs to logged-in user
    cursor.execute("""
        DELETE FROM trusted_contacts
        WHERE id = ? AND user_id = ?
    """, (contact_id, user_id))

    conn.commit()
    conn.close()

    return redirect("/contacts")


# =========================
# EMERGENCY HISTORY
# =========================

@app.route("/history")
def history():

    # Check login
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    conn = sqlite3.connect("safety_belt.db")
    cursor = conn.cursor()

    # Get only this user's emergencies
    cursor.execute("""
        SELECT *
        FROM emergencies
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,))

    emergencies = cursor.fetchall()

    conn.close()

    return render_template(
        "history.html",
        emergencies=emergencies
    )


# =========================
# ABOUT
# =========================

@app.route("/about")
def about():
    return render_template("about.html")


# =========================
# DEVICE LOCATION API
# =========================

@app.route("/api/location", methods=["POST"])
def api_location():
    if "user_id" not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 401
    data = request.get_json(silent=True) or {}
    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid latitude/longitude"}), 400
    conn = sqlite3.connect("safety_belt.db")
    conn.execute("""INSERT INTO device_locations(user_id,latitude,longitude,source,updated_at)\n                   VALUES(?,?,?,?,datetime('now'))\n                   ON CONFLICT(user_id) DO UPDATE SET latitude=excluded.latitude, longitude=excluded.longitude, source=excluded.source, updated_at=excluded.updated_at""",
                 (session["user_id"], latitude, longitude, data.get("source", "Browser/Device")))
    conn.commit(); conn.close()
    return jsonify({"status":"Location updated", "latitude":latitude, "longitude":longitude})


@app.route("/api/alerts", methods=["GET"])
def api_alerts():
    if "user_id" not in session:
        return jsonify({"status":"error", "message":"User not logged in"}), 401
    conn = sqlite3.connect("safety_belt.db")
    row = conn.execute("SELECT id,latitude,longitude,status,date_time FROM emergencies WHERE user_id=? ORDER BY id DESC LIMIT 1", (session["user_id"],)).fetchone()
    conn.close()
    if not row:
        return jsonify({"status":"ok", "emergency":None})
    return jsonify({"status":"ok", "emergency":{"id":row[0],"latitude":row[1],"longitude":row[2],"status":row[3],"date_time":row[4]}})


# =========================
# ESP32 SOS API
# =========================

@app.route("/api/sos", methods=["POST"])
def api_sos():
    if not authenticate_device(request):
        return jsonify({"status":"error", "message":"Invalid device token"}), 401

    data = request.get_json(silent=True) or {}
    user = get_device_user()
    if not user:
        return jsonify({"status":"error", "message":"No SafeBelt user configured for the device"}), 500
    user_id = user[0]

    conn = sqlite3.connect("safety_belt.db")
    cursor = conn.cursor()

    # Prefer GPS coordinates supplied by ESP32. If unavailable, use the latest
    # dashboard/device location. This lets the demo work before NEO-6M is added.
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    source = data.get("source", "ESP32")
    if latitude is None or longitude is None:
        loc = cursor.execute("SELECT latitude,longitude,source FROM device_locations WHERE user_id=?", (user_id,)).fetchone()
        if loc:
            latitude, longitude = loc[0], loc[1]
            source = loc[2]
    try:
        latitude = float(latitude); longitude = float(longitude)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180): raise ValueError
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"status":"error", "message":"Location unavailable. Open the dashboard once to save device location, or send GPS coordinates from ESP32."}), 400

    cursor.execute("INSERT INTO emergencies(user_id,latitude,longitude,status,date_time) VALUES(?,?,?,?,datetime('now'))",
                   (user_id, latitude, longitude, "Emergency Detected"))
    emergency_id = cursor.lastrowid
    contacts = cursor.execute("SELECT name,mobile FROM trusted_contacts WHERE user_id=?", (user_id,)).fetchall()
    map_url = f"https://www.google.com/maps?q={latitude},{longitude}"
    message = f"SAFE BELT EMERGENCY! {user[1]} may need help. Location: {map_url}"

    results=[]
    for name,mobile in contacts:
        status = send_sms(mobile, message)
        cursor.execute("INSERT INTO notification_logs(emergency_id,contact_name,mobile,status,sent_at) VALUES(?,?,?,?,datetime('now'))",
                       (emergency_id,name,mobile,status))
        results.append({"name":name,"mobile":mobile,"status":status})

    conn.commit(); conn.close()
    print("ESP32 SOS:", emergency_id, latitude, longitude, source, results)
    return jsonify({"status":"Emergency Detected", "emergency_id":emergency_id,
                    "latitude":latitude,"longitude":longitude,"source":source,
                    "map_url":map_url,"sms":results})


# =========================
# SOS / EMERGENCY SIMULATION
# =========================

@app.route("/sos", methods=["POST"])
def sos():

    # Check login
    if "user_id" not in session:
        return jsonify({
            "status": "error",
            "message": "User not logged in"
        }), 401

    user_id = session["user_id"]

    try:

        data = request.get_json(silent=True)

        if not data:
            return jsonify({
                "status": "error",
                "message": "No emergency data received"
            }), 400

        latitude = data.get("latitude")
        longitude = data.get("longitude")

        # source tells us where the coordinates came from.
        # For now the browser/device sends "Browser/Device".
        # Later the ESP32 + NEO-6M GPS will send "GPS".
        source_key = data.get("source", "Browser/Device")
        source_label = LOCATION_SOURCE_LABELS.get(
            source_key, LOCATION_SOURCE_LABELS["Browser/Device"]
        )

        if latitude is None or longitude is None:
            return jsonify({
                "status": "error",
                "message": "Location not available"
            }), 400

        # Validate that latitude/longitude are real numbers
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Invalid latitude/longitude received"
            }), 400

        if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
            return jsonify({
                "status": "error",
                "message": "Latitude/longitude out of valid range"
            }), 400

        conn = sqlite3.connect("safety_belt.db")
        cursor = conn.cursor()

        # Get only this user's trusted contacts
        cursor.execute("""
            SELECT name, mobile
            FROM trusted_contacts
            WHERE user_id = ?
        """, (user_id,))

        trusted_contacts = cursor.fetchall()

        # Emergency message for simulation
        alert_message = (
            f"EMERGENCY! Location: "
            f"https://www.google.com/maps?q={latitude},{longitude}"
        )

        # Show information in VS Code terminal
        print("\n==============================")
        print("      EMERGENCY DETECTED")
        print("==============================")
        print("User ID:", user_id)
        print("Latitude:", latitude)
        print("Longitude:", longitude)
        print("Source:", source_label)
        print("Trusted Contacts:", trusted_contacts)
        print("Alert Message:", alert_message)
        print("==============================\n")

        # Save emergency for THIS USER
        # (latitude/longitude columns stay source-agnostic so
        # NEO-6M GPS can be plugged in later with no schema change)
        cursor.execute("""
            INSERT INTO emergencies
            (user_id, latitude, longitude, status, date_time)
            VALUES (?, ?, ?, ?, datetime('now'))
        """, (
            user_id,
            latitude,
            longitude,
            "Emergency Detected"
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "status": "Emergency Detected",
            "message": "Emergency saved successfully",
            "latitude": latitude,
            "longitude": longitude,
            "source": source_label
        })

    except Exception as e:
        # Never leak a Python stack trace to the user interface
        print("SOS ERROR:", e)
        return jsonify({
            "status": "error",
            "message": "Something went wrong while saving the emergency. Please try again."
        }), 500


# =========================
# CREATE DATABASE
# =========================

create_database()


# =========================
# RUN APPLICATION
# =========================

if __name__ == "__main__":
    # host="0.0.0.0" so devices on the same Wi-Fi (like the future
    # ESP32) can reach this server at http://LAPTOP_IP:5000
    app.run(host="0.0.0.0", port=5000, debug=True)