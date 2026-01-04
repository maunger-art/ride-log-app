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

export type ScBlock = {
  block_id: number;
  start_date: string;
  weeks: number;
  model: string;
  deload_week: number;
  sessions_per_week: number;
  goal: string;
  notes?: string | null;
  created_at: string;
};

export type ScSessionExercise = {
  row_id: number;
  exercise_name: string;
  sets_target: number;
  reps_target: number;
  pct_1rm_target?: number | null;
  load_kg_target?: number | null;
  rpe_target?: number | null;
  rest_sec_target?: number | null;
  intent?: string | null;
  notes?: string | null;
  sets_actual?: number | null;
  reps_actual?: number | null;
  load_kg_actual?: number | null;
  completed?: boolean | null;
  actual_notes?: string | null;
};

export type ScSession = {
  week_no: number;
  week_start: string;
  focus: string;
  is_deload: boolean;
  session_label: string;
  day_hint?: string | null;
  exercises: ScSessionExercise[];
};

export type StravaStatus = {
  connected: boolean;
  auth_url?: string | null;
  athlete_id?: number | null;
  scope?: string | null;
  expires_at?: number | null;
};
