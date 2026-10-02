import type {
  Delivery,
  DeliveryConfiguration,
  Pipeline,
  TopicMapping,
} from "@cluecdc/contracts";
import { post } from "@/lib/api";

export type PipelineCreateInput = Record<string, unknown> & {
  name: string;
  source_id: string;
  kafka_cluster_id: string;
  connect_cluster_id: string;
  tables: { schema_name: string; table_name: string }[];
};

export type DeliveryCreateInput = DeliveryConfiguration & {
  mappings: TopicMapping[];
};

export type PipelineCreationStep =
  | "capture_validated"
  | "pipeline_saved"
  | "capture_deployed"
  | "delivery_validated"
  | "delivery_deployed";

export class PipelineCreationError extends Error {
  constructor(
    message: string,
    readonly completed: PipelineCreationStep[],
    readonly pipeline: Pipeline | null,
    readonly failedStage: "capture" | "delivery",
    readonly cause: unknown,
  ) {
    super(message);
    this.name = "PipelineCreationError";
  }
}

export async function createPipelineFlow(
  capture: PipelineCreateInput,
  destinationId: string,
  delivery: Omit<DeliveryCreateInput, "pipeline_id">,
): Promise<{
  pipeline: Pipeline;
  delivery: Delivery;
  completed: PipelineCreationStep[];
}> {
  const completed: PipelineCreationStep[] = [];
  let pipeline: Pipeline | null = null;
  try {
    await post("/pipelines/preview", capture);
    completed.push("capture_validated");
    pipeline = await post<Pipeline>("/pipelines", capture);
    completed.push("pipeline_saved");
    await post(`/pipelines/${pipeline.id}/deploy`);
    completed.push("capture_deployed");
  } catch (error) {
    throw new PipelineCreationError(
      "Capture setup failed",
      completed,
      pipeline,
      "capture",
      error,
    );
  }

  const payload = { ...delivery, pipeline_id: pipeline.id };
  try {
    await post(`/pipelines/${pipeline.id}/prepare-topics`);
    await post(`/destinations/${destinationId}/preview`, payload);
    completed.push("delivery_validated");
    const created = await post<Delivery>(
      `/destinations/${destinationId}/deploy`,
      payload,
    );
    completed.push("delivery_deployed");
    return { pipeline, delivery: created, completed };
  } catch (error) {
    throw new PipelineCreationError(
      "Capture connector created successfully. Delivery creation failed.",
      completed,
      pipeline,
      "delivery",
      error,
    );
  }
}

export async function retryPipelineDelivery(
  pipelineId: string,
  destinationId: string,
  delivery: Omit<DeliveryCreateInput, "pipeline_id">,
): Promise<Delivery> {
  const payload = { ...delivery, pipeline_id: pipelineId };
  await post(`/pipelines/${pipelineId}/prepare-topics`);
  await post(`/destinations/${destinationId}/preview`, payload);
  return post<Delivery>(`/destinations/${destinationId}/deploy`, payload);
}
