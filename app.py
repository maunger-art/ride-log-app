import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta

from db import init_db, upsert_patient, list_patients, add_ride, fetch_rides, upsert_week_plan, fetch_week_plans
from plan import parse_plan_csv, rides_to_weekly_summary, to_monday

st.set_page_config(page_title="Ride Log – Plan vs Actual", layout="wide")

init_db()

st.title("Ride Log – Plan vs Actual")

# Sidebar: patient selection / creation
st.sidebar.header("Patient")
patients = list_patients()
names = [p[1] for p in patients]
selected = st.sidebar.selectbox("Select patient", options=["(New patient)"] + names)

if selected == "(New patient)":
    new_name = st.sidebar.text_input("Enter patient name")
    if st.sidebar.button("Create patient") and new_name.strip():
        pid = upsert_patient(new_name.strip())
        st.sidebar.success("Patient created. Select them from the dropdown.")
        st.stop()
else:
    pid = [p[0] for p in patients if p[1] == selected][0]

# Tabs
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
        add_ride(pid, ride_date.isoformat(), float(distance_km), int(duration_min), int(rpe), notes.strip() if notes else None)
        st.success("Ride saved.")

    st.divider()
    st.subheader("Recent rides")
    rides = fetch_rides(pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date","distance_km","duration_min","rpe","notes"])
    st.dataframe(rides_df, use_container_width=True)

with tab2:
    st.subheader("Plan vs actual (weekly)")

    # Pull rides
    rides = fetch_rides(pid)
    rides_df = pd.DataFrame(rides, columns=["ride_date","distance_km","duration_min","rpe","notes"])

    # Pull plan
    plan_rows = fetch_week_plans(pid)
    plan_df = pd.DataFrame(plan_rows, columns=["week_start","planned_km","planned_hours","phase","notes"])
    if not plan_df.empty:
        plan_df["week_start"] = pd.to_datetime(plan_df["week_start"])

    weekly_actual = rides_to_weekly_summary(rides_df)

    # Merge
    if plan_df.empty and weekly_actual.empty:
        st.info("No plan or rides yet. Add rides or import a plan on the Plan tab.")
    else:
        merged = None
        if plan_df.empty:
            merged = weekly_actual.copy()
        elif weekly_actual.empty:
            merged = plan_df.copy()
        else:
            merged = pd.merge(plan_df, weekly_actual, on="week_start", how="outer").sort_values("week_start")

        # Fill NA
        for c in ["planned_km","planned_hours","actual_km","actual_hours","rides_count"]:
            if c in merged.columns:
                merged[c] = merged[c].fillna(0)

        # Variance
        if "planned_km" in merged.columns and "actual_km" in merged.columns:
            merged["km_variance"] = merged["actual_km"] - merged["planned_km"]
        if "planned_hours" in merged.columns and "actual_hours" in merged.columns:
            merged["hours_variance"] = merged["actual_hours"] - merged["planned_hours"]

        st.dataframe(merged, use_container_width=True)

        st.divider()
        st.subheader("Export for coaching review")
        csv = rides_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download rides CSV", data=csv, file_name=f"{selected}_rides.csv", mime="text/csv")

        # Prompt generator (copy/paste into ChatGPT)
        st.subheader("Copy/paste prompt for ChatGPT weekly review")
        latest_week = to_monday(date.today())
        prompt = f"""You are my cycling coach. Review my last 4 weeks of training versus plan.

Patient: {selected}
Today: {date.today().isoformat()}

Weekly Plan vs Actual (most recent):
{merged.tail(6).to_string(index=False)}

Rides (most recent 25):
{rides_df.head(25).to_string(index=False)}

Please provide:
1) adherence summary (hours/km), 2) fatigue/risk flags, 3) suggested adjustments for next 2 weeks, 4) key coaching points."""
        st.code(prompt, language="text")

with tab3:
    st.subheader("Plan import (CSV)")

    st.write("Upload a CSV with columns: week_start (Monday, YYYY-MM-DD), planned_km, planned_hours, phase, notes.")
    uploaded = st.file_uploader("Upload plan CSV", type=["csv"])
    if uploaded is not None:
        try:
            df = parse_plan_csv(uploaded)
            st.success(f"Loaded {len(df)} plan rows.")
            st.dataframe(df, use_container_width=True)

            if st.button("Save plan to patient"):
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
        except Exception as e:
            st.error(f"Plan import error: {e}")

    st.divider()
    st.subheader("Manual plan edit (single week)")
    week_start = st.date_input("Week start (Monday)", value=to_monday(date.today()))
    col1, col2, col3 = st.columns(3)
    with col1:
        planned_km = st.number_input("Planned km", min_value=0.0, step=10.0)
    with col2:
        planned_hours = st.number_input("Planned hours", min_value=0.0, step=1.0)
    with col3:
        phase = st.text_input("Phase (e.g., Base/Build/Peak/Deload/Event)")
    note = st.text_area("Notes", height=80)

    if st.button("Save this week"):
        upsert_week_plan(pid, week_start.isoformat(), planned_km, planned_hours, phase.strip(), note.strip() if note else None)
        st.success("Week saved to plan.")

