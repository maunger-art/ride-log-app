import os
import sqlite3
from typing import Optional, List, Tuple, Any

# -------------------------------------------------------------------
# Database connection
# -------------------------------------------------------------------

DB_FILENAME = "ride_log.db"

def get_db_path() -> str:
    # Store DB alongside this file for Streamlit Cloud compatibility
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, DB_FILENAME)

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# -------------------------------------------------------------------
# Schema init
# -------------------------------------------------------------------

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # -----------------------------
    # Core app tables
    # -----------------------------

    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        ride_date TEXT NOT NULL,          -- YYYY-MM-DD
        distance_km REAL NOT NULL DEFAULT 0,
        duration_min INTEGER NOT NULL DEFAULT 0,
        rpe INTEGER,                      -- nullable
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_rides_patient_date
    ON rides(patient_id, ride_date)
    """)

    # Weekly plan table: IMPORTANT patient_id is NOT NULL
    cur.execute("""
    CREATE TABLE IF NOT EXISTS weekly_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        week_start TEXT NOT NULL,         -- YYYY-MM-DD (Monday)
        planned_km REAL,
        planned_hours REAL,
        phase TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_plan_patient_week
    ON weekly_plan(patient_id, week_start)
    """)

    # -----------------------------
    # Strava integration tables
    # -----------------------------

    cur.execute("""
    CREATE TABLE IF NOT EXISTS strava_tokens (
        patient_id INTEGER PRIMARY KEY,
        access_token TEXT NOT NULL,
        refresh_token TEXT NOT NULL,
        expires_at INTEGER NOT NULL,      -- epoch seconds
        athlete_id INTEGER,
        scope TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS strava_synced (
        patient_id INTEGER NOT NULL,
        strava_activity_id INTEGER NOT NULL,
        synced_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (patient_id, strava_activity_id),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    # -----------------------------
    # Strength standards + S&C planning tables (MVP)
    # -----------------------------

    cur.execute("""
    CREATE TABLE IF NOT EXISTS exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        movement_pattern TEXT,
        laterality TEXT NOT NULL,     -- bilateral / unilateral
        modality TEXT,               -- barbell / dumbbell / kettlebell / machine / bodyweight / band
        primary_muscles TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rep_schemes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal TEXT NOT NULL,          -- endurance / hypertrophy / strength / power
        phase TEXT,                  -- optional: base/build/peak/maintain
        reps_min INTEGER NOT NULL,
        reps_max INTEGER NOT NULL,
        sets_min INTEGER NOT NULL,
        sets_max INTEGER NOT NULL,
        pct_1rm_min REAL,
        pct_1rm_max REAL,
        rpe_min REAL,
        rpe_max REAL,
        rest_sec_min INTEGER,
        rest_sec_max INTEGER,
        intent TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS norm_strength_standards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exercise_id INTEGER NOT NULL,
        sex TEXT NOT NULL,           -- male / female
        age_min INTEGER NOT NULL,
        age_max INTEGER NOT NULL,
        metric TEXT NOT NULL,        -- rel_1rm_bw or pullup_reps
        poor REAL,
        fair REAL,
        good REAL,
        excellent REAL,
        source TEXT,
        notes TEXT,
        FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients_profile (
        patient_id INTEGER PRIMARY KEY,
        sex TEXT,                    -- male/female
        dob TEXT,                    -- YYYY-MM-DD (optional)
        bodyweight_kg REAL,
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS patient_strength_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        exercise_id INTEGER NOT NULL,
        test_date TEXT NOT NULL,     -- YYYY-MM-DD
        estimated_1rm_kg REAL,
        reps INTEGER,
        load_kg REAL,
        side TEXT,                   -- left/right/bilateral
        notes TEXT,
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
    )
    """)

    conn.commit()
    conn.close()


# -------------------------------------------------------------------
# Patients
# -------------------------------------------------------------------

def upsert_patient(name: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO patients(name)
        VALUES (?)
        ON CONFLICT(name) DO NOTHING
    """, (name,))
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


# -------------------------------------------------------------------
# Rides
# -------------------------------------------------------------------

def add_ride(patient_id: int, ride_date: str, distance_km: float, duration_min: int,
             rpe: Optional[int], notes: Optional[str]):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO rides(patient_id, ride_date, distance_km, duration_min, rpe, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (patient_id, ride_date, float(distance_km), int(duration_min), rpe, notes))
    conn.commit()
    conn.close()

def fetch_rides(patient_id: int, limit: int = 2000) -> List[Tuple[str, float, int, Optional[int], Optional[str]]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ride_date, distance_km, duration_min, rpe, notes
        FROM rides
        WHERE patient_id = ?
        ORDER BY ride_date DESC, id DESC
        LIMIT ?
    """, (patient_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


# -------------------------------------------------------------------
# Weekly plan
# -------------------------------------------------------------------

def upsert_week_plan(patient_id: int, week_start: str,
                     planned_km: Optional[float],
                     planned_hours: Optional[float],
                     phase: Optional[str],
                     notes: Optional[str]):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO weekly_plan(patient_id, week_start, planned_km, planned_hours, phase, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(patient_id, week_start) DO UPDATE SET
            planned_km=excluded.planned_km,
            planned_hours=excluded.planned_hours,
            phase=excluded.phase,
            notes=excluded.notes,
            updated_at=datetime('now')
    """, (patient_id, week_start, planned_km, planned_hours, phase, notes))
    conn.commit()
    conn.close()

def fetch_week_plans(patient_id: int) -> List[Tuple[str, Optional[float], Optional[float], Optional[str], Optional[str]]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT week_start, planned_km, planned_hours, phase, notes
        FROM weekly_plan
        WHERE patient_id = ?
        ORDER BY week_start ASC
    """, (patient_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


# -------------------------------------------------------------------
# Strava tokens + sync
# -------------------------------------------------------------------

def save_strava_tokens(patient_id: int, access_token: str, refresh_token: str, expires_at: int,
                       athlete_id: Optional[int], scope: Optional[str]):
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
    """, (patient_id, access_token, refresh_token, int(expires_at), athlete_id, scope))
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

def mark_activity_synced(patient_id: int, activity_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO strava_synced(patient_id, strava_activity_id)
        VALUES (?, ?)
    """, (patient_id, int(activity_id)))
    conn.commit()
    conn.close()

def is_activity_synced(patient_id: int, activity_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM strava_synced
        WHERE patient_id = ? AND strava_activity_id = ?
        LIMIT 1
    """, (patient_id, int(activity_id)))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


# -------------------------------------------------------------------
# Strength standards + rep schemes helpers
# -------------------------------------------------------------------

def count_norm_rows() -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM norm_strength_standards")
    n = cur.fetchone()[0]
    conn.close()
    return n

def list_exercises():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, movement_pattern, laterality, modality
        FROM exercises
        ORDER BY name
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def upsert_exercise(name: str, movement_pattern: Optional[str], laterality: str, modality: Optional[str],
                    primary_muscles: Optional[str] = None, notes: Optional[str] = None) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO exercises(name, movement_pattern, laterality, modality, primary_muscles, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            movement_pattern=excluded.movement_pattern,
            laterality=excluded.laterality,
            modality=excluded.modality,
            primary_muscles=excluded.primary_muscles,
            notes=excluded.notes
    """, (name, movement_pattern, laterality, modality, primary_muscles, notes))
    conn.commit()
    cur.execute("SELECT id FROM exercises WHERE name = ?", (name,))
    ex_id = cur.fetchone()[0]
    conn.close()
    return ex_id

def upsert_rep_scheme(goal: str, phase: Optional[str],
                      reps_min: int, reps_max: int,
                      sets_min: int, sets_max: int,
                      pct_1rm_min: Optional[float], pct_1rm_max: Optional[float],
                      rpe_min: Optional[float], rpe_max: Optional[float],
                      rest_sec_min: Optional[int], rest_sec_max: Optional[int],
                      intent: Optional[str]):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO rep_schemes(goal, phase, reps_min, reps_max, sets_min, sets_max,
                               pct_1rm_min, pct_1rm_max, rpe_min, rpe_max,
                               rest_sec_min, rest_sec_max, intent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (goal, phase, reps_min, reps_max, sets_min, sets_max,
          pct_1rm_min, pct_1rm_max, rpe_min, rpe_max,
          rest_sec_min, rest_sec_max, intent))
    conn.commit()
    conn.close()

def list_rep_schemes(goal: Optional[str] = None):
    conn = get_conn()
    cur = conn.cursor()
    if goal:
        cur.execute("""
            SELECT id, goal, phase, reps_min, reps_max, sets_min, sets_max,
                   pct_1rm_min, pct_1rm_max, rpe_min, rpe_max, rest_sec_min, rest_sec_max, intent
            FROM rep_schemes
            WHERE goal = ?
            ORDER BY goal, phase, reps_min
        """, (goal,))
    else:
        cur.execute("""
            SELECT id, goal, phase, reps_min, reps_max, sets_min, sets_max,
                   pct_1rm_min, pct_1rm_max, rpe_min, rpe_max, rest_sec_min, rest_sec_max, intent
            FROM rep_schemes
            ORDER BY goal, phase, reps_min
        """)
    rows = cur.fetchall()
    conn.close()
    return rows

def upsert_norm_standard(exercise_id: int, sex: str, age_min: int, age_max: int, metric: str,
                         poor: Optional[float], fair: Optional[float], good: Optional[float], excellent: Optional[float],
                         source: Optional[str] = None, notes: Optional[str] = None):
    conn = get_conn()
    cur = conn.cursor()

    # Replace row for uniqueness (exercise_id, sex, age_min, age_max, metric)
    cur.execute("""
        DELETE FROM norm_strength_standards
        WHERE exercise_id = ? AND sex = ? AND age_min = ? AND age_max = ? AND metric = ?
    """, (exercise_id, sex, age_min, age_max, metric))

    cur.execute("""
        INSERT INTO norm_strength_standards(exercise_id, sex, age_min, age_max, metric,
                                            poor, fair, good, excellent, source, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (exercise_id, sex, age_min, age_max, metric,
          poor, fair, good, excellent, source, notes))

    conn.commit()
    conn.close()

def get_norm_standard(exercise_id: int, sex: str, age: int, metric: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT poor, fair, good, excellent, source, notes, age_min, age_max
        FROM norm_strength_standards
        WHERE exercise_id = ? AND sex = ? AND metric = ?
          AND age_min <= ? AND age_max >= ?
        ORDER BY age_min DESC
        LIMIT 1
    """, (exercise_id, sex, metric, age, age))
    row = cur.fetchone()
    conn.close()
    return row

def upsert_patient_profile(patient_id: int, sex: Optional[str], dob: Optional[str], bodyweight_kg: Optional[float]):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO patients_profile(patient_id, sex, dob, bodyweight_kg)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(patient_id) DO UPDATE SET
            sex=excluded.sex,
            dob=excluded.dob,
            bodyweight_kg=excluded.bodyweight_kg,
            updated_at=datetime('now')
    """, (patient_id, sex, dob, bodyweight_kg))
    conn.commit()
    conn.close()

def get_patient_profile(patient_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT sex, dob, bodyweight_kg
        FROM patients_profile
        WHERE patient_id = ?
    """, (patient_id,))
    row = cur.fetchone()
    conn.close()
    return row

def insert_strength_test(patient_id: int, exercise_id: int, test_date: str,
                         estimated_1rm_kg: Optional[float],
                         reps: Optional[int],
                         load_kg: Optional[float],
                         side: Optional[str],
                         notes: Optional[str]):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO patient_strength_tests(patient_id, exercise_id, test_date, estimated_1rm_kg, reps, load_kg, side, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (patient_id, exercise_id, test_date, estimated_1rm_kg, reps, load_kg, side, notes))
    conn.commit()
    conn.close()

def list_strength_tests(patient_id: int, exercise_id: Optional[int] = None):
    conn = get_conn()
    cur = conn.cursor()
    if exercise_id:
        cur.execute("""
            SELECT test_date, estimated_1rm_kg, reps, load_kg, side, notes
            FROM patient_strength_tests
            WHERE patient_id = ? AND exercise_id = ?
            ORDER BY test_date DESC
        """, (patient_id, exercise_id))
    else:
        cur.execute("""
            SELECT exercise_id, test_date, estimated_1rm_kg, reps, load_kg, side, notes
            FROM patient_strength_tests
            WHERE patient_id = ?
            ORDER BY test_date DESC
        """, (patient_id,))
    rows = cur.fetchall()
    conn.close()
    return rows
