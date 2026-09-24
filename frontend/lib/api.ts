export type Readiness = {
  status: "ready";
  service: "course-rag-api";
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function fetchReadiness(fetcher: typeof fetch = fetch): Promise<Readiness> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/readiness`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Readiness request failed with status ${response.status}`);
  }
  const body: unknown = await response.json();
  if (
    typeof body !== "object" ||
    body === null ||
    !("status" in body) ||
    body.status !== "ready" ||
    !("service" in body) ||
    body.service !== "course-rag-api"
  ) {
    throw new Error("Readiness response has an invalid shape");
  }
  return { status: body.status, service: body.service };
}
