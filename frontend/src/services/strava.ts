import { apiRequest } from "./apiClient";
import type { StravaStatus, UserContext } from "./types";

export const getStravaStatus = async (context: UserContext) => {
  return apiRequest<StravaStatus>("/strava/status", {
    query: {
      user_id: context.userId,
      role: context.role,
      patient_id: context.patientId,
    },
  });
};

export const connectStrava = async (
  context: UserContext,
  payload: { code: string; state: string }
) => {
  await apiRequest<{ status: string }>("/strava/connect", {
    method: "POST",
    body: {
      user_id: context.userId,
      role: context.role,
      patient_id: context.patientId,
      ...payload,
    },
  });
};
