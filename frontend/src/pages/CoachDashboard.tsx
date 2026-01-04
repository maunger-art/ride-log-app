import type { ChangeEvent, FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getWeeklySummary, upsertPlan } from "../services/plan";
import { getRides } from "../services/rides";
import { getSncBlock } from "../services/snc";
import type { PlanEntry, Ride, ScSession, WeeklySummary } from "../services/types";

type LoadState = {
  isLoading: boolean;
  error: string | null;
};

type UploadState = {
  isLoading: boolean;
  error: string | null;
  successMessage: string | null;
};

type PlanFormState = {
  week_start: string;
  planned_km: string;
  planned_hours: string;
  phase: string;
  notes: string;
};

const userContext = {
  userId: "demo-coach",
  role: "coach",
  patientId: 1,
};

const CoachDashboard = () => {
  const [weeklySummary, setWeeklySummary] = useState<WeeklySummary[]>([]);
  const [weeklyState, setWeeklyState] = useState<LoadState>({
    isLoading: true,
    error: null,
  });
  const [rides, setRides] = useState<Ride[]>([]);
  const [rideState, setRideState] = useState<LoadState>({
    isLoading: true,
    error: null,
  });
  const [sncSessions, setSncSessions] = useState<ScSession[]>([]);
  const [sncBlockSummary, setSncBlockSummary] = useState<string | null>(null);
  const [sncState, setSncState] = useState<LoadState>({
    isLoading: true,
    error: null,
  });

  const [planForm, setPlanForm] = useState<PlanFormState>({
    week_start: "",
    planned_km: "",
    planned_hours: "",
    phase: "",
    notes: "",
  });
  const [planUploadState, setPlanUploadState] = useState<UploadState>({
    isLoading: false,
    error: null,
    successMessage: null,
  });
  const [csvUploadState, setCsvUploadState] = useState<UploadState>({
    isLoading: false,
    error: null,
    successMessage: null,
  });

  const fetchWeeklySummary = useCallback(async () => {
    setWeeklyState({ isLoading: true, error: null });
    try {
      const summary = await getWeeklySummary(userContext);
      setWeeklySummary(summary);
      setWeeklyState({ isLoading: false, error: null });
    } catch (error) {
      setWeeklySummary([]);
      setWeeklyState({
        isLoading: false,
        error: error instanceof Error ? error.message : "Unable to load weekly summary.",
      });
    }
  }, []);

  const fetchRides = useCallback(async () => {
    setRideState({ isLoading: true, error: null });
    try {
      const rideData = await getRides(userContext);
      setRides(rideData);
      setRideState({ isLoading: false, error: null });
    } catch (error) {
      setRides([]);
      setRideState({
        isLoading: false,
        error: error instanceof Error ? error.message : "Unable to load rides.",
      });
    }
  }, []);

  const fetchSncBlock = useCallback(async () => {
    setSncState({ isLoading: true, error: null });
    try {
      const response = await getSncBlock(userContext);
      setSncSessions(response.sessions ?? []);
      if (response.block) {
        setSncBlockSummary(
          `${response.block.goal} · ${response.block.model} · ${response.block.weeks} weeks`
        );
      } else {
        setSncBlockSummary(null);
      }
      setSncState({ isLoading: false, error: null });
    } catch (error) {
      setSncSessions([]);
      setSncBlockSummary(null);
      setSncState({
        isLoading: false,
        error: error instanceof Error ? error.message : "Unable to load strength block.",
      });
    }
  }, []);

  useEffect(() => {
    fetchWeeklySummary();
    fetchRides();
    fetchSncBlock();
  }, [fetchRides, fetchSncBlock, fetchWeeklySummary]);

  const overviewTotals = useMemo(() => {
    const plannedKm = weeklySummary.reduce(
      (total, week) => total + (week.planned_km ?? 0),
      0
    );
    const actualKm = weeklySummary.reduce(
      (total, week) => total + (week.actual_km ?? 0),
      0
    );
    const plannedHours = weeklySummary.reduce(
      (total, week) => total + (week.planned_hours ?? 0),
      0
    );
    const actualHours = weeklySummary.reduce(
      (total, week) => total + (week.actual_hours ?? 0),
      0
    );
    return {
      plannedKm,
      actualKm,
      plannedHours,
      actualHours,
      rideCount: rides.length,
      sncSessions: sncSessions.length,
    };
  }, [rides.length, sncSessions.length, weeklySummary]);

  const handlePlanChange = (field: keyof PlanFormState, value: string) => {
    setPlanForm((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const handlePlanSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPlanUploadState({ isLoading: true, error: null, successMessage: null });
    try {
      const plannedKmValue =
        planForm.planned_km.trim() === "" ? undefined : Number(planForm.planned_km);
      const plannedHoursValue =
        planForm.planned_hours.trim() === "" ? undefined : Number(planForm.planned_hours);
      await upsertPlan(userContext, {
        week_start: planForm.week_start,
        planned_km:
          plannedKmValue === undefined || Number.isNaN(plannedKmValue) ? undefined : plannedKmValue,
        planned_hours:
          plannedHoursValue === undefined || Number.isNaN(plannedHoursValue)
            ? undefined
            : plannedHoursValue,
        phase: planForm.phase || undefined,
        notes: planForm.notes || undefined,
      });
      setPlanUploadState({
        isLoading: false,
        error: null,
        successMessage: "Plan entry saved.",
      });
      setPlanForm({
        week_start: "",
        planned_km: "",
        planned_hours: "",
        phase: "",
        notes: "",
      });
      await fetchWeeklySummary();
    } catch (error) {
      setPlanUploadState({
        isLoading: false,
        error: error instanceof Error ? error.message : "Unable to save plan entry.",
        successMessage: null,
      });
    }
  };

  const parseCsvToPlanEntries = (content: string): PlanEntry[] => {
    const lines = content.split(/\r?\n/).filter((line) => line.trim() !== "");
    if (lines.length === 0) {
      return [];
    }

    const headers = lines[0].split(",").map((header) => header.trim());
    const entries = lines.slice(1).map((line) => {
      const values = line.split(",");
      const record: Record<string, string> = {};
      headers.forEach((header, index) => {
        record[header] = (values[index] ?? "").trim().replace(/^"|"$/g, "");
      });

      const plannedKm =
        record.planned_km && record.planned_km.trim() !== ""
          ? Number(record.planned_km)
          : undefined;
      const plannedHours =
        record.planned_hours && record.planned_hours.trim() !== ""
          ? Number(record.planned_hours)
          : undefined;

      return {
        week_start: record.week_start,
        planned_km: plannedKm === undefined || Number.isNaN(plannedKm) ? undefined : plannedKm,
        planned_hours:
          plannedHours === undefined || Number.isNaN(plannedHours) ? undefined : plannedHours,
        phase: record.phase || undefined,
        notes: record.notes || undefined,
      };
    });

    return entries.filter((entry) => entry.week_start);
  };

  const handleCsvUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setCsvUploadState({ isLoading: true, error: null, successMessage: null });
    try {
      const content = await file.text();
      const entries = parseCsvToPlanEntries(content);
      if (entries.length === 0) {
        throw new Error("No plan rows found. Make sure week_start is included.");
      }

      await Promise.all(entries.map((entry) => upsertPlan(userContext, entry)));
      setCsvUploadState({
        isLoading: false,
        error: null,
        successMessage: `Uploaded ${entries.length} plan rows.`,
      });
      await fetchWeeklySummary();
    } catch (error) {
      setCsvUploadState({
        isLoading: false,
        error: error instanceof Error ? error.message : "Unable to upload CSV.",
        successMessage: null,
      });
    } finally {
      event.target.value = "";
    }
  };

  const overviewError = weeklyState.error || rideState.error || sncState.error;
  const overviewLoading = weeklyState.isLoading || rideState.isLoading || sncState.isLoading;

  return (
    <div className="dashboard coach-dashboard">
      <header className="dashboard__header">
        <div>
          <p className="pill">Coach View</p>
          <h1>Coach Dashboard</h1>
          <p>Overview of patient workload, compliance, and weekly trends.</p>
        </div>
      </header>

      <section className="section-card">
        <div className="section-card__header">
          <h2>Overview</h2>
          <p>Totals calculated from plan, ride, and S&amp;C data feeds.</p>
        </div>
        {overviewLoading ? (
          <p className="muted">Loading overview metrics…</p>
        ) : overviewError ? (
          <p className="error">{overviewError}</p>
        ) : (
          <div className="kpi-grid">
            <div className="kpi-card">
              <span>Planned volume</span>
              <strong>{overviewTotals.plannedKm.toFixed(1)} km</strong>
              <small>{overviewTotals.plannedHours.toFixed(1)} hours scheduled</small>
            </div>
            <div className="kpi-card">
              <span>Actual volume</span>
              <strong>{overviewTotals.actualKm.toFixed(1)} km</strong>
              <small>{overviewTotals.actualHours.toFixed(1)} hours completed</small>
            </div>
            <div className="kpi-card">
              <span>Ride compliance</span>
              <strong>{overviewTotals.rideCount}</strong>
              <small>rides logged in the period</small>
            </div>
            <div className="kpi-card">
              <span>S&amp;C sessions</span>
              <strong>{overviewTotals.sncSessions}</strong>
              <small>sessions in the current block</small>
            </div>
          </div>
        )}
      </section>

      <div className="dashboard-grid">
        <section className="section-card">
          <div className="section-card__header">
            <h2>Ride Log</h2>
            <p>Latest rides synced from the athlete feed.</p>
          </div>
          {rideState.isLoading ? (
            <p className="muted">Loading ride log…</p>
          ) : rideState.error ? (
            <p className="error">{rideState.error}</p>
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
                      <td>{ride.rpe ?? "—"}</td>
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
            <h2>Upload Plan</h2>
            <p>Manually add weekly targets or upload a CSV plan file.</p>
          </div>
          <form className="plan-form" onSubmit={handlePlanSubmit}>
            <label>
              Week start (Monday)
              <input
                type="date"
                value={planForm.week_start}
                onChange={(event) => handlePlanChange("week_start", event.target.value)}
                required
              />
            </label>
            <div className="form-row">
              <label>
                Planned km
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={planForm.planned_km ?? ""}
                  onChange={(event) => handlePlanChange("planned_km", event.target.value)}
                />
              </label>
              <label>
                Planned hours
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={planForm.planned_hours ?? ""}
                  onChange={(event) => handlePlanChange("planned_hours", event.target.value)}
                />
              </label>
            </div>
            <label>
              Phase
              <input
                type="text"
                value={planForm.phase ?? ""}
                onChange={(event) => handlePlanChange("phase", event.target.value)}
                placeholder="Base, Build, Peak"
              />
            </label>
            <label>
              Notes
              <textarea
                rows={3}
                value={planForm.notes ?? ""}
                onChange={(event) => handlePlanChange("notes", event.target.value)}
                placeholder="Optional notes for the athlete"
              />
            </label>
            <button className="primary" type="submit" disabled={planUploadState.isLoading}>
              {planUploadState.isLoading ? "Saving…" : "Save plan entry"}
            </button>
            {planUploadState.error ? <p className="error">{planUploadState.error}</p> : null}
            {planUploadState.successMessage ? (
              <p className="success">{planUploadState.successMessage}</p>
            ) : null}
          </form>

          <div className="divider" />

          <div className="csv-upload">
            <h3>CSV uploader</h3>
            <p className="muted">
              Expected columns: week_start, planned_km, planned_hours, phase, notes.
            </p>
            <input type="file" accept=".csv" onChange={handleCsvUpload} />
            {csvUploadState.isLoading ? <p className="muted">Uploading CSV…</p> : null}
            {csvUploadState.error ? <p className="error">{csvUploadState.error}</p> : null}
            {csvUploadState.successMessage ? (
              <p className="success">{csvUploadState.successMessage}</p>
            ) : null}
          </div>
        </section>
      </div>

      <section className="section-card">
        <div className="section-card__header">
          <h2>S&amp;C Blocks</h2>
          <p>Latest strength and conditioning block plus session templates.</p>
        </div>
        {sncState.isLoading ? (
          <p className="muted">Loading strength block…</p>
        ) : sncState.error ? (
          <p className="error">{sncState.error}</p>
        ) : sncSessions.length === 0 ? (
          <p className="muted">No S&amp;C block found for this athlete.</p>
        ) : (
          <div className="snc-block">
            <div className="snc-block__summary">
              <strong>Current block</strong>
              <span>{sncBlockSummary}</span>
              <span>{sncSessions.length} total sessions</span>
            </div>
            <div className="snc-block__sessions">
              {sncSessions.map((session) => (
                <details key={`${session.week_no}-${session.session_label}`}>
                  <summary>
                    Week {session.week_no} · {session.session_label} · {session.focus}
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
          </div>
        )}
      </section>
    </div>
  );
};

export default CoachDashboard;
