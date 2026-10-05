import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/pipelines", () => ({
  PipelinesPage: () => null,
  PipelineDetailPage: () => null,
}));
vi.mock("@/components/deliveries", () => ({
  DeliveriesPage: () => null,
  DeliveryCreatePage: () => null,
  DeliveryDetailPage: () => null,
}));
vi.mock("@/components/consumer-groups", () => ({
  ConsumerGroupsPage: () => null,
}));

import Pipelines from "../src/app/pipelines/page";
import PipelineDetail from "../src/app/pipelines/[id]/page";
import Deliveries from "../src/app/deliveries/page";
import DeliveryCreate from "../src/app/deliveries/new/page";
import DeliveryDetail from "../src/app/deliveries/[id]/page";
import ConsumerGroups from "../src/app/kafka/consumer-groups/page";

describe("App Router pages", () => {
  it("exposes route entry points", () => {
    expect(typeof Pipelines).toBe("function");
    expect(typeof PipelineDetail).toBe("function");
    expect(typeof Deliveries).toBe("function");
    expect(typeof DeliveryCreate).toBe("function");
    expect(typeof DeliveryDetail).toBe("function");
    expect(typeof ConsumerGroups).toBe("function");
  });
});
