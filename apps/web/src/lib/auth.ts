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
    can: (permission: string) =>
      permissions.includes("*") || permissions.includes(permission),
  };
}
