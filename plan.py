import pandas as pd
from datetime import date, timedelta

def to_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())

def parse_plan_csv(file) -> pd.DataFrame:
    """
    Expected columns:
      week_start (YYYY-MM-DD, Monday), planned_km, planned_hours, phase, notes
    """
    df = pd.read_csv(file)
    if "week_start" not in df.columns:
        raise ValueError("Plan CSV must include at least: week_start (YYYY-MM-DD, Monday).")

    week_start = pd.to_datetime(df["week_start"], errors="coerce")
    if week_start.isna().any():
        raise ValueError("Plan CSV has invalid week_start dates; expected YYYY-MM-DD.")
    if not week_start.apply(lambda d: d.weekday() == 0).all():
        raise ValueError("Plan CSV week_start dates must be Mondays.")
    df["week_start"] = week_start.dt.date

    for col in ["planned_km", "planned_hours"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["phase", "notes"]:
        if col in df.columns:
            df[col] = df[col].astype("string")

    return df

def rides_to_weekly_summary(rides_df: pd.DataFrame) -> pd.DataFrame:
    """
    rides_df columns: ride_date, distance_km, duration_min
    returns weekly: week_start, actual_km, actual_hours, rides_count
    """
    if rides_df.empty:
        return pd.DataFrame(columns=["week_start", "actual_km", "actual_hours", "rides_count"])

    d = rides_df.copy()
    d["ride_date"] = pd.to_datetime(d["ride_date"]).dt.date
    d["week_start"] = d["ride_date"].apply(to_monday)
    d["actual_km"] = d["distance_km"]
    d["actual_hours"] = d["duration_min"] / 60.0

    out = (
        d.groupby("week_start", as_index=False)
         .agg(
            actual_km=("actual_km", "sum"),
            actual_hours=("actual_hours", "sum"),
            rides_count=("actual_km", "count"),
         )
    )
    out["week_start"] = pd.to_datetime(out["week_start"])
    return out.sort_values("week_start")
