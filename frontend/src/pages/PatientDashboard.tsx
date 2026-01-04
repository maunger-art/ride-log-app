import { useEffect, useState } from "react";
import { getPlan } from "../services/plan";
import { getRides } from "../services/rides";
import type { PlanEntry } from "../services/types";

const PatientDashboard = () => {
  const [planEntries, setPlanEntries] = useState<PlanEntry[]>([]);
  const [rideCount, setRideCount] = useState<number | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      const { plan } = await getPlan({
        userId: "demo-patient",
        role: "patient",
        patientId: 1,
      });
      setPlanEntries(plan);

      const rides = await getRides({
        userId: "demo-patient",
        role: "patient",
        patientId: 1,
      });
      setRideCount(rides.length);
    };

    fetchData().catch(() => {
      setPlanEntries([]);
      setRideCount(null);
    });
  }, []);

  return (
    <div className="dashboard">
      <header>
        <h1>Patient Dashboard</h1>
        <p>Daily focus, recovery signals, and your upcoming plan.</p>
      </header>
      <div className="grid">
        <div className="card">
          <h2>Upcoming plan</h2>
          {planEntries.length === 0 ? (
            <p className="muted">No plan entries yet. Connect the API to fetch them.</p>
          ) : (
            <ul>
              {planEntries.map((entry) => (
                <li key={entry.week_start}>
                  {entry.week_start}: {entry.phase ?? "Base"} · {entry.planned_km ?? 0} km
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <h2>Rides logged</h2>
          <p className="metric">{rideCount ?? "—"}</p>
          <span>rides synced to date</span>
        </div>
      </div>
    </div>
  );
};

export default PatientDashboard;
