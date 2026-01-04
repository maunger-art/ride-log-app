from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

import db_store as db
from plan import rides_to_weekly_summary
from strava import build_auth_url, exchange_code_for_token, ensure_fresh_token, list_activities


@dataclass
class StravaStatus:
    connected: bool
    auth_url: Optional[str]
    athlete_id: Optional[int]
    scope: Optional[str]
    expires_at: Optional[int]


def list_rides(user_id: str, role: str, patient_id: int) -> list[dict[str, Any]]:
    rides = db.fetch_rides_for_user(user_id, role, patient_id)
    return [
        {
            "ride_date": row[0],
            "distance_km": row[1],
            "duration_min": row[2],
            "rpe": row[3],
            "notes": row[4],
        }
        for row in rides
    ]


def add_ride(
    user_id: str,
    role: str,
    patient_id: int,
    ride_date: str,
    distance_km: float,
    duration_min: int,
    rpe: Optional[int],
    notes: Optional[str],
) -> None:
    db.add_ride_for_user(user_id, role, patient_id, ride_date, distance_km, duration_min, rpe, notes)


def list_week_plans(user_id: str, role: str, patient_id: int) -> list[dict[str, Any]]:
    plans = db.fetch_week_plans_for_user(user_id, role, patient_id)
    return [
        {
            "week_start": row[0],
            "planned_km": row[1],
            "planned_hours": row[2],
            "phase": row[3],
            "notes": row[4],
        }
        for row in plans
    ]


def upsert_week_plan(
    user_id: str,
    role: str,
    patient_id: int,
    week_start: str,
    planned_km: Optional[float],
    planned_hours: Optional[float],
    phase: Optional[str],
    notes: Optional[str],
) -> None:
    db.upsert_week_plan_for_user(user_id, role, patient_id, week_start, planned_km, planned_hours, phase, notes)


def weekly_plan_vs_actual(user_id: str, role: str, patient_id: int) -> pd.DataFrame:
    rides_df = pd.DataFrame(
        db.fetch_rides_for_user(user_id, role, patient_id),
        columns=["ride_date", "distance_km", "duration_min", "rpe", "notes"],
    )
    plan_df = pd.DataFrame(
        db.fetch_week_plans_for_user(user_id, role, patient_id),
        columns=["week_start", "planned_km", "planned_hours", "phase", "notes"],
    )
    if not plan_df.empty:
        plan_df["week_start"] = pd.to_datetime(plan_df["week_start"], errors="coerce").dt.normalize()

    weekly_actual = rides_to_weekly_summary(rides_df)
    if not weekly_actual.empty:
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"], errors="coerce").dt.normalize()
    else:
        weekly_actual = pd.DataFrame(columns=["week_start", "actual_km", "actual_hours", "rides_count"])
        weekly_actual["week_start"] = pd.to_datetime(weekly_actual["week_start"])

    if plan_df.empty and weekly_actual.empty:
        return pd.DataFrame()

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

    return merged


def connect_strava(user_id: str, role: str, patient_id: int, code: str, state: str) -> None:
    if str(state) != str(patient_id):
        raise ValueError("Strava callback state did not match patient.")
    data = exchange_code_for_token(code)
    db.save_strava_tokens_for_user(
        user_id,
        role,
        patient_id,
        data["access_token"],
        data["refresh_token"],
        int(data["expires_at"]),
        data.get("athlete", {}).get("id"),
        str(data.get("scope")),
    )


def get_strava_status(user_id: str, role: str, patient_id: int) -> StravaStatus:
    token_row = db.get_strava_tokens_for_user(user_id, role, patient_id)
    if token_row is None:
        return StravaStatus(
            connected=False,
            auth_url=build_auth_url(state=str(patient_id)),
            athlete_id=None,
            scope=None,
            expires_at=None,
        )

    access_token, refresh_token, expires_at, athlete_id, scope, refreshed = ensure_fresh_token(token_row)
    if refreshed:
        db.save_strava_tokens_for_user(user_id, role, patient_id, access_token, refresh_token, expires_at, athlete_id, str(scope))

    return StravaStatus(
        connected=True,
        auth_url=None,
        athlete_id=athlete_id,
        scope=str(scope) if scope is not None else None,
        expires_at=expires_at,
    )


def sync_strava_rides(user_id: str, role: str, patient_id: int, days_back: int) -> int:
    token_row = db.get_strava_tokens_for_user(user_id, role, patient_id)
    if token_row is None:
        raise ValueError("Strava not connected.")

    access_token, refresh_token, expires_at, athlete_id, scope, refreshed = ensure_fresh_token(token_row)
    if refreshed:
        db.save_strava_tokens_for_user(user_id, role, patient_id, access_token, refresh_token, expires_at, athlete_id, str(scope))

    after_epoch = int(pd.Timestamp.utcnow().timestamp() - int(days_back) * 86400)
    imported = 0
    page = 1

    while True:
        acts = list_activities(access_token, after_epoch=after_epoch, per_page=50, page=page)
        if not acts:
            break

        for activity in acts:
            sport = activity.get("sport_type") or activity.get("type")
            if sport not in ["Ride", "VirtualRide", "EBikeRide", "GravelRide", "MountainBikeRide"]:
                continue

            act_id = int(activity["id"])
            if db.is_activity_synced_for_user(user_id, role, patient_id, act_id):
                continue

            ride_date_str = activity["start_date_local"][:10]
            distance_km_val = float(activity.get("distance", 0)) / 1000.0
            duration_min_val = int(round(float(activity.get("elapsed_time", 0)) / 60.0))
            name = activity.get("name", "Strava ride")

            db.add_ride_for_user(
                user_id,
                role,
                patient_id,
                ride_date_str,
                distance_km_val,
                duration_min_val,
                None,
                f"[Strava] {name}",
            )
            db.mark_activity_synced_for_user(user_id, role, patient_id, act_id)
            imported += 1

        page += 1

    return imported


def _parse_exercise_style(ex_row) -> str:
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
    if deload:
        sets_t = max(1, int(round(sets_base * 0.6)))
        reps_t = max(1, int(round(reps_base * 0.7)))
        load_t = None if load_base is None else round(load_base * 0.9, 1)
        pct_t = None if pct_base is None else round(pct_base * 0.9, 3)
        return sets_t, reps_t, load_t, pct_t

    if style in ["isometric", "bodyweight"]:
        reps_t = reps_base + (week_no - 1) * 5
        return sets_base, reps_t, load_base, pct_base

    if style == "conditioning":
        reps_t = reps_base + (week_no - 1) * 1
        return sets_base, reps_t, load_base, pct_base

    if style == "db_kb":
        reps_t = reps_base + (week_no - 1) * 1
        load_t = load_base
        if load_base is not None:
            if reps_t > 12:
                reps_t = 8
                load_t = round(load_base + 2.5, 1)
        return sets_base, reps_t, load_t, pct_base

    if style == "barbell":
        load_t = load_base
        pct_t = pct_base
        if load_base is not None:
            load_t = round(load_base + (week_no - 1) * 2.5, 1)
        elif pct_base is not None:
            pct_t = round(pct_base + (week_no - 1) * 0.02, 3)
        return sets_base, reps_base, load_t, pct_t

    reps_t = reps_base + (week_no - 1) * 1
    return sets_base, reps_t, load_base, pct_base


def create_sc_block(
    user_id: str,
    role: str,
    patient_id: int,
    start_date: str,
    goal: str,
    notes: Optional[str],
    weeks: int,
    model: str,
    deload_week: int,
    sessions_per_week: int,
    template_a: list[dict[str, Any]],
    template_b: list[dict[str, Any]],
) -> int:
    block_id = db.create_sc_block_for_user(
        user_id,
        role,
        patient_id,
        start_date=start_date,
        goal=goal,
        notes=notes,
        weeks=int(weeks),
        model=model,
        deload_week=int(deload_week),
        sessions_per_week=int(sessions_per_week),
    )

    for wk in range(1, int(weeks) + 1):
        wk_start = (date.fromisoformat(start_date) + timedelta(days=(wk - 1) * 7)).isoformat()
        is_deload = wk == int(deload_week)
        focus = "deload" if is_deload else goal

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

        labels = ["A"] if int(sessions_per_week) == 1 else ["A", "B"]

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

            tpl = template_a if lab == "A" else template_b

            for row in tpl:
                exercise_id = row.get("exercise_id")
                if exercise_id is None:
                    continue
                ex_row = db.get_exercise(int(exercise_id))
                style = _parse_exercise_style(ex_row)

                sets_t, reps_t, load_t, pct_t = _suggest_progression(
                    style=style,
                    week_no=wk,
                    deload=is_deload,
                    sets_base=int(row["sets"]),
                    reps_base=int(row["reps"]),
                    load_base=row.get("load"),
                    pct_base=row.get("pct"),
                )

                db.add_sc_session_exercise_for_user(
                    user_id=user_id,
                    role=role,
                    session_id=sess_id,
                    exercise_id=int(exercise_id),
                    sets_target=int(sets_t),
                    reps_target=int(reps_t),
                    pct_1rm_target=pct_t,
                    load_kg_target=load_t,
                    rpe_target=None,
                    rest_sec_target=None,
                    intent=None,
                    notes=f"Auto-suggest ({style})",
                )

    return block_id


def latest_sc_block_with_detail(user_id: str, role: str, patient_id: int) -> Optional[dict[str, Any]]:
    latest = db.fetch_latest_sc_block_for_user(user_id, role, patient_id)
    if latest is None:
        return None

    block_id, start_date_s, weeks, model, deload_wk, spw, goal_s, notes_s, created_at = latest
    detail = db.fetch_sc_block_detail_for_user(user_id, role, block_id)

    sessions = []
    for (wk_no, wk_start, focus, is_deload, label, day_hint, exs) in detail:
        exercises = []
        for ex in exs:
            (
                row_id,
                ex_name,
                sets_t,
                reps_t,
                pct_t,
                load_t,
                rpe_t,
                rest_t,
                intent,
                n_notes,
                sets_a,
                reps_a,
                load_a,
                completed,
                a_notes,
            ) = ex
            exercises.append(
                {
                    "row_id": row_id,
                    "exercise_name": ex_name,
                    "sets_target": sets_t,
                    "reps_target": reps_t,
                    "pct_1rm_target": pct_t,
                    "load_kg_target": load_t,
                    "rpe_target": rpe_t,
                    "rest_sec_target": rest_t,
                    "intent": intent,
                    "notes": n_notes,
                    "sets_actual": sets_a,
                    "reps_actual": reps_a,
                    "load_kg_actual": load_a,
                    "completed": completed,
                    "actual_notes": a_notes,
                }
            )
        sessions.append(
            {
                "week_no": wk_no,
                "week_start": wk_start,
                "focus": focus,
                "is_deload": is_deload,
                "session_label": label,
                "day_hint": day_hint,
                "exercises": exercises,
            }
        )

    return {
        "block": {
            "block_id": block_id,
            "start_date": start_date_s,
            "weeks": weeks,
            "model": model,
            "deload_week": deload_wk,
            "sessions_per_week": spw,
            "goal": goal_s,
            "notes": notes_s,
            "created_at": created_at,
        },
        "sessions": sessions,
    }


def update_sc_actuals(
    user_id: str,
    role: str,
    row_id: int,
    sets_actual: Optional[int],
    reps_actual: Optional[int],
    load_kg_actual: Optional[float],
    completed: bool,
    actual_notes: Optional[str],
) -> None:
    db.update_sc_session_exercise_actual_for_user(
        user_id=user_id,
        role=role,
        row_id=row_id,
        sets_actual=sets_actual,
        reps_actual=reps_actual,
        load_kg_actual=load_kg_actual,
        completed_flag=bool(completed),
        actual_notes=actual_notes,
    )
