from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import services

app = FastAPI(title="Ride Log API")


class UserContext(BaseModel):
    user_id: str
    role: str
    patient_id: int


class RideCreateRequest(UserContext):
    ride_date: str
    distance_km: float
    duration_min: int
    rpe: Optional[int] = None
    notes: Optional[str] = None


class PlanUpsertRequest(UserContext):
    week_start: str
    planned_km: Optional[float] = None
    planned_hours: Optional[float] = None
    phase: Optional[str] = None
    notes: Optional[str] = None


class StravaConnectRequest(UserContext):
    code: str
    state: str


class StravaSyncRequest(UserContext):
    days_back: int = Field(ge=1, le=365)


class ScExerciseTemplate(BaseModel):
    exercise_id: int
    sets: int
    reps: int
    pct: Optional[float] = None
    load: Optional[float] = None


class ScBlockCreateRequest(UserContext):
    start_date: str
    goal: str
    notes: Optional[str] = None
    weeks: int
    model: str
    deload_week: int
    sessions_per_week: int
    template_a: list[ScExerciseTemplate]
    template_b: list[ScExerciseTemplate] = Field(default_factory=list)


class ScActualsUpdateRequest(UserContext):
    row_id: int
    sets_actual: Optional[int] = None
    reps_actual: Optional[int] = None
    load_kg_actual: Optional[float] = None
    completed: bool
    actual_notes: Optional[str] = None


def _serialize_weekly_summary(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    data = frame.copy()
    if "week_start" in data.columns:
        data["week_start"] = data["week_start"].astype(str)
    return data.to_dict(orient="records")


@app.get("/rides")
def get_rides(user_id: str, role: str, patient_id: int) -> dict[str, Any]:
    return {"rides": services.list_rides(user_id, role, patient_id)}


@app.post("/rides")
def post_rides(payload: RideCreateRequest) -> dict[str, Any]:
    services.add_ride(
        payload.user_id,
        payload.role,
        payload.patient_id,
        payload.ride_date,
        payload.distance_km,
        payload.duration_min,
        payload.rpe,
        payload.notes,
    )
    return {"status": "saved"}


@app.get("/plan")
def get_plan(user_id: str, role: str, patient_id: int) -> dict[str, Any]:
    plan = services.list_week_plans(user_id, role, patient_id)
    weekly = services.weekly_plan_vs_actual(user_id, role, patient_id)
    return {
        "plan": plan,
        "weekly_summary": _serialize_weekly_summary(weekly),
    }


@app.post("/plan")
def post_plan(payload: PlanUpsertRequest) -> dict[str, Any]:
    services.upsert_week_plan(
        payload.user_id,
        payload.role,
        payload.patient_id,
        payload.week_start,
        payload.planned_km,
        payload.planned_hours,
        payload.phase,
        payload.notes,
    )
    return {"status": "saved"}


@app.post("/strava/connect")
def post_strava_connect(payload: StravaConnectRequest) -> dict[str, Any]:
    try:
        services.connect_strava(payload.user_id, payload.role, payload.patient_id, payload.code, payload.state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "connected"}


@app.post("/strava/sync")
def post_strava_sync(payload: StravaSyncRequest) -> dict[str, Any]:
    try:
        imported = services.sync_strava_rides(
            payload.user_id, payload.role, payload.patient_id, payload.days_back
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"imported": imported}


@app.get("/strava/status")
def get_strava_status(user_id: str, role: str, patient_id: int) -> dict[str, Any]:
    status = services.get_strava_status(user_id, role, patient_id)
    return {
        "connected": status.connected,
        "auth_url": status.auth_url,
        "athlete_id": status.athlete_id,
        "scope": status.scope,
        "expires_at": status.expires_at,
    }


@app.post("/snc/block")
def post_snc_block(payload: ScBlockCreateRequest) -> dict[str, Any]:
    block_id = services.create_sc_block(
        user_id=payload.user_id,
        role=payload.role,
        patient_id=payload.patient_id,
        start_date=payload.start_date,
        goal=payload.goal,
        notes=payload.notes,
        weeks=payload.weeks,
        model=payload.model,
        deload_week=payload.deload_week,
        sessions_per_week=payload.sessions_per_week,
        template_a=[item.model_dump() for item in payload.template_a],
        template_b=[item.model_dump() for item in payload.template_b],
    )
    return {"block_id": block_id}


@app.get("/snc/block")
def get_snc_block(user_id: str, role: str, patient_id: int) -> dict[str, Any]:
    detail = services.latest_sc_block_with_detail(user_id, role, patient_id)
    if detail is None:
        return {"block": None, "sessions": []}
    return detail


@app.post("/snc/actuals")
def post_snc_actuals(payload: ScActualsUpdateRequest) -> dict[str, Any]:
    services.update_sc_actuals(
        user_id=payload.user_id,
        role=payload.role,
        row_id=payload.row_id,
        sets_actual=payload.sets_actual,
        reps_actual=payload.reps_actual,
        load_kg_actual=payload.load_kg_actual,
        completed=payload.completed,
        actual_notes=payload.actual_notes,
    )
    return {"status": "saved"}
