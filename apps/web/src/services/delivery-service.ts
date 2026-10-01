import type { Delivery } from "@cluecdc/contracts";
import { api, post } from "@/lib/api";

export const deliveryKeys = {
  all: ["deliveries"] as const,
  detail: (id: string) => ["deliveries", id] as const,
  status: (id: string) => ["deliveries", id, "status"] as const,
};

export const deliveryService = {
  list: () => api<Delivery[]>("/deliveries"),
  detail: (id: string) => api<Delivery>(`/deliveries/${id}`),
  status: (id: string) =>
    api<{
      actual_state: string;
      desired_state: string;
      tasks: { id: number; state: string; worker_id: string; error?: string }[];
    }>(`/deliveries/${id}/status`),
  operate: (id: string, operation: "pause" | "resume" | "restart") =>
    post(`/deliveries/${id}/${operation}`),
  remove: (id: string) => api(`/deliveries/${id}`, { method: "DELETE" }),
};
