"""
db_store.py

SQLite persistence layer for Ride Log app:
- Patients
- Rides
- Weekly plan
- Strava OAuth tokens + sync tracking
- S&C library: exercises, rep schemes, normative strength standards
- Patient profile (sex, dob, bodyweight, presumed_level)
- Strength estimates (auto-estimated e1RM audit trail)
- S&C programming: blocks/weeks/sessions/session_exercises (+ actual tracking)

Designed to be safe on Streamlit Cloud:
- Creates ./data directory
- Uses CREATE TABLE IF NOT EXISTS
- Includes lightweight, safe migrations for new columns
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional, Any, List, Tuple, Dict


# -----------------------------
# Database location
# -----------------------------
DB_DIR = os.environ.get("RIDELOG_DB_DIR", "data")
DB_PATH = os.environ.get("RIDELOG_DB_PATH", os.path.join(DB_DIR, "ride_log.db"))


def get_conn() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _table_columns(cur: sqlite3.Cursor, table_name: str) -> List[str]:
    cur.execute(f"PRAGMA table_info({table_name})")
    return [r[1] for r in cur.fetchall()]


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def _ensure_column(cur: sqlite3.Cursor, table: str, col: str, ddl: str) -> None:
    cols = _table_columns(cur, table)
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _patients_unique_on_name_exists(cur: sqlite3.Cursor) -> bool:
    try:
        cur.execute("PRAGMA index_list(patients)")
    except sqlite3.OperationalError:
        return False
    for row in cur.fetchall():
        index_name = row[1]
        is_unique = row[2]
        if not is_unique:
            continue
        cur.execute(f"PRAGMA index_info({index_name})")
        cols = [row[2] for row in cur.fetchall()]
        if cols == ["name"]:
            return True
    return False


def _rebuild_patients_table(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=OFF")
    cur.execute("ALTER TABLE patients RENAME TO patients_old")
    cur.execute("""
        CREATE TABLE patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_user_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    old_cols = _table_columns(cur, "patients_old")
    owner_select = "owner_user_id" if "owner_user_id" in old_cols else "NULL AS owner_user_id"
    created_select = "created_at" if "created_at" in old_cols else "datetime('now') AS created_at"
    cur.execute(f"""
        INSERT INTO patients(id, name, owner_user_id, created_at)
        SELECT id, name, {owner_select}, {created_select}
        FROM patients_old
    """)
    cur.execute("DROP TABLE patients_old")
    cur.execute("PRAGMA foreign_keys=ON")


# -----------------------------
# Init / migrations
# -----------------------------
def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    # -----------------------------
    # Core tables
    # -----------------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        owner_user_id TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    if _patients_unique_on_name_exists(cur):
        _rebuild_patients_table(conn)
        cur = conn.cursor()

    _ensure_column(cur, "patients", "owner_user_id", "owner_user_id TEXT")

    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_patients_owner_name_unique
    ON patients(owner_user_id, name)
    WHERE owner_user_id IS NOT NULL
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_roles (
        user_id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS organization_coaches (
        owner_user_id TEXT NOT NULL,
        coach_user_id TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (owner_user_id, coach_user_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS organization_domains (
        email_suffix TEXT PRIMARY KEY,
        owner_user_id TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS coach_patient_access (
        coach_user_id TEXT NOT NULL,
        patient_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (coach_user_id, patient_id),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS client_invites (
        email TEXT PRIMARY KEY,
        patient_id INTEGER NOT NULL,
        coach_user_id TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        ride_date TEXT NOT NULL,              -- YYYY-MM-DD
        distance_km REAL NOT NULL,
        duration_min INTEGER NOT NULL,
        rpe INTEGER,                          -- nullable
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_rides_patient_date
    ON rides(patient_id, ride_date)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS weekly_plan (
        patient_id INTEGER NOT NULL,
        week_start TEXT NOT NULL,             -- Monday YYYY-MM-DD
        planned_km REAL,
        planned_hours REAL,
        phase TEXT,
        notes TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (patient_id, week_start),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    # -----------------------------
    # Strava integration tables
    # -----------------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strava_tokens (
        patient_id INTEGER PRIMARY KEY,
        access_token TEXT NOT NULL,
        refresh_token TEXT NOT NULL,
        expires_at INTEGER NOT NULL,          -- epoch seconds
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
    # S&C library tables
    # -----------------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        category TEXT,                        -- squat/hinge/push/pull/ankle/core/conditioning/etc
        laterality TEXT,                      -- bilateral/unilateral
        implement TEXT,                       -- barbell/dumbbell/bodyweight/machine/band/etc
        primary_muscles TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rep_schemes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal TEXT NOT NULL,                   -- endurance/hypertrophy/strength/power
        phase TEXT,                           -- base/build/peak etc
        reps_min INTEGER NOT NULL,
        reps_max INTEGER NOT NULL,
        sets_min INTEGER NOT NULL,
        sets_max INTEGER NOT NULL,
        pct_1rm_min REAL,                     -- 0.00–1.00 (nullable for BW/isometric)
        pct_1rm_max REAL,                     -- 0.00–1.00
        rpe_min INTEGER,
        rpe_max INTEGER,
        rest_sec_min INTEGER,
        rest_sec_max INTEGER,
        intent TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_rep_schemes_goal
    ON rep_schemes(goal)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS norm_strength_standards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exercise_id INTEGER NOT NULL,
        sex TEXT NOT NULL,                    -- male/female
        age_min INTEGER NOT NULL,
        age_max INTEGER NOT NULL,
        metric TEXT NOT NULL,                 -- rel_1rm_bw | pullup_reps
        poor REAL NOT NULL,
        fair REAL NOT NULL,
        good REAL NOT NULL,
        excellent REAL NOT NULL,
        source TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_norm_lookup
    ON norm_strength_standards(exercise_id, sex, metric, age_min, age_max)
    """)

    # -----------------------------
    # Patient profile (sex/dob/BW/level)
    # -----------------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS patient_profile (
        patient_id INTEGER PRIMARY KEY,
        sex TEXT,                             -- 'male' | 'female'
        dob TEXT,                             -- 'YYYY-MM-DD' optional
        bodyweight_kg REAL,
        presumed_level TEXT,                  -- 'novice' | 'intermediate' | 'advanced' | 'expert'
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    # Safe migration: add presumed_level if table existed without it
    _ensure_column(cur, "patient_profile", "presumed_level", "presumed_level TEXT")

    # -----------------------------
    # Strength estimates (auto-estimated e1RM audit trail)
    # -----------------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strength_estimates (
        patient_id INTEGER NOT NULL,
        exercise_id INTEGER NOT NULL,
        as_of_date TEXT NOT NULL,             -- YYYY-MM-DD
        estimated_1rm_kg REAL,                -- null for pull-ups
        estimated_rel_1rm_bw REAL,            -- ratio used (audit)
        level_used TEXT NOT NULL,
        sex_used TEXT NOT NULL,
        age_used INTEGER NOT NULL,
        bw_used REAL,                         -- can be null if unknown
        method TEXT NOT NULL,                 -- 'norm_level_band_v1' etc
        notes TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (patient_id, exercise_id),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
    )
    """)

    # =========================================================
    # S&C Programming: blocks/weeks/sessions/exercises (+ actuals)
    # =========================================================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,            -- YYYY-MM-DD (Mon recommended)
        weeks INTEGER NOT NULL DEFAULT 6,
        model TEXT NOT NULL,                 -- 'hybrid_v1'
        deload_week INTEGER NOT NULL DEFAULT 4,
        sessions_per_week INTEGER NOT NULL DEFAULT 2,
        goal TEXT,                           -- endurance/hypertrophy/strength/power/hybrid
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_weeks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block_id INTEGER NOT NULL,
        week_no INTEGER NOT NULL,            -- 1..N
        week_start TEXT NOT NULL,            -- YYYY-MM-DD
        focus TEXT,                          -- capacity/hypertrophy/strength/power/deload/hybrid
        deload_flag INTEGER NOT NULL DEFAULT 0,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (block_id) REFERENCES sc_blocks(id) ON DELETE CASCADE,
        UNIQUE(block_id, week_no)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_id INTEGER NOT NULL,
        session_label TEXT NOT NULL,         -- 'A' | 'B' | 'C' ...
        day_hint TEXT,                       -- optional
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (week_id) REFERENCES sc_weeks(id) ON DELETE CASCADE,
        UNIQUE(week_id, session_label)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_session_exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        exercise_id INTEGER NOT NULL,

        -- targets (planned)
        sets_target INTEGER NOT NULL,
        reps_target INTEGER NOT NULL,
        pct_1rm_target REAL,                 -- 0..1 nullable
        load_kg_target REAL,                 -- nullable
        rpe_target INTEGER,                  -- nullable
        rest_sec_target INTEGER,             -- nullable
        intent TEXT,
        notes TEXT,

        -- actuals
        sets_actual INTEGER,
        reps_actual INTEGER,
        load_kg_actual REAL,
        completed_flag INTEGER NOT NULL DEFAULT 0,
        actual_notes TEXT,

        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (session_id) REFERENCES sc_sessions(id) ON DELETE CASCADE,
        FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
    )
    """)

    # Safe migrations if table existed before actual columns were added
    cols = _table_columns(cur, "sc_session_exercises")
    if "sets_target" not in cols:
        # older schema support (if you had sets/reps columns)
        # We do not auto-map legacy columns; we just add missing ones for forward compatibility.
        _ensure_column(cur, "sc_session_exercises", "sets_target", "sets_target INTEGER NOT NULL DEFAULT 0")
        _ensure_column(cur, "sc_session_exercises", "reps_target", "reps_target INTEGER NOT NULL DEFAULT 0")
        _ensure_column(cur, "sc_session_exercises", "pct_1rm_target", "pct_1rm_target REAL")
        _ensure_column(cur, "sc_session_exercises", "load_kg_target", "load_kg_target REAL")
        _ensure_column(cur, "sc_session_exercises", "rpe_target", "rpe_target INTEGER")
        _ensure_column(cur, "sc_session_exercises", "rest_sec_target", "rest_sec_target INTEGER")

    _ensure_column(cur, "sc_session_exercises", "sets_actual", "sets_actual INTEGER")
    _ensure_column(cur, "sc_session_exercises", "reps_actual", "reps_actual INTEGER")
    _ensure_column(cur, "sc_session_exercises", "load_kg_actual", "load_kg_actual REAL")
    _ensure_column(cur, "sc_session_exercises", "completed_flag", "completed_flag INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, "sc_session_exercises", "actual_notes", "actual_notes TEXT")

    conn.commit()
    conn.close()


# -----------------------------
# Patients
# -----------------------------
def upsert_patient(name: str, owner_user_id: Optional[str] = None) -> int:
    conn = get_conn()
    cur = conn.cursor()
    if owner_user_id is not None:
        cur.execute(
            "SELECT id FROM patients WHERE name = ? AND owner_user_id = ?",
            (name, owner_user_id),
        )
    else:
        cur.execute(
            "SELECT id FROM patients WHERE name = ? AND owner_user_id IS NULL",
            (name,),
        )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO patients(name, owner_user_id) VALUES (?, ?)",
            (name, owner_user_id),
        )
        pid = int(cur.lastrowid)
        if owner_user_id is not None:
            cur.execute("""
                INSERT OR IGNORE INTO patient_access(user_id, patient_id, access_level)
                VALUES (?, ?, 'client')
            """, (owner_user_id, pid))
    else:
        pid = int(row[0])
        if owner_user_id is not None:
            cur.execute("""
                INSERT OR IGNORE INTO patient_access(user_id, patient_id, access_level)
                VALUES (?, ?, 'client')
            """, (owner_user_id, pid))
    conn.commit()
    conn.close()
    return pid


def list_patients() -> List[Tuple[int, str]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM patients ORDER BY name ASC")
    rows = cur.fetchall()
    conn.close()
    return [(int(r[0]), str(r[1])) for r in rows]


def get_user_role(user_id: str) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute("SELECT role FROM user_roles WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
    conn.close()
    return None if row is None else str(row[0])


def upsert_user_role(user_id: str, role: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users(user_id, role)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            role=excluded.role
    """, (user_id, role))
    cur.execute("""
        INSERT INTO user_roles(user_id, role)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            role=excluded.role
    """, (user_id, role))
    conn.commit()
    conn.close()


def add_coach_to_org(owner_user_id: str, coach_user_id: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO organization_coaches(owner_user_id, coach_user_id)
        VALUES (?, ?)
    """, (owner_user_id, coach_user_id))
    conn.commit()
    conn.close()


def get_owner_for_email_suffix(email_suffix: str) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT owner_user_id
        FROM organization_domains
        WHERE email_suffix = ?
        LIMIT 1
    """, (email_suffix.lower(),))
    row = cur.fetchone()
    conn.close()
    return None if row is None else str(row[0])


def register_owner_email_suffix(owner_user_id: str, email_suffix: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO organization_domains(email_suffix, owner_user_id)
        VALUES (?, ?)
    """, (email_suffix.lower(), owner_user_id))
    conn.commit()
    conn.close()


def remove_coach_from_org(owner_user_id: str, coach_user_id: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM organization_coaches
        WHERE owner_user_id = ? AND coach_user_id = ?
    """, (owner_user_id, coach_user_id))
    conn.commit()
    conn.close()


def list_org_coaches(owner_user_id: str) -> List[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT coach_user_id
        FROM organization_coaches
        WHERE owner_user_id = ?
        ORDER BY coach_user_id ASC
    """, (owner_user_id,))
    rows = cur.fetchall()
    conn.close()
    return [str(r[0]) for r in rows]


def assign_patient_to_coach(coach_user_id: str, patient_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO coach_patient_access(coach_user_id, patient_id)
        VALUES (?, ?)
    """, (coach_user_id, int(patient_id)))
    cur.execute("""
        INSERT OR IGNORE INTO patient_access(user_id, patient_id, access_level)
        VALUES (?, ?, 'coach')
    """, (coach_user_id, int(patient_id)))
    conn.commit()
    conn.close()


def set_patient_owner(patient_id: int, owner_user_id: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE patients
        SET owner_user_id = ?
        WHERE id = ?
    """, (owner_user_id, int(patient_id)))
    conn.commit()
    conn.close()


def create_client_invite(email: str, patient_id: int, coach_user_id: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO client_invites(email, patient_id, coach_user_id)
        VALUES (?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            patient_id=excluded.patient_id,
            coach_user_id=excluded.coach_user_id,
            created_at=datetime('now')
    """, (email.lower(), int(patient_id), coach_user_id))
    conn.commit()
    conn.close()


def get_client_invite(email: str) -> Optional[Tuple[int, str]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT patient_id, coach_user_id
        FROM client_invites
        WHERE email = ?
        LIMIT 1
    """, (email.lower(),))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return int(row[0]), str(row[1])


def claim_client_invite(email: str, user_id: str) -> Optional[int]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT patient_id, coach_user_id
        FROM client_invites
        WHERE email = ?
        LIMIT 1
    """, (email.lower(),))
    row = cur.fetchone()
    if row is None:
        conn.close()
        return None
    patient_id, coach_user_id = int(row[0]), str(row[1])
    cur.execute("""
        UPDATE patients
        SET owner_user_id = ?
        WHERE id = ? AND owner_user_id IS NULL
    """, (user_id, patient_id))
    cur.execute("""
        INSERT OR IGNORE INTO coach_patient_access(coach_user_id, patient_id)
        VALUES (?, ?)
    """, (coach_user_id, patient_id))
    cur.execute("DELETE FROM client_invites WHERE email = ?", (email.lower(),))
    conn.commit()
    conn.close()
    return patient_id


def list_patients_for_user(user_id: str, role: str) -> List[Tuple[int, str]]:
    conn = get_conn()
    cur = conn.cursor()
    if role in {"coach", "client"}:
        cur.execute("""
            SELECT DISTINCT p.id, p.name
            FROM patients p
            LEFT JOIN coach_patient_access cpa
                ON cpa.patient_id = p.id AND cpa.coach_user_id = ?
            LEFT JOIN organization_coaches oc
                ON oc.owner_user_id = p.owner_user_id AND oc.coach_user_id = ?
            WHERE cpa.coach_user_id IS NOT NULL OR oc.coach_user_id IS NOT NULL
            ORDER BY p.name ASC
        """, (user_id, user_id))
    elif role == "super_admin":
        cur.execute("""
            SELECT id, name
            FROM patients
            WHERE owner_user_id = ?
            ORDER BY name ASC
        """, (user_id,))
    elif role == "client":
        cur.execute("""
            SELECT id, name
            FROM patients
            WHERE owner_user_id = ?
            ORDER BY name ASC
        """, (user_id,))
    else:
        conn.close()
        return []
    rows = cur.fetchall()
    conn.close()
    return [(int(r[0]), str(r[1])) for r in rows]


def _user_can_access_patient(cur: sqlite3.Cursor, user_id: str, role: str, patient_id: int) -> bool:
    if role not in {"coach", "client"}:
        return False
    if _table_exists(cur, "patient_access"):
        cur.execute("""
            SELECT 1
            FROM patients p
            LEFT JOIN coach_patient_access cpa
                ON cpa.patient_id = p.id AND cpa.coach_user_id = ?
            LEFT JOIN organization_coaches oc
                ON oc.owner_user_id = p.owner_user_id AND oc.coach_user_id = ?
            WHERE p.id = ?
              AND (cpa.coach_user_id IS NOT NULL OR oc.coach_user_id IS NOT NULL)
            LIMIT 1
        """, (user_id, user_id, int(patient_id)))
        return cur.fetchone() is not None
    if role == "super_admin":
        cur.execute("""
            SELECT 1
            FROM coach_patient_access
            WHERE coach_user_id = ? AND patient_id = ?
            LIMIT 1
        """, (user_id, int(patient_id)))
        return cur.fetchone() is not None
    if role == "coach":
        cur.execute("""
            SELECT 1
            FROM coach_patient_access
            WHERE coach_user_id = ? AND patient_id = ?
            LIMIT 1
        """, (user_id, int(patient_id)))
        return cur.fetchone() is not None
    cur.execute("""
        SELECT 1
        FROM patients
        WHERE id = ? AND owner_user_id = ?
        LIMIT 1
    """, (int(patient_id), user_id))
    return cur.fetchone() is not None


def _assert_patient_access(user_id: str, role: str, patient_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    ok = _user_can_access_patient(cur, user_id, role, patient_id)
    conn.close()
    if not ok:
        raise PermissionError("User is not permitted to access this patient.")


def _assert_coach(role: str) -> None:
    if role not in ["coach", "super_admin"]:
        raise PermissionError("User does not have coach permissions.")


def _get_block_patient_id(cur: sqlite3.Cursor, block_id: int) -> Optional[int]:
    cur.execute("SELECT patient_id FROM sc_blocks WHERE id = ?", (int(block_id),))
    row = cur.fetchone()
    return None if row is None else int(row[0])


def _get_week_patient_id(cur: sqlite3.Cursor, week_id: int) -> Optional[int]:
    cur.execute("""
        SELECT b.patient_id
        FROM sc_weeks w
        JOIN sc_blocks b ON b.id = w.block_id
        WHERE w.id = ?
    """, (int(week_id),))
    row = cur.fetchone()
    return None if row is None else int(row[0])


def _get_session_patient_id(cur: sqlite3.Cursor, session_id: int) -> Optional[int]:
    cur.execute("""
        SELECT b.patient_id
        FROM sc_sessions s
        JOIN sc_weeks w ON w.id = s.week_id
        JOIN sc_blocks b ON b.id = w.block_id
        WHERE s.id = ?
    """, (int(session_id),))
    row = cur.fetchone()
    return None if row is None else int(row[0])


def _get_session_exercise_patient_id(cur: sqlite3.Cursor, row_id: int) -> Optional[int]:
    cur.execute("""
        SELECT b.patient_id
        FROM sc_session_exercises x
        JOIN sc_sessions s ON s.id = x.session_id
        JOIN sc_weeks w ON w.id = s.week_id
        JOIN sc_blocks b ON b.id = w.block_id
        WHERE x.id = ?
    """, (int(row_id),))
    row = cur.fetchone()
    return None if row is None else int(row[0])


def _assert_block_access(user_id: str, role: str, block_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    patient_id = _get_block_patient_id(cur, block_id)
    if patient_id is None:
        conn.close()
        raise ValueError("Block not found.")
    ok = _user_can_access_patient(cur, user_id, role, patient_id)
    conn.close()
    if not ok:
        raise PermissionError("User is not permitted to access this block.")


def _assert_week_access(user_id: str, role: str, week_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    patient_id = _get_week_patient_id(cur, week_id)
    if patient_id is None:
        conn.close()
        raise ValueError("Week not found.")
    ok = _user_can_access_patient(cur, user_id, role, patient_id)
    conn.close()
    if not ok:
        raise PermissionError("User is not permitted to access this week.")


def _assert_session_access(user_id: str, role: str, session_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    patient_id = _get_session_patient_id(cur, session_id)
    if patient_id is None:
        conn.close()
        raise ValueError("Session not found.")
    ok = _user_can_access_patient(cur, user_id, role, patient_id)
    conn.close()
    if not ok:
        raise PermissionError("User is not permitted to access this session.")


def add_ride_for_user(
    user_id: str,
    role: str,
    patient_id: int,
    ride_date: str,
    distance_km: float,
    duration_min: int,
    rpe: Optional[int],
    notes: Optional[str],
) -> None:
    _assert_patient_access(user_id, role, patient_id)
    add_ride(patient_id, ride_date, distance_km, duration_min, rpe, notes)


def fetch_rides_for_user(
    user_id: str,
    role: str,
    patient_id: int,
) -> List[Tuple[str, float, int, Optional[int], Optional[str]]]:
    _assert_patient_access(user_id, role, patient_id)
    return fetch_rides(patient_id)


def upsert_week_plan_for_user(
    user_id: str,
    role: str,
    patient_id: int,
    week_start: str,
    planned_km: Optional[float],
    planned_hours: Optional[float],
    phase: Optional[str],
    notes: Optional[str],
) -> None:
    _assert_coach(role)
    _assert_patient_access(user_id, role, patient_id)
    upsert_week_plan(patient_id, week_start, planned_km, planned_hours, phase, notes)


def fetch_week_plans_for_user(
    user_id: str,
    role: str,
    patient_id: int,
) -> List[Tuple[str, Optional[float], Optional[float], Optional[str], Optional[str]]]:
    _assert_patient_access(user_id, role, patient_id)
    return fetch_week_plans(patient_id)


def save_strava_tokens_for_user(
    user_id: str,
    role: str,
    patient_id: int,
    access_token: str,
    refresh_token: str,
    expires_at: int,
    athlete_id: Optional[int],
    scope: Optional[str],
) -> None:
    _assert_patient_access(user_id, role, patient_id)
    save_strava_tokens(patient_id, access_token, refresh_token, expires_at, athlete_id, scope)


def get_strava_tokens_for_user(user_id: str, role: str, patient_id: int):
    _assert_patient_access(user_id, role, patient_id)
    return get_strava_tokens(patient_id)


def mark_activity_synced_for_user(user_id: str, role: str, patient_id: int, activity_id: int) -> None:
    _assert_patient_access(user_id, role, patient_id)
    mark_activity_synced(patient_id, activity_id)


def is_activity_synced_for_user(user_id: str, role: str, patient_id: int, activity_id: int) -> bool:
    _assert_patient_access(user_id, role, patient_id)
    return is_activity_synced(patient_id, activity_id)


def upsert_patient_profile_for_user(
    user_id: str,
    role: str,
    patient_id: int,
    sex: Optional[str],
    dob: Optional[str],
    bodyweight_kg: Optional[float],
    presumed_level: Optional[str],
) -> None:
    _assert_patient_access(user_id, role, patient_id)
    upsert_patient_profile(patient_id, sex, dob, bodyweight_kg, presumed_level)


def get_patient_profile_for_user(user_id: str, role: str, patient_id: int):
    _assert_patient_access(user_id, role, patient_id)
    return get_patient_profile(patient_id)


def upsert_strength_estimate_for_user(
    user_id: str,
    role: str,
    patient_id: int,
    exercise_id: int,
    as_of_date: str,
    estimated_1rm_kg: Optional[float],
    estimated_rel_1rm_bw: Optional[float],
    level_used: str,
    sex_used: str,
    age_used: int,
    bw_used: Optional[float],
    method: str,
    notes: Optional[str] = None,
) -> None:
    _assert_patient_access(user_id, role, patient_id)
    upsert_strength_estimate(
        patient_id,
        exercise_id,
        as_of_date,
        estimated_1rm_kg,
        estimated_rel_1rm_bw,
        level_used,
        sex_used,
        age_used,
        bw_used,
        method,
        notes,
    )


def get_strength_estimate_for_user(user_id: str, role: str, patient_id: int, exercise_id: int):
    _assert_patient_access(user_id, role, patient_id)
    return get_strength_estimate(patient_id, exercise_id)


def create_sc_block_for_user(
    user_id: str,
    role: str,
    patient_id: int,
    start_date: str,
    goal: str,
    notes: Optional[str] = None,
    weeks: int = 6,
    model: str = "hybrid_v1",
    deload_week: int = 4,
    sessions_per_week: int = 2,
) -> int:
    _assert_coach(role)
    _assert_patient_access(user_id, role, patient_id)
    return create_sc_block(
        patient_id=patient_id,
        start_date=start_date,
        goal=goal,
        notes=notes,
        weeks=weeks,
        model=model,
        deload_week=deload_week,
        sessions_per_week=sessions_per_week,
    )


def upsert_sc_week_for_user(
    user_id: str,
    role: str,
    block_id: int,
    week_no: int,
    week_start: str,
    focus: Optional[str],
    deload_flag: bool,
    notes: Optional[str] = None,
) -> int:
    _assert_coach(role)
    _assert_block_access(user_id, role, block_id)
    return upsert_sc_week(block_id, week_no, week_start, focus, deload_flag, notes)


def upsert_sc_session_for_user(
    user_id: str,
    role: str,
    week_id: int,
    session_label: str,
    day_hint: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    _assert_coach(role)
    _assert_week_access(user_id, role, week_id)
    return upsert_sc_session(week_id, session_label, day_hint, notes)


def clear_sc_session_exercises_for_user(user_id: str, role: str, session_id: int) -> None:
    _assert_coach(role)
    _assert_session_access(user_id, role, session_id)
    clear_sc_session_exercises(session_id)


def add_sc_session_exercise_for_user(
    user_id: str,
    role: str,
    session_id: int,
    exercise_id: int,
    sets_target: int,
    reps_target: int,
    pct_1rm_target: Optional[float],
    load_kg_target: Optional[float],
    rpe_target: Optional[int],
    rest_sec_target: Optional[int],
    intent: Optional[str],
    notes: Optional[str],
) -> int:
    _assert_coach(role)
    _assert_session_access(user_id, role, session_id)
    return add_sc_session_exercise(
        session_id,
        exercise_id,
        sets_target,
        reps_target,
        pct_1rm_target,
        load_kg_target,
        rpe_target,
        rest_sec_target,
        intent,
        notes,
    )


def update_sc_session_exercise_actual_for_user(
    user_id: str,
    role: str,
    row_id: int,
    sets_actual: Optional[int],
    reps_actual: Optional[int],
    load_kg_actual: Optional[float],
    completed_flag: bool,
    actual_notes: Optional[str],
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    patient_id = _get_session_exercise_patient_id(cur, row_id)
    conn.close()
    if patient_id is None:
        raise ValueError("Session exercise not found.")
    _assert_patient_access(user_id, role, patient_id)
    update_sc_session_exercise_actual(row_id, sets_actual, reps_actual, load_kg_actual, completed_flag, actual_notes)


def fetch_latest_sc_block_for_user(user_id: str, role: str, patient_id: int):
    _assert_patient_access(user_id, role, patient_id)
    return fetch_latest_sc_block(patient_id)


def fetch_sc_block_detail_for_user(user_id: str, role: str, block_id: int):
    _assert_block_access(user_id, role, block_id)
    return fetch_sc_block_detail(block_id)


# -----------------------------
# Rides
# -----------------------------
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
    """, (int(patient_id), ride_date, float(distance_km), int(duration_min), rpe, notes))
    conn.commit()
    conn.close()


def fetch_rides(patient_id: int) -> List[Tuple[str, float, int, Optional[int], Optional[str]]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ride_date, distance_km, duration_min, rpe, notes
        FROM rides
        WHERE patient_id = ?
        ORDER BY ride_date DESC, id DESC
    """, (int(patient_id),))
    rows = cur.fetchall()
    conn.close()
    return [(str(r[0]), float(r[1]), int(r[2]), r[3] if r[3] is None else int(r[3]), r[4]) for r in rows]


# -----------------------------
# Weekly plan
# -----------------------------
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
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(patient_id, week_start) DO UPDATE SET
            planned_km=excluded.planned_km,
            planned_hours=excluded.planned_hours,
            phase=excluded.phase,
            notes=excluded.notes,
            updated_at=datetime('now')
    """, (int(patient_id), week_start, planned_km, planned_hours, phase, notes))
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
    """, (int(patient_id),))
    rows = cur.fetchall()
    conn.close()
    out: List[Tuple[str, Optional[float], Optional[float], Optional[str], Optional[str]]] = []
    for r in rows:
        out.append((
            str(r[0]),
            None if r[1] is None else float(r[1]),
            None if r[2] is None else float(r[2]),
            r[3],
            r[4],
        ))
    return out


# -----------------------------
# Strava tokens + sync tracking
# -----------------------------
def save_strava_tokens(
    patient_id: int,
    access_token: str,
    refresh_token: str,
    expires_at: int,
    athlete_id: Optional[int],
    scope: Optional[str],
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
    """, (int(patient_id), access_token, refresh_token, int(expires_at), athlete_id, scope))
    conn.commit()
    conn.close()


def get_strava_tokens(patient_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT access_token, refresh_token, expires_at, athlete_id, scope
        FROM strava_tokens
        WHERE patient_id = ?
    """, (int(patient_id),))
    row = cur.fetchone()
    conn.close()
    return row  # None or tuple(access, refresh, expires_at, athlete_id, scope)


def mark_activity_synced(patient_id: int, activity_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO strava_synced(patient_id, strava_activity_id)
        VALUES (?, ?)
    """, (int(patient_id), int(activity_id)))
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
    """, (int(patient_id), int(activity_id)))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


# -----------------------------
# S&C library: exercises
# -----------------------------
def upsert_exercise(
    name: str,
    category: Optional[str] = None,
    laterality: Optional[str] = None,
    implement: Optional[str] = None,
    primary_muscles: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO exercises(name, category, laterality, implement, primary_muscles, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            category=excluded.category,
            laterality=excluded.laterality,
            implement=excluded.implement,
            primary_muscles=excluded.primary_muscles,
            notes=excluded.notes
    """, (name, category, laterality, implement, primary_muscles, notes))
    conn.commit()
    cur.execute("SELECT id FROM exercises WHERE name = ?", (name,))
    ex_id = int(cur.fetchone()[0])
    conn.close()
    return ex_id


def get_exercise(exercise_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, category, laterality, implement, primary_muscles, notes
        FROM exercises
        WHERE id = ?
    """, (int(exercise_id),))
    row = cur.fetchone()
    conn.close()
    return row


def list_exercises() -> List[Tuple[int, str, Optional[str], Optional[str], Optional[str]]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, category, laterality, implement
        FROM exercises
        ORDER BY name ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return [(int(r[0]), str(r[1]), r[2], r[3], r[4]) for r in rows]


# -----------------------------
# S&C library: rep schemes
# -----------------------------
def upsert_rep_scheme(
    goal: str,
    phase: Optional[str],
    reps_min: int,
    reps_max: int,
    sets_min: int,
    sets_max: int,
    pct_1rm_min: Optional[float],
    pct_1rm_max: Optional[float],
    rpe_min: Optional[int],
    rpe_max: Optional[int],
    rest_sec_min: Optional[int],
    rest_sec_max: Optional[int],
    intent: Optional[str],
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO rep_schemes(
            goal, phase, reps_min, reps_max, sets_min, sets_max,
            pct_1rm_min, pct_1rm_max, rpe_min, rpe_max,
            rest_sec_min, rest_sec_max, intent
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        goal, phase, int(reps_min), int(reps_max), int(sets_min), int(sets_max),
        pct_1rm_min, pct_1rm_max, rpe_min, rpe_max,
        rest_sec_min, rest_sec_max, intent
    ))
    conn.commit()
    rs_id = int(cur.lastrowid)
    conn.close()
    return rs_id


def list_rep_schemes(goal: str) -> List[Tuple]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, goal, phase, reps_min, reps_max, sets_min, sets_max,
               pct_1rm_min, pct_1rm_max, rpe_min, rpe_max,
               rest_sec_min, rest_sec_max, intent
        FROM rep_schemes
        WHERE goal = ?
        ORDER BY id ASC
    """, (goal,))
    rows = cur.fetchall()
    conn.close()
    return rows


# -----------------------------
# S&C library: normative standards
# -----------------------------
def upsert_norm_standard(
    exercise_id: int,
    sex: str,
    age_min: int,
    age_max: int,
    metric: str,
    poor: float,
    fair: float,
    good: float,
    excellent: float,
    source: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO norm_strength_standards(
            exercise_id, sex, age_min, age_max, metric,
            poor, fair, good, excellent, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(exercise_id), sex, int(age_min), int(age_max), metric,
        float(poor), float(fair), float(good), float(excellent), source, notes
    ))
    conn.commit()
    ns_id = int(cur.lastrowid)
    conn.close()
    return ns_id


def count_norm_rows() -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) FROM norm_strength_standards")
    n = int(cur.fetchone()[0])
    conn.close()
    return n


def get_norm_standard(exercise_id: int, sex: str, age: int, metric: str):
    """
    Returns:
      poor, fair, good, excellent, source, notes, age_min, age_max
    or None if not found
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT poor, fair, good, excellent, source, notes, age_min, age_max
        FROM norm_strength_standards
        WHERE exercise_id = ?
          AND sex = ?
          AND metric = ?
          AND age_min <= ?
          AND age_max >= ?
        ORDER BY age_min DESC
        LIMIT 1
    """, (int(exercise_id), sex, metric, int(age), int(age)))
    row = cur.fetchone()
    conn.close()
    return row


# -----------------------------
# Patient profile (sex/dob/BW/level)
# -----------------------------
def upsert_patient_profile(
    patient_id: int,
    sex: Optional[str],
    dob: Optional[str],
    bodyweight_kg: Optional[float],
    presumed_level: Optional[str],
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO patient_profile(patient_id, sex, dob, bodyweight_kg, presumed_level)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(patient_id) DO UPDATE SET
            sex=excluded.sex,
            dob=excluded.dob,
            bodyweight_kg=excluded.bodyweight_kg,
            presumed_level=excluded.presumed_level,
            updated_at=datetime('now')
    """, (int(patient_id), sex, dob, bodyweight_kg, presumed_level))
    conn.commit()
    conn.close()


def get_patient_profile(patient_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT sex, dob, bodyweight_kg, presumed_level
        FROM patient_profile
        WHERE patient_id = ?
    """, (int(patient_id),))
    row = cur.fetchone()
    conn.close()
    return row  # None or (sex, dob, bodyweight_kg, presumed_level)


# -----------------------------
# Strength estimates (auto-estimated e1RM audit)
# -----------------------------
def upsert_strength_estimate(
    patient_id: int,
    exercise_id: int,
    as_of_date: str,
    estimated_1rm_kg: Optional[float],
    estimated_rel_1rm_bw: Optional[float],
    level_used: str,
    sex_used: str,
    age_used: int,
    bw_used: Optional[float],
    method: str,
    notes: Optional[str] = None,
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO strength_estimates(
            patient_id, exercise_id, as_of_date,
            estimated_1rm_kg, estimated_rel_1rm_bw,
            level_used, sex_used, age_used, bw_used,
            method, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(patient_id, exercise_id) DO UPDATE SET
            as_of_date=excluded.as_of_date,
            estimated_1rm_kg=excluded.estimated_1rm_kg,
            estimated_rel_1rm_bw=excluded.estimated_rel_1rm_bw,
            level_used=excluded.level_used,
            sex_used=excluded.sex_used,
            age_used=excluded.age_used,
            bw_used=excluded.bw_used,
            method=excluded.method,
            notes=excluded.notes,
            updated_at=datetime('now')
    """, (
        int(patient_id), int(exercise_id), as_of_date,
        estimated_1rm_kg, estimated_rel_1rm_bw,
        level_used, sex_used, int(age_used), bw_used,
        method, notes
    ))
    conn.commit()
    conn.close()


def get_strength_estimate(patient_id: int, exercise_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT as_of_date, estimated_1rm_kg, estimated_rel_1rm_bw,
               level_used, sex_used, age_used, bw_used, method, notes
        FROM strength_estimates
        WHERE patient_id = ? AND exercise_id = ?
    """, (int(patient_id), int(exercise_id)))
    row = cur.fetchone()
    conn.close()
    return row


# -----------------------------
# Estimation engine helpers (Option A anchor scaling)
# -----------------------------
def _level_to_target_ratio(poor: float, fair: float, good: float, excellent: float, level: str) -> float:
    level = (level or "intermediate").lower()
    if level == "novice":
        return float(fair)
    if level == "intermediate":
        return (float(fair) + float(good)) / 2.0
    if level == "advanced":
        return float(good)
    if level == "expert":
        return (float(good) + float(excellent)) / 2.0
    return (float(fair) + float(good)) / 2.0


def estimate_e1rm_kg_for_exercise(
    patient_sex: str,
    patient_age: int,
    patient_bw_kg: Optional[float],
    presumed_level: str,
    exercise_id: int,
    metric: str,
) -> Dict[str, Any]:
    """
    Returns dict:
      estimated_1rm_kg, estimated_rel_1rm_bw, method, notes, band_used
    """
    if metric == "pullup_reps":
        return {
            "estimated_1rm_kg": None,
            "estimated_rel_1rm_bw": None,
            "method": "not_applicable_pullup",
            "notes": "Pull-ups prescribed via reps/sets; no 1RM estimate.",
            "band_used": None,
        }

    if not patient_bw_kg or patient_bw_kg <= 0:
        return {
            "estimated_1rm_kg": None,
            "estimated_rel_1rm_bw": None,
            "method": "missing_bodyweight",
            "notes": "Bodyweight is required to estimate 1RM from relative norms.",
            "band_used": None,
        }

    norm = get_norm_standard(exercise_id, patient_sex, int(patient_age), metric)
    if norm is None:
        return {
            "estimated_1rm_kg": None,
            "estimated_rel_1rm_bw": None,
            "method": "no_norm_found",
            "notes": "No normative standard found for this exercise/sex/age/metric.",
            "band_used": None,
        }

    poor, fair, good, excellent, source, notes, age_min, age_max = norm
    target_rel = _level_to_target_ratio(poor, fair, good, excellent, presumed_level)
    e1rm = float(target_rel) * float(patient_bw_kg)

    return {
        "estimated_1rm_kg": float(e1rm),
        "estimated_rel_1rm_bw": float(target_rel),
        "method": "norm_level_band_v1",
        "notes": f"Norms: {source or ''} {notes or ''}".strip(),
        "band_used": f"{age_min}-{age_max}",
    }


def estimate_unilateral_from_bilateral(
    bilateral_e1rm_kg: Optional[float],
    movement: str,
    presumed_level: str,
) -> Optional[float]:
    """
    Option A scaling rules:
      - 'bss'/'stepup' anchored to squat e1RM
      - 'sl_rdl' anchored to deadlift e1RM
    Returns per-leg 'e1RM-equivalent' used for % prescriptions.
    """
    if bilateral_e1rm_kg is None:
        return None

    lvl = (presumed_level or "intermediate").lower()
    mv = (movement or "").lower().strip()

    if mv in ["bss", "stepup"]:
        base = 0.40
        if lvl == "novice":
            base = 0.35
        elif lvl == "advanced":
            base = 0.45
        elif lvl == "expert":
            base = 0.50
    elif mv in ["sl_rdl"]:
        base = 0.35
        if lvl == "novice":
            base = 0.30
        elif lvl == "advanced":
            base = 0.40
        elif lvl == "expert":
            base = 0.45
    else:
        base = 0.35

    return float(bilateral_e1rm_kg) * float(base)


# =========================================================
# S&C Programming helpers (blocks/weeks/sessions/exercises)
# =========================================================
def create_sc_block(
    patient_id: int,
    start_date: str,
    goal: str,
    notes: Optional[str] = None,
    weeks: int = 6,
    model: str = "hybrid_v1",
    deload_week: int = 4,
    sessions_per_week: int = 2,
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sc_blocks(patient_id, start_date, weeks, model, deload_week, sessions_per_week, goal, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (int(patient_id), start_date, int(weeks), model, int(deload_week), int(sessions_per_week), goal, notes))
    conn.commit()
    block_id = int(cur.lastrowid)
    conn.close()
    return block_id


def fetch_latest_sc_block(patient_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, start_date, weeks, model, deload_week, sessions_per_week, goal, notes, created_at
        FROM sc_blocks
        WHERE patient_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (int(patient_id),))
    row = cur.fetchone()
    conn.close()
    return row


def upsert_sc_week(
    block_id: int,
    week_no: int,
    week_start: str,
    focus: Optional[str],
    deload_flag: bool,
    notes: Optional[str] = None,
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sc_weeks(block_id, week_no, week_start, focus, deload_flag, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(block_id, week_no) DO UPDATE SET
            week_start=excluded.week_start,
            focus=excluded.focus,
            deload_flag=excluded.deload_flag,
            notes=excluded.notes
    """, (int(block_id), int(week_no), week_start, focus, 1 if deload_flag else 0, notes))
    conn.commit()
    cur.execute("SELECT id FROM sc_weeks WHERE block_id=? AND week_no=?", (int(block_id), int(week_no)))
    week_id = int(cur.fetchone()[0])
    conn.close()
    return week_id


def upsert_sc_session(
    week_id: int,
    session_label: str,
    day_hint: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sc_sessions(week_id, session_label, day_hint, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(week_id, session_label) DO UPDATE SET
            day_hint=excluded.day_hint,
            notes=excluded.notes
    """, (int(week_id), session_label, day_hint, notes))
    conn.commit()
    cur.execute("SELECT id FROM sc_sessions WHERE week_id=? AND session_label=?", (int(week_id), session_label))
    sid = int(cur.fetchone()[0])
    conn.close()
    return sid


def clear_sc_session_exercises(session_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM sc_session_exercises WHERE session_id = ?", (int(session_id),))
    conn.commit()
    conn.close()


def add_sc_session_exercise(
    session_id: int,
    exercise_id: int,
    sets_target: int,
    reps_target: int,
    pct_1rm_target: Optional[float],
    load_kg_target: Optional[float],
    rpe_target: Optional[int],
    rest_sec_target: Optional[int],
    intent: Optional[str],
    notes: Optional[str],
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sc_session_exercises(
            session_id, exercise_id,
            sets_target, reps_target, pct_1rm_target, load_kg_target,
            rpe_target, rest_sec_target, intent, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(session_id), int(exercise_id),
        int(sets_target), int(reps_target), pct_1rm_target, load_kg_target,
        rpe_target, rest_sec_target, intent, notes
    ))
    conn.commit()
    rid = int(cur.lastrowid)
    conn.close()
    return rid


def update_sc_session_exercise_actual(
    row_id: int,
    sets_actual: Optional[int],
    reps_actual: Optional[int],
    load_kg_actual: Optional[float],
    completed_flag: bool,
    actual_notes: Optional[str],
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE sc_session_exercises
        SET sets_actual=?,
            reps_actual=?,
            load_kg_actual=?,
            completed_flag=?,
            actual_notes=?
        WHERE id = ?
    """, (
        sets_actual,
        reps_actual,
        load_kg_actual,
        1 if completed_flag else 0,
        actual_notes,
        int(row_id),
    ))
    conn.commit()
    conn.close()


def fetch_sc_block_detail(block_id: int):
    """
    Returns list of tuples:
      (week_no, week_start, focus, deload_flag, session_label, day_hint, exercises_list)

    exercises_list rows:
      (row_id, exercise_name, sets_t, reps_t, pct_t, load_t, rpe_t, rest_t, intent, notes,
       sets_a, reps_a, load_a, completed, actual_notes)
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT w.id, w.week_no, w.week_start, w.focus, w.deload_flag,
               s.id, s.session_label, s.day_hint
        FROM sc_weeks w
        JOIN sc_sessions s ON s.week_id = w.id
        WHERE w.block_id = ?
        ORDER BY w.week_no ASC, s.session_label ASC
    """, (int(block_id),))
    rows = cur.fetchall()

    out = []
    for r in rows:
        week_id, week_no, week_start, focus, deload_flag, session_id, label, day_hint = r
        cur.execute("""
            SELECT x.id, e.name,
                   x.sets_target, x.reps_target, x.pct_1rm_target, x.load_kg_target,
                   x.rpe_target, x.rest_sec_target, x.intent, x.notes,
                   x.sets_actual, x.reps_actual, x.load_kg_actual, x.completed_flag, x.actual_notes
            FROM sc_session_exercises x
            JOIN exercises e ON e.id = x.exercise_id
            WHERE x.session_id = ?
            ORDER BY x.id ASC
        """, (int(session_id),))
        exs = cur.fetchall()
        out.append((int(week_no), str(week_start), focus, bool(deload_flag), str(label), day_hint, exs))

    conn.close()
    return out
