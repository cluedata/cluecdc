const DEVELOPMENT_API_URL = "http://localhost:8000";

export function apiInternalUrl(): string {
  const configured = process.env.API_INTERNAL_URL;
  if (configured) {
    const parsed = new URL(configured);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      throw new Error("API_INTERNAL_URL must use http or https");
    }
    return parsed.origin;
  }
  if (process.env.NODE_ENV === "production") {
    throw new Error("API_INTERNAL_URL is required in production");
  }
  return DEVELOPMENT_API_URL;
}
