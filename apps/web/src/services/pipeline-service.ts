import type { Pipeline, PipelineDetail } from "@cluecdc/contracts";
import { api } from "@/lib/api";

export const pipelineKeys = {
  all: ["pipelines"] as const,
  detail: (id: string) => ["pipelines", id] as const,
};

export const pipelineService = {
  list: () => api<Pipeline[]>("/pipelines"),
  detail: (id: string) => api<PipelineDetail>(`/pipelines/${id}`),
};
