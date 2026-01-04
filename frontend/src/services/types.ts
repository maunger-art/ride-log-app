export type UserContext = {
  userId: string;
  role: string;
  patientId: number;
};

export type Ride = {
  ride_date: string;
  distance_km: number;
  duration_min: number;
  rpe?: number | null;
  notes?: string | null;
};

export type PlanEntry = {
  week_start: string;
  planned_km?: number | null;
  planned_hours?: number | null;
  phase?: string | null;
  notes?: string | null;
};

export type WeeklySummary = {
  week_start: string;
  planned_km?: number | null;
  planned_hours?: number | null;
  actual_km?: number | null;
  actual_hours?: number | null;
};
