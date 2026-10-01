"use client";
import { Suspense, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Loading, Empty } from "./common";
import { SourceCreatePage, SourceDetailPage } from "./sources";
import { PipelineWizard } from "./wizard";
import { PipelineDetailPage, PipelinesPage } from "./pipelines";
import { DestinationWizard, DestinationDetailPage } from "./destinations";
import { ClustersPage, ConnectorsPage } from "./infrastructure";
import { ConsumerGroupsPage } from "./consumer-groups";
import { EventsPage, TopicsPage } from "./stream";
import {
  AuditPage,
  ErrorsPage,
  OverviewPage,
  SchemasPage,
  SettingsPage,
} from "./operations";
import { MonitoringPage } from "./monitoring";
import {
  DeliveriesPage,
  DeliveryCreatePage,
  DeliveryDetailPage,
} from "./deliveries";
import {
  ConnectionDetailPage,
  ConnectionEditPage,
  ConnectionsPage,
  ConnectionWizard,
} from "./connections";
import {
  AlertDetailPage,
  AlertRulesPage,
  AlertsPage,
  NotificationChannelsPage,
} from "./alerts";
function LegacyDeliveryRedirect({ id }: { id: string }) {
  const router = useRouter();
  useEffect(() => router.replace(`/deliveries/${id}`), [id, router]);
  return <Loading />;
}
function Routes() {
  const path = usePathname();
  const params = useSearchParams();
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (path === "/" || path === "/overview") return <OverviewPage />;
  if (path === "/sources/new") return <SourceCreatePage />;
  if (path === "/sources") return <ConnectionsPage capability="SOURCE" />;
  if (parts[0] === "sources" && parts[1])
    return <SourceDetailPage key={parts[1]} id={parts[1]} />;
  if (path === "/connections/new") return <ConnectionWizard />;
  if (path === "/connections") return <ConnectionsPage />;
  if (parts[0] === "connections" && parts[1] && parts[2] === "edit")
    return <ConnectionEditPage key={parts[1]} id={parts[1]} />;
  if (parts[0] === "connections" && parts[1])
    return <ConnectionDetailPage key={parts[1]} id={parts[1]} />;
  if (path === "/pipelines/new") return <PipelineWizard />;
  if (path === "/pipelines") return <PipelinesPage />;
  if (path === "/deliveries/new") return <DeliveryCreatePage />;
  if (path === "/deliveries") return <DeliveriesPage />;
  if (parts[0] === "deliveries" && parts[1])
    return <DeliveryDetailPage key={parts[1]} id={parts[1]} />;
  if (path === "/destinations")
    return <ConnectionsPage capability="DESTINATION" />;
  if (path === "/destinations/new") return <DestinationWizard />;
  if (
    parts[0] === "destinations" &&
    parts[1] &&
    parts[2] === "deliveries" &&
    parts[3]
  )
    return <LegacyDeliveryRedirect id={parts[3]} />;
  if (parts[0] === "destinations" && parts[1])
    return <DestinationDetailPage key={parts[1]} id={parts[1]} />;
  if (parts[0] === "pipelines" && parts[1])
    return <PipelineDetailPage key={parts[1]} id={parts[1]} />;
  if (path === "/kafka/clusters") return <ClustersPage kind="kafka" />;
  if (path === "/connect/clusters" || path === "/kafka/connect-clusters")
    return <ClustersPage kind="connect" />;
  if (path === "/connect/connectors") return <ConnectorsPage />;
  if (parts[0] === "kafka" && parts[1] === "topics")
    return (
      <TopicsPage
        key={path}
        name={parts[2]}
        initialCluster={params.get("cluster") || ""}
      />
    );
  if (path === "/events") return <EventsPage />;
  if (parts[0] === "data" && parts[1] === "schemas")
    return (
      <SchemasPage sourceId={parts[2]} schema={parts[3]} table={parts[4]} />
    );
  if (path === "/monitoring") return <MonitoringPage />;
  if (path === "/alerts") return <AlertsPage />;
  if (path === "/alerts/rules") return <AlertRulesPage />;
  if (path === "/alerts/channels") return <NotificationChannelsPage />;
  if (parts[0] === "alerts" && parts[1])
    return <AlertDetailPage key={parts[1]} id={parts[1]} />;
  if (path === "/operations/errors") return <ErrorsPage />;
  if (path === "/logs") return <ErrorsPage />;
  if (path === "/kafka/consumer-groups") return <ConsumerGroupsPage />;
  if (path === "/operations/audit") return <AuditPage />;
  if (path === "/settings") return <SettingsPage />;
  return (
    <Empty
      title="Page not found"
      description="Choose a page from the navigation."
      href="/"
      action="Open overview"
    />
  );
}
export function ControlPlane() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes />
    </Suspense>
  );
}
