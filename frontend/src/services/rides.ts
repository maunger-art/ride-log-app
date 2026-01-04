import { apiRequest } from "./apiClient";
import type { Ride, UserContext } from "./types";

type RidesResponse = {
  rides: Ride[];
};

export const getRides = async (context: UserContext) => {
  const response = await apiRequest<RidesResponse>("/rides", {
    query: {
      user_id: context.userId,
      role: context.role,
      patient_id: context.patientId,
    },
  });

  return response.rides;
};

export const createRide = async (context: UserContext, ride: Ride) => {
  await apiRequest<{ status: string }>("/rides", {
    method: "POST",
    body: {
      user_id: context.userId,
      role: context.role,
      patient_id: context.patientId,
      ...ride,
    },
  });
};
