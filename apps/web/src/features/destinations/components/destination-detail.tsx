"use client";

import { useQuery } from "@tanstack/react-query";
import type { Connection } from "@cluecdc/contracts";
import { api } from "@/lib/api";
import { ErrorPanel, Loading } from "@/components/common";
import { ConnectionDetailPage } from "@/features/connections/components/connection-detail";
import { DestinationDetailPage } from "./database-destination-detail";

export function DestinationConnectionDetail({ id }: { id: string }) {
  const connection = useQuery({
    queryKey: ["connection", id],
    queryFn: () => api<Connection>(`/connections/${id}`),
  });
  if (connection.isPending) return <Loading />;
  if (connection.isError) return <ErrorPanel error={connection.error} />;
  return connection.data.category === "OBJECT_STORAGE" ? (
    <ConnectionDetailPage id={id} />
  ) : (
    <DestinationDetailPage id={id} />
  );
}
