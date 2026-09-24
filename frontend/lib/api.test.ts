import { describe, expect, it, vi } from "vitest";

import { fetchReadiness } from "./api";

describe("fetchReadiness", () => {
  it("requests the readiness endpoint without caching", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready", service: "course-rag-api" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await fetchReadiness(fetcher);

    expect(fetcher).toHaveBeenCalledWith("http://localhost:8000/api/v1/readiness", {
      cache: "no-store",
    });
  });

  it("returns a valid readiness response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready", service: "course-rag-api" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchReadiness(fetcher)).resolves.toEqual({
      status: "ready",
      service: "course-rag-api",
    });
  });

  it("rejects a malformed successful response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready", service: "other" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchReadiness(fetcher)).rejects.toThrow(
      "Readiness response has an invalid shape",
    );
  });

  it("rejects a non-OK response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 503 }));

    await expect(fetchReadiness(fetcher)).rejects.toThrow(
      "Readiness request failed with status 503",
    );
  });
});
