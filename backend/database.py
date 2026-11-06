
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "queuesense.db"


def get_db_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    connection = get_db_connection()

    try:
        # Create patient, staff and counter tables.
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS patients (
                patient_id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                mobile_number TEXT NOT NULL UNIQUE,
                pin_hash TEXT NOT NULL,
                age INTEGER,
                gender TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS staff (
                staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS counters (
                counter_id INTEGER PRIMARY KEY AUTOINCREMENT,
                counter_name TEXT NOT NULL,
                department TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
        """)

        # Upgrade older patient tables safely.
        patient_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(patients)"
            ).fetchall()
        }

        if "age" not in patient_columns:
            connection.execute(
                "ALTER TABLE patients ADD COLUMN age INTEGER"
            )

        if "gender" not in patient_columns:
            connection.execute(
                "ALTER TABLE patients ADD COLUMN gender TEXT"
            )

        # Inspect the existing queue table.
        queue_columns = {
            row["name"]: row
            for row in connection.execute(
                "PRAGMA table_info(queue_tokens)"
            ).fetchall()
        }

        required_columns = {
            "token_prefix",
            "patient_name",
            "patient_age",
            "patient_mobile",
            "reason"
        }

        needs_rebuild = (
            not required_columns.issubset(queue_columns)
            or (
                "patient_id" in queue_columns
                and queue_columns["patient_id"]["notnull"] == 1
            )
        )

        if needs_rebuild:

            connection.execute(
                "DROP TABLE IF EXISTS queue_tokens_new"
            )

            # Create the upgraded queue table.
            connection.execute("""
                CREATE TABLE queue_tokens_new (
                    token_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER,
                    token_number INTEGER NOT NULL,
                    token_prefix TEXT NOT NULL,
                    department TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'Routine',
                    status TEXT NOT NULL DEFAULT 'Waiting',
                    reason TEXT,
                    patient_name TEXT,
                    patient_age INTEGER,
                    patient_mobile TEXT,
                    booking_date TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    service_started_at TEXT,
                    completed_at TEXT,
                    counter_id INTEGER,

                    FOREIGN KEY (patient_id)
                        REFERENCES patients(patient_id),

                    FOREIGN KEY (counter_id)
                        REFERENCES counters(counter_id),

                    UNIQUE (department, booking_date, token_number),

                    CHECK (priority IN (
                        'Emergency', 'Urgent', 'Routine'
                    )),

                    CHECK (status IN (
                        'Waiting',
                        'Serving',
                        'Completed',
                        'Cancelled',
                        'No-show',
                        'Expired'
                    ))
                )
            """)

            # Preserve existing queue records when upgrading.
            if queue_columns:
                old_columns = set(queue_columns.keys())

                def old_value(column, fallback):
                    if column in old_columns:
                        return f"q.{column}"
                    return fallback

                connection.execute(f"""
                    INSERT INTO queue_tokens_new (
                        token_id,
                        patient_id,
                        token_number,
                        token_prefix,
                        department,
                        priority,
                        status,
                        reason,
                        patient_name,
                        patient_age,
                        patient_mobile,
                        booking_date,
                        created_at,
                        service_started_at,
                        completed_at,
                        counter_id
                    )
                    SELECT
                        q.token_id,
                        q.patient_id,
                        q.token_number,

                        CASE q.department
                            WHEN 'opd' THEN 'O'
                            WHEN 'ped' THEN 'P'
                            WHEN 'lab' THEN 'L'
                            WHEN 'ph' THEN 'M'
                            ELSE 'T'
                        END,

                        q.department,
                        q.priority,
                        q.status,
                        {old_value('reason', 'NULL')},
                        p.full_name,
                        p.age,
                        p.mobile_number,
                        q.booking_date,
                        q.created_at,
                        q.service_started_at,
                        q.completed_at,
                        {old_value('counter_id', 'NULL')}

                    FROM queue_tokens q

                    LEFT JOIN patients p
                        ON p.patient_id = q.patient_id
                """)

                connection.execute(
                    "DROP TABLE queue_tokens"
                )

            connection.execute("""
                ALTER TABLE queue_tokens_new
                RENAME TO queue_tokens
            """)

        else:
            # Create queue table if it does not exist.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS queue_tokens (
                    token_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER,
                    token_number INTEGER NOT NULL,
                    token_prefix TEXT NOT NULL,
                    department TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'Routine',
                    status TEXT NOT NULL DEFAULT 'Waiting',
                    reason TEXT,
                    patient_name TEXT,
                    patient_age INTEGER,
                    patient_mobile TEXT,
                    booking_date TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    service_started_at TEXT,
                    completed_at TEXT,
                    counter_id INTEGER,

                    FOREIGN KEY (patient_id)
                        REFERENCES patients(patient_id),

                    FOREIGN KEY (counter_id)
                        REFERENCES counters(counter_id),

                    UNIQUE (department, booking_date, token_number),

                    CHECK (priority IN (
                        'Emergency', 'Urgent', 'Routine'
                    )),

                    CHECK (status IN (
                        'Waiting',
                        'Serving',
                        'Completed',
                        'Cancelled',
                        'No-show',
                        'Expired'
                    ))
                )
            """)

        # Create indexes for faster database queries.
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_department_date
            ON queue_tokens(department, booking_date, status)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_patient
            ON queue_tokens(patient_id)
        """)

        # Insert default hospital counters without duplicates.
        default_counters = [
            ("OPD Counter 1", "opd"),
            ("OPD Counter 2", "opd"),
            ("Pediatrics Counter 1", "ped"),
            ("Laboratory Counter 1", "lab"),
            ("Pharmacy Counter 1", "ph")
        ]

        for counter_name, department in default_counters:
            connection.execute("""
                INSERT INTO counters (counter_name, department)
                SELECT ?, ?
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM counters
                    WHERE counter_name = ?
                    AND department = ?
                )
            """, (
                counter_name,
                department,
                counter_name,
                department
            ))

        connection.commit()

        print("QueueSense database upgraded successfully.")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


if __name__ == "__main__":
    initialize_database()
