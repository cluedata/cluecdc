import { NextRequest } from "next/server";
import { expect, it, vi } from "vitest";
import { GET, PATCH } from "../src/app/api/v1/[...path]/route";
it("rejects decoded traversal before forwarding a request", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch");
  const response = await GET(
    new NextRequest("http://localhost:3000/api/v1/sources"),
    { params: Promise.resolve({ path: ["..", "..", "internal", "secrets"] }) },
  );
  expect(response.status).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});
it("forwards incident acknowledgement with PATCH and its query", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(Response.json({ status: "ACKNOWLEDGED" }));
  const response = await PATCH(
    new NextRequest(
      "http://localhost:3000/api/v1/operations/errors/id?status=ACKNOWLEDGED",
      { method: "PATCH" },
    ),
    { params: Promise.resolve({ path: ["operations", "errors", "id"] }) },
  );
  expect(response.status).toBe(200);
  expect(fetcher.mock.calls.at(-1)?.[0].toString()).toContain(
    "status=ACKNOWLEDGED",
  );
  expect(fetcher.mock.calls.at(-1)?.[1]?.method).toBe("PATCH");
});
