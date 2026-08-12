import type { ComplianceItem, Fixture, LocalAIStatus, Product, ProjectDetails, QuoteLine } from "./types";

const BASE_URL = "http://127.0.0.1:8765/api";

async function parsed<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail || "The local service could not complete the request.");
  }
  return response.json() as Promise<T>;
}

export async function health() {
  return parsed<{ status: string; api_key_configured: boolean; local_ai: LocalAIStatus }>(await fetch(`${BASE_URL}/health`));
}

export async function saveApiKey(apiKey: string) {
  return parsed<{ saved: boolean }>(
    await fetch(`${BASE_URL}/settings/api-key`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    }),
  );
}

export async function extract(projectName: string, files: File[]) {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return parsed<{ project_id: string; project_name: string; fixtures: Fixture[]; warnings: string[]; analysis_engine: "local_ai" | "rules" }>(
    await fetch(`${BASE_URL}/extract?project_name=${encodeURIComponent(projectName)}`, {
      method: "POST",
      body: form,
    }),
  );
}

export async function approveFixtures(projectId: string, fixtures: Fixture[]) {
  return parsed<{ approved: number }>(
    await fetch(`${BASE_URL}/projects/${encodeURIComponent(projectId)}/fixtures/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fixtures }),
    }),
  );
}

export function evidenceUrl(path: string) {
  return `http://127.0.0.1:8765${path}`;
}

export async function searchProducts(
  fixture: Fixture,
  brand: string,
  tolerances: { lumens_percent: number; wattage_percent: number },
) {
  return parsed<{ matches: Product[]; warnings: string[] }>(
    await fetch(`${BASE_URL}/products/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fixture,
        brand,
        tolerances: { ...tolerances, dimensions_percent: 15 },
      }),
    }),
  );
}

export async function downloadQuote(
  format: "xlsx" | "pdf",
  projectName: string,
  customerName: string,
  reference: string,
  lines: QuoteLine[],
) {
  const response = await fetch(`${BASE_URL}/quote/${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_name: projectName, customer_name: customerName, reference, lines }),
  });
  if (!response.ok) throw new Error("Could not create the quotation.");
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `TECS-Lighting-Quotation.${format}`;
  link.click();
  URL.revokeObjectURL(link.href);
}

export async function downloadCompliance(
  format: "xlsx" | "pdf",
  project: ProjectDetails,
  items: ComplianceItem[],
  filename = "TECS-Technical-Compliance",
) {
  const response = await fetch(`${BASE_URL}/compliance/${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, items }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Could not create the technical sheets." }));
    throw new Error(payload.detail || "Could not create the technical sheets.");
  }
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${filename}.${format}`;
  link.click();
  URL.revokeObjectURL(link.href);
}
