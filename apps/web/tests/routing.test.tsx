import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let path = "/pipelines";
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => path,
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace }),
}));
vi.mock("../src/components/sources", () => ({
  SourceCreatePage: () => null,
  SourceDetailPage: () => null,
  SourcesPage: () => null,
}));
vi.mock("../src/components/wizard", () => ({ PipelineWizard: () => null }));
vi.mock("../src/components/pipelines", () => ({
  PipelinesPage: () => <div>Pipelines route</div>,
  PipelineDetailPage: () => <div>Pipeline detail route</div>,
}));
vi.mock("../src/components/destinations", () => ({
  DestinationsPage: () => null,
  DestinationWizard: () => null,
  DestinationDetailPage: () => null,
}));
vi.mock("../src/components/deliveries", () => ({
  DeliveriesPage: () => <div>Deliveries route</div>,
  DeliveryCreatePage: () => <div>Create delivery route</div>,
  DeliveryDetailPage: () => <div>Delivery detail route</div>,
}));
vi.mock("../src/components/infrastructure", () => ({
  ClustersPage: () => null,
  ConnectorsPage: () => null,
}));
vi.mock("../src/components/consumer-groups", () => ({
  ConsumerGroupsPage: () => <div>Consumer groups route</div>,
}));
vi.mock("../src/components/stream", () => ({
  EventsPage: () => null,
  TopicsPage: () => null,
}));
vi.mock("../src/components/operations", () => ({
  AuditPage: () => null,
  ErrorsPage: () => null,
  OverviewPage: () => null,
  SchemasPage: () => null,
  SettingsPage: () => null,
}));
vi.mock("../src/components/monitoring", () => ({ MonitoringPage: () => null }));
vi.mock("../src/components/alerts", () => ({
  AlertDetailPage: () => null,
  AlertRulesPage: () => null,
  AlertsPage: () => null,
  NotificationChannelsPage: () => null,
}));

import { ControlPlane } from "../src/components/control-plane";

describe("pipeline-centric routes", () => {
  beforeEach(() => replace.mockReset());

  it.each([
    ["/pipelines", "Pipelines route"],
    ["/pipelines/p1", "Pipeline detail route"],
    ["/deliveries", "Deliveries route"],
    ["/deliveries/new", "Create delivery route"],
    ["/deliveries/d1", "Delivery detail route"],
    ["/kafka/consumer-groups", "Consumer groups route"],
  ])("renders %s", (route, expected) => {
    path = route;
    render(<ControlPlane />);
    expect(screen.getByText(expected)).toBeInTheDocument();
  });

  it("redirects the legacy nested delivery route", async () => {
    path = "/destinations/target/deliveries/delivery";
    render(<ControlPlane />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/deliveries/delivery"),
    );
  });
});
