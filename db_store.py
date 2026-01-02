import sqlite3
from pathlib import Path
from typing import Optional, Tuple, List

DB_PATH = Path("ride_log.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    # Patients
    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    )
    """)

    # Rides
    cur.execute("""
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        ride_date TEXT NOT NULL,          -- YYYY-MM-DD
        distance_km REAL NOT NULL,
        duration_min INTEGER NOT NULL,
        rpe INTEGER,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id)
    )
    """)

    # Weekly plan
    cur.execute("""
    CREATE TABLE IF NOT EXISTS weekly_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        week_start TEXT NOT NULL,         -- Monday YYYY-MM-DD
        planned_km REAL,
        planned_hours REAL,
        phase TEXT,
        notes TEXT,
        UNIQUE(patient_id, week_start),
        FOREIGN KEY (patient_id) REFERENCES patients(id)
    )
    """)

    # Strava tokens (per patient)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strava_tokens (
        patient_id INTEGER PRIMARY KEY,
        access_token TEXT NOT NULL,
        refresh_token TEXT NOT NULL,
        expires_at INTEGER NOT NULL,      -- epoch seconds
        athlete_id INTEGER,
        scope TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id)
    )
    """)

    # Dedup table so we don't re-import the same Strava activity twice
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strava_synced (
        patient_id INTEGER NOT NULL,
        strava_activity_id INTEGER NOT NULL,
        synced_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (patient_id, strava_activity_id),
        FOREIGN KEY (patient_id) REFERENCES patients(id)
    )
    """)

    conn.commit()
    conn.close()


def upsert_patient(name: str) -> int:
    name = name.strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO patients(name) VALUES(?)", (name,))
    conn.commit()
    cur.execute("SELECT id FROM patients WHERE name = ?", (name,))
    pid = cur.fetchone()[0]
    conn.close()
    return pid


def list_patients() -> List[Tuple[int, str]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM patients ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    return rows


def add_ride(
    patient_id: int,
    ride_date: str,
    distance_km: float,
    duration_min: int,
    rpe: Optional[int],
    notes: Optional[str],
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO rides(patient_id, ride_date, distance_km, duration_min, rpe, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (patient_id, ride_date, distance_km, duration_min, rpe, notes))
    conn.commit()
    conn.close()


def fetch_rides(patient_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None):
    conn = get_conn()
    cur = conn.cursor()
    q = "SELECT ride_date, distance_km, duration_min, rpe, notes FROM rides WHERE patient_id = ?"
    params = [patient_id]

    if start_date:
        q += " AND ride_date >= ?"
        params.append(start_date)
    if end_date:
        q += " AND ride_date <= ?"
        params.append(end_date)

    q += " ORDER BY ride_date DESC"
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_week_plan(
    patient_id: int,
    week_start: str,
    planned_km: Optional[float],
    planned_hours: Optional[float],
    phase: Optional[str],
    notes: Optional[str],
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO weekly_plan(patient_id, week_start, planned_km, planned_hours, phase, notes)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(patient_id, week_start) DO UPDATE SET
            planned_km=excluded.planned_km,
            planned_hours=excluded.planned_hours,
            phase=excluded.phase,
            notes=excluded.notes
    """, (patient_id, week_start, planned_km, planned_hours, phase, notes))
    conn.commit()
    conn.close()


def fetch_week_plans(patient_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT week_start, planned_km, planned_hours, phase, notes
        FROM weekly_plan
        WHERE patient_id = ?
        ORDER BY week_start
    """, (patient_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


# -----------------------------
# Strava helpers (per patient)
# -----------------------------

def save_strava_tokens(
    patient_id: int,
    access_token: str,
    refresh_token: str,
    expires_at: int,
    athlete_id,
    scope,
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO strava_tokens(patient_id, access_token, refresh_token, expires_at, athlete_id, scope)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(patient_id) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            expires_at=excluded.expires_at,
            athlete_id=excluded.athlete_id,
            scope=excluded.scope,
            updated_at=datetime('now')
    """, (patient_id, access_token, refresh_token, expires_at, athlete_id, scope))
    conn.commit()
    conn.close()


def get_strava_tokens(patient_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT access_token, refresh_token, expires_at, athlete_id, scope
        FROM strava_tokens
        WHERE patient_id = ?
    """, (patient_id,))
    row = cur.fetchone()
    conn.close()
    return row


def mark_activity_synced(patient_id: int, activity_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO strava_synced(patient_id, strava_activity_id)
        VALUES (?, ?)
    """, (patient_id, activity_id))
    conn.commit()
    conn.close()


def is_activity_synced(patient_id: int, activity_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM strava_synced
        WHERE patient_id = ? AND strava_activity_id = ?
    """, (patient_id, activity_id))
    ok = cur.fetchone() is not None
    conn.close()
    return ok
