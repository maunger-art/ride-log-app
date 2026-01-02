import streamlit as st
import pandas as pd
import time
from datetime import date

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

st.set_page_config(page_title="Ride Log – Plan vs Actual", layout="wide")

init_db()

st.title("Ride Log – Plan vs Actual")

# Sidebar: patient selection / creation
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

# Guard: must have a patient selected for everything below
if pid is None:
    st.warning("Please create or select a patient in the sidebar before using the app.")
    st.stop()

tab1, tab2, tab3 = st.tabs(["Log Ride", "Dashboard", "Plan Import / Edit"])

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

    st.divider()
    st.subheader("Recent rides")
    rides = fetch_rides(pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date", "distance_km", "duration_min", "rpe", "notes"])
    st.dataframe(rides_df, use_container_width=True)

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
        else:
            st.warning("Strava callback state did not match the selected patient. Please try again.")
            st.query_params.clear()

    # IMPORTANT: this must be OUTSIDE the callback block
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

                    ride_date = a["start_date_local"][:10]  # YYYY-MM-DD
                    distance_km = float(a.get("distance", 0)) / 1000.0
                    duration_min = int(round(float(a.get("elapsed_time", 0)) / 60.0))
                    name = a.get("name", "Strava ride")

                    add_ride(
                        pid,
                        ride_date,
                        distance_km,
                        duration_min,
                        None,
                        f"[Strava] {name}"
                    )

                    mark_activity_synced(pid, act_id)
                    imported += 1

                page += 1

            st.success(f"Imported {imported} new Strava rides.")
            st.rerun()
