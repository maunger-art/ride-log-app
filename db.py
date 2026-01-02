def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # ... existing tables ...

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
def save_strava_tokens(patient_id: int, access_token: str, refresh_token: str, expires_at: int, athlete_id: int | None, scope: str | None):
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
        FROM strava_tokens WHERE patient_id = ?
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
    """, (patient_id, activity_id))
    conn.commit()
    conn.close()

def is_activity_synced(patient_id: int, activity_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM strava_synced WHERE patient_id = ? AND strava_activity_id = ?
    """, (patient_id, activity_id))
    ok = cur.fetchone() is not None
    conn.close()
    return ok
    
