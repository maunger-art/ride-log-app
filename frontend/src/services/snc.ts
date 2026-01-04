import { apiRequest } from "./apiClient";
import type { ScBlock, ScSession, UserContext } from "./types";

type SncBlockResponse = {
  block: ScBlock | null;
  sessions: ScSession[];
};

export const getSncBlock = async (context: UserContext) => {
  return apiRequest<SncBlockResponse>("/snc/block", {
    query: {
      user_id: context.userId,
      role: context.role,
      patient_id: context.patientId,
    },
  });
};
