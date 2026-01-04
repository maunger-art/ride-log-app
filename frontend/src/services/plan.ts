import { apiRequest } from "./apiClient";
import type { PlanEntry, UserContext, WeeklySummary } from "./types";

type PlanResponse = {
  plan: PlanEntry[];
  weekly_summary: WeeklySummary[];
};

export const getPlan = async (context: UserContext) => {
  return apiRequest<PlanResponse>("/plan", {
    query: {
      user_id: context.userId,
      role: context.role,
      patient_id: context.patientId,
    },
  });
};

export const getWeeklySummary = async (context: UserContext) => {
  const response = await getPlan(context);
  return response.weekly_summary;
};

export const upsertPlan = async (context: UserContext, entry: PlanEntry) => {
  await apiRequest<{ status: string }>("/plan", {
    method: "POST",
    body: {
      user_id: context.userId,
      role: context.role,
      patient_id: context.patientId,
      ...entry,
    },
  });
};
