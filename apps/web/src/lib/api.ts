export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public details: unknown,
    public correlationId: string,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  const data = response.status === 204 ? undefined : await response.json();
  if (!response.ok)
    throw new ApiError(
      data.error?.code || "REQUEST_FAILED",
      data.error?.message || "Request failed",
      data.error?.details,
      response.headers.get("X-Correlation-ID") || "",
    );
  return data as T;
}
export function post<T>(path: string, data?: unknown) {
  return api<T>(path, {
    method: "POST",
    body: data ? JSON.stringify(data) : undefined,
  });
}
export function date(value: string | number | null | undefined) {
  return value === null || value === undefined || value === ""
    ? "Never"
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value));
}
export function relativeTime(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "")
    return "Unavailable";
  const seconds = Math.max(
    0,
    Math.floor((Date.now() - new Date(value).getTime()) / 1000),
  );
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
export function number(value: number | null | undefined) {
  return value === null || value === undefined
    ? "Unavailable"
    : value.toLocaleString();
}
