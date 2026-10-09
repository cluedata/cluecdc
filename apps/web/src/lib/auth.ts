"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type Session = {
  actor: string;
  email: string;
  role: "Admin" | "Ops" | "Viewer";
  environment: string;
  auth_mode: string;
  permissions: string[];
};

export function hasPermission(
  permissions: string[] | undefined,
  permission: string,
) {
  return !!permissions?.includes("*") || !!permissions?.includes(permission);
}

export function requiredPermissionForPath(path: string): string | null {
  if (path === "/login" || path.startsWith("/invite/")) return null;
  if (path === "/" || path.startsWith("/overview")) return "overview.read";
  if (path.startsWith("/pipelines/new")) return "pipelines.write";
  if (path.startsWith("/pipelines")) return "pipelines.read";
  if (path.startsWith("/deliveries/new")) return "deliveries.write";
  if (path.startsWith("/deliveries")) return "deliveries.read";
  if (path.startsWith("/sources/new")) return "sources.write";
  if (path.startsWith("/sources")) return "sources.read";
  if (path.startsWith("/destinations/new")) return "destinations.write";
  if (path.startsWith("/destinations")) return "destinations.read";
  if (path.startsWith("/connections/new") || path.endsWith("/edit")) {
    return "destinations.write";
  }
  if (path.startsWith("/connections")) return "destinations.read";
  return "*";
}

export function useAuthorization() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => api<Session>("/session"),
    staleTime: 30_000,
  });
  const permissions = session.data?.permissions || [];
  return {
    session,
    role: session.data?.role,
    can: (permission: string) => hasPermission(permissions, permission),
  };
}
