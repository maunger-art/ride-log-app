import streamlit as st
import pandas as pd
import time
from datetime import date, datetime

from db_store import (
    init_db,
    upsert_patient,
    list_patients,
    add_ride,
    fetch_rides,
    upsert_week_plan,
    fetch_week_plans,
    # Strava
    save_strava_tokens,
    get_strava_tokens,
    is_activity_synced,
    mark_activity_synced,
    # Strength DB status (optional/admin)
    count_norm_rows,
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
    st.subheader("S&C Planning (MVP)")

    # --- Quick status / seeding helper ---
    st.caption(f"Strength standards rows: {count_norm_rows()}")

    if count_norm_rows() == 0:
        st.warning(
            "Strength standards are not seeded yet. "
            "Add seed_strength_standards.py to the repo root and run 'Seed strength DB' from the sidebar."
        )

    st.divider()

    # --- Patient profile (needed for age/sex/BW comparisons) ---
    st.subheader("Patient profile")

    profile = get_patient_profile(pid)
    sex_default = profile[0] if profile else None
    dob_default = profile[1] if profile else None
    bw_default = profile[2] if profile else None

    colp1, colp2, colp3 = st.columns(3)
    with colp1:
        sex = st.selectbox(
            "Sex",
            options=["", "male", "female"],
            index=(["", "male", "female"].index(sex_default) if sex_default in ["male", "female"] else 0),
        )
    with colp2:
        dob = st.text_input(
            "Date of birth (YYYY-MM-DD) – optional",
            value=(dob_default if dob_default else ""),
            help="If you don’t want DOB stored, leave blank and enter age below when needed.",
        )
    with colp3:
        bodyweight_kg = st.number_input(
            "Bodyweight (kg)",
            min_value=0.0,
            step=0.1,
            value=(float(bw_default) if bw_default is not None else 0.0),
        )

    if st.button("Save profile"):
        upsert_patient_profile(pid, sex if sex else None, dob.strip() if dob else None, bodyweight_kg if bodyweight_kg > 0 else None)
        st.success("Profile saved.")
        st.rerun()

    st.divider()

    # --- Exercise selection ---
    st.subheader("Strength benchmark + prescription")

    exercises = list_exercises()
    if not exercises:
        st.info("No exercises found in the database yet. Seed the DB, or add exercises manually via a seed script.")
        st.stop()

    ex_name_map = {row[1]: row[0] for row in exercises}  # name -> id
    ex_names = list(ex_name_map.keys())
    selected_ex = st.selectbox("Select exercise", options=ex_names)
    ex_id = ex_name_map[selected_ex]

    # Metric: pull-ups are reps; everything else uses rel_1rm_bw
    metric = "pullup_reps" if selected_ex.lower().startswith("pull-up") or selected_ex.lower().startswith("pullup") else "rel_1rm_bw"

    col1, col2 = st.columns(2)
    with col1:
        if metric == "pullup_reps":
            test_value = st.number_input("Test result (strict reps)", min_value=0, step=1, value=0)
        else:
            test_value = st.number_input("Estimated 1RM (kg)", min_value=0.0, step=2.5, value=0.0)

    with col2:
        # If DOB not provided, allow manual age entry for benchmarks
        age_manual = st.number_input("Age (years) – used if DOB blank", min_value=18, max_value=65, value=35)

    # Compute age
    age_years = None
    if dob and dob.strip():
        try:
            dob_dt = datetime.strptime(dob.strip(), "%Y-%m-%d").date()
            today = date.today()
            age_years = today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))
        except Exception:
            age_years = int(age_manual)
            st.warning("DOB format invalid. Using manual age.")
    else:
        age_years = int(age_manual)

    # Compute relative strength if applicable
    rel_strength = None
    if metric == "rel_1rm_bw":
        if bodyweight_kg and bodyweight_kg > 0 and test_value and test_value > 0:
            rel_strength = float(test_value) / float(bodyweight_kg)

    # Pull benchmark row
    if not sex:
        st.info("Select sex above to enable benchmarking.")
        st.stop()

    bench = get_norm_standard(ex_id, sex, age_years, metric)

    if bench is None:
        st.warning("No benchmark found for this exercise/sex/age/metric. (Seed may be missing or incomplete.)")
    else:
        poor, fair, good, excellent, source, notes, age_min, age_max = bench

        st.markdown(
            f"**Benchmark band used:** {sex}, ages {age_min}–{age_max}, metric `{metric}`"
        )
        if source:
            st.caption(f"Source: {source}")
        if notes:
            st.caption(f"Notes: {notes}")

        # Determine category
        category = None
        if metric == "pullup_reps":
            v = int(test_value)
            if excellent is not None and v >= excellent:
                category = "Excellent"
            elif good is not None and v >= good:
                category = "Good"
            elif fair is not None and v >= fair:
                category = "Fair"
            else:
                category = "Poor"
            st.metric("Result", f"{v} reps", help="Strict pull-ups")
        else:
            if rel_strength is None:
                st.info("Enter a bodyweight and 1RM to compute relative strength.")
            else:
                v = rel_strength
                if excellent is not None and v >= excellent:
                    category = "Excellent"
                elif good is not None and v >= good:
                    category = "Good"
                elif fair is not None and v >= fair:
                    category = "Fair"
                else:
                    category = "Poor"
                st.metric("Relative strength", f"{v:.2f} x BW")

        if category:
            st.success(f"Classification: **{category}**")

    st.divider()

    # --- Prescription from rep schemes ---
    st.subheader("Prescription builder")

    goal = st.selectbox("Adaptation goal", options=["endurance", "hypertrophy", "strength", "power"], index=0)
    schemes = list_rep_schemes(goal)

    if not schemes:
        st.warning("No rep schemes found for this goal. Seed the DB first.")
    else:
        # pick first scheme for MVP; later you can let user choose among multiple phases
        s = schemes[0]
        _, s_goal, s_phase, reps_min, reps_max, sets_min, sets_max, pct_min, pct_max, rpe_min, rpe_max, rest_min, rest_max, intent = s

        st.markdown(f"**Scheme:** {s_goal} ({s_phase if s_phase else 'default'})")
        st.write(
            f"- Sets: **{sets_min}–{sets_max}**\n"
            f"- Reps: **{reps_min}–{reps_max}**\n"
            f"- %1RM: **{int(pct_min*100)}–{int(pct_max*100)}%**" if pct_min is not None and pct_max is not None else
            f"- %1RM: **n/a**"
        )
        if rest_min and rest_max:
            st.write(f"- Rest: **{rest_min}–{rest_max} sec**")
        if intent:
            st.write(f"- Intent: **{intent}**")
        if rpe_min and rpe_max:
            st.write(f"- RPE target: **{rpe_min}–{rpe_max}**")

        if metric == "rel_1rm_bw":
            if test_value and test_value > 0 and pct_min is not None and pct_max is not None:
                w_min = float(test_value) * float(pct_min)
                w_max = float(test_value) * float(pct_max)
                st.info(f"Suggested working range: **{w_min:.1f}–{w_max:.1f} kg**")
            else:
                st.info("Enter an estimated 1RM to generate working loads.")
        else:
            st.info("Pull-ups are prescribed using reps/sets and intent rather than %1RM.")

    st.divider()

    # --- Save test result (optional) ---
    st.subheader("Save this test result to the patient record")

    if st.button("Save test"):
        if metric == "pullup_reps":
            # store reps as note; estimated_1rm_kg remains null
            insert_strength_test(
                pid, ex_id, date.today().isoformat(),
                None, int(test_value), None,
                "bilateral", f"Pull-up reps saved: {int(test_value)}"
            )
        else:
            insert_strength_test(
                pid, ex_id, date.today().isoformat(),
                float(test_value) if test_value and test_value > 0 else None,
                None, None,
                "bilateral",
                "Estimated 1RM entry"
            )
        st.success("Test saved.")
        st.rerun()
