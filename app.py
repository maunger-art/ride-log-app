import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List

from db_store import (
    init_db,

    # patients
    upsert_patient,
    list_patients,

    # rides
    add_ride,
    fetch_rides,

    # weekly plan
    upsert_week_plan,
    fetch_week_plans,

    # strava
    save_strava_tokens,
    get_strava_tokens,
    is_activity_synced,
    mark_activity_synced,

    # profile
    upsert_patient_profile,
    get_patient_profile,

    # S&C library
    list_exercises,
    list_rep_schemes,
    count_norm_rows,
    get_exercise,

    # strength estimation
    estimate_e1rm_kg_for_exercise,
    estimate_unilateral_from_bilateral,
    upsert_strength_estimate,
    get_strength_estimate,

    # S&C programming engine
    create_sc_block,
    fetch_latest_sc_block,
    upsert_sc_week,
    upsert_sc_session,
    upsert_sc_session_template,
    clear_sc_template_exercises,
    add_sc_template_exercise,
    list_sc_session_templates,
    list_sc_template_exercises,
    upsert_sc_week_target,
    set_sc_week_actuals,
    fetch_sc_week_targets_for_block,
    generate_sc_targets_for_template_row,
)

from plan import parse_plan_csv, rides_to_weekly_summary, to_monday
from strava import build_auth_url, exchange_code_for_token, ensure_fresh_token, list_activities

# Optional: seed strength DB via sidebar button (safe to omit if file not present)
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


def _metric_for_exercise_name(name: str) -> str:
    n = (name or "").lower()
    if n.startswith("pull-up") or n.startswith("pullup") or "pull-up" in n or "pullup" in n:
        return "pullup_reps"
    return "rel_1rm_bw"


def _to_iso(d: date) -> str:
    return d.isoformat()


def _default_monday(d: date) -> date:
    return to_monday(d)


def _ensure_block_weeks_sessions(block_id: int, start_date_iso: str, weeks: int, deload_week: int, sessions_per_week: int) -> None:
    """
    Creates/updates sc_weeks + sc_sessions for the block.
    Session labels: A, B, C (up to 3 supported by UI; you can extend).
    """
    start_dt = datetime.strptime(start_date_iso, "%Y-%m-%d").date()
    labels = ["A", "B", "C"][: max(1, min(int(sessions_per_week), 3))]

    for wk in range(1, int(weeks) + 1):
        wk_start = start_dt + timedelta(days=7 * (wk - 1))
        deload_flag = (wk == int(deload_week))
        focus = "deload" if deload_flag else None
        week_id = upsert_sc_week(
            block_id=block_id,
            week_no=wk,
            week_start=wk_start.isoformat(),
            focus=focus,
            deload_flag=deload_flag,
            notes=None,
        )
        for lab in labels:
            upsert_sc_session(week_id=week_id, session_label=lab, day_hint=None, notes=None)


def _init_template_state() -> None:
    if "sc_template_rows" not in st.session_state:
        # block_id -> session_label -> list[dict]
        st.session_state["sc_template_rows"] = {}


def _get_template_rows(block_id: int, session_label: str) -> List[Dict[str, Any]]:
    _init_template_state()
    bkey = str(block_id)
    if bkey not in st.session_state["sc_template_rows"]:
        st.session_state["sc_template_rows"][bkey] = {}
    if session_label not in st.session_state["sc_template_rows"][bkey]:
        st.session_state["sc_template_rows"][bkey][session_label] = []
    return st.session_state["sc_template_rows"][bkey][session_label]


def _set_template_rows(block_id: int, session_label: str, rows: List[Dict[str, Any]]) -> None:
    _init_template_state()
    bkey = str(block_id)
    if bkey not in st.session_state["sc_template_rows"]:
        st.session_state["sc_template_rows"][bkey] = {}
    st.session_state["sc_template_rows"][bkey][session_label] = rows


def _load_templates_from_db(block_id: int) -> None:
    """
    Pull existing templates + rows from DB into session_state (once).
    """
    _init_template_state()
    bkey = str(block_id)
    if st.session_state["sc_template_rows"].get(bkey):
        return  # already loaded for this block

    st.session_state["sc_template_rows"][bkey] = {}
    templates = list_sc_session_templates(block_id)
    for tid, session_label, title, notes in templates:
        rows = list_sc_template_exercises(tid)
        out_rows = []
        for r in rows:
            (
                te_id, sort_order, group_key, group_order,
                exercise_id, ex_name, implement,
                mode, sets,
                reps_start, reps_step, reps_cap,
                time_start_sec, time_step_sec, time_cap_sec,
                pct_1rm_start, pct_1rm_step, pct_1rm_cap,
                load_increment_kg,
                rpe_target, rest_sec, intent, te_notes
            ) = r
            out_rows.append({
                "sort_order": int(sort_order or 0),
                "group_key": group_key,
                "group_order": None if group_order is None else int(group_order),
                "exercise_id": int(exercise_id),
                "exercise_name": ex_name,
                "mode": mode or "reps",
                "sets": int(sets or 3),
                "reps_start": None if reps_start is None else int(reps_start),
                "reps_step": int(reps_step or 2),
                "reps_cap": None if reps_cap is None else int(reps_cap),
                "time_start_sec": None if time_start_sec is None else int(time_start_sec),
                "time_step_sec": int(time_step_sec or 10),
                "time_cap_sec": None if time_cap_sec is None else int(time_cap_sec),
                "pct_1rm_start": None if pct_1rm_start is None else float(pct_1rm_start),
                "pct_1rm_step": float(pct_1rm_step or 0.0),
                "pct_1rm_cap": None if pct_1rm_cap is None else float(pct_1rm_cap),
                "load_increment_kg": float(load_increment_kg or 2.5),
                "rpe_target": None if rpe_target is None else int(rpe_target),
                "rest_sec": None if rest_sec is None else int(rest_sec),
                "intent": intent,
                "notes": te_notes,
            })
        st.session_state["sc_template_rows"][bkey][session_label] = sorted(out_rows, key=lambda x: x["sort_order"])


# -----------------------------
# App setup
# -----------------------------
st.set_page_config(page_title="Ride Log – Plan vs Actual", layout="wide")

# Initialize DB schema
init_db()

st.title("Ride Log – Plan vs Actual")


# -------------------------------------------------------------------
# Sidebar: Patient selection / creation
# -------------------------------------------------------------------
st.sidebar.header("Patient")
patients = list_patients()
names = [p[1] for p in patients]
selected = st.sidebar.selectbox("Select patient", options=["(New patient)"] + names)

pid = None
if selected == "(New patient)":
    new_name = st.sidebar.text_input("Enter patient name")
    if st.sidebar.button("Create patient") and new_name.strip():
        pid = upsert_patient(new_name.strip())
        st.sidebar.success("Patient created. Select them from the dropdown.")
        st.stop()
else:
    pid = [p[0] for p in patients if p[1] == selected][0]

# Guard: must have a patient selected
if pid is None:
    st.warning("Please create or select a patient in the sidebar before using the app.")
    st.stop()


# -------------------------------------------------------------------
# Sidebar: Admin (optional)
# -------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("Admin")
st.sidebar.caption(f"Strength standards rows: {count_norm_rows()}")

if seed_strength_db is not None:
    if st.sidebar.button("Seed strength DB"):
        seed_strength_db()
        st.sidebar.success("Seed complete (or already seeded).")
else:
    st.sidebar.caption("Seed tool not available (seed_strength_standards.py not found).")


# -------------------------------------------------------------------
# Tabs
# -------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs(["Log Ride", "Dashboard", "Plan Import / Edit", "S&C Programming"])


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
        add_ride(
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
    rides = fetch_rides(pid)
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
            save_strava_tokens(
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

    token_row = get_strava_tokens(pid)

    if token_row is None:
        st.link_button("Connect Strava", build_auth_url(state=str(pid)))
        st.caption("Connect Strava to automatically import rides into the log.")
    else:
        access_token, refresh_token, expires_at, athlete_id, scope, refreshed = ensure_fresh_token(token_row)

        if refreshed:
            save_strava_tokens(pid, access_token, refresh_token, expires_at, athlete_id, str(scope))

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
                    if is_activity_synced(pid, act_id):
                        continue

                    ride_date_str = a["start_date_local"][:10]  # YYYY-MM-DD
                    distance_km_val = float(a.get("distance", 0)) / 1000.0
                    duration_min_val = int(round(float(a.get("elapsed_time", 0)) / 60.0))
                    name = a.get("name", "Strava ride")

                    add_ride(pid, ride_date_str, distance_km_val, duration_min_val, None, f"[Strava] {name}")
                    mark_activity_synced(pid, act_id)
                    imported += 1

                page += 1

            st.success(f"Imported {imported} new Strava rides.")
            st.rerun()

    # -----------------------------
    # PLAN VS ACTUAL (WEEKLY)
    # -----------------------------
    st.divider()
    st.subheader("Weekly plan vs actual")

    rides = fetch_rides(pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date", "distance_km", "duration_min", "rpe", "notes"])

    plan_rows = fetch_week_plans(pid)
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

        st.divider()
        st.subheader("Export for coaching review")
        csv = rides_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download rides CSV", data=csv, file_name=f"{selected}_rides.csv", mime="text/csv")

        st.subheader("Copy/paste prompt for ChatGPT weekly review")
        prompt = f"""You are my cycling coach. Review my last 4 weeks of training versus plan.

Patient: {selected}
Today: {date.today().isoformat()}

Weekly Plan vs Actual (most recent):
{merged.tail(8).to_string(index=False)}

Rides (most recent 25):
{rides_df.head(25).to_string(index=False)}

Please provide:
1) adherence summary (hours/km),
2) fatigue/risk flags,
3) suggested adjustments for next 2 weeks,
4) key coaching points."""
        st.code(prompt, language="text")


# -------------------------------------------------------------------
# TAB 3: Plan Import / Edit
# -------------------------------------------------------------------
with tab3:
    st.subheader("Plan import (CSV)")
    st.write("Upload a CSV with columns: week_start (Monday, YYYY-MM-DD), planned_km, planned_hours, phase, notes.")

    uploaded = st.file_uploader("Upload plan CSV", type=["csv"], key="plan_csv_uploader")
    if uploaded is not None:
        try:
            df = parse_plan_csv(uploaded)
            st.success(f"Loaded {len(df)} plan rows.")
            st.dataframe(df, use_container_width=True)

            if st.button("Save plan to patient", key="save_plan_btn"):
                for _, row in df.iterrows():
                    upsert_week_plan(
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
    week_start = st.date_input("Week start (Monday)", value=_default_monday(date.today()), key="manual_week_start")
    col1, col2, col3 = st.columns(3)
    with col1:
        planned_km = st.number_input("Planned km", min_value=0.0, step=10.0, key="manual_planned_km")
    with col2:
        planned_hours = st.number_input("Planned hours", min_value=0.0, step=1.0, key="manual_planned_hours")
    with col3:
        phase = st.text_input("Phase (e.g., Base/Build/Peak/Deload/Event)", key="manual_phase")
    note = st.text_area("Notes", height=80, key="manual_note")

    if st.button("Save this week", key="save_week_btn"):
        upsert_week_plan(
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
# TAB 4: S&C Programming (Blocks + Linear Progression + Actual Tracking)
# -------------------------------------------------------------------
with tab4:
    st.subheader("S&C Programming (Blocks + Linear Progression)")

    st.caption(f"Strength standards rows: {count_norm_rows()}")
    if count_norm_rows() == 0:
        st.warning(
            "Strength standards are not seeded yet. "
            "Add seed_strength_standards.py to the repo root and run 'Seed strength DB' from the sidebar."
        )
        st.stop()

    st.divider()

    # -----------------------------
    # Patient profile (drives auto-estimates)
    # -----------------------------
    st.subheader("Patient profile (drives auto-estimates)")

    profile = get_patient_profile(pid)
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
            key="profile_sex_tab4",
        )
    with colp2:
        dob = st.text_input(
            "DOB (YYYY-MM-DD) – optional",
            value=(dob_default if dob_default else ""),
            help="Leave blank if you do not want DOB stored; use Age below instead.",
            key="profile_dob_tab4",
        )
    with colp3:
        bodyweight_kg = st.number_input(
            "Bodyweight (kg)",
            min_value=0.0,
            step=0.1,
            value=(float(bw_default) if bw_default is not None else 0.0),
            key="profile_bw_tab4",
        )
    with colp4:
        presumed_level = st.selectbox(
            "Presumed strength level",
            options=level_options,
            index=level_options.index(level_default),
            help="Used to estimate starting e1RM from your seeded norms (no 1RM input required).",
            key="profile_level_tab4",
        )

    if st.button("Save profile", key="save_profile_tab4"):
        upsert_patient_profile(
            pid,
            sex if sex else None,
            dob.strip() if dob else None,
            float(bodyweight_kg) if bodyweight_kg and bodyweight_kg > 0 else None,
            presumed_level,
        )
        st.success("Profile saved.")
        st.rerun()

    if not sex:
        st.info("Select sex and save the profile to enable strength estimates.")
        st.stop()

    st.divider()

    # Age (manual fallback)
    st.subheader("Age (for selecting the correct norm band)")
    age_manual = st.number_input(
        "Age (years) – used if DOB blank/invalid",
        min_value=18,
        max_value=65,
        value=35,
        key="age_manual_tab4",
    )
    age_years = _age_from_dob_or_manual(dob, int(age_manual))
    bw_use = float(bodyweight_kg) if (bodyweight_kg and bodyweight_kg > 0) else None
    st.caption(f"Age used for norms: {age_years} years | Level used: {presumed_level}")

    st.divider()

    # -----------------------------
    # Block builder
    # -----------------------------
    st.subheader("Create / manage S&C blocks")

    latest = fetch_latest_sc_block(pid)
    if latest:
        st.caption(
            f"Latest block: id={latest[0]} | start={latest[1]} | weeks={latest[2]} | "
            f"model={latest[3]} | deload_week={latest[4]} | sessions/week={latest[5]} | goal={latest[6] or ''}"
        )
    else:
        st.caption("No S&C block found for this patient yet.")

    colb1, colb2, colb3, colb4, colb5 = st.columns(5)
    with colb1:
        start_date = st.date_input("Block start (Mon)", value=_default_monday(date.today()), key="sc_block_start")
    with colb2:
        weeks = st.selectbox("Length (weeks)", options=[4, 6, 8], index=1, key="sc_block_weeks")
    with colb3:
        deload_week = st.number_input("Deload week", min_value=1, max_value=int(weeks), value=min(4, int(weeks)), key="sc_block_deload")
    with colb4:
        sessions_per_week = st.selectbox("Sessions/week", options=[1, 2, 3], index=1, key="sc_block_spw")
    with colb5:
        goal = st.selectbox("Goal", options=["hybrid", "endurance", "hypertrophy", "strength", "power"], index=0, key="sc_block_goal")

    block_notes = st.text_area("Block notes (optional)", height=80, key="sc_block_notes")

    if st.button("Create new block", key="create_sc_block_btn"):
        block_id = create_sc_block(
            patient_id=pid,
            start_date=start_date.isoformat(),
            goal=goal,
            notes=block_notes.strip() if block_notes else None,
            weeks=int(weeks),
            model="hybrid_v1",
            deload_week=int(deload_week),
            sessions_per_week=int(sessions_per_week),
        )
        _ensure_block_weeks_sessions(block_id, start_date.isoformat(), int(weeks), int(deload_week), int(sessions_per_week))

        # Ensure session templates exist for labels
        labels = ["A", "B", "C"][: max(1, min(int(sessions_per_week), 3))]
        for lab in labels:
            upsert_sc_session_template(block_id, lab, title=f"Session {lab}", notes=None)

        st.success(f"Block created (id={block_id}).")
        st.rerun()

    st.divider()

    # Select which block to work on (for now: latest only)
    active = fetch_latest_sc_block(pid)
    if not active:
        st.info("Create a block to begin S&C programming.")
        st.stop()

    block_id = int(active[0])
    block_start_iso = str(active[1])
    block_weeks = int(active[2])
    block_model = str(active[3])
    block_deload_week = int(active[4])
    block_spw = int(active[5])
    block_goal = active[6] or "hybrid"

    # Load templates into memory
    _load_templates_from_db(block_id)

    # -----------------------------
    # Template editor (Session A/B)
    # -----------------------------
    st.subheader("Session templates (define once; targets generated week-to-week)")

    ex_rows = list_exercises()
    if not ex_rows:
        st.warning("No exercises found. Seed the DB (exercises + norms) first.")
        st.stop()

    ex_name_map = {r[1]: int(r[0]) for r in ex_rows}
    ex_names = sorted(list(ex_name_map.keys()))

    labels = ["A", "B", "C"][: max(1, min(int(block_spw), 3))]
    template_label = st.selectbox("Select template session", options=labels, key="sc_template_label")

    # Current template rows
    t_rows = _get_template_rows(block_id, template_label)

    st.caption("Add rows below. Default progression rules apply when you generate week targets (editable afterwards).")

    cola, colb = st.columns([2, 1])
    with cola:
        ex_sel = st.selectbox("Exercise", options=ex_names, key="sc_add_ex")
    with colb:
        mode = st.selectbox("Mode", options=["reps", "time"], index=0, key="sc_add_mode")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        sets = st.number_input("Sets", min_value=1, max_value=10, value=3, step=1, key="sc_add_sets")
    with col2:
        group_key = st.text_input("Superset group (optional)", value="", key="sc_add_group")
    with col3:
        group_order = st.number_input("Group order (optional)", min_value=0, max_value=9, value=0, step=1, key="sc_add_group_order")
    with col4:
        sort_order = st.number_input("Row order", min_value=1, max_value=50, value=(len(t_rows) + 1), step=1, key="sc_add_sort")

    if mode == "reps":
        colr1, colr2, colr3 = st.columns(3)
        with colr1:
            reps_start = st.number_input("Reps start", min_value=1, max_value=50, value=8, step=1, key="sc_add_reps_start")
        with colr2:
            reps_step = st.number_input("Reps step/week", min_value=0, max_value=10, value=2, step=1, key="sc_add_reps_step")
        with colr3:
            reps_cap = st.number_input("Reps cap (0 = none)", min_value=0, max_value=100, value=12, step=1, key="sc_add_reps_cap")
        time_start_sec = None
        time_step_sec = 10
        time_cap_sec = None
    else:
        colt1, colt2, colt3 = st.columns(3)
        with colt1:
            time_start_sec = st.number_input("Time start (sec)", min_value=5, max_value=600, value=30, step=5, key="sc_add_time_start")
        with colt2:
            time_step_sec = st.number_input("Time step/week (sec)", min_value=0, max_value=120, value=10, step=5, key="sc_add_time_step")
        with colt3:
            time_cap_sec = st.number_input("Time cap (sec, 0 = none)", min_value=0, max_value=2000, value=60, step=10, key="sc_add_time_cap")
        reps_start = None
        reps_step = 2
        reps_cap = None

    colp1, colp2, colp3, colp4 = st.columns(4)
    with colp1:
        pct_start = st.number_input("%1RM start (0 = none)", min_value=0.0, max_value=1.0, value=0.0, step=0.05, key="sc_add_pct_start")
    with colp2:
        pct_step = st.number_input("%1RM step/week", min_value=0.0, max_value=0.20, value=0.00, step=0.01, key="sc_add_pct_step")
    with colp3:
        pct_cap = st.number_input("%1RM cap (0 = none)", min_value=0.0, max_value=1.0, value=0.0, step=0.05, key="sc_add_pct_cap")
    with colp4:
        load_inc = st.number_input("Load increment (kg)", min_value=0.5, max_value=20.0, value=2.5, step=0.5, key="sc_add_load_inc")

    colq1, colq2, colq3 = st.columns(3)
    with colq1:
        rpe_target = st.number_input("RPE target (0 = none)", min_value=0, max_value=10, value=7, step=1, key="sc_add_rpe")
    with colq2:
        rest_sec = st.number_input("Rest (sec, 0 = none)", min_value=0, max_value=600, value=90, step=15, key="sc_add_rest")
    with colq3:
        intent = st.text_input("Intent (optional)", value="", key="sc_add_intent")

    row_notes = st.text_input("Row notes (optional)", value="", key="sc_add_notes")

    if st.button("Add row to template", key="sc_add_row_btn"):
        ex_id = ex_name_map[ex_sel]
        t_rows.append({
            "sort_order": int(sort_order),
            "group_key": group_key.strip() if group_key else None,
            "group_order": int(group_order) if group_order and int(group_order) > 0 else None,
            "exercise_id": int(ex_id),
            "exercise_name": ex_sel,
            "mode": mode,
            "sets": int(sets),
            "reps_start": None if reps_start is None else int(reps_start),
            "reps_step": int(reps_step),
            "reps_cap": None if (mode == "reps" and int(reps_cap) == 0) else (int(reps_cap) if mode == "reps" else None),
            "time_start_sec": None if time_start_sec is None else int(time_start_sec),
            "time_step_sec": int(time_step_sec),
            "time_cap_sec": None if (mode == "time" and int(time_cap_sec or 0) == 0) else (int(time_cap_sec) if mode == "time" else None),
            "pct_1rm_start": None if float(pct_start) == 0.0 else float(pct_start),
            "pct_1rm_step": float(pct_step),
            "pct_1rm_cap": None if float(pct_cap) == 0.0 else float(pct_cap),
            "load_increment_kg": float(load_inc),
            "rpe_target": None if int(rpe_target) == 0 else int(rpe_target),
            "rest_sec": None if int(rest_sec) == 0 else int(rest_sec),
            "intent": intent.strip() if intent else None,
            "notes": row_notes.strip() if row_notes else None,
        })
        t_rows = sorted(t_rows, key=lambda x: x["sort_order"])
        _set_template_rows(block_id, template_label, t_rows)
        st.success("Row added to template (in-memory). Click 'Save template to DB' to persist.")
        st.rerun()

    if t_rows:
        st.write("Current template rows:")
        t_df = pd.DataFrame([{
            "order": r["sort_order"],
            "group": r["group_key"],
            "g_order": r["group_order"],
            "exercise": r["exercise_name"],
            "mode": r["mode"],
            "sets": r["sets"],
            "reps_start": r["reps_start"],
            "reps_step": r["reps_step"],
            "reps_cap": r["reps_cap"],
            "time_start": r["time_start_sec"],
            "time_step": r["time_step_sec"],
            "time_cap": r["time_cap_sec"],
            "pct_start": r["pct_1rm_start"],
            "pct_step": r["pct_1rm_step"],
            "pct_cap": r["pct_1rm_cap"],
            "load_inc": r["load_increment_kg"],
            "rpe": r["rpe_target"],
            "rest": r["rest_sec"],
            "intent": r["intent"],
            "notes": r["notes"],
        } for r in t_rows])
        st.dataframe(t_df, use_container_width=True)

        colsave, colclear = st.columns(2)
        with colsave:
            if st.button("Save template to DB", key="sc_save_template_db_btn"):
                template_id = upsert_sc_session_template(block_id, template_label, title=f"Session {template_label}", notes=None)
                clear_sc_template_exercises(template_id)
                for r in t_rows:
                    add_sc_template_exercise(
                        template_id=template_id,
                        exercise_id=int(r["exercise_id"]),
                        sort_order=int(r["sort_order"]),
                        group_key=r.get("group_key"),
                        group_order=r.get("group_order"),
                        mode=r.get("mode") or "reps",
                        sets=int(r.get("sets") or 3),
                        reps_start=r.get("reps_start"),
                        reps_step=int(r.get("reps_step") or 2),
                        reps_cap=r.get("reps_cap"),
                        time_start_sec=r.get("time_start_sec"),
                        time_step_sec=int(r.get("time_step_sec") or 10),
                        time_cap_sec=r.get("time_cap_sec"),
                        pct_1rm_start=r.get("pct_1rm_start"),
                        pct_1rm_step=float(r.get("pct_1rm_step") or 0.0),
                        pct_1rm_cap=r.get("pct_1rm_cap"),
                        load_increment_kg=float(r.get("load_increment_kg") or 2.5),
                        rpe_target=r.get("rpe_target"),
                        rest_sec=r.get("rest_sec"),
                        intent=r.get("intent"),
                        notes=r.get("notes"),
                    )
                st.success("Template saved to DB.")
                st.rerun()

        with colclear:
            if st.button("Clear template (in-memory)", key="sc_clear_template_mem_btn"):
                _set_template_rows(block_id, template_label, [])
                st.success("Template cleared in memory. (DB unchanged until you save.)")
                st.rerun()
    else:
        st.info("No rows in this template yet. Add at least one exercise row.")

    st.divider()

    # -----------------------------
    # Generate week targets (auto-suggest) from templates
    # -----------------------------
    st.subheader("Generate week targets (auto-suggest linear progressions)")

    st.caption(
        "This creates planned targets for each week based on your template rows. "
        "You can edit planned targets later in the table and track actuals."
    )

    if st.button("Generate / refresh targets for this block", key="sc_generate_targets_btn"):
        # Ensure templates exist in DB before generating
        templates = list_sc_session_templates(block_id)
        if not templates:
            st.error("No templates found for this block. Save at least one session template to DB first.")
            st.stop()

        # Compute e1RM per exercise as needed (cached per exercise_id)
        e1rm_cache: Dict[int, Optional[float]] = {}

        for tid, sess_label, title, notes in templates:
            rows = list_sc_template_exercises(tid)
            for row in rows:
                (
                    te_id, sort_order, group_key, group_order,
                    exercise_id, ex_name, implement,
                    mode, sets,
                    reps_start, reps_step, reps_cap,
                    time_start_sec, time_step_sec, time_cap_sec,
                    pct_1rm_start, pct_1rm_step, pct_1rm_cap,
                    load_increment_kg,
                    rpe_target, rest_sec, intent, te_notes
                ) = row

                # e1RM only required if pct_1rm_start is set and metric supports it
                e1rm_val: Optional[float] = None
                if pct_1rm_start is not None and bw_use and bw_use > 0:
                    if exercise_id not in e1rm_cache:
                        metric = _metric_for_exercise_name(ex_name)
                        if metric == "pullup_reps":
                            e1rm_cache[exercise_id] = None
                        else:
                            est = estimate_e1rm_kg_for_exercise(
                                patient_sex=sex,
                                patient_age=int(age_years),
                                patient_bw_kg=bw_use,
                                presumed_level=presumed_level,
                                exercise_id=int(exercise_id),
                                metric="rel_1rm_bw",
                            )
                            e1rm_cache[exercise_id] = est.get("estimated_1rm_kg")
                    e1rm_val = e1rm_cache.get(exercise_id)

                targets = generate_sc_targets_for_template_row(
                    weeks=int(block_weeks),
                    deload_week=int(block_deload_week),
                    implement=implement,
                    mode=mode,
                    sets=int(sets),
                    reps_start=None if reps_start is None else int(reps_start),
                    reps_step=int(reps_step or 2),
                    reps_cap=None if reps_cap is None else int(reps_cap),
                    time_start_sec=None if time_start_sec is None else int(time_start_sec),
                    time_step_sec=int(time_step_sec or 10),
                    time_cap_sec=None if time_cap_sec is None else int(time_cap_sec),
                    pct_1rm_start=None if pct_1rm_start is None else float(pct_1rm_start),
                    pct_1rm_step=float(pct_1rm_step or 0.0),
                    pct_1rm_cap=None if pct_1rm_cap is None else float(pct_1rm_cap),
                    load_increment_kg=float(load_increment_kg or 2.5),
                    e1rm_kg=e1rm_val,
                    rpe_target=None if rpe_target is None else int(rpe_target),
                    rest_sec=None if rest_sec is None else int(rest_sec),
                    intent=intent,
                )

                for t in targets:
                    upsert_sc_week_target(
                        template_exercise_id=int(te_id),
                        week_no=int(t["week_no"]),
                        sets=int(t["sets"]),
                        reps=None if t["reps"] is None else int(t["reps"]),
                        time_sec=None if t["time_sec"] is None else int(t["time_sec"]),
                        pct_1rm=None if t["pct_1rm"] is None else float(t["pct_1rm"]),
                        load_kg=None if t["load_kg"] is None else float(t["load_kg"]),
                        rpe_target=None if t["rpe_target"] is None else int(t["rpe_target"]),
                        rest_sec=None if t["rest_sec"] is None else int(t["rest_sec"]),
                        intent=t["intent"],
                        notes=t.get("notes"),
                    )

        st.success("Targets generated/updated for this block.")
        st.rerun()

    st.divider()

    # -----------------------------
    # View / edit targets + actual tracking
    # -----------------------------
    st.subheader("Block table (planned targets + actual tracking)")

    rows = fetch_sc_week_targets_for_block(block_id)
    if not rows:
        st.info("No week targets exist yet. Save templates, then click 'Generate / refresh targets for this block'.")
        st.stop()

    df = pd.DataFrame(rows, columns=[
        "block_id",
        "session_label",
        "template_id",
        "template_exercise_id",
        "sort_order",
        "group_key",
        "group_order",
        "exercise_name",
        "implement",
        "mode",
        "week_no",
        "sets",
        "reps",
        "time_sec",
        "pct_1rm",
        "load_kg",
        "rpe_target",
        "rest_sec",
        "intent",
        "notes",
        "actual_sets",
        "actual_reps",
        "actual_time_sec",
        "actual_load_kg",
        "completed_flag",
    ])

    # Display-friendly columns
    df_view = df.copy()
    df_view["completed_flag"] = df_view["completed_flag"].astype(int)

    # Make a wide-ish but editable view
    display_cols = [
        "session_label", "week_no", "sort_order", "group_key", "group_order",
        "exercise_name", "implement", "mode",
        "sets", "reps", "time_sec", "pct_1rm", "load_kg", "rpe_target", "rest_sec", "intent", "notes",
        "actual_sets", "actual_reps", "actual_time_sec", "actual_load_kg", "completed_flag",
        "template_exercise_id",
    ]

    st.caption(
        "Edit planned targets and/or actuals. "
        "Planned fields update the target row; actual fields update actual tracking."
    )

    edited = st.data_editor(
        df_view[display_cols],
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "completed_flag": st.column_config.CheckboxColumn("Completed", help="Tick when completed", default=False),
            "pct_1rm": st.column_config.NumberColumn("%1RM", format="%.2f"),
            "load_kg": st.column_config.NumberColumn("Load (kg)", format="%.1f"),
            "actual_load_kg": st.column_config.NumberColumn("Actual load (kg)", format="%.1f"),
            "template_exercise_id": st.column_config.NumberColumn("Row ID", disabled=True),
        },
        disabled=[
            "session_label", "week_no", "sort_order", "group_key", "group_order", "exercise_name", "implement", "mode",
            "template_exercise_id",
        ],
        key="sc_block_editor",
    )

    if st.button("Save edits (planned + actual)", key="sc_save_edits_btn"):
        # Merge edited values back with identifiers for updates
        merged = df.merge(
            edited[["template_exercise_id", "sets", "reps", "time_sec", "pct_1rm", "load_kg", "rpe_target", "rest_sec", "intent", "notes",
                    "actual_sets", "actual_reps", "actual_time_sec", "actual_load_kg", "completed_flag"]],
            on="template_exercise_id",
            suffixes=("", "_new"),
        )

        # For each row, update planned and actuals
        for _, r in merged.iterrows():
            te_id = int(r["template_exercise_id"])
            wk = int(r["week_no"])

            upsert_sc_week_target(
                template_exercise_id=te_id,
                week_no=wk,
                sets=int(r["sets_new"]) if pd.notna(r["sets_new"]) else int(r["sets"]),
                reps=None if pd.isna(r["reps_new"]) else int(r["reps_new"]),
                time_sec=None if pd.isna(r["time_sec_new"]) else int(r["time_sec_new"]),
                pct_1rm=None if pd.isna(r["pct_1rm_new"]) else float(r["pct_1rm_new"]),
                load_kg=None if pd.isna(r["load_kg_new"]) else float(r["load_kg_new"]),
                rpe_target=None if pd.isna(r["rpe_target_new"]) else int(r["rpe_target_new"]),
                rest_sec=None if pd.isna(r["rest_sec_new"]) else int(r["rest_sec_new"]),
                intent=None if pd.isna(r["intent_new"]) else str(r["intent_new"]),
                notes=None if pd.isna(r["notes_new"]) else str(r["notes_new"]),
            )

            set_sc_week_actuals(
                template_exercise_id=te_id,
                week_no=wk,
                actual_sets=None if pd.isna(r["actual_sets_new"]) else int(r["actual_sets_new"]),
                actual_reps=None if pd.isna(r["actual_reps_new"]) else int(r["actual_reps_new"]),
                actual_time_sec=None if pd.isna(r["actual_time_sec_new"]) else int(r["actual_time_sec_new"]),
                actual_load_kg=None if pd.isna(r["actual_load_kg_new"]) else float(r["actual_load_kg_new"]),
                completed_flag=bool(int(r["completed_flag_new"])) if pd.notna(r["completed_flag_new"]) else bool(int(r["completed_flag"])),
            )

        st.success("Saved planned + actual updates.")
        st.rerun()

    st.divider()

    # -----------------------------
    # Optional: quick summary view by week/session
    # -----------------------------
    st.subheader("Completion summary")
    df_sum = df.copy()
    df_sum["completed_flag"] = df_sum["completed_flag"].astype(int)
    summary = (
        df_sum.groupby(["week_no", "session_label"], as_index=False)
        .agg(rows=("template_exercise_id", "count"), completed=("completed_flag", "sum"))
        .sort_values(["week_no", "session_label"])
    )
    summary["completion_rate"] = summary["completed"] / summary["rows"]
    st.dataframe(summary, use_container_width=True)
