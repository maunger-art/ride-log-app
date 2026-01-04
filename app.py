import streamlit as st
import os
from supabase import Client, create_client

# -------------------------------------------------
# Streamlit app config (MUST be first Streamlit call)
# -------------------------------------------------
st.set_page_config(
    page_title="Technique | Performance & Rehab",
    page_icon="assets/technique_favicon.png",
    layout="wide",
)

# -------------------------------------------------
# Supabase configuration (ENV)
# -------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Supabase environment variables are not set.")
    st.stop()

# -------------------------------------------------
# OPTIONAL sanity check during setup
# -------------------------------------------------
# st.sidebar.success("Supabase connected")

# -------------------------------------------------
# The rest of your imports
# -------------------------------------------------
import pandas as pd
import time
from datetime import date, datetime, timedelta
from typing import Optional

from plan import parse_plan_csv, rides_to_weekly_summary, to_monday
from strava import build_auth_url, exchange_code_for_token, ensure_fresh_token, list_activities
import db_store as db

# Optional: seed strength DB via sidebar button
try:
    from seed_strength_standards import seed as seed_strength_db
except Exception:
    seed_strength_db = None


# -----------------------------
# Helpers
# -----------------------------
def _age_from_dob_or_manual(dob_str: str, manual_age: int) -> int:
    if dob_str and dob_str.strip():
        try:
            dob_dt = datetime.strptime(dob_str.strip(), "%Y-%m-%d").date()
            today = date.today()
            return today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))
        except Exception:
            return int(manual_age)
    return int(manual_age)


def _parse_exercise_style(ex_row) -> str:
    """
    Heuristic to decide progression behaviour:
    - Isometric/bodyweight/core: reps are seconds typically
    - Conditioning machine: reps are minutes
    - DB/KB: reps-first then load
    - Barbell: load progression
    """
    # ex_row: (id, name, category, laterality, implement, primary_muscles, notes)
    if not ex_row:
        return "unknown"

    _, name, category, laterality, implement, _, notes = ex_row
    name_l = (name or "").lower()
    cat = (category or "").lower()
    impl = (implement or "").lower()
    nts = (notes or "").lower()

    if "isometric" in name_l or "isometric" in nts or "wall sit" in name_l or "plank" in name_l:
        return "isometric"

    if cat == "conditioning" or "bike erg" in name_l or "erg" in name_l:
        return "conditioning"

    if impl in ["dumbbell", "kettlebell", "band"]:
        return "db_kb"

    if impl in ["barbell"]:
        return "barbell"

    if impl in ["bodyweight"]:
        return "bodyweight"

    return "generic"


def _suggest_progression(
    style: str,
    week_no: int,
    deload: bool,
    sets_base: int,
    reps_base: int,
    load_base: Optional[float],
    pct_base: Optional[float],
) -> tuple[int, int, Optional[float], Optional[float]]:
    """
    Default suggestion engine (editable in UI):
    - bodyweight/isometric: increase reps/time linearly (deload reduces)
    - DB/KB: increase reps first then load
    - Barbell: increase load within rep range
    """
    # Deload rules
    if deload:
        # reduce volume and load slightly
        sets_t = max(1, int(round(sets_base * 0.6)))
        reps_t = max(1, int(round(reps_base * 0.7)))
        load_t = None if load_base is None else round(load_base * 0.9, 1)
        pct_t = None if pct_base is None else round(pct_base * 0.9, 3)
        return sets_t, reps_t, load_t, pct_t

    # Non-deload: progression
    if style in ["isometric", "bodyweight"]:
        # linear time/reps: +5 per week
        reps_t = reps_base + (week_no - 1) * 5
        return sets_base, reps_t, load_base, pct_base

    if style == "conditioning":
        # minutes: +1 min per week
        reps_t = reps_base + (week_no - 1) * 1
        return sets_base, reps_t, load_base, pct_base

    if style == "db_kb":
        # reps-first then load: add reps until 12, then +2.5kg and drop reps back to 8
        reps_t = reps_base + (week_no - 1) * 1
        load_t = load_base
        if load_base is not None:
            if reps_t > 12:
                reps_t = 8
                load_t = round(load_base + 2.5, 1)
        return sets_base, reps_t, load_t, pct_base

    if style == "barbell":
        # load progression: +2.5kg per week if load known; pct +0.02 otherwise
        load_t = load_base
        pct_t = pct_base
        if load_base is not None:
            load_t = round(load_base + (week_no - 1) * 2.5, 1)
        elif pct_base is not None:
            pct_t = round(pct_base + (week_no - 1) * 0.02, 3)
        return sets_base, reps_base, load_t, pct_t

    # generic: gentle reps progression
    reps_t = reps_base + (week_no - 1) * 1
    return sets_base, reps_t, load_base, pct_base


# -----------------------------
# Auth helpers
# -----------------------------
@st.cache_resource
def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def require_authenticated_user() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Supabase configuration missing. Set SUPABASE_URL and SUPABASE_KEY.")
        st.stop()

    if "auth_user" in st.session_state:
        return st.session_state["auth_user"]

    st.title("Sign in")
    st.caption("Use your email and password to access the Ride Log.")

    tab_sign_in, tab_sign_up = st.tabs(["Sign in", "Sign up"])

    with tab_sign_in:
        with st.form("sign_in_form", clear_on_submit=False):
            email = st.text_input("Email", key="sign_in_email")
            password = st.text_input("Password", type="password", key="sign_in_password")
            submitted = st.form_submit_button("Sign in")
        if submitted:
            try:
                client = get_supabase_client()
                response = client.auth.sign_in_with_password({"email": email, "password": password})
                user = response.user
                st.session_state["auth_user"] = {"id": user.id, "email": user.email}
                st.session_state["auth_session"] = {
                    "access_token": response.session.access_token if response.session else None,
                    "refresh_token": response.session.refresh_token if response.session else None,
                }
                st.rerun()
            except Exception as exc:
                st.error(f"Sign in failed: {exc}")

    with tab_sign_up:
        with st.form("sign_up_form", clear_on_submit=False):
            email = st.text_input("Email", key="sign_up_email")
            password = st.text_input("Password", type="password", key="sign_up_password")
            submitted = st.form_submit_button("Create account")
        if submitted:
            try:
                client = get_supabase_client()
                client.auth.sign_up({"email": email, "password": password})
                st.success("Check your email to confirm your account, then sign in.")
            except Exception as exc:
                st.error(f"Sign up failed: {exc}")

    st.stop()


# -----------------------------
# Streamlit config
# -----------------------------

db.init_db()

auth_user = require_authenticated_user()
user_id = auth_user["id"]
user_email = auth_user.get("email") or "Unknown"

role = db.get_user_role(user_id)
if role not in ["client", "coach"]:
    st.title("Choose your role")
    st.info("Select a role to finish setting up your account.")
    role_choice = st.selectbox("Role", options=["client", "coach"], key="role_choice")
    if st.button("Save role"):
        db.upsert_user_role(user_id, role_choice)
        st.success("Role saved. Reloading...")
        st.rerun()
    st.stop()

st.sidebar.caption(f"Signed in as {user_email}")
st.sidebar.caption(f"Role: {role}")
if st.sidebar.button("Sign out"):
    st.session_state.pop("auth_user", None)
    st.session_state.pop("auth_session", None)
    st.rerun()

st.title("Ride Log – Plan vs Actual")


# -------------------------------------------------------------------
# Sidebar: Patient selection / creation
# -------------------------------------------------------------------
st.sidebar.header("Patient")
patients = db.list_patients_for_user(user_id, role)
names = [p[1] for p in patients]
pid = None

if role == "client":
    if not patients:
        default_name = user_email.split("@")[0] if "@" in user_email else ""
        new_name = st.sidebar.text_input("Enter your name", value=default_name)
        if st.sidebar.button("Create your patient profile") and new_name.strip():
            pid = db.upsert_patient(new_name.strip(), owner_user_id=user_id)
            st.sidebar.success("Profile created.")
            st.rerun()
        st.stop()

    if len(names) == 1:
        selected = names[0]
    else:
        selected = st.sidebar.selectbox("Select patient", options=names)

    pid = [p[0] for p in patients if p[1] == selected][0]
else:
    selected = st.sidebar.selectbox("Select patient", options=["(New patient)"] + names)
    if selected == "(New patient)":
        new_name = st.sidebar.text_input("Enter patient name")
        if st.sidebar.button("Create patient") and new_name.strip():
            pid = db.upsert_patient(new_name.strip())
            db.assign_patient_to_coach(user_id, pid)
            st.sidebar.success("Patient created and assigned.")
            st.rerun()
    else:
        pid = [p[0] for p in patients if p[1] == selected][0]

    st.sidebar.caption("Assign existing patient by ID (coach only).")
    assign_id = st.sidebar.text_input("Patient ID", key="assign_patient_id")
    if st.sidebar.button("Assign patient"):
        if assign_id.strip().isdigit():
            db.assign_patient_to_coach(user_id, int(assign_id))
            st.sidebar.success("Patient assigned.")
            st.rerun()
        else:
            st.sidebar.error("Enter a numeric patient ID.")

if pid is None:
    st.warning("Please create or select a patient in the sidebar before using the app.")
    st.stop()

# -------------------------------------------------------------------
# Sidebar: Admin / Seeding
# -------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("Admin")
st.sidebar.caption(f"Strength standards rows: {db.count_norm_rows()}")

if seed_strength_db is not None:
    if st.sidebar.button("Seed strength DB"):
        seed_strength_db()
        st.sidebar.success("Seed complete (or already seeded).")
        st.rerun()
else:
    st.sidebar.caption("Seed tool not available (seed_strength_standards.py not found).")


# -------------------------------------------------------------------
# Tabs
# -------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs(["Log Ride", "Dashboard", "Plan Import / Edit", "S&C Planning"])


# -------------------------------------------------------------------
# TAB 1: Log Ride
# -------------------------------------------------------------------
with tab1:
    st.subheader("Log a ride")

    col1, col2, col3 = st.columns(3)
    with col1:
        ride_date = st.date_input("Date", value=date.today())
        distance_km = st.number_input("Distance (km)", min_value=0.0, step=1.0)
    with col2:
        duration_min = st.number_input("Duration (minutes)", min_value=0, step=5)
        rpe = st.number_input("RPE (1–10)", min_value=1, max_value=10, value=3)
    with col3:
        notes = st.text_area("Notes (optional)", height=120)

    if st.button("Save ride"):
        db.add_ride_for_user(
            user_id,
            role,
            pid,
            ride_date.isoformat(),
            float(distance_km),
            int(duration_min),
            int(rpe),
            notes.strip() if notes else None,
        )
        st.success("Ride saved.")
        st.rerun()

    st.divider()
    st.subheader("Recent rides")
    rides = db.fetch_rides_for_user(user_id, role, pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date", "distance_km", "duration_min", "rpe", "notes"])
    st.dataframe(rides_df, use_container_width=True)


# -------------------------------------------------------------------
# TAB 2: Dashboard (Plan vs Actual + Strava)
# -------------------------------------------------------------------
with tab2:
    st.subheader("Plan vs actual (weekly)")

    # -----------------------------
    # STRAVA CONNECT + SYNC
    # -----------------------------
    st.divider()
    st.subheader("Strava (import actual rides)")

    qp = st.query_params
    if "code" in qp and "state" in qp:
        if str(qp["state"]) == str(pid):
            data = exchange_code_for_token(qp["code"])
            db.save_strava_tokens_for_user(
                user_id,
                role,
                pid,
                data["access_token"],
                data["refresh_token"],
                int(data["expires_at"]),
                data.get("athlete", {}).get("id"),
                str(data.get("scope")),
            )
            st.success("Strava connected.")
            st.query_params.clear()
            st.rerun()
        else:
            st.warning("Strava callback state did not match the selected patient. Please try again.")
            st.query_params.clear()
            st.rerun()

    token_row = db.get_strava_tokens_for_user(user_id, role, pid)

    if token_row is None:
        st.link_button("Connect Strava", build_auth_url(state=str(pid)))
        st.caption("Connect Strava to automatically import rides into the log.")
    else:
        access_token, refresh_token, expires_at, athlete_id, scope, refreshed = ensure_fresh_token(token_row)
        if refreshed:
            db.save_strava_tokens_for_user(user_id, role, pid, access_token, refresh_token, expires_at, athlete_id, str(scope))

        days_back = st.number_input("Sync how many days back?", min_value=1, max_value=365, value=30)

        if st.button("Sync Strava rides"):
            after_epoch = int(time.time() - int(days_back) * 86400)
            imported = 0
            page = 1

            while True:
                acts = list_activities(access_token, after_epoch=after_epoch, per_page=50, page=page)
                if not acts:
                    break

                for a in acts:
                    sport = a.get("sport_type") or a.get("type")
                    if sport not in ["Ride", "VirtualRide", "EBikeRide", "GravelRide", "MountainBikeRide"]:
                        continue

                    act_id = int(a["id"])
                    if db.is_activity_synced_for_user(user_id, role, pid, act_id):
                        continue

                    ride_date_str = a["start_date_local"][:10]  # YYYY-MM-DD
                    distance_km_val = float(a.get("distance", 0)) / 1000.0
                    duration_min_val = int(round(float(a.get("elapsed_time", 0)) / 60.0))
                    name = a.get("name", "Strava ride")

                    db.add_ride_for_user(
                        user_id,
                        role,
                        pid,
                        ride_date_str,
                        distance_km_val,
                        duration_min_val,
                        None,
                        f"[Strava] {name}",
                    )

                    db.mark_activity_synced_for_user(user_id, role, pid, act_id)
                    imported += 1

                page += 1

            st.success(f"Imported {imported} new Strava rides.")
            st.rerun()

    # -----------------------------
    # PLAN VS ACTUAL (WEEKLY)
    # -----------------------------
    st.divider()
    st.subheader("Weekly plan vs actual")

    rides = db.fetch_rides_for_user(user_id, role, pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date", "distance_km", "duration_min", "rpe", "notes"])

    plan_rows = db.fetch_week_plans_for_user(user_id, role, pid)
    plan_df = pd.DataFrame(plan_rows, columns=["week_start", "planned_km", "planned_hours", "phase", "notes"])

    if not plan_df.empty:
        plan_df["week_start"] = pd.to_datetime(plan_df["week_start"], errors="coerce").dt.normalize()

    weekly_actual = rides_to_weekly_summary(rides_df)
    if not weekly_actual.empty:
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"], errors="coerce").dt.normalize()
    else:
        weekly_actual = pd.DataFrame(columns=["week_start", "actual_km", "actual_hours", "rides_count"])
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"])

    if plan_df.empty and weekly_actual.empty:
        st.info("No plan or rides yet. Add rides or import a plan on the Plan tab.")
    else:
        if plan_df.empty:
            merged = weekly_actual.copy()
        elif weekly_actual.empty:
            merged = plan_df.copy()
        else:
            merged = pd.merge(plan_df, weekly_actual, on="week_start", how="outer").sort_values("week_start")

        for c in ["planned_km", "planned_hours", "actual_km", "actual_hours", "rides_count"]:
            if c in merged.columns:
                merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0)

        if "planned_km" in merged.columns and "actual_km" in merged.columns:
            merged["km_variance"] = merged["actual_km"] - merged["planned_km"]
        if "planned_hours" in merged.columns and "actual_hours" in merged.columns:
            merged["hours_variance"] = merged["actual_hours"] - merged["planned_hours"]

        st.dataframe(merged, use_container_width=True)


# -------------------------------------------------------------------
# TAB 3: Plan Import / Edit
# -------------------------------------------------------------------
with tab3:
    st.subheader("Plan import (CSV)")
    st.write("Upload a CSV with columns: week_start (Monday, YYYY-MM-DD), planned_km, planned_hours, phase, notes.")

    if role != "coach":
        st.info("Your coach manages the training plan. You can view it below.")
        plan_rows = db.fetch_week_plans_for_user(user_id, role, pid)
        plan_df = pd.DataFrame(plan_rows, columns=["week_start", "planned_km", "planned_hours", "phase", "notes"])
        if plan_df.empty:
            st.caption("No plan uploaded yet.")
        else:
            st.dataframe(plan_df, use_container_width=True)
    else:
        uploaded = st.file_uploader("Upload plan CSV", type=["csv"], key="plan_csv_uploader")
        if uploaded is not None:
            try:
                df = parse_plan_csv(uploaded)
                st.success(f"Loaded {len(df)} plan rows.")
                st.dataframe(df, use_container_width=True)

                if st.button("Save plan to patient", key="save_plan_btn"):
                    for _, row in df.iterrows():
                        db.upsert_week_plan_for_user(
                            user_id,
                            role,
                            pid,
                            row["week_start"].isoformat(),
                            float(row["planned_km"]) if "planned_km" in df.columns and pd.notna(row.get("planned_km")) else None,
                            float(row["planned_hours"]) if "planned_hours" in df.columns and pd.notna(row.get("planned_hours")) else None,
                            str(row["phase"]) if "phase" in df.columns and pd.notna(row.get("phase")) else None,
                            str(row["notes"]) if "notes" in df.columns and pd.notna(row.get("notes")) else None,
                        )
                    st.success("Plan saved.")
                    st.rerun()

            except Exception as e:
                st.error(f"Plan import error: {e}")

        st.divider()
        st.subheader("Manual plan edit (single week)")
        week_start = st.date_input("Week start (Monday)", value=to_monday(date.today()), key="manual_week_start")
        col1, col2, col3 = st.columns(3)
        with col1:
            planned_km = st.number_input("Planned km", min_value=0.0, step=10.0, key="manual_planned_km")
        with col2:
            planned_hours = st.number_input("Planned hours", min_value=0.0, step=1.0, key="manual_planned_hours")
        with col3:
            phase = st.text_input("Phase (e.g., Base/Build/Peak/Deload/Event)", key="manual_phase")
        note = st.text_area("Notes", height=80, key="manual_note")

        if st.button("Save this week", key="save_week_btn"):
            db.upsert_week_plan_for_user(
                user_id,
                role,
                pid,
                week_start.isoformat(),
                planned_km,
                planned_hours,
                phase.strip() if phase else None,
                note.strip() if note else None,
            )
            st.success("Week saved to plan.")
            st.rerun()


# -------------------------------------------------------------------
# TAB 4: S&C Planning (MVP v2)
# -------------------------------------------------------------------
with tab4:
    st.subheader("S&C Planning")

    st.caption(f"Strength standards rows: {db.count_norm_rows()}")
    if db.count_norm_rows() == 0:
        st.warning("Strength standards are not seeded. Add seed_strength_standards.py and run 'Seed strength DB'.")
        st.stop()

    st.divider()

    # -----------------------------
    # Patient profile
    # -----------------------------
    st.subheader("Patient profile (drives auto-estimates)")

    profile = db.get_patient_profile_for_user(user_id, role, pid)
    sex_default = profile[0] if profile else None
    dob_default = profile[1] if profile else ""
    bw_default = profile[2] if profile else None
    level_default = profile[3] if (profile and len(profile) > 3) else "intermediate"

    level_options = ["novice", "intermediate", "advanced", "expert"]
    if level_default not in level_options:
        level_default = "intermediate"

    colp1, colp2, colp3, colp4 = st.columns(4)
    with colp1:
        sex = st.selectbox(
            "Sex",
            options=["", "male", "female"],
            index=(["", "male", "female"].index(sex_default) if sex_default in ["male", "female"] else 0),
            key="profile_sex",
        )
    with colp2:
        dob = st.text_input("DOB (YYYY-MM-DD) – optional", value=(dob_default or ""), key="profile_dob")
    with colp3:
        bodyweight_kg = st.number_input(
            "Bodyweight (kg)", min_value=0.0, step=0.1,
            value=(float(bw_default) if bw_default is not None else 0.0),
            key="profile_bw",
        )
    with colp4:
        presumed_level = st.selectbox(
            "Presumed strength level",
            options=level_options,
            index=level_options.index(level_default),
            key="profile_level",
        )

    if st.button("Save profile", key="save_profile_tab4"):
        db.upsert_patient_profile_for_user(
            user_id,
            role,
            pid,
            sex if sex else None,
            dob.strip() if dob else None,
            float(bodyweight_kg) if bodyweight_kg and bodyweight_kg > 0 else None,
            presumed_level,
        )
        st.success("Profile saved.")
        st.rerun()

    if not sex:
        st.info("Select sex and save profile to enable estimates.")
        st.stop()

    age_manual = st.number_input("Age (years) – used if DOB blank/invalid", min_value=18, max_value=65, value=35)
    age_years = _age_from_dob_or_manual(dob, int(age_manual))
    bw_use = float(bodyweight_kg) if (bodyweight_kg and bodyweight_kg > 0) else None

    st.caption(f"Age used: {age_years} | Level used: {presumed_level}")

    st.divider()

    # -----------------------------
    # 1RM predictor (restored)
    # -----------------------------
    st.subheader("1RM predictor (auto-estimated from norms + BW + presumed level)")

    exercises = db.list_exercises()
    if not exercises:
        st.warning("No exercises found. Seed exercises first.")
        st.stop()

    ex_name_map = {row[1]: row[0] for row in exercises}
    ex_names = sorted(ex_name_map.keys())
    selected_ex = st.selectbox("Exercise", options=ex_names)
    ex_id = ex_name_map[selected_ex]

    metric = "pullup_reps" if selected_ex.lower().startswith(("pull-up", "pullup")) else "rel_1rm_bw"

    est = db.estimate_e1rm_kg_for_exercise(
        patient_sex=sex,
        patient_age=int(age_years),
        patient_bw_kg=bw_use,
        presumed_level=presumed_level,
        exercise_id=ex_id,
        metric=metric,
    )

    if metric == "pullup_reps":
        st.info("Pull-ups use reps/sets. No 1RM estimate.")
    else:
        if est["estimated_1rm_kg"] is None:
            st.warning(est["notes"])
        else:
            st.metric("Estimated 1RM", f"{est['estimated_1rm_kg']:.1f} kg")
            st.caption(
                f"Rel strength: {est['estimated_rel_1rm_bw']:.2f}×BW | "
                f"Age band: {est['band_used']} | Method: {est['method']}"
            )
            if st.button("Save this estimate"):
                db.upsert_strength_estimate_for_user(
                    user_id,
                    role,
                    pid,
                    exercise_id=ex_id,
                    as_of_date=date.today().isoformat(),
                    estimated_1rm_kg=float(est["estimated_1rm_kg"]),
                    estimated_rel_1rm_bw=float(est["estimated_rel_1rm_bw"]),
                    level_used=presumed_level,
                    sex_used=sex,
                    age_used=int(age_years),
                    bw_used=bw_use,
                    method=est["method"],
                    notes=est["notes"],
                )
                st.success("Estimate saved.")
                st.rerun()

    st.divider()

    # -----------------------------
    # Rep range tool (restored)
    # -----------------------------
    st.subheader("Rep schemes (endurance / hypertrophy / strength / power)")

    goal = st.selectbox("Goal", options=["endurance", "hypertrophy", "strength", "power"], index=0)
    schemes = db.list_rep_schemes(goal)
    if not schemes:
        st.warning("No rep schemes found for this goal. Seed rep schemes first.")
    else:
        s = schemes[0]
        _, s_goal, s_phase, reps_min, reps_max, sets_min, sets_max, pct_min, pct_max, rpe_min, rpe_max, rest_min, rest_max, intent = s
        st.write(f"**Scheme:** {s_goal} ({s_phase or 'default'})")
        st.write(f"- Sets: {sets_min}–{sets_max}")
        st.write(f"- Reps: {reps_min}–{reps_max}")
        if pct_min is not None and pct_max is not None:
            st.write(f"- %1RM: {int(pct_min*100)}–{int(pct_max*100)}%")
        else:
            st.write("- %1RM: n/a")
        if rest_min and rest_max:
            st.write(f"- Rest: {rest_min}–{rest_max} sec")
        if rpe_min and rpe_max:
            st.write(f"- RPE: {rpe_min}–{rpe_max}")
        if intent:
            st.write(f"- Intent: {intent}")

        if metric == "rel_1rm_bw" and est.get("estimated_1rm_kg") and pct_min is not None and pct_max is not None:
            w_min = float(est["estimated_1rm_kg"]) * float(pct_min)
            w_max = float(est["estimated_1rm_kg"]) * float(pct_max)
            st.info(f"Working load range (based on estimate): {w_min:.1f}–{w_max:.1f} kg")

    st.divider()

    # -----------------------------
    # 6-week block builder (4/6/8 selectable)
    # -----------------------------
    st.subheader("Create a block (4 / 6 / 8 weeks, hybrid progression, editable)")

    if role != "coach":
        st.info("Your coach manages S&C blocks. You can log actuals below.")
    else:
        colb1, colb2, colb3, colb4 = st.columns(4)
        with colb1:
            block_start = st.date_input("Block start date (Mon recommended)", value=to_monday(date.today()))
        with colb2:
            block_weeks = st.selectbox("Block length (weeks)", options=[4, 6, 8], index=1)
        with colb3:
            sessions_pw = st.selectbox("Sessions per week", options=[1, 2], index=1)
        with colb4:
            deload_week = st.number_input("Deload week #", min_value=1, max_value=int(block_weeks), value=min(4, int(block_weeks)))

        block_goal = st.selectbox("Block goal label", options=["hybrid", "endurance", "hypertrophy", "strength", "power"], index=0)
        block_notes = st.text_area("Block notes (optional)", height=80)

        st.caption("Templates: define Week 1 Session A/B below. The app will auto-suggest progressions across weeks (Week 4 deload by default), but you can edit targets and record actuals.")

        exercises_rows = [db.get_exercise(ex_name_map[n]) for n in ex_names]
        ex_by_name = {r[1]: r for r in exercises_rows}

        def _template_editor(session_label: str):
            st.markdown(f"### Session {session_label} template (Week 1)")
            n_rows = st.number_input(f"How many exercises in Session {session_label}?", min_value=1, max_value=12, value=6, key=f"n_{session_label}")
            rows = []
            for i in range(int(n_rows)):
                c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 2])
                with c1:
                    ex_name = st.selectbox(f"Exercise {i+1}", options=["(none)"] + ex_names, key=f"{session_label}_ex_{i}")
                with c2:
                    sets = st.number_input("Sets", min_value=1, max_value=10, value=3, key=f"{session_label}_sets_{i}")
                with c3:
                    reps = st.number_input("Reps/Time", min_value=1, max_value=999, value=10, key=f"{session_label}_reps_{i}")
                with c4:
                    pct = st.number_input("%1RM", min_value=0.0, max_value=1.0, value=0.70, step=0.05, key=f"{session_label}_pct_{i}")
                with c5:
                    load = st.number_input("Load kg (optional)", min_value=0.0, value=0.0, step=2.5, key=f"{session_label}_load_{i}")

                if ex_name != "(none)":
                    rows.append({
                        "exercise_name": ex_name,
                        "sets": int(sets),
                        "reps": int(reps),
                        "pct": float(pct) if pct > 0 else None,
                        "load": float(load) if load and load > 0 else None,
                    })
            return rows

        template_A = _template_editor("A")
        template_B = _template_editor("B") if sessions_pw == 2 else []

        if st.button("Create block + auto-generate weeks/sessions"):
            block_id = db.create_sc_block_for_user(
                user_id,
                role,
                pid,
                start_date=block_start.isoformat(),
                goal=block_goal,
                notes=block_notes.strip() if block_notes else None,
                weeks=int(block_weeks),
                model="hybrid_v1",
                deload_week=int(deload_week),
                sessions_per_week=int(sessions_pw),
            )

            # Create weeks + sessions + exercises with progression
            for wk in range(1, int(block_weeks) + 1):
                wk_start = (block_start + timedelta(days=(wk - 1) * 7)).isoformat()
                is_deload = (wk == int(deload_week))
                focus = "deload" if is_deload else block_goal

                week_id = db.upsert_sc_week_for_user(
                    user_id=user_id,
                    role=role,
                    block_id=block_id,
                    week_no=wk,
                    week_start=wk_start,
                    focus=focus,
                    deload_flag=is_deload,
                    notes=None,
                )

                # Session labels
                labels = ["A"] if int(sessions_pw) == 1 else ["A", "B"]

                for lab in labels:
                    sess_id = db.upsert_sc_session_for_user(
                        user_id=user_id,
                        role=role,
                        week_id=week_id,
                        session_label=lab,
                        day_hint=None,
                        notes=None,
                    )
                    db.clear_sc_session_exercises_for_user(user_id, role, sess_id)

                    tpl = template_A if lab == "A" else template_B

                    for row in tpl:
                        ex_row = ex_by_name.get(row["exercise_name"])
                        style = _parse_exercise_style(ex_row)

                        sets_t, reps_t, load_t, pct_t = _suggest_progression(
                            style=style,
                            week_no=wk,
                            deload=is_deload,
                            sets_base=row["sets"],
                            reps_base=row["reps"],
                            load_base=row["load"],
                            pct_base=row["pct"],
                        )

                        db.add_sc_session_exercise_for_user(
                            user_id=user_id,
                            role=role,
                            session_id=sess_id,
                            exercise_id=ex_name_map[row["exercise_name"]],
                            sets_target=int(sets_t),
                            reps_target=int(reps_t),
                            pct_1rm_target=pct_t,
                            load_kg_target=load_t,
                            rpe_target=None,
                            rest_sec_target=None,
                            intent=None,
                            notes=f"Auto-suggest ({style})",
                        )

            st.success(f"Block created (ID: {block_id}).")
            st.rerun()

    st.divider()

    # -----------------------------
    # View latest block + edit actuals
    # -----------------------------
    st.subheader("Latest block (targets + actuals)")

    latest = db.fetch_latest_sc_block_for_user(user_id, role, pid)
    if latest is None:
        st.info("No S&C block created yet.")
        st.stop()

    block_id, start_date_s, weeks, model, deload_wk, spw, goal_s, notes_s, created_at = latest
    st.caption(f"Block #{block_id} | Start {start_date_s} | {weeks}w | deload week {deload_wk} | {spw} sessions/wk | goal={goal_s}")

    detail = db.fetch_sc_block_detail_for_user(user_id, role, block_id)

    # Render week by week
    for (wk_no, wk_start, focus, is_deload, label, day_hint, exs) in detail:
        with st.expander(f"Week {wk_no} ({wk_start}) - Session {label} {'(DELOAD)' if is_deload else ''}", expanded=(wk_no == 1)):
            if not exs:
                st.info("No exercises found for this session.")
                continue

            for ex in exs:
                (row_id, ex_name, sets_t, reps_t, pct_t, load_t, rpe_t, rest_t, intent, n_notes,
                 sets_a, reps_a, load_a, completed, a_notes) = ex

                st.markdown(f"**{ex_name}**")
                st.caption(f"Target: {sets_t} x {reps_t} | %1RM={pct_t if pct_t is not None else 'n/a'} | load={load_t if load_t is not None else 'n/a'} | {n_notes or ''}")

                c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
                with c1:
                    sets_actual = st.number_input("Actual sets", min_value=0, value=int(sets_a) if sets_a is not None else 0, key=f"a_sets_{row_id}")
                with c2:
                    reps_actual = st.number_input("Actual reps/time", min_value=0, value=int(reps_a) if reps_a is not None else 0, key=f"a_reps_{row_id}")
                with c3:
                    load_actual = st.number_input("Actual load (kg)", min_value=0.0, value=float(load_a) if load_a is not None else 0.0, step=2.5, key=f"a_load_{row_id}")
                with c4:
                    done = st.checkbox("Completed", value=bool(completed), key=f"a_done_{row_id}")
                    note_actual = st.text_input("Actual notes", value=a_notes or "", key=f"a_note_{row_id}")

                if st.button("Save actual", key=f"save_actual_{row_id}"):
                    db.update_sc_session_exercise_actual_for_user(
                        user_id=user_id,
                        role=role,
                        row_id=row_id,
                        sets_actual=int(sets_actual) if sets_actual > 0 else None,
                        reps_actual=int(reps_actual) if reps_actual > 0 else None,
                        load_kg_actual=float(load_actual) if load_actual > 0 else None,
                        completed_flag=bool(done),
                        actual_notes=note_actual.strip() if note_actual else None,
                    )
                    st.success("Saved.")
                    st.rerun()
