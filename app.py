import streamlit as st
import pandas as pd
import time
from datetime import date, datetime
from datetime import timedelta
from typing import Optional

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

    # strength estimation
    estimate_e1rm_kg_for_exercise,
    estimate_unilateral_from_bilateral,
    upsert_strength_estimate,
    get_strength_estimate,
)

from plan import parse_plan_csv, rides_to_weekly_summary, to_monday
from strava import build_auth_url, exchange_code_for_token, ensure_fresh_token, list_activities

# Optional: seed strength DB via sidebar button (safe to omit if file not present)
try:
    from seed_strength_standards import seed as seed_strength_db
except Exception:
    seed_strength_db = None

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

    # Handle OAuth callback (Strava redirects back with ?code=...&state=...)
    qp = st.query_params
    if "code" in qp and "state" in qp:
        # Bind the connection to the selected patient using state=patient_id
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

    # IMPORTANT: must be outside callback block
    token_row = get_strava_tokens(pid)

    if token_row is None:
        st.link_button("Connect Strava", build_auth_url(state=str(pid)))
        st.caption("Connect Strava to automatically import rides into the log.")
    else:
        access_token, refresh_token, expires_at, athlete_id, scope, refreshed = ensure_fresh_token(token_row)

        if refreshed:
            save_strava_tokens(pid, access_token, refresh_token, expires_at, athlete_id, str(scope))

        days_back = st.number_input(
            "Sync how many days back?",
            min_value=1,
            max_value=365,
            value=30
        )

        if st.button("Sync Strava rides"):
            after_epoch = int(time.time() - int(days_back) * 86400)
            imported = 0
            page = 1

            while True:
                acts = list_activities(
                    access_token,
                    after_epoch=after_epoch,
                    per_page=50,
                    page=page
                )

                if not acts:
                    break

                for a in acts:
                    sport = a.get("sport_type") or a.get("type")
                    if sport not in [
                        "Ride",
                        "VirtualRide",
                        "EBikeRide",
                        "GravelRide",
                        "MountainBikeRide"
                    ]:
                        continue

                    act_id = int(a["id"])
                    if is_activity_synced(pid, act_id):
                        continue

                    ride_date_str = a["start_date_local"][:10]  # YYYY-MM-DD
                    distance_km_val = float(a.get("distance", 0)) / 1000.0
                    duration_min_val = int(round(float(a.get("elapsed_time", 0)) / 60.0))
                    name = a.get("name", "Strava ride")

                    add_ride(
                        pid,
                        ride_date_str,
                        distance_km_val,
                        duration_min_val,
                        None,
                        f"[Strava] {name}"
                    )

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

    # Pull rides
    rides = fetch_rides(pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date", "distance_km", "duration_min", "rpe", "notes"])

    # Pull plan
    plan_rows = fetch_week_plans(pid)
    plan_df = pd.DataFrame(plan_rows, columns=["week_start", "planned_km", "planned_hours", "phase", "notes"])

    # Normalize plan weeks
    if not plan_df.empty:
        plan_df["week_start"] = pd.to_datetime(plan_df["week_start"], errors="coerce").dt.normalize()

    # Weekly actual
    weekly_actual = rides_to_weekly_summary(rides_df)

    # Normalize actual weeks
    if not weekly_actual.empty:
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"], errors="coerce").dt.normalize()
    else:
        weekly_actual = pd.DataFrame(columns=["week_start", "actual_km", "actual_hours", "rides_count"])
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"])

    # Merge + display
    if plan_df.empty and weekly_actual.empty:
        st.info("No plan or rides yet. Add rides or import a plan on the Plan tab.")
    else:
        if plan_df.empty:
            merged = weekly_actual.copy()
        elif weekly_actual.empty:
            merged = plan_df.copy()
        else:
            merged = pd.merge(plan_df, weekly_actual, on="week_start", how="outer").sort_values("week_start")

        # Fill NA numeric columns
        for c in ["planned_km", "planned_hours", "actual_km", "actual_hours", "rides_count"]:
            if c in merged.columns:
                merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0)

        # Variance
        if "planned_km" in merged.columns and "actual_km" in merged.columns:
            merged["km_variance"] = merged["actual_km"] - merged["planned_km"]
        if "planned_hours" in merged.columns and "actual_hours" in merged.columns:
            merged["hours_variance"] = merged["actual_hours"] - merged["planned_hours"]

        st.dataframe(merged, use_container_width=True)

        st.divider()
        st.subheader("Export for coaching review")
        csv = rides_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download rides CSV",
            data=csv,
            file_name=f"{selected}_rides.csv",
            mime="text/csv"
        )

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
# TAB 4: S&C Planning (MVP)
# -------------------------------------------------------------------

with tab4:
    st.subheader("S&C Planning")

    # ---------------------------------------------------------
    # Norms status
    # ---------------------------------------------------------
    st.caption(f"Strength standards rows: {count_norm_rows()}")
    if count_norm_rows() == 0:
        st.warning(
            "Strength standards are not seeded yet. "
            "Add seed_strength_standards.py to the repo root and run 'Seed strength DB' from the sidebar."
        )
        st.stop()

    st.divider()

    # ---------------------------------------------------------
    # Patient profile (sex / DOB or age / BW / presumed level)
    # ---------------------------------------------------------
    st.subheader("Patient profile (drives auto-estimates)")

    profile = get_patient_profile(pid)
    sex_default = profile[0] if profile else None
    dob_default = profile[1] if profile else ""
    bw_default = profile[2] if profile else None
    level_default = profile[3] if (profile and len(profile) > 3 and profile[3]) else "intermediate"

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
        dob = st.text_input(
            "DOB (YYYY-MM-DD) – optional",
            value=(dob_default if dob_default else ""),
            help="Leave blank if you do not want DOB stored; use Age below instead.",
            key="profile_dob",
        )

    with colp3:
        bodyweight_kg = st.number_input(
            "Bodyweight (kg)",
            min_value=0.0,
            step=0.1,
            value=(float(bw_default) if bw_default is not None else 0.0),
            key="profile_bw",
        )

    with colp4:
        presumed_level = st.selectbox(
            "Presumed strength level",
            options=level_options,
            index=level_options.index(level_default),
            help="Used to estimate starting e1RM from your seeded norms (no 1RM input required).",
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

    # ---------------------------------------------------------
    # Age handling
    # ---------------------------------------------------------
    st.subheader("Age (for selecting the correct norm band)")
    age_manual = st.number_input(
        "Age (years) – used if DOB blank/invalid",
        min_value=18,
        max_value=65,
        value=35,
        key="age_manual_tab4",
    )

    def _age_from_dob_or_manual(dob_str: str, manual_age: int) -> int:
        if dob_str and dob_str.strip():
            try:
                dob_dt = datetime.strptime(dob_str.strip(), "%Y-%m-%d").date()
                today = date.today()
                return today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))
            except Exception:
                return int(manual_age)
        return int(manual_age)

    age_years = _age_from_dob_or_manual(dob, int(age_manual))
    st.caption(f"Age used for norms: {age_years} years | Level used: {presumed_level}")

    st.divider()

    # ---------------------------------------------------------
    # Exercise selection + Auto-estimated e1RM
    # ---------------------------------------------------------
    st.subheader("Auto-estimated e1RM (from norms + level + BW)")

    exercises = list_exercises()
    if not exercises:
        st.warning("No exercises found. Seed the DB (exercises + norms) first.")
        st.stop()

    ex_name_map = {row[1]: row[0] for row in exercises}  # name -> id
    ex_names = sorted(list(ex_name_map.keys()))
    selected_ex = st.selectbox("Select exercise", options=ex_names, key="sc_ex_select")
    ex_id = ex_name_map[selected_ex]

    metric = "pullup_reps" if selected_ex.lower().startswith(("pull-up", "pullup")) else "rel_1rm_bw"
    bw_use = float(bodyweight_kg) if (bodyweight_kg and bodyweight_kg > 0) else None

    est = estimate_e1rm_kg_for_exercise(
        patient_sex=sex,
        patient_age=int(age_years),
        patient_bw_kg=bw_use,
        presumed_level=presumed_level,
        exercise_id=ex_id,
        metric=metric,
    )

    if metric == "pullup_reps":
        st.info("Pull-ups are prescribed via reps/sets and intent (no 1RM estimate).")
    else:
        if est["estimated_1rm_kg"] is None:
            st.warning(est["notes"])
        else:
            st.metric("Estimated 1RM (auto)", f"{est['estimated_1rm_kg']:.1f} kg")
            st.caption(
                f"Rel strength used: {est['estimated_rel_1rm_bw']:.2f} × BW | "
                f"Age band: {est['band_used']} | Method: {est['method']}"
            )

            if st.button("Save estimate", key="save_estimate_btn"):
                upsert_strength_estimate(
                    patient_id=pid,
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

    # ---------------------------------------------------------
    # Unilateral anchor preview (Option A)
    # ---------------------------------------------------------
    st.subheader("Unilateral anchor preview (Option A)")
    st.caption("Unilateral 'e1RM-equivalent' estimated by scaling the parent bilateral lift.")

    uni_choice = st.selectbox(
        "Unilateral movement to preview",
        options=["(none)", "Bulgarian Split Squat (from Squat)", "Step-Up (from Squat)", "Single-Leg RDL (from Deadlift)"],
        key="uni_preview_choice",
    )

    def _get_parent_e1rm(parent_name: str) -> Optional[float]:
        parent_id = ex_name_map.get(parent_name)
        if not parent_id:
            return None
        parent_est = estimate_e1rm_kg_for_exercise(
            patient_sex=sex,
            patient_age=int(age_years),
            patient_bw_kg=bw_use,
            presumed_level=presumed_level,
            exercise_id=parent_id,
            metric="rel_1rm_bw",
        )
        return parent_est.get("estimated_1rm_kg")

    if uni_choice != "(none)":
        if bw_use is None:
            st.warning("Enter bodyweight and save the profile to preview unilateral estimates.")
        else:
            if "Bulgarian" in uni_choice or "Step-Up" in uni_choice:
                parent_e1rm = _get_parent_e1rm("Back Squat")
                movement_key = "bss" if "Bulgarian" in uni_choice else "stepup"
                if parent_e1rm is None:
                    st.warning("Could not estimate parent lift (Back Squat). Ensure it exists in Exercises.")
                else:
                    uni_e1rm_eq = estimate_unilateral_from_bilateral(parent_e1rm, movement_key, presumed_level)
                    st.metric("Unilateral e1RM-equivalent (per leg)", f"{uni_e1rm_eq:.1f} kg")
                    st.caption(f"Derived from Back Squat e1RM {parent_e1rm:.1f} kg using movement={movement_key}.")
            else:
                parent_e1rm = _get_parent_e1rm("Deadlift")
                if parent_e1rm is None:
                    st.warning("Could not estimate parent lift (Deadlift). Ensure it exists in Exercises.")
                else:
                    uni_e1rm_eq = estimate_unilateral_from_bilateral(parent_e1rm, "sl_rdl", presumed_level)
                    st.metric("Unilateral e1RM-equivalent (per leg)", f"{uni_e1rm_eq:.1f} kg")
                    st.caption(f"Derived from Deadlift e1RM {parent_e1rm:.1f} kg using movement=sl_rdl.")

    st.divider()

    # ---------------------------------------------------------
    # Prescription builder from rep schemes (uses auto e1RM)
    # ---------------------------------------------------------
    st.subheader("Prescription builder (from rep schemes)")

    goal = st.selectbox("Adaptation goal", options=["endurance", "hypertrophy", "strength", "power"], index=0, key="sc_goal")
    schemes = list_rep_schemes(goal)

    if not schemes:
        st.warning("No rep schemes found for this goal. Seed rep schemes first.")
        st.stop()

    s = schemes[0]  # MVP: first scheme
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

    if metric == "pullup_reps":
        st.info("Pull-ups: prescribe reps/sets + intent. (No %1RM load range).")
    else:
        if est["estimated_1rm_kg"] is None or pct_min is None or pct_max is None:
            st.info("Missing e1RM estimate or %1RM range. Ensure BW is set and norms exist.")
        else:
            # Conservative cap for early prescribing
            cap_max = 0.70 if goal in ["strength"] else 0.75
            pct_min_safe = float(pct_min)
            pct_max_safe = min(float(pct_max), cap_max)

            w_min = float(est["estimated_1rm_kg"]) * pct_min_safe
            w_max = float(est["estimated_1rm_kg"]) * pct_max_safe
            st.info(f"Suggested working load range (capped): **{w_min:.1f}–{w_max:.1f} kg**")
            st.caption("Cap keeps early prescribing conservative; 6-week blocks will progress loads week-to-week.")

    st.divider()

    # ---------------------------------------------------------
    # Show saved estimate (if exists) for the selected exercise
    # ---------------------------------------------------------
    st.subheader("Saved estimate (if previously stored)")
    saved = get_strength_estimate(pid, ex_id)
    if saved is None:
        st.caption("No saved estimate for this exercise yet.")
    else:
        as_of_date, e1rm_kg, rel_bw, lvl_used, sex_used, age_used, bw_used, method, notes = saved
        st.write(f"- As of: **{as_of_date}**")
        if e1rm_kg is not None:
            st.write(f"- e1RM: **{float(e1rm_kg):.1f} kg** (rel: {float(rel_bw):.2f}×BW)")
        else:
            st.write("- e1RM: **n/a** (exercise uses reps/sets rather than 1RM)")
        st.write(f"- Inputs: sex={sex_used}, age={age_used}, bw={bw_used}, level={lvl_used}")
        st.caption(f"Method: {method} | Notes: {notes}")

    # =========================================================
    # S&C Programming: 6-week blocks (meso) + sessions + exercises
    # =========================================================
    st.divider()
    st.subheader("6-week block generator (Hybrid / Week 4 Deload / 2 sessions)")

    # Helper: Monday
    def _to_monday(d: date) -> date:
        return d - timedelta(days=d.weekday())

    # Build exercise lookup
    ex_id_by_name = {row[1]: row[0] for row in exercises}

    # These must exist in your Exercises table (seed them if missing)
    REQUIRED = [
        "Bike Erg (High Seat)",
        "Wall Sit",
        "Isometric Single-Leg Hamstring Bridge",
        "Isometric Split Squat",
        "Side Plank",
        "Hip Abduction (Band, Seated)",
        "Single-Leg RDL",
    ]

    missing = [n for n in REQUIRED if n not in ex_id_by_name]
    if missing:
        st.warning(
            "Missing exercises required for the template: "
            + ", ".join(missing)
            + ". Add them in seed_strength_standards.py via upsert_exercise, then reseed."
        )

    colg1, colg2, colg3 = st.columns(3)
    with colg1:
        block_start = st.date_input("Block start (Monday recommended)", value=_to_monday(date.today()), key="sc_block_start")
    with colg2:
        sessions_pw = st.selectbox("Sessions / week", options=[1, 2], index=1, key="sc_sessions_pw")
    with colg3:
        deload_week = st.selectbox("Deload week", options=[3, 4, 5], index=1, key="sc_deload_week")

    block_goal = st.selectbox("Block goal", options=["hybrid", "endurance", "hypertrophy", "strength", "power"], index=0, key="sc_block_goal")
    block_notes = st.text_area("Block notes (optional)", height=70, key="sc_block_notes")

    # Simple template writer (store bike work as minutes in reps field)
    def _seed_xmas_template(session_id: int, is_deload: bool) -> None:
        clear_sc_session_exercises(session_id)
        set_factor = 0.6 if is_deload else 1.0

        bike = ex_id_by_name.get("Bike Erg (High Seat)")
        wall = ex_id_by_name.get("Wall Sit")
        ham = ex_id_by_name.get("Isometric Single-Leg Hamstring Bridge")
        split = ex_id_by_name.get("Isometric Split Squat")
        plank = ex_id_by_name.get("Side Plank")
        abd = ex_id_by_name.get("Hip Abduction (Band, Seated)")
        slrdl = ex_id_by_name.get("Single-Leg RDL")

        if not all([bike, wall, ham, split, plank, abd, slrdl]):
            return  # missing required exercises

        # Warm-up / between / cooldown (MVP: reps=minutes)
        add_sc_session_exercise(session_id, bike, sets=1, reps=5, pct_1rm=None, load_kg=None,
                                rpe_target=3 if is_deload else 4, rest_sec=None,
                                intent="Easy spin", notes="Warm-up 5 min")

        # Superset A
        add_sc_session_exercise(session_id, wall, sets=int(round(3 * set_factor)), reps=40, pct_1rm=None, load_kg=None,
                                rpe_target=6 if not is_deload else 5, rest_sec=45, intent="Isometric hold", notes="Seconds")
        add_sc_session_exercise(session_id, ham, sets=int(round(3 * set_factor)), reps=30, pct_1rm=None, load_kg=None,
                                rpe_target=6 if not is_deload else 5, rest_sec=45, intent="Isometric hold", notes="Seconds")

        add_sc_session_exercise(session_id, bike, sets=1, reps=3, pct_1rm=None, load_kg=None,
                                rpe_target=3 if is_deload else 4, rest_sec=None,
                                intent="Easy spin", notes="3 min between sets")

        # Superset B
        add_sc_session_exercise(session_id, split, sets=int(round(3 * set_factor)), reps=30, pct_1rm=None, load_kg=None,
                                rpe_target=7 if not is_deload else 5, rest_sec=60, intent="Isometric hold", notes="Seconds (KB optional)")
        add_sc_session_exercise(session_id, plank, sets=int(round(3 * set_factor)), reps=30, pct_1rm=None, load_kg=None,
                                rpe_target=6 if not is_deload else 5, rest_sec=60, intent="Isometric hold", notes="Seconds")

        add_sc_session_exercise(session_id, bike, sets=1, reps=3, pct_1rm=None, load_kg=None,
                                rpe_target=3 if is_deload else 4, rest_sec=None,
                                intent="Easy spin", notes="3 min between sets")

        # Superset C
        add_sc_session_exercise(session_id, abd, sets=int(round(3 * set_factor)), reps=10, pct_1rm=None, load_kg=None,
                                rpe_target=6 if not is_deload else 5, rest_sec=60, intent="Controlled", notes="Band: medium-firm")
        add_sc_session_exercise(session_id, slrdl, sets=int(round(3 * set_factor)), reps=10, pct_1rm=None, load_kg=None,
                                rpe_target=7 if not is_deload else 5, rest_sec=60, intent="Controlled", notes="DB optional")

        add_sc_session_exercise(session_id, bike, sets=1, reps=10, pct_1rm=None, load_kg=None,
                                rpe_target=3 if is_deload else 4, rest_sec=None,
                                intent="Easy spin", notes="Cool down 10 min")

    if st.button("Generate 6-week block", key="gen_sc_block"):
        if missing:
            st.error("Cannot generate: add missing exercises first (see warning above).")
            st.stop()

        block_id = create_sc_block(
            patient_id=pid,
            start_date=block_start.isoformat(),
            goal=block_goal,
            notes=block_notes.strip() if block_notes else None,
            weeks=6,
            model="hybrid_v1",
            deload_week=int(deload_week),
            sessions_per_week=int(sessions_pw),
        )

        for wk in range(1, 7):
            wk_start = (block_start + timedelta(days=(wk - 1) * 7)).isoformat()
            is_deload = (wk == int(deload_week))
            focus = "deload" if is_deload else "hybrid"

            week_id = upsert_sc_week(
                block_id=block_id,
                week_no=wk,
                week_start=wk_start,
                focus=focus,
                deload_flag=is_deload,
                notes=None,
            )

            sA = upsert_sc_session(week_id=week_id, session_label="A", day_hint="Tue", notes=None)
            _seed_xmas_template(sA, is_deload=is_deload)

            if int(sessions_pw) == 2:
                sB = upsert_sc_session(week_id=week_id, session_label="B", day_hint="Thu", notes=None)
                _seed_xmas_template(sB, is_deload=is_deload)

        st.success("6-week block created.")
        st.rerun()

    st.divider()
    st.subheader("Latest saved block")

    latest = fetch_latest_sc_block(pid)
    if latest is None:
        st.info("No block created yet.")
    else:
        block_id, b_start, b_weeks, b_model, b_deload, b_spw, b_goal, b_notes, b_created = latest
        st.write(f"Block **{block_id}** | Start **{b_start}** | Model **{b_model}** | Deload week **{b_deload}** | Sessions/week **{b_spw}** | Goal **{b_goal}**")
        if b_notes:
            st.caption(f"Notes: {b_notes}")

        detail = fetch_sc_block_detail(block_id)
        if not detail:
            st.warning("Block exists but has no detail (weeks/sessions).")
        else:
            for week_no, week_start_str, focus, is_deload, label, day_hint, exs in detail:
                st.markdown(f"### Week {week_no} ({week_start_str}) — {focus}{' (DELOAD)' if is_deload else ''}")
                st.markdown(f"**Session {label}** ({day_hint or 'day TBD'})")
                if not exs:
                    st.info("No exercises in this session yet.")
                else:
                    df = pd.DataFrame(
                        exs,
                        columns=["exercise", "sets", "reps", "pct_1rm", "load_kg", "rpe", "rest_sec", "intent", "notes"]
                    )
                    st.dataframe(df, use_container_width=True)
