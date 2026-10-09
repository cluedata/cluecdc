"use client";
import Link from "next/link";
import type { Destination, PipelineDetail } from "@cluecdc/contracts";
import { Button } from "@cluecdc/ui";
import { Panel, QuietState } from "./operational";
import { DataFlowRail } from "./data-flow-rail";
import { useAuthorization } from "@/lib/auth";

export function DataFlow({
  pipeline,
  state,
}: {
  pipeline: PipelineDetail;
  state: string;
}) {
  const { can, role } = useAuthorization();
  const destinations = pipeline.destinations || [];
  return (
    <section className="panel flow-panel">
      <div className="section-heading">
        <div>
          <h2>Data flow</h2>
          <p className="muted">
            Independent capture and downstream delivery runtimes.
          </p>
        </div>
        {can("deliveries.write") && (
          <Button asChild variant="outline">
            <Link href={`/deliveries/new?pipeline_id=${pipeline.id}`}>
              + Add delivery
            </Link>
          </Button>
        )}
      </div>
      <div className="detail-flow-rails">
        {(destinations.length ? destinations : [null]).map((delivery) => (
          <DataFlowRail
            key={delivery?.id || "unconfigured"}
            label={`${pipeline.name} data flow`}
            throughput={pipeline.metrics.throughput}
            lag={pipeline.metrics.cdc_lag}
            stages={[
              {
                label: "Source",
                name: pipeline.source.name,
                detail: `${pipeline.source.type === "mysql" ? "MySQL" : "PostgreSQL"} / ${pipeline.source.database_name}`,
                status: pipeline.source.status,
                href: can("sources.read")
                  ? `/sources/${pipeline.source_id}`
                  : undefined,
              },
              {
                label: "Capture",
                name: pipeline.name,
                detail: `Debezium ${pipeline.source.type === "mysql" ? "MySQL" : "PostgreSQL"} Connector`,
                status: state,
                href: `/pipelines/${pipeline.id}?tab=Configuration`,
              },
              {
                label: "Stream",
                name: pipeline.kafka_cluster.name,
                detail: `Apache Kafka · ${pipeline.tables.length} topics`,
                status: pipeline.kafka_cluster.status,
                href:
                  role === "Admin"
                    ? `/kafka/topics?cluster=${pipeline.kafka_cluster_id}`
                    : undefined,
              },
              {
                label: "Delivery",
                name: delivery?.name || "No delivery",
                detail: delivery?.connector
                  ? delivery.connector.connector_class.split(".").at(-1)
                  : "Add a Kafka Connect sink",
                status: delivery?.actual_state || "NOT_CONFIGURED",
                href: delivery
                  ? `/deliveries/${delivery.id}`
                  : can("deliveries.write")
                    ? `/deliveries/new?pipeline_id=${pipeline.id}`
                    : undefined,
              },
              {
                label: "Destination",
                name: delivery?.destination.name || "No destination",
                detail: delivery
                  ? `${delivery.destination.type} / ${delivery.destination.database_name}`
                  : "Select an endpoint",
                status: delivery?.destination.status || "NOT_CONFIGURED",
                href: delivery
                  ? can("destinations.read")
                    ? `/destinations/${delivery.destination_id}`
                    : undefined
                  : can("deliveries.write")
                    ? `/deliveries/new?pipeline_id=${pipeline.id}`
                    : undefined,
              },
            ]}
          />
        ))}
      </div>
    </section>
  );
}

export function DestinationFlow({
  target,
  state,
}: {
  target: Destination;
  state: string;
}) {
  return (
    <Panel
      title="Delivery data flow"
      description="Kafka topics, independent sink connectors and the selected database target"
    >
      {target.deliveries.length ? (
        <div className="delivery-flow-list">
          {target.deliveries.map((d) => (
            <DataFlowRail
              key={d.id}
              label={`${d.name} delivery flow`}
              stages={[
                {
                  label: "Kafka topics",
                  name: `${d.topic_mapping_json.length} selected topics`,
                  detail: d.topic_mapping_json.map((m) => m.topic).join(", "),
                  status: "UNKNOWN",
                  href: `/pipelines/${d.pipeline_id}`,
                },
                {
                  label: "Sink connector",
                  name: d.connector?.name || d.name,
                  detail: d.connector
                    ? `${target.type === "mysql" ? "MySQL" : "PostgreSQL"} sink`
                    : "Connector not deployed",
                  status: d.connector ? d.actual_state : "NOT_DEPLOYED",
                  href: `/deliveries/${d.id}`,
                },
                {
                  label: "Destination",
                  name: target.name,
                  detail: `${target.type === "mysql" ? "MySQL" : "PostgreSQL"} / ${target.environment}`,
                  status: state,
                },
              ]}
            />
          ))}
        </div>
      ) : (
        <QuietState
          title="No delivery flow configured"
          description="Add a delivery to connect captured topics to this destination."
        />
      )}
    </Panel>
  );
}
