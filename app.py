import time
from datetime import date

import pandas as pd
import streamlit as st

from db_store import (
    init_db,
    upsert_patient,
    list_patients,
    add_ride,
    fetch_rides,
    upsert_week_plan,
    fetch_week_plans,
    save_strava_tokens,
    get_strava_tokens,
    is_activity_synced,
    mark_activity_synced,
)
from plan import parse_plan_csv, rides_to_weekly_summary, to_monday
from strava import build_auth_url, exchange_code_for_token, ensure_fresh_token, list_activities


# -----------------------------
# Page + DB init
# -----------------------------
st.set_page_config(page_title="Ride Log – Plan vs Actual", layout="wide")
init_db()

st.title("Ride Log – Plan vs Actual")


# -----------------------------
# Sidebar: patient selection / creation
# -----------------------------
st.sidebar.header("Patient")

patients = list_patients()
names = [p[1] for p in patients]
selected = st.sidebar.selectbox("Select patient", options=["(New patient)"] + names, key="patient_select")

pid = None
if selected == "(New patient)":
    new_name = st.sidebar.text_input("Enter patient name", key="new_patient_name")
    if st.sidebar.button("Create patient", key="create_patient_btn") and new_name.strip():
        pid = upsert_patient(new_name.strip())
        st.sidebar.success("Patient created. Select them from the dropdown.")
        st.stop()
else:
    pid = [p[0] for p in patients if p[1] == selected][0]

if pid is None:
    st.warning("Please create or select a patient in the sidebar before using the app.")
    st.stop()

st.sidebar.caption(f"Active patient: {selected} (pid={pid})")


# -----------------------------
# Tabs
# -----------------------------
tab1, tab2, tab3 = st.tabs(["Log Ride", "Dashboard", "Plan Import / Edit"])


# -----------------------------
# Tab 1: Manual log + recent rides
# -----------------------------
with tab1:
    st.subheader("Log a ride")
    st.caption(f"Active patient: {selected} (pid={pid})")

    col1, col2, col3 = st.columns(3)
    with col1:
        ride_date = st.date_input("Date", value=date.today(), key="ride_date")
        distance_km = st.number_input("Distance (km)", min_value=0.0, step=1.0, key="distance_km")
    with col2:
        duration_min = st.number_input("Duration (minutes)", min_value=0, step=5, key="duration_min")
        rpe = st.number_input("RPE (1–10)", min_value=1, max_value=10, value=3, key="rpe")
    with col3:
        notes = st.text_area("Notes (optional)", height=120, key="ride_notes")

    if st.button("Save ride", key="save_ride_btn"):
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


# -----------------------------
# Tab 2: Dashboard + Strava + Plan vs Actual
# -----------------------------
with tab2:
    st.subheader("Plan vs actual (weekly)")
    st.caption(f"Active patient: {selected} (pid={pid})")

    # Manual refresh button (useful when Streamlit state gets sticky)
    colA, colB = st.columns([1, 4])
    with colA:
        if st.button("Refresh dashboard", key="refresh_dashboard_btn"):
            st.rerun()

    # -----------------------------
    # Strava
    # -----------------------------
    st.divider()
    st.subheader("Strava (import actual rides)")

    # Handle OAuth callback (Strava redirects back with ?code=...&state=...)
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
            save_strava_tokens(
                pid,
                access_token,
                refresh_token,
                expires_at,
                athlete_id,
                str(scope),
            )

        days_back = st.number_input(
            "Sync how many days back?",
            min_value=1,
            max_value=365,
            value=30,
            key="strava_days_back",
        )

        if st.button("Sync Strava rides", key="sync_strava_btn"):
            after_epoch = int(time.time() - int(days_back) * 86400)
            imported = 0
            page = 1

            while True:
                acts = list_activities(
                    access_token,
                    after_epoch=after_epoch,
                    per_page=50,
                    page=page,
                )
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
                    distance_km = float(a.get("distance", 0)) / 1000.0
                    duration_min = int(round(float(a.get("elapsed_time", 0)) / 60.0))
                    name = a.get("name", "Strava ride")

                    add_ride(
                        pid,
                        ride_date_str,
                        distance_km,
                        duration_min,
                        None,
                        f"[Strava] {name}",
                    )
                    mark_activity_synced(pid, act_id)
                    imported += 1

                page += 1

            st.success(f"Imported {imported} new Strava rides.")
            st.rerun()

    # -----------------------------
    # Plan vs Actual (weekly)
    # -----------------------------
    st.divider()
    st.subheader("Weekly summary (plan vs actual)")

    # Pull rides
    rides = fetch_rides(pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date", "distance_km", "duration_min", "rpe", "notes"])

    # Pull plan
    plan_rows = fetch_week_plans(pid)
    plan_df = pd.DataFrame(plan_rows, columns=["week_start", "planned_km", "planned_hours", "phase", "notes"])

    # Normalise keys for robust merge
    if not plan_df.empty:
        plan_df["week_start"] = pd.to_datetime(plan_df["week_start"], errors="coerce").dt.normalize()

    weekly_actual = rides_to_weekly_summary(rides_df)
    if not weekly_actual.empty:
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"], errors="coerce").dt.normalize()
    else:
        weekly_actual = pd.DataFrame(columns=["week_start", "actual_km", "actual_hours", "rides_count"])
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"])

    # Merge
    if plan_df.empty and weekly_actual.empty:
        st.info("No plan or rides yet. Add rides or import a plan on the Plan Import / Edit tab.")
    else:
        if plan_df.empty:
            merged = weekly_actual.copy()
        elif weekly_actual.empty:
            merged = plan_df.copy()
        else:
            merged = pd.merge(plan_df, weekly_actual, on="week_start", how="outer").sort_values("week_start")

        for c in ["planned_km", "planned_hours", "actual_km", "actual_hours", "rides_count"]:
            if c in merged.columns:
                merged[c] = merged[c].fillna(0)

        if "planned_km" in merged.columns and "actual_km" in merged.columns:
            merged["km_variance"] = merged["actual_km"] - merged["planned_km"]
        if "planned_hours" in merged.columns and "actual_hours" in merged.columns:
            merged["hours_variance"] = merged["actual_hours"] - merged["planned_hours"]

        st.dataframe(merged, use_container_width=True)

    # Exports + prompt
    st.divider()
    st.subheader("Export for coaching review")

    csv = rides_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download rides CSV",
        data=csv,
        file_name=f"{selected}_rides.csv",
        mime="text/csv",
        key="download_rides_csv",
    )

    st.subheader("Copy/paste prompt for ChatGPT weekly review")
    merged_tail = "(no weekly summary available yet)"
    if "merged" in locals() and isinstance(locals().get("merged"), pd.DataFrame) and not merged.empty:
        merged_tail = merged.tail(6).to_string(index=False)

    prompt = f"""You are my cycling coach. Review my last 4 weeks of training versus plan.

Patient: {selected}
Today: {date.today().isoformat()}

Weekly Plan vs Actual (most recent):
{merged_tail}

Rides (most recent 25):
{rides_df.head(25).to_string(index=False)}

Please provide:
1) adherence summary (hours/km),
2) fatigue/risk flags,
3) suggested adjustments for next 2 weeks,
4) key coaching points.
"""
    st.code(prompt, language="text")


# -----------------------------
# Tab 3: Plan import / edit
# -----------------------------
with tab3:
    st.subheader("Plan import (CSV)")
    st.caption(f"Active patient: {selected} (pid={pid})")

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
