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
- S&C programming engine:
    blocks -> weeks -> sessions
    session templates -> week targets -> actuals

Designed to be safe on Streamlit Cloud:
- Creates ./data directory
- Uses CREATE TABLE IF NOT EXISTS
- Includes lightweight, safe migrations for new columns
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional, List, Tuple, Dict, Any


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
        name TEXT NOT NULL UNIQUE,
        created_at TEXT DEFAULT (datetime('now'))
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
        category TEXT,                        -- squat/hinge/push/pull/ankle/etc
        laterality TEXT,                      -- bilateral/unilateral
        implement TEXT,                       -- barbell/dumbbell/bodyweight/band/machine/etc
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
        pct_1rm_min REAL,                     -- 0.00–1.00
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
    cols = _table_columns(cur, "patient_profile")
    if "presumed_level" not in cols:
        cur.execute("ALTER TABLE patient_profile ADD COLUMN presumed_level TEXT")

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
    # S&C Programming engine (Block -> Weeks -> Sessions)
    # =========================================================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,            -- YYYY-MM-DD (Mon recommended)
        weeks INTEGER NOT NULL DEFAULT 6,
        model TEXT NOT NULL DEFAULT 'hybrid_v1',
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
        focus TEXT,                          -- capacity/hypertrophy/strength/power/deload
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
        session_label TEXT NOT NULL,         -- 'A' | 'B' | 'C'
        day_hint TEXT,                       -- 'Mon'/'Tue' etc (optional)
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (week_id) REFERENCES sc_weeks(id) ON DELETE CASCADE,
        UNIQUE(week_id, session_label)
    )
    """)

    # =========================================================
    # Templates (the row definitions that persist across weeks)
    # and generated targets per week (editable)
    # =========================================================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_session_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block_id INTEGER NOT NULL,
        session_label TEXT NOT NULL,          -- template for 'A' or 'B'
        title TEXT,                           -- optional display name
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (block_id) REFERENCES sc_blocks(id) ON DELETE CASCADE,
        UNIQUE(block_id, session_label)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_template_exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,

        -- grouping for supersets (e.g., 'A', 'B', 'C') and ordering inside the group (1,2)
        group_key TEXT,
        group_order INTEGER,

        exercise_id INTEGER NOT NULL,

        -- prescription mode: 'reps' or 'time'
        mode TEXT NOT NULL DEFAULT 'reps',

        -- sets are stable; reps/time will progress
        sets INTEGER NOT NULL DEFAULT 3,

        -- reps progression
        reps_start INTEGER,
        reps_step INTEGER DEFAULT 2,          -- +2 reps/week by default
        reps_cap INTEGER,                      -- optional cap

        -- time progression (seconds)
        time_start_sec INTEGER,
        time_step_sec INTEGER DEFAULT 10,     -- +10s/week by default
        time_cap_sec INTEGER,

        -- load prescription basis
        pct_1rm_start REAL,                   -- 0..1 optional
        pct_1rm_step REAL DEFAULT 0.00,       -- barbell can step by % if desired
        pct_1rm_cap REAL,

        -- for DB/KB progression rules: when reps cap reached, add load_increment_kg and reset reps to reps_start
        load_increment_kg REAL DEFAULT 2.5,

        rpe_target INTEGER,
        rest_sec INTEGER,
        intent TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),

        FOREIGN KEY (template_id) REFERENCES sc_session_templates(id) ON DELETE CASCADE,
        FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_sc_template_exercises_template
    ON sc_template_exercises(template_id, sort_order)
    """)

    # Generated + editable per week targets, plus actual tracking
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sc_week_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_exercise_id INTEGER NOT NULL,
        week_no INTEGER NOT NULL,

        -- planned/target
        sets INTEGER NOT NULL,
        reps INTEGER,
        time_sec INTEGER,
        pct_1rm REAL,
        load_kg REAL,
        rpe_target INTEGER,
        rest_sec INTEGER,
        intent TEXT,
        notes TEXT,

        -- actuals (user requested)
        actual_sets INTEGER,
        actual_reps INTEGER,
        actual_time_sec INTEGER,
        actual_load_kg REAL,
        completed_flag INTEGER NOT NULL DEFAULT 0,

        updated_at TEXT DEFAULT (datetime('now')),
        created_at TEXT DEFAULT (datetime('now')),

        FOREIGN KEY (template_exercise_id) REFERENCES sc_template_exercises(id) ON DELETE CASCADE,
        UNIQUE(template_exercise_id, week_no)
    )
    """)

    conn.commit()
    conn.close()


# -----------------------------
# Patients
# -----------------------------
def upsert_patient(name: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO patients(name) VALUES (?)
        ON CONFLICT(name) DO UPDATE SET name=excluded.name
    """, (name,))
    conn.commit()
    cur.execute("SELECT id FROM patients WHERE name = ?", (name,))
    pid = int(cur.fetchone()[0])
    conn.close()
    return pid


def list_patients() -> List[Tuple[int, str]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM patients ORDER BY name ASC")
    rows = cur.fetchall()
    conn.close()
    return [(int(r[0]), str(r[1])) for r in rows]


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
    """, (patient_id, ride_date, float(distance_km), int(duration_min), rpe, notes))
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
    """, (patient_id,))
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
    out: List[Tuple[str, Optional[float], Optional[float], Optional[str], Optional[str]]] = []
    for r in rows:
        out.append((str(r[0]),
                    None if r[1] is None else float(r[1]),
                    None if r[2] is None else float(r[2]),
                    r[3],
                    r[4]))
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


def mark_activity_synced(patient_id: int, activity_id: int) -> None:
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


def get_exercise(exercise_id: int) -> Optional[Tuple]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, category, laterality, implement, primary_muscles, notes
        FROM exercises
        WHERE id = ?
        LIMIT 1
    """, (int(exercise_id),))
    row = cur.fetchone()
    conn.close()
    return row


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
# Patient profile
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
    return row


# -----------------------------
# Strength estimates (stored)
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
# S&C programming helpers (blocks/weeks/sessions)
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


# =========================================================
# Template helpers (persist across weeks)
# =========================================================
def upsert_sc_session_template(block_id: int, session_label: str, title: Optional[str] = None, notes: Optional[str] = None) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sc_session_templates(block_id, session_label, title, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(block_id, session_label) DO UPDATE SET
            title=excluded.title,
            notes=excluded.notes
    """, (int(block_id), session_label, title, notes))
    conn.commit()
    cur.execute("SELECT id FROM sc_session_templates WHERE block_id=? AND session_label=?", (int(block_id), session_label))
    tid = int(cur.fetchone()[0])
    conn.close()
    return tid


def clear_sc_template_exercises(template_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM sc_template_exercises WHERE template_id = ?", (int(template_id),))
    conn.commit()
    conn.close()


def add_sc_template_exercise(
    template_id: int,
    exercise_id: int,
    sort_order: int,
    group_key: Optional[str],
    group_order: Optional[int],
    mode: str,
    sets: int,
    reps_start: Optional[int],
    reps_step: int,
    reps_cap: Optional[int],
    time_start_sec: Optional[int],
    time_step_sec: int,
    time_cap_sec: Optional[int],
    pct_1rm_start: Optional[float],
    pct_1rm_step: float,
    pct_1rm_cap: Optional[float],
    load_increment_kg: float,
    rpe_target: Optional[int],
    rest_sec: Optional[int],
    intent: Optional[str],
    notes: Optional[str],
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sc_template_exercises(
            template_id, sort_order, group_key, group_order, exercise_id,
            mode, sets,
            reps_start, reps_step, reps_cap,
            time_start_sec, time_step_sec, time_cap_sec,
            pct_1rm_start, pct_1rm_step, pct_1rm_cap,
            load_increment_kg,
            rpe_target, rest_sec, intent, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(template_id), int(sort_order), group_key, group_order, int(exercise_id),
        (mode or "reps"),
        int(sets),
        reps_start, int(reps_step), reps_cap,
        time_start_sec, int(time_step_sec), time_cap_sec,
        pct_1rm_start, float(pct_1rm_step), pct_1rm_cap,
        float(load_increment_kg),
        rpe_target, rest_sec, intent, notes
    ))
    conn.commit()
    rid = int(cur.lastrowid)
    conn.close()
    return rid


def list_sc_session_templates(block_id: int) -> List[Tuple]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, session_label, title, notes
        FROM sc_session_templates
        WHERE block_id = ?
        ORDER BY session_label ASC
    """, (int(block_id),))
    rows = cur.fetchall()
    conn.close()
    return rows


def list_sc_template_exercises(template_id: int) -> List[Tuple]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            te.id, te.sort_order, te.group_key, te.group_order,
            te.exercise_id, e.name, e.implement,
            te.mode, te.sets,
            te.reps_start, te.reps_step, te.reps_cap,
            te.time_start_sec, te.time_step_sec, te.time_cap_sec,
            te.pct_1rm_start, te.pct_1rm_step, te.pct_1rm_cap,
            te.load_increment_kg,
            te.rpe_target, te.rest_sec, te.intent, te.notes
        FROM sc_template_exercises te
        JOIN exercises e ON e.id = te.exercise_id
        WHERE te.template_id = ?
        ORDER BY te.sort_order ASC, te.id ASC
    """, (int(template_id),))
    rows = cur.fetchall()
    conn.close()
    return rows


# =========================================================
# Week targets (generated + editable) + actuals
# =========================================================
def upsert_sc_week_target(
    template_exercise_id: int,
    week_no: int,
    sets: int,
    reps: Optional[int],
    time_sec: Optional[int],
    pct_1rm: Optional[float],
    load_kg: Optional[float],
    rpe_target: Optional[int],
    rest_sec: Optional[int],
    intent: Optional[str],
    notes: Optional[str],
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sc_week_targets(
            template_exercise_id, week_no,
            sets, reps, time_sec, pct_1rm, load_kg,
            rpe_target, rest_sec, intent, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(template_exercise_id, week_no) DO UPDATE SET
            sets=excluded.sets,
            reps=excluded.reps,
            time_sec=excluded.time_sec,
            pct_1rm=excluded.pct_1rm,
            load_kg=excluded.load_kg,
            rpe_target=excluded.rpe_target,
            rest_sec=excluded.rest_sec,
            intent=excluded.intent,
            notes=excluded.notes,
            updated_at=datetime('now')
    """, (
        int(template_exercise_id), int(week_no),
        int(sets), reps, time_sec, pct_1rm, load_kg,
        rpe_target, rest_sec, intent, notes
    ))
    conn.commit()
    cur.execute("SELECT id FROM sc_week_targets WHERE template_exercise_id=? AND week_no=?", (int(template_exercise_id), int(week_no)))
    wid = int(cur.fetchone()[0])
    conn.close()
    return wid


def set_sc_week_actuals(
    template_exercise_id: int,
    week_no: int,
    actual_sets: Optional[int],
    actual_reps: Optional[int],
    actual_time_sec: Optional[int],
    actual_load_kg: Optional[float],
    completed_flag: bool,
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE sc_week_targets
        SET actual_sets = ?,
            actual_reps = ?,
            actual_time_sec = ?,
            actual_load_kg = ?,
            completed_flag = ?,
            updated_at = datetime('now')
        WHERE template_exercise_id = ? AND week_no = ?
    """, (
        actual_sets, actual_reps, actual_time_sec, actual_load_kg,
        1 if completed_flag else 0,
        int(template_exercise_id), int(week_no)
    ))
    conn.commit()
    conn.close()


def fetch_sc_week_targets_for_block(block_id: int) -> List[Tuple]:
    """
    Flat rows for UI tables:

    block_id, session_label, template_id, template_exercise_id, sort_order, group_key, group_order,
    exercise_name, implement, mode,
    week_no,
    sets, reps, time_sec, pct_1rm, load_kg, rpe_target, rest_sec, intent, notes,
    actual_sets, actual_reps, actual_time_sec, actual_load_kg, completed_flag
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            st.block_id,
            st.session_label,
            st.id AS template_id,
            te.id AS template_exercise_id,
            te.sort_order, te.group_key, te.group_order,
            e.name AS exercise_name,
            e.implement,
            te.mode,
            wt.week_no,
            wt.sets, wt.reps, wt.time_sec, wt.pct_1rm, wt.load_kg, wt.rpe_target, wt.rest_sec, wt.intent, wt.notes,
            wt.actual_sets, wt.actual_reps, wt.actual_time_sec, wt.actual_load_kg, wt.completed_flag
        FROM sc_session_templates st
        JOIN sc_template_exercises te ON te.template_id = st.id
        JOIN exercises e ON e.id = te.exercise_id
        JOIN sc_week_targets wt ON wt.template_exercise_id = te.id
        WHERE st.block_id = ?
        ORDER BY st.session_label ASC, te.sort_order ASC, wt.week_no ASC
    """, (int(block_id),))
    rows = cur.fetchall()
    conn.close()
    return rows


# =========================================================
# Auto-suggestion engine for week targets
# =========================================================
def _round_load(load: float, inc: float = 2.5) -> float:
    if inc <= 0:
        return float(load)
    return round(float(load) / float(inc)) * float(inc)


def _auto_progress_reps_dbkb(reps_start: int, reps_step: int, reps_cap: int, week_no: int) -> int:
    # linear reps, capped
    return min(int(reps_start) + int(reps_step) * (int(week_no) - 1), int(reps_cap))


def generate_sc_targets_for_template_row(
    *,
    weeks: int,
    deload_week: int,
    implement: Optional[str],
    mode: str,
    sets: int,
    reps_start: Optional[int],
    reps_step: int,
    reps_cap: Optional[int],
    time_start_sec: Optional[int],
    time_step_sec: int,
    time_cap_sec: Optional[int],
    pct_1rm_start: Optional[float],
    pct_1rm_step: float,
    pct_1rm_cap: Optional[float],
    load_increment_kg: float,
    e1rm_kg: Optional[float],
    rpe_target: Optional[int],
    rest_sec: Optional[int],
    intent: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Implements your default hierarchy:
    - bodyweight/isometric: linear reps or time
    - DB/KB: reps up then load
    - barbell: load up within rep range

    Deload week: reduce volume (sets -1) and reduce load/pct by ~10% if load-based.
    """
    impl = (implement or "").lower()
    md = (mode or "reps").lower()

    out: List[Dict[str, Any]] = []

    for wk in range(1, int(weeks) + 1):
        deload = (int(wk) == int(deload_week))
        sets_wk = max(1, int(sets) - 1) if deload else int(sets)

        reps_wk: Optional[int] = None
        time_wk: Optional[int] = None
        pct_wk: Optional[float] = None
        load_wk: Optional[float] = None

        if md == "time":
            base = int(time_start_sec or 30)
            step = int(time_step_sec or 10)
            cap = int(time_cap_sec) if time_cap_sec else None
            t = base + step * (wk - 1)
            if cap is not None:
                t = min(t, cap)
            if deload:
                t = max(10, int(round(t * 0.85)))
            time_wk = int(t)

        else:
            # reps mode
            base = int(reps_start or 8)
            step = int(reps_step or 2)
            cap = int(reps_cap) if reps_cap else None
            r = base + step * (wk - 1)
            if cap is not None:
                r = min(r, cap)
            if deload:
                r = max(1, int(round(r * 0.85)))
            reps_wk = int(r)

        # load-based handling
        if e1rm_kg and e1rm_kg > 0 and pct_1rm_start is not None:
            # pct-based prescription (good default for barbell)
            p0 = float(pct_1rm_start)
            ps = float(pct_1rm_step or 0.0)
            pcap = float(pct_1rm_cap) if pct_1rm_cap is not None else None
            p = p0 + ps * (wk - 1)
            if pcap is not None:
                p = min(p, pcap)
            if deload:
                p = max(0.30, p * 0.90)
            pct_wk = float(p)

            # compute load
            inc = float(load_increment_kg or 2.5)
            load_wk = _round_load(float(e1rm_kg) * float(pct_wk), inc=inc)

        else:
            # DB/KB (reps up then load) uses no pct; clinician can add pct later if desired
            # For now we only compute load if clinician supplies pct; otherwise leave null.
            pct_wk = None
            load_wk = None

        out.append({
            "week_no": wk,
            "sets": sets_wk,
            "reps": reps_wk,
            "time_sec": time_wk,
            "pct_1rm": pct_wk,
            "load_kg": load_wk,
            "rpe_target": rpe_target,
            "rest_sec": rest_sec,
            "intent": intent,
            "notes": "Deload" if deload else None,
        })

    return out
