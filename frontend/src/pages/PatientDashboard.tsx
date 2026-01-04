import { useCallback, useEffect, useMemo, useState } from "react";
import { getPlan } from "../services/plan";
import { getRides } from "../services/rides";
import { getSncBlock } from "../services/snc";
import { getStravaStatus } from "../services/strava";
import type { Ride, ScBlock, ScSession, StravaStatus, WeeklySummary } from "../services/types";

const PatientDashboard = () => {
  const userContext = useMemo(
    () => ({
      userId: "demo-patient",
      role: "patient",
      patientId: 1,
    }),
    []
  );

  const [weeklySummary, setWeeklySummary] = useState<WeeklySummary[]>([]);
  const [weeklyError, setWeeklyError] = useState<string | null>(null);
  const [rides, setRides] = useState<Ride[]>([]);
  const [rideError, setRideError] = useState<string | null>(null);
  const [sncBlock, setSncBlock] = useState<ScBlock | null>(null);
  const [sncSessions, setSncSessions] = useState<ScSession[]>([]);
  const [sncError, setSncError] = useState<string | null>(null);
  const [stravaStatus, setStravaStatus] = useState<StravaStatus | null>(null);
  const [stravaError, setStravaError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchPlan = useCallback(async () => {
    try {
      const { weekly_summary } = await getPlan(userContext);
      setWeeklySummary(weekly_summary ?? []);
      setWeeklyError(null);
    } catch (error) {
      setWeeklySummary([]);
      setWeeklyError(error instanceof Error ? error.message : "Unable to load plan summary.");
    }
  }, [userContext]);

  const fetchRides = useCallback(async () => {
    try {
      const rideData = await getRides(userContext);
      setRides(rideData);
      setRideError(null);
    } catch (error) {
      setRides([]);
      setRideError(error instanceof Error ? error.message : "Unable to load rides.");
    }
  }, [userContext]);

  const fetchSnc = useCallback(async () => {
    try {
      const response = await getSncBlock(userContext);
      setSncBlock(response.block ?? null);
      setSncSessions(response.sessions ?? []);
      setSncError(null);
    } catch (error) {
      setSncBlock(null);
      setSncSessions([]);
      setSncError(error instanceof Error ? error.message : "Unable to load S&C plan.");
    }
  }, [userContext]);

  const fetchStrava = useCallback(async () => {
    try {
      const status = await getStravaStatus(userContext);
      setStravaStatus(status);
      setStravaError(null);
    } catch (error) {
      setStravaStatus(null);
      setStravaError(error instanceof Error ? error.message : "Unable to load Strava status.");
    }
  }, [userContext]);

  useEffect(() => {
    const loadAll = async () => {
      setIsLoading(true);
      await Promise.all([fetchPlan(), fetchRides(), fetchSnc(), fetchStrava()]);
      setIsLoading(false);
    };

    loadAll().catch(() => setIsLoading(false));
  }, [fetchPlan, fetchRides, fetchSnc, fetchStrava]);

  const rideMetrics = useMemo(() => {
    if (rides.length === 0) {
      return {
        weekDistance: 0,
        monthDistance: 0,
        avgRpe: null as number | null,
      };
    }

    const today = new Date();
    const day = today.getDay();
    const diffToMonday = day === 0 ? -6 : 1 - day;
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() + diffToMonday);
    weekStart.setHours(0, 0, 0, 0);
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);

    const toDate = (value: string) => {
      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? null : parsed;
    };

    const weekDistance = rides.reduce((total, ride) => {
      const date = toDate(ride.ride_date);
      if (!date || date < weekStart) {
        return total;
      }
      return total + (ride.distance_km ?? 0);
    }, 0);

    const monthDistance = rides.reduce((total, ride) => {
      const date = toDate(ride.ride_date);
      if (!date || date < monthStart) {
        return total;
      }
      return total + (ride.distance_km ?? 0);
    }, 0);

    const rpeValues = rides
      .map((ride) => ride.rpe)
      .filter((value): value is number => typeof value === "number");
    const avgRpe = rpeValues.length
      ? rpeValues.reduce((sum, value) => sum + value, 0) / rpeValues.length
      : null;

    return {
      weekDistance,
      monthDistance,
      avgRpe,
    };
  }, [rides]);

  const currentWeekSessions = useMemo(() => {
    if (!sncBlock || sncSessions.length === 0) {
      return { weekIndex: 1, sessions: [] as ScSession[] };
    }

    const blockStart = new Date(sncBlock.start_date);
    const weekIndex = Number.isNaN(blockStart.getTime())
      ? 1
      : Math.min(
          sncBlock.weeks,
          Math.max(1, Math.floor((Date.now() - blockStart.getTime()) / 604800000) + 1)
        );

    return {
      weekIndex,
      sessions: sncSessions.filter((session) => session.week_no === weekIndex),
    };
  }, [sncBlock, sncSessions]);

  const formatVariance = (actual?: number | null, planned?: number | null) => {
    const actualValue = actual ?? 0;
    const plannedValue = planned ?? 0;
    const variance = actualValue - plannedValue;
    const variancePct = plannedValue !== 0 ? (variance / plannedValue) * 100 : null;
    const label = variancePct === null ? `${variance.toFixed(1)}` : `${variance.toFixed(1)} (${variancePct.toFixed(0)}%)`;
    return {
      label: variance === 0 ? "0.0" : label,
      tone: variance > 0 ? "positive" : variance < 0 ? "negative" : "neutral",
    };
  };

  const getRpeTone = (rpe?: number | null) => {
    if (!rpe) {
      return "rpe-unknown";
    }
    if (rpe <= 4) {
      return "rpe-low";
    }
    if (rpe <= 7) {
      return "rpe-medium";
    }
    return "rpe-high";
  };

  return (
    <div className="dashboard patient-dashboard">
      <header className="dashboard__header">
        <div>
          <p className="pill">Patient View</p>
          <h1>Patient Dashboard</h1>
          <p>Track your weekly plan, ride effort, and strength sessions.</p>
        </div>
      </header>

      <section className="section-card">
        <div className="section-card__header">
          <h2>Plan vs Actual</h2>
          <p>Weekly targets compared with completed rides.</p>
        </div>
        {isLoading ? (
          <p className="muted">Loading weekly summary…</p>
        ) : weeklyError ? (
          <p className="error">{weeklyError}</p>
        ) : weeklySummary.length === 0 ? (
          <p className="muted">No weekly plan or ride data yet.</p>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Week</th>
                  <th>Planned km</th>
                  <th>Actual km</th>
                  <th>KM variance</th>
                  <th>Planned hours</th>
                  <th>Actual hours</th>
                  <th>Hours variance</th>
                </tr>
              </thead>
              <tbody>
                {weeklySummary.map((week) => {
                  const kmVariance = formatVariance(week.actual_km, week.planned_km);
                  const hoursVariance = formatVariance(week.actual_hours, week.planned_hours);
                  return (
                    <tr key={week.week_start}>
                      <td>{week.week_start}</td>
                      <td>{(week.planned_km ?? 0).toFixed(1)}</td>
                      <td>{(week.actual_km ?? 0).toFixed(1)}</td>
                      <td>
                        <span className={`variance ${kmVariance.tone}`}>{kmVariance.label}</span>
                      </td>
                      <td>{(week.planned_hours ?? 0).toFixed(1)}</td>
                      <td>{(week.actual_hours ?? 0).toFixed(1)}</td>
                      <td>
                        <span className={`variance ${hoursVariance.tone}`}>{hoursVariance.label}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="dashboard-grid">
        <section className="section-card">
          <div className="section-card__header">
            <h2>My Rides</h2>
            <p>Effort, distance, and weekly progress.</p>
          </div>
          {rideError ? <p className="error">{rideError}</p> : null}
          <div className="kpi-grid">
            <div className="kpi-card">
              <span>This week</span>
              <strong>{rideMetrics.weekDistance.toFixed(1)} km</strong>
              <small>completed since Monday</small>
            </div>
            <div className="kpi-card">
              <span>This month</span>
              <strong>{rideMetrics.monthDistance.toFixed(1)} km</strong>
              <small>total distance logged</small>
            </div>
            <div className="kpi-card">
              <span>Average RPE</span>
              <strong>{rideMetrics.avgRpe ? rideMetrics.avgRpe.toFixed(1) : "—"}</strong>
              <small>perceived exertion score</small>
            </div>
          </div>
          {isLoading ? (
            <p className="muted">Loading rides…</p>
          ) : rides.length === 0 ? (
            <p className="muted">No rides logged yet.</p>
          ) : (
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Distance (km)</th>
                    <th>Duration (min)</th>
                    <th>RPE</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {rides.map((ride) => (
                    <tr key={`${ride.ride_date}-${ride.duration_min}`}>
                      <td>{ride.ride_date}</td>
                      <td>{ride.distance_km.toFixed(1)}</td>
                      <td>{ride.duration_min}</td>
                      <td>
                        <span className={`rpe-pill ${getRpeTone(ride.rpe)}`}>
                          {ride.rpe ?? "—"}
                        </span>
                      </td>
                      <td>{ride.notes ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="section-card">
          <div className="section-card__header">
            <h2>S&amp;C Plan</h2>
            <p>Strength block focus and current week sessions.</p>
          </div>
          {sncError ? <p className="error">{sncError}</p> : null}
          {isLoading ? (
            <p className="muted">Loading S&amp;C plan…</p>
          ) : !sncBlock ? (
            <p className="muted">No strength block assigned yet.</p>
          ) : (
            <>
              <div className="snc-block__summary">
                <strong>
                  Block #{sncBlock.block_id} · {sncBlock.goal}
                </strong>
                <span>
                  {sncBlock.model} · {sncBlock.weeks} weeks ·{" "}
                  {sncBlock.sessions_per_week} sessions/week
                </span>
                <span>Week {currentWeekSessions.weekIndex} focus</span>
              </div>
              {currentWeekSessions.sessions.length === 0 ? (
                <p className="muted">No sessions scheduled for this week.</p>
              ) : (
                <div className="snc-block__sessions">
                  {currentWeekSessions.sessions.map((session) => (
                    <details key={`${session.week_no}-${session.session_label}`} open>
                      <summary>
                        {session.session_label} · {session.focus}
                        {session.is_deload ? " (Deload)" : ""}
                      </summary>
                      <div className="table-wrapper">
                        <table>
                          <thead>
                            <tr>
                              <th>Exercise</th>
                              <th>Sets</th>
                              <th>Reps</th>
                              <th>Load/Pct</th>
                              <th>Notes</th>
                            </tr>
                          </thead>
                          <tbody>
                            {session.exercises.map((exercise) => (
                              <tr key={exercise.row_id}>
                                <td>{exercise.exercise_name}</td>
                                <td>{exercise.sets_target}</td>
                                <td>{exercise.reps_target}</td>
                                <td>
                                  {exercise.load_kg_target
                                    ? `${exercise.load_kg_target} kg`
                                    : exercise.pct_1rm_target
                                      ? `${exercise.pct_1rm_target}%`
                                      : "—"}
                                </td>
                                <td>{exercise.notes ?? "—"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      </div>

      <section className="section-card">
        <div className="section-card__header">
          <h2>Settings &amp; Strava</h2>
          <p>Connect your Strava account to import rides automatically.</p>
        </div>
        {stravaError ? <p className="error">{stravaError}</p> : null}
        {isLoading ? (
          <p className="muted">Loading Strava status…</p>
        ) : !stravaStatus ? (
          <p className="muted">Strava status unavailable.</p>
        ) : stravaStatus.connected ? (
          <div className="settings-grid">
            <div>
              <p className="muted">Status</p>
              <strong>Connected</strong>
            </div>
            <div>
              <p className="muted">Athlete ID</p>
              <strong>{stravaStatus.athlete_id ?? "—"}</strong>
            </div>
            <div>
              <p className="muted">Scope</p>
              <strong>{stravaStatus.scope ?? "—"}</strong>
            </div>
          </div>
        ) : (
          <div className="strava-connect">
            <p className="muted">
              Connect Strava to automatically sync rides into your log.
            </p>
            {stravaStatus.auth_url ? (
              <a className="primary" href={stravaStatus.auth_url}>
                Connect Strava
              </a>
            ) : (
              <p className="muted">Strava configuration missing.</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
};

export default PatientDashboard;
