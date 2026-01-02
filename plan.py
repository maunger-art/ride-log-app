import pandas as pd
from datetime import date, datetime, timedelta

def to_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())

def iso(d: date) -> str:
    return d.isoformat()

def parse_plan_csv(file) -> pd.DataFrame:
    """
    Expected columns:
      week_start (YYYY-MM-DD, Monday), planned_km, planned_hours, phase, notes
    """
    df = pd.read_csv(file)
    required = {"week_start"}
    if not required.issubset(set(df.columns)):
        raise ValueError("Plan CSV must include at least: week_start (YYYY-MM-DD, Monday).")
    # Normalize
    df["week_start"] = pd.to_datetime(df["week_start"]).dt.date
    for col in ["planned_km", "planned_hours"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["phase", "notes"]:
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df

def rides_to_weekly_summary(rides_df: pd.DataFrame) -> pd.DataFrame:
    """
    rides_df columns: ride_date (date), distance_km, duration_min
    returns weekly: week_start, actual_km, actual_hours, rides_count
    """
    if rides_df.empty:
        return pd.DataFrame(columns=["week_start","actual_km","actual_hours","rides_count"])

    d = rides_df.copy()
    d["ride_date"] = pd.to_datetime(d["ride_date"]).dt.date
    d["week_start"] = d["ride_date"].apply(to_monday)
    d["actual_km"] = d["distance_km"]
    d["actual_hours"] = d["duration_min"] / 60.0

    out = (d.groupby("week_start", as_index=False)
             .agg(actual_km=("actual_km","sum"),
                  actual_hours=("actual_hours","sum"),
                  rides_count=("actual_km","count")))
    out["week_start"] = pd.to_datetime(out["week_start"])
    return out.sort_values("week_start")
