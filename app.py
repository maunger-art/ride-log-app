import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timedelta
from typing import Optional

from db_store import (
    init_db,
    upsert_patient, list_patients,
    add_ride, fetch_rides,
    upsert_week_plan, fetch_week_plans,
    save_strava_tokens, get_strava_tokens, is_activity_synced, mark_activity_synced,
    upsert_patient_profile, get_patient_profile,
    list_exercises, list_rep_schemes, count_norm_rows,
    estimate_e1rm_kg_for_exercise, estimate_unilateral_from_bilateral,
    upsert_strength_estimate, get_strength_estimate,

    # S&C programming (current architecture)
    create_sc_block,
    upsert_sc_week,
    upsert_sc_session,
    clear_sc_session_exercises,
    add_sc_session_exercise,
    fetch_latest_sc_block,
    fetch_sc_block_detail,
    update_sc_session_exercise_actual,  # not implemented yet
 )

from plan import parse_plan_csv, rides_to_weekly_summary, to_monday
from strava import build_auth_url, exchange_code_for_token, ensure_fresh_token, list_activities


# Optional: seed DB via sidebar button (safe to omit if file not present)
try:
    from seed_strength_standards import seed as seed_strength_db
except Exception:
    seed_strength_db = None


# -----------------------------
# Helpers
# -----------------------------
def _metric_for_exercise_name(name: str) -> str:
    n = (name or "").lower()
    if n.startswith("pull-up") or n.startswith("pullup"):
        return "pullup_reps"
    return "rel_1rm_bw"


def _age_from_dob_or_manual(dob_str: str, manual_age: int) -> int:
    if dob_str and dob_str.strip():
        try:
            dob_dt = datetime.strptime(dob_str.strip(), "%Y-%m-%d").date()
            today = date.today()
            return today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))
        except Exception:
            return int(manual_age)
    return int(manual_age)


def _week_start_from_date(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _session_labels(n: int):
    return ["A", "B", "C"][:max(1, min(3, n))]


# -----------------------------
# Streamlit setup
# -----------------------------
st.set_page_config(page_title="Ride Log – Plan vs Actual", layout="wide")
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
    st.dataframe(rides_df, width="stretch")


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

                    ride_date_str = a["start_date_local"][:10]
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

        st.dataframe(merged, width="stretch")


# -------------------------------------------------------------------
# TAB 3: Plan Import / Edit
# -------------------------------------------------------------------
with tab3:
    st.subheader("Plan import (CSV)")
    st.write("Upload CSV columns: week_start (Monday, YYYY-MM-DD), planned_km, planned_hours, phase, notes.")

    uploaded = st.file_uploader("Upload plan CSV", type=["csv"], key="plan_csv_uploader")
    if uploaded is not None:
        try:
            df = parse_plan_csv(uploaded)
            st.success(f"Loaded {len(df)} plan rows.")
            st.dataframe(df, width="stretch")

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
# TAB 4: S&C Planning
# -------------------------------------------------------------------
with tab4:
    st.subheader("S&C Planning")

    st.caption(f"Strength standards rows: {count_norm_rows()}")
    if count_norm_rows() == 0:
        st.warning(
            "Strength standards are not seeded yet. "
            "Add seed_strength_standards.py to repo root and run 'Seed strength DB' from sidebar."
        )

    st.divider()

    # -----------------------------
    # Patient profile
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
            key="profile_sex",
        )
    with colp2:
        dob = st.text_input("DOB (YYYY-MM-DD) – optional", value=dob_default or "", key="profile_dob")
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

    # -----------------------------
    # Age handling
    # -----------------------------
    st.subheader("Age (for selecting the correct norm band)")
    age_manual = st.number_input("Age (years) – used if DOB blank/invalid", min_value=18, max_value=65, value=35)
    age_years = _age_from_dob_or_manual(dob, int(age_manual))
    bw_use = float(bodyweight_kg) if (bodyweight_kg and bodyweight_kg > 0) else None

    st.caption(f"Age used for norms: {age_years} years | Level used: {presumed_level}")

    st.divider()

    # =========================================================
    # 1RM Predictor (RESTORED) + Save estimate + Unilateral preview
    # =========================================================
    st.subheader("1RM predictor (auto)")

    exercises = list_exercises()
    if not exercises:
        st.warning("No exercises found. Seed the DB first.")
        st.stop()

    ex_name_map = {row[1]: row[0] for row in exercises}
    ex_names = sorted(list(ex_name_map.keys()))

    selected_ex_pred = st.selectbox("Select exercise for e1RM estimate", options=ex_names, key="e1rm_ex_select")
    ex_id_pred = ex_name_map[selected_ex_pred]
    metric_pred = _metric_for_exercise_name(selected_ex_pred)

    if metric_pred == "pullup_reps":
        st.info("Pull-ups are prescribed via reps/sets and intent (no 1RM estimate).")
    else:
        if bw_use is None:
            st.warning("Enter bodyweight to enable e1RM estimation.")
        else:
            est = estimate_e1rm_kg_for_exercise(
                patient_sex=sex,
                patient_age=int(age_years),
                patient_bw_kg=bw_use,
                presumed_level=presumed_level,
                exercise_id=int(ex_id_pred),
                metric="rel_1rm_bw",
            )

            if est.get("estimated_1rm_kg") is None:
                st.warning(est.get("notes") or "Could not estimate e1RM.")
            else:
                st.metric("Estimated 1RM (auto)", f"{float(est['estimated_1rm_kg']):.1f} kg")
                st.caption(
                    f"Rel strength used: {float(est['estimated_rel_1rm_bw']):.2f} × BW | "
                    f"Age band: {est.get('band_used')} | Method: {est.get('method')}"
                )

                col_est1, col_est2 = st.columns(2)
                with col_est1:
                    if st.button("Save estimate", key="save_estimate_btn_tab4"):
                        upsert_strength_estimate(
                            patient_id=pid,
                            exercise_id=int(ex_id_pred),
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

                with col_est2:
                    saved = get_strength_estimate(pid, int(ex_id_pred))
                    if saved is not None:
                        as_of_date, e1rm_kg, rel_bw, *_ = saved
                        if e1rm_kg is not None:
                            st.caption(f"Saved: {as_of_date} | {float(e1rm_kg):.1f} kg ({float(rel_bw):.2f}×BW)")

    st.divider()
    st.subheader("Unilateral anchor preview (Option A)")

    uni_choice = st.selectbox(
        "Unilateral movement to preview",
        options=["(none)", "Bulgarian Split Squat (from Squat)", "Step-Up (from Squat)", "Single-Leg RDL (from Deadlift)"],
        key="uni_preview_choice",
    )

    def _get_parent_e1rm(parent_name: str) -> Optional[float]:
        parent_id = ex_name_map.get(parent_name)
        if not parent_id or bw_use is None:
            return None
        parent_est = estimate_e1rm_kg_for_exercise(
            patient_sex=sex,
            patient_age=int(age_years),
            patient_bw_kg=bw_use,
            presumed_level=presumed_level,
            exercise_id=int(parent_id),
            metric="rel_1rm_bw",
        )
        return parent_est.get("estimated_1rm_kg")

    if uni_choice != "(none)":
        if bw_use is None:
            st.warning("Enter bodyweight to preview unilateral estimates.")
        else:
            if "Bulgarian" in uni_choice or "Step-Up" in uni_choice:
                parent_e1rm = _get_parent_e1rm("Back Squat")
                movement_key = "bss" if "Bulgarian" in uni_choice else "stepup"
                if parent_e1rm is None:
                    st.warning("Could not estimate parent lift (Back Squat). Ensure it exists in Exercises.")
                else:
                    uni_e1rm_eq = estimate_unilateral_from_bilateral(parent_e1rm, movement_key, presumed_level)
                    st.metric("Unilateral e1RM-equivalent (per leg)", f"{uni_e1rm_eq:.1f} kg")
            else:
                parent_e1rm = _get_parent_e1rm("Deadlift")
                if parent_e1rm is None:
                    st.warning("Could not estimate parent lift (Deadlift). Ensure it exists in Exercises.")
                else:
                    uni_e1rm_eq = estimate_unilateral_from_bilateral(parent_e1rm, "sl_rdl", presumed_level)
                    st.metric("Unilateral e1RM-equivalent (per leg)", f"{uni_e1rm_eq:.1f} kg")

    st.divider()

    # =========================================================
    # Rep scheme tool (RESTORED)
    # =========================================================
    st.subheader("Rep scheme tool (endurance / hypertrophy / strength / power)")

    goal_tool = st.selectbox("Adaptation goal", options=["endurance", "hypertrophy", "strength", "power"], index=0, key="rep_scheme_goal_tool")
    schemes = list_rep_schemes(goal_tool)

    if not schemes:
        st.warning("No rep schemes found for this goal. Seed rep schemes first.")
    else:
        s = schemes[0]
        _, s_goal, s_phase, reps_min, reps_max, sets_min, sets_max, pct_min, pct_max, rpe_min, rpe_max, rest_min, rest_max, intent = s

        st.markdown(f"**Scheme:** {s_goal} ({s_phase if s_phase else 'default'})")
        st.write(f"- Sets: **{sets_min}–{sets_max}**")
        st.write(f"- Reps: **{reps_min}–{reps_max}**")
        if pct_min is not None and pct_max is not None:
            st.write(f"- %1RM: **{int(pct_min*100)}–{int(pct_max*100)}%**")
        else:
            st.write("- %1RM: **n/a**")
        if rest_min and rest_max:
            st.write(f"- Rest: **{rest_min}–{rest_max} sec**")
        if intent:
            st.write(f"- Intent: **{intent}**")
        if rpe_min and rpe_max:
            st.write(f"- RPE target: **{rpe_min}–{rpe_max}**")

        if metric_pred == "pullup_reps":
            st.info("Pull-ups: prescribe via reps/sets + intent (no %1RM load range).")
        else:
            if bw_use is None:
                st.info("Enter bodyweight to compute a working-load range from %1RM.")
            else:
                est2 = estimate_e1rm_kg_for_exercise(
                    patient_sex=sex,
                    patient_age=int(age_years),
                    patient_bw_kg=bw_use,
                    presumed_level=presumed_level,
                    exercise_id=int(ex_id_pred),
                    metric="rel_1rm_bw",
                )
                e1rm_val = est2.get("estimated_1rm_kg")
                if e1rm_val is None or pct_min is None or pct_max is None:
                    st.info("Missing e1RM estimate or %1RM range. Ensure BW is set and norms exist.")
                else:
                    cap_max = 0.70 if goal_tool == "strength" else 0.75
                    pct_min_safe = float(pct_min)
                    pct_max_safe = min(float(pct_max), cap_max)
                    w_min = float(e1rm_val) * pct_min_safe
                    w_max = float(e1rm_val) * pct_max_safe
                    st.info(f"Suggested working load range (capped): **{w_min:.1f}–{w_max:.1f} kg**")

    st.divider()

    # =========================================================
    # S&C Blocks (new engine): selectable length + hybrid suggestions + actual tracking
    # =========================================================
    st.subheader("Create / manage S&C blocks")

    latest = fetch_latest_sc_block(pid)
    if latest:
        st.caption(f"Latest block: id={latest[0]} | start={latest[1]} | weeks={latest[2]} | deload_week={latest[4]} | sessions/wk={latest[5]} | goal={latest[6]}")

    colb1, colb2, colb3, colb4 = st.columns(4)
    with colb1:
        block_start = st.date_input("Block start (Mon recommended)", value=_week_start_from_date(date.today()), key="block_start")
    with colb2:
        block_weeks = st.selectbox("Block length (weeks)", options=[4, 6, 8], index=1, key="block_weeks")
    with colb3:
        deload_week = st.selectbox("Deload week", options=list(range(1, block_weeks + 1)), index=min(3, block_weeks - 1), key="deload_week")
    with colb4:
        sessions_per_week = st.selectbox("Sessions per week", options=[1, 2, 3], index=1, key="sessions_per_week")

    block_goal = st.selectbox("Block goal", options=["hybrid", "endurance", "hypertrophy", "strength", "power"], index=0, key="block_goal")
    block_notes = st.text_area("Block notes", height=80, key="block_notes")

    st.caption(
        "Default progression rules:\n"
        "- Isometrics/bodyweight: increase time/reps linearly\n"
        "- DB/KB: increase reps first, then load\n"
        "- Barbell: increase load within rep range\n"
        "All targets are editable after generation."
    )

    # Template: Mark Xmas Session 1 (2 sessions/week A+B)
    # We keep it neutral: clinician can edit afterwards.
    template_A = [
        ("Bike Erg (High Seat)", "conditioning", "minutes", {"sets": 1, "reps": 5}),
        ("Wall Sit", "iso", "seconds", {"sets": 3, "reps": 40}),
        ("Isometric Single-Leg Hamstring Bridge", "iso", "seconds", {"sets": 3, "reps": 30}),
        ("Bike Erg (High Seat)", "conditioning", "minutes", {"sets": 1, "reps": 3}),
        ("Isometric Split Squat", "kb_iso", "seconds", {"sets": 3, "reps": 30, "load_kg": 5.0}),
        ("Side Plank", "iso", "seconds", {"sets": 3, "reps": 30}),
    ]
    template_B = [
        ("Bike Erg (High Seat)", "conditioning", "minutes", {"sets": 1, "reps": 3}),
        ("Hip Abduction (Band, Seated)", "band", "reps", {"sets": 3, "reps": 10}),
        ("Single-Leg RDL", "db", "reps", {"sets": 3, "reps": 10, "load_kg": 5.0}),
        ("Bike Erg (High Seat)", "conditioning", "minutes", {"sets": 1, "reps": 10}),
    ]

    # Map exercise name to id
    name_to_id = {r[1]: r[0] for r in exercises}

    def _progress_value(base: float, week_no: int, deload: bool, kind: str):
        """
        Linear suggestion:
          - deload: drop ~20%
          - otherwise: +5% per week for timed/reps; for loads small step
        """
        if deload:
            return base * 0.80

        if kind in ["seconds", "minutes", "reps"]:
            # +5% per week (from week1 baseline)
            return base * (1.0 + 0.05 * max(0, week_no - 1))

        # load
        return base * (1.0 + 0.025 * max(0, week_no - 1))

    def _round_load(x: float) -> float:
        # simple 0.5kg rounding for DB/KB
        return round(x * 2.0) / 2.0

    if st.button("Generate new block", key="gen_block_btn"):
        # Create block
        block_id = create_sc_block(
            patient_id=pid,
            start_date=block_start.isoformat(),
            goal=block_goal,
            notes=block_notes.strip() if block_notes else None,
            weeks=int(block_weeks),
            model="hybrid_v1",
            deload_week=int(deload_week),
            sessions_per_week=int(sessions_per_week),
        )

        # Create weeks + sessions + planned exercises
        for w in range(1, int(block_weeks) + 1):
            wk_start = (block_start + timedelta(days=7 * (w - 1))).isoformat()
            is_deload = (w == int(deload_week))

            # Focus label (simple)
            focus = "deload" if is_deload else ("capacity" if w <= 2 else "hybrid")

            week_id = upsert_sc_week(block_id, w, wk_start, focus, is_deload, None)

            labels = _session_labels(int(sessions_per_week))
            for lab in labels:
                sid = upsert_sc_session(week_id, lab, None, None)
                clear_sc_session_exercises(sid)

                # Choose template per label
                tmpl = template_A if lab == "A" else template_B if lab == "B" else template_A

                for ex_name, ex_type, unit, base in tmpl:
                    ex_id = name_to_id.get(ex_name)
                    if not ex_id:
                        # skip if not in DB
                        continue

                    sets = int(base.get("sets", 3))
                    base_reps = float(base.get("reps", 10))
                    reps_suggest = int(round(_progress_value(base_reps, w, is_deload, unit)))

                    # Load logic
                    load = base.get("load_kg")
                    if load is not None:
                        load_suggest = _round_load(_progress_value(float(load), w, is_deload, "load"))
                    else:
                        load_suggest = None

                    # pct_1rm: only for barbell/DB strength templates; this block is mostly iso + light DB
                    pct_1rm = None

                    add_sc_session_exercise(
                        session_id=sid,
                        exercise_id=int(ex_id),
                        sets=sets,
                        reps=reps_suggest,
                        pct_1rm=pct_1rm,
                        load_kg=load_suggest,
                        rpe_target=None,
                        rest_sec=None,
                        intent="Controlled; stop if form breaks" if not is_deload else "Easy / recovery intent",
                        notes=f"Unit={unit}",
                    )

        st.success(f"Block generated (id={block_id}).")
        st.rerun()

    st.divider()
    st.subheader("View / edit latest block (planned + actual)")

    latest = fetch_latest_sc_block(pid)
    if not latest:
        st.info("No S&C blocks yet. Generate one above.")
        st.stop()

    block_id = int(latest[0])
    detail = fetch_sc_block_detail(block_id)

    if not detail:
        st.info("Block exists but has no sessions. Re-generate or check DB.")
        st.stop()

    # Render each session with editable planned + actual fields
    for (week_id, week_no, week_start, focus, deload_flag, session_id, label, day_hint, exs) in detail:
        with st.expander(f"Week {week_no} ({week_start}) | {focus} | Session {label}", expanded=(week_no == 1)):
            if not exs:
                st.caption("No exercises in this session.")
                continue

            for row in exs:
                (
                    row_id, ex_name,
                    sets, reps, pct_1rm, load_kg, rpe_t, rest_s, intent, notes,
                    a_sets, a_reps, a_load, done, a_notes
                ) = row

                st.markdown(f"**{ex_name}**")
                c1, c2, c3, c4, c5 = st.columns([1.1, 1.1, 1.2, 1.2, 2.2])

                with c1:
                    st.caption("Planned")
                    st.write(f"{sets} x {reps}")
                    if load_kg is not None:
                        st.write(f"Load: {float(load_kg):.1f} kg")
                    if pct_1rm is not None:
                        st.write(f"%1RM: {int(float(pct_1rm)*100)}%")

                with c2:
                    completed_flag = st.checkbox("Done", value=bool(done), key=f"done_{row_id}")

                with c3:
                    actual_sets = st.number_input("Actual sets", min_value=0, step=1, value=int(a_sets) if a_sets is not None else 0, key=f"aset_{row_id}")
                    actual_reps = st.number_input("Actual reps/time", min_value=0, step=1, value=int(a_reps) if a_reps is not None else 0, key=f"areps_{row_id}")

                with c4:
                    actual_load = st.number_input(
                        "Actual load (kg)",
                        min_value=0.0, step=0.5,
                        value=float(a_load) if a_load is not None else 0.0,
                        key=f"aload_{row_id}"
                    )

                with c5:
                    actual_notes = st.text_input("Actual notes", value=a_notes or "", key=f"anote_{row_id}")

                if st.button("Save actual", key=f"save_actual_{row_id}"):
                    update_sc_session_exercise_actual(
                        row_id=int(row_id),
                        actual_sets=(int(actual_sets) if actual_sets > 0 else None),
                        actual_reps=(int(actual_reps) if actual_reps > 0 else None),
                        actual_load_kg=(float(actual_load) if actual_load > 0 else None),
                        completed_flag=bool(completed_flag),
                        actual_notes=actual_notes.strip() if actual_notes else None,
                    )
                    st.success("Saved actual.")
                    st.rerun()
