import { useEffect, useState } from "react";
import { getWeeklySummary } from "../services/plan";
import { getRides } from "../services/rides";
import type { WeeklySummary } from "../services/types";

const CoachDashboard = () => {
  const [weeklySummary, setWeeklySummary] = useState<WeeklySummary[]>([]);
  const [rideCount, setRideCount] = useState<number | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      const summary = await getWeeklySummary({
        userId: "demo-coach",
        role: "coach",
        patientId: 1,
      });
      setWeeklySummary(summary);

      const rides = await getRides({
        userId: "demo-coach",
        role: "coach",
        patientId: 1,
      });
      setRideCount(rides.length);
    };

    fetchData().catch(() => {
      setWeeklySummary([]);
      setRideCount(null);
    });
  }, []);

  return (
    <div className="dashboard">
      <header>
        <h1>Coach Dashboard</h1>
        <p>Overview of patient workload, compliance, and weekly trends.</p>
      </header>
      <div className="grid">
        <div className="card">
          <h2>Ride volume</h2>
          <p className="metric">{rideCount ?? "—"}</p>
          <span>rides logged this cycle</span>
        </div>
        <div className="card">
          <h2>Weekly summary</h2>
          {weeklySummary.length === 0 ? (
            <p className="muted">Connect to the API to see weekly totals.</p>
          ) : (
            <ul>
              {weeklySummary.map((week) => (
                <li key={week.week_start}>
                  {week.week_start}: {week.planned_km ?? 0} km planned, {week.actual_km ?? 0} km actual
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
};

export default CoachDashboard;
