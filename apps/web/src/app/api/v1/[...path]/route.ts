import { NextRequest } from "next/server";
import { apiInternalUrl } from "@/lib/server-config";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (
    path.some(
      (segment) => [".", ".."].includes(segment) || /[/\\]/.test(segment),
    )
  ) {
    return Response.json(
      {
        error: {
          code: "INVALID_PATH",
          message: "Invalid API path",
          details: {},
        },
      },
      { status: 400 },
    );
  }
  const url = new URL(
    `/api/v1/${path.map(encodeURIComponent).join("/")}`,
    apiInternalUrl(),
  );
  url.search = request.nextUrl.search;
  const headers = new Headers({ "Content-Type": "application/json" });
  for (const name of ["authorization", "x-correlation-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const body = ["GET", "HEAD"].includes(request.method)
      ? undefined
      : await request.text();
    if (body && body.length > 1_000_000)
      return Response.json(
        {
          error: {
            code: "REQUEST_TOO_LARGE",
            message: "Request exceeds 1 MB",
            details: {},
          },
        },
        { status: 413 },
      );
    const response = await fetch(url, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(45000),
    });
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "X-Correlation-ID": response.headers.get("X-Correlation-ID") || "",
      },
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "API_UNAVAILABLE",
          message:
            "ClueCDC API is unavailable. Check the API service and metadata database.",
          details: {},
        },
      },
      { status: 503 },
    );
  }
}
export {
  proxy as GET,
  proxy as POST,
  proxy as PUT,
  proxy as PATCH,
  proxy as DELETE,
};
