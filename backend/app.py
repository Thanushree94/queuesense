
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

from database import initialize_database, get_db_connection


app = Flask(__name__)

app.secret_key = os.environ.get("QUEUESENSE_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Set QUEUESENSE_SECRET_KEY before starting Flask.")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False
)

INDIA_TZ = ZoneInfo("Asia/Kolkata")

DEPARTMENTS = {
    "opd": {"name": "General OPD", "prefix": "O"},
    "ped": {"name": "Pediatrics", "prefix": "P"},
    "lab": {"name": "Laboratory", "prefix": "L"},
    "ph": {"name": "Pharmacy", "prefix": "M"}
}

PRIORITIES = ["Emergency", "Urgent", "Routine"]

REASONS = [
    "Fever or cold", "Pain", "Injury", "Follow-up visit",
    "Report collection", "Medicine pickup", "Other"
]


def db():
    connection = get_db_connection()
    connection.row_factory = sqlite3.Row
    return connection


def now_india():
    return datetime.now(INDIA_TZ)


def patient_session():
    return session.get("patient_id")


def staff_session():
    return session.get("staff_id")


def staff_required():
    if not staff_session():
        return jsonify({
            "status": "error",
            "message": "Staff login required"
        }), 401
    return None


initialize_database()


@app.route("/")
def home():
    return send_from_directory(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "index.html"
    )


@app.route("/api/health")
def health():
    return jsonify({
        "status": "success",
        "message": "QueueSense backend is running"
    })


# ---------------- PATIENT REGISTRATION ----------------

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    name = data.get("full_name")
    mobile = data.get("mobile_number")
    pin = data.get("pin")
    age = data.get("age")
    gender = data.get("gender")

    if not all(isinstance(x, str) for x in [name, mobile, pin, gender]):
        return jsonify({"status": "error", "message": "Invalid input"}), 400

    name, mobile, gender = name.strip(), mobile.strip(), gender.strip()

    if not name or not mobile.isdigit() or len(mobile) != 10:
        return jsonify({"status": "error", "message": "Invalid name or mobile number"}), 400

    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        return jsonify({"status": "error", "message": "PIN must contain 4–6 digits"}), 400

    try:
        age = int(age)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid age"}), 400

    if not 1 <= age <= 119 or gender not in ["Female", "Male", "Other"]:
        return jsonify({"status": "error", "message": "Invalid age or gender"}), 400

    try:
        with db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO patients
                (full_name, mobile_number, pin_hash, age, gender)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, mobile, generate_password_hash(pin), age, gender)
            )
            patient_id = cursor.lastrowid

        return jsonify({
            "status": "success",
            "message": "Patient registered successfully",
            "patient_id": patient_id
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({
            "status": "error",
            "message": "Mobile number already registered"
        }), 409


# ---------------- PATIENT LOGIN ----------------

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    mobile = data.get("mobile_number")
    pin = data.get("pin")

    if not isinstance(mobile, str) or not isinstance(pin, str):
        return jsonify({"status": "error", "message": "Invalid credentials"}), 400

    with db() as conn:
        patient = conn.execute(
            "SELECT * FROM patients WHERE mobile_number = ?",
            (mobile.strip(),)
        ).fetchone()

    if not patient or not check_password_hash(patient["pin_hash"], pin):
        return jsonify({"status": "error", "message": "Invalid mobile number or PIN"}), 401

    session.clear()
    session["patient_id"] = patient["patient_id"]

    return jsonify({
        "status": "success",
        "message": "Login successful",
        "patient": {
            "patient_id": patient["patient_id"],
            "full_name": patient["full_name"],
            "mobile_number": patient["mobile_number"],
            "age": patient["age"],
            "gender": patient["gender"]
        }
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Logged out"})


@app.route("/api/me")
def me():
    patient_id = patient_session()

    if not patient_id:
        return jsonify({"status": "error", "message": "Login required"}), 401

    with db() as conn:
        patient = conn.execute(
            """
            SELECT patient_id, full_name, mobile_number, age, gender
            FROM patients WHERE patient_id = ?
            """,
            (patient_id,)
        ).fetchone()

    if not patient:
        session.clear()
        return jsonify({"status": "error", "message": "Patient not found"}), 404

    return jsonify({"status": "success", "patient": dict(patient)})


# ---------------- STAFF REGISTRATION ----------------

@app.route("/api/staff/register", methods=["POST"])
def staff_register():
    registration_key = os.environ.get("QUEUESENSE_STAFF_REGISTRATION_KEY")

    if not registration_key:
        return jsonify({
            "status": "error",
            "message": "Staff registration is not configured"
        }), 503

    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")
    supplied_key = data.get("registration_key")

    if not all(isinstance(x, str) for x in [username, password, supplied_key]):
        return jsonify({"status": "error", "message": "Invalid input"}), 400

    if supplied_key != registration_key:
        return jsonify({"status": "error", "message": "Invalid registration key"}), 403

    username = username.strip().lower()

    if len(username) < 4 or len(password) < 8:
        return jsonify({
            "status": "error",
            "message": "Username needs 4 characters and password needs 8"
        }), 400

    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO staff (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password))
            )

        return jsonify({
            "status": "success",
            "message": "Staff account created successfully"
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({"status": "error", "message": "Username already exists"}), 409


# ---------------- STAFF LOGIN ----------------

@app.route("/api/staff/login", methods=["POST"])
def staff_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")

    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify({"status": "error", "message": "Invalid credentials"}), 400

    with db() as conn:
        staff = conn.execute(
            "SELECT * FROM staff WHERE username = ?",
            (username.strip().lower(),)
        ).fetchone()

    if not staff or not check_password_hash(staff["password_hash"], password):
        return jsonify({"status": "error", "message": "Invalid username or password"}), 401

    session.clear()
    session["staff_id"] = staff["staff_id"]

    return jsonify({
        "status": "success",
        "message": "Staff login successful",
        "staff": {
            "staff_id": staff["staff_id"],
            "username": staff["username"]
        }
    })


@app.route("/api/staff/me")
def staff_me():
    error = staff_required()
    if error:
        return error

    with db() as conn:
        staff = conn.execute(
            "SELECT staff_id, username FROM staff WHERE staff_id = ?",
            (staff_session(),)
        ).fetchone()

    if not staff:
        session.clear()
        return jsonify({"status": "error", "message": "Staff account not found"}), 404

    return jsonify({"status": "success", "staff": dict(staff)})


@app.route("/api/staff/logout", methods=["POST"])
def staff_logout():
    session.clear()
    return jsonify({"status": "success", "message": "Staff logged out"})


# ---------------- PATIENT TOKEN BOOKING ----------------

@app.route("/api/book", methods=["POST"])
def book_token():
    patient_id = patient_session()

    if not patient_id:
        return jsonify({"status": "error", "message": "Patient login required"}), 401

    data = request.get_json(silent=True) or {}
    department = data.get("department")
    priority = data.get("priority", "Routine")
    reason = data.get("reason")

    if department not in DEPARTMENTS or priority not in PRIORITIES or reason not in REASONS:
        return jsonify({"status": "error", "message": "Invalid booking details"}), 400

    with db() as conn:
        patient = conn.execute(
            "SELECT * FROM patients WHERE patient_id = ?",
            (patient_id,)
        ).fetchone()

        if not patient:
            session.clear()
            return jsonify({"status": "error", "message": "Patient not found"}), 404

        now = now_india()
        booking_date = now.strftime("%Y-%m-%d")
        booking_time = now.strftime("%H:%M:%S")
        prefix = DEPARTMENTS[department]["prefix"]

        try:
            conn.execute("BEGIN IMMEDIATE")

            existing = conn.execute(
                """
                SELECT token_id FROM queue_tokens
                WHERE patient_id = ? AND department = ? AND booking_date = ?
                AND status NOT IN ('Cancelled', 'Expired')
                """,
                (patient_id, department, booking_date)
            ).fetchone()

            if existing:
                return jsonify({
                    "status": "error",
                    "message": "You already have a token for this department today"
                }), 409

            last = conn.execute(
                """
                SELECT MAX(token_number) AS last_number
                FROM queue_tokens
                WHERE department = ? AND booking_date = ?
                """,
                (department, booking_date)
            ).fetchone()

            number = (last["last_number"] or 0) + 1

            cursor = conn.execute(
                """
                INSERT INTO queue_tokens (
                    patient_id, token_number, token_prefix, department,
                    priority, status, reason, patient_name, patient_age,
                    patient_mobile, booking_date, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'Waiting', ?, ?, ?, ?, ?, ?)
                """,
                (
                    patient_id, number, prefix, department, priority,
                    reason, patient["full_name"], patient["age"],
                    patient["mobile_number"], booking_date, booking_time
                )
            )

            token_id = cursor.lastrowid

        except sqlite3.Error:
            app.logger.exception("Token booking failed")
            return jsonify({"status": "error", "message": "Unable to book token"}), 500

    return jsonify({
        "status": "success",
        "message": "Token booked successfully",
        "token": {
            "token_id": token_id,
            "token_number": number,
            "token_prefix": prefix,
            "token_display": f"{prefix}{number:03d}",
            "department": DEPARTMENTS[department]["name"],
            "priority": priority,
            "reason": reason,
            "patient_name": patient["full_name"],
            "booking_date": booking_date,
            "booking_time": booking_time,
            "status": "Waiting"
        }
    }), 201


# ---------------- PATIENT TOKEN HISTORY ----------------

@app.route("/api/my-tokens")
def my_tokens():
    patient_id = patient_session()

    if not patient_id:
        return jsonify({"status": "error", "message": "Patient login required"}), 401

    with db() as conn:
        tokens = conn.execute(
            """
            SELECT * FROM queue_tokens
            WHERE patient_id = ?
            ORDER BY booking_date DESC, created_at DESC, token_id DESC
            """,
            (patient_id,)
        ).fetchall()

    result = []

    for token in tokens:
        item = dict(token)
        item["token_display"] = f"{item['token_prefix']}{item['token_number']:03d}"
        result.append(item)

    return jsonify({"status": "success", "tokens": result})


# ---------------- STAFF QUEUE ----------------

@app.route("/api/staff/queue", methods=["GET"])
def staff_queue():
    error = staff_required()
    if error:
        return error

    department = request.args.get("department")
    today = now_india().strftime("%Y-%m-%d")

    query = """
        SELECT q.*, c.counter_name
        FROM queue_tokens q
        LEFT JOIN counters c ON q.counter_id = c.counter_id
        WHERE q.booking_date = ?
    """
    params = [today]

    if department:
        if department not in DEPARTMENTS:
            return jsonify({"status": "error", "message": "Invalid department"}), 400
        query += " AND q.department = ?"
        params.append(department)

    query += """
        ORDER BY
        CASE q.priority
            WHEN 'Emergency' THEN 1
            WHEN 'Urgent' THEN 2
            ELSE 3
        END,
        q.token_number ASC
    """

    with db() as conn:
        tokens = conn.execute(query, params).fetchall()

    result = []

    for token in tokens:
        item = dict(token)
        item["token_display"] = f"{item['token_prefix']}{item['token_number']:03d}"
        result.append(item)

    return jsonify({"status": "success", "tokens": result})


# ---------------- STAFF CALL NEXT PATIENT ----------------

@app.route("/api/staff/call-next", methods=["POST"])
def call_next():
    error = staff_required()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    department = data.get("department")

    if department not in DEPARTMENTS:
        return jsonify({"status": "error", "message": "Invalid department"}), 400

    today = now_india().strftime("%Y-%m-%d")
    timestamp = now_india().strftime("%H:%M:%S")

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        serving = conn.execute(
            """
            SELECT token_id FROM queue_tokens
            WHERE department = ? AND booking_date = ? AND status = 'Serving'
            """,
            (department, today)
        ).fetchone()

        if serving:
            return jsonify({
                "status": "error",
                "message": "Complete the currently serving token first"
            }), 409

        token = conn.execute(
            """
            SELECT token_id FROM queue_tokens
            WHERE department = ? AND booking_date = ? AND status = 'Waiting'
            ORDER BY
                CASE priority
                    WHEN 'Emergency' THEN 1
                    WHEN 'Urgent' THEN 2
                    ELSE 3
                END,
                token_number ASC
            LIMIT 1
            """,
            (department, today)
        ).fetchone()

        if not token:
            return jsonify({"status": "error", "message": "No waiting patients"}), 404

        conn.execute(
            """
            UPDATE queue_tokens
            SET status = 'Serving', service_started_at = ?
            WHERE token_id = ?
            """,
            (timestamp, token["token_id"])
        )

        updated = conn.execute(
            "SELECT * FROM queue_tokens WHERE token_id = ?",
            (token["token_id"],)
        ).fetchone()

    item = dict(updated)
    item["token_display"] = f"{item['token_prefix']}{item['token_number']:03d}"

    return jsonify({
        "status": "success",
        "message": "Next patient called",
        "token": item
    })


# ---------------- STAFF COMPLETE / CANCEL TOKEN ----------------

@app.route("/api/staff/token/<int:token_id>/status", methods=["PATCH"])
def update_token_status(token_id):
    error = staff_required()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    new_status = data.get("status")

    allowed = {
        "Completed": ["Serving"],
        "Cancelled": ["Waiting", "Serving"],
        "No-show": ["Serving"]
    }

    if new_status not in allowed:
        return jsonify({"status": "error", "message": "Invalid status"}), 400

    now = now_india().strftime("%H:%M:%S")

    with db() as conn:
        token = conn.execute(
            "SELECT status FROM queue_tokens WHERE token_id = ?",
            (token_id,)
        ).fetchone()

        if not token:
            return jsonify({"status": "error", "message": "Token not found"}), 404

        if token["status"] not in allowed[new_status]:
            return jsonify({
                "status": "error",
                "message": f"Cannot change {token['status']} to {new_status}"
            }), 409

        if new_status == "Completed":
            conn.execute(
                """
                UPDATE queue_tokens
                SET status = ?, completed_at = ?
                WHERE token_id = ?
                """,
                (new_status, now, token_id)
            )
        else:
            conn.execute(
                "UPDATE queue_tokens SET status = ? WHERE token_id = ?",
                (new_status, token_id)
            )

    return jsonify({
        "status": "success",
        "message": f"Token marked as {new_status}"
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
