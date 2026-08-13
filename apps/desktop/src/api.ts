import type { CatalogBrowseResponse, CatalogStatus, ComplianceItem, Currency, Fixture, LocalAIStatus, ProductSearchResponse, ProjectDetails, QuoteLine, TeamCatalogStatus } from "./types";
import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-shell";

const BASE_URL = "http://127.0.0.1:8765/api";

async function parsed<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail || "The local service could not complete the request.");
  }
  return response.json() as Promise<T>;
}

export async function health() {
  return parsed<{ status: string; engine_version?: string; catalog_api?: boolean; api_key_configured: boolean; local_ai: LocalAIStatus }>(await fetch(`${BASE_URL}/health`));
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

export async function removeApiKey() {
  return parsed<{ removed: boolean; api_key_configured: boolean }>(
    await fetch(`${BASE_URL}/settings/api-key`, { method: "DELETE" }),
  );
}

export async function getExportFolder() {
  return parsed<{ path: string }>(await fetch(`${BASE_URL}/settings/export-folder`));
}

export async function chooseExportFolder() {
  return parsed<{ selected: boolean; path: string }>(
    await fetch(`${BASE_URL}/settings/export-folder/choose`, { method: "POST" }),
  );
}

export async function teamCatalogStatus() {
  return parsed<TeamCatalogStatus>(await fetch(`${BASE_URL}/settings/team-catalog`));
}

export async function syncTeamCatalog() {
  return parsed<{ started: boolean; uploaded: number; downloaded: number }>(
    await fetch(`${BASE_URL}/catalog/team-sync`, { method: "POST" }),
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
  refresh = false,
) {
  return parsed<ProductSearchResponse>(
    await fetch(`${BASE_URL}/products/search?refresh=${refresh}`, {
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

export async function catalogStatus() {
  return parsed<CatalogStatus>(await fetch(`${BASE_URL}/catalog/status`));
}

export async function browseCatalog(
  fixture: Fixture,
  brand: string,
  tolerances: { lumens_percent: number; wattage_percent: number },
) {
  return parsed<CatalogBrowseResponse>(await fetch(`${BASE_URL}/catalog/browse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fixture,
      brand,
      tolerances: { ...tolerances, dimensions_percent: 15 },
    }),
  }));
}

export async function catalogSearchStatus(fixture: Fixture, brand: string) {
  return parsed<{
    refreshing: boolean;
    status: string;
    last_attempt_at?: string | null;
    last_verified_at?: string | null;
    last_error?: string | null;
    products: number;
  }>(await fetch(`${BASE_URL}/catalog/search-status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fixture, brand }),
  }));
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
  try {
    return await parsed<{ saved: boolean; filename: string; path: string }>(
      await fetch(`${BASE_URL}/compliance/${format}/save?filename=${encodeURIComponent(filename)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, items }),
      }),
    );
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("The local TECS service stopped responding. Restart the application and export again.");
    }
    throw error;
  }
}

export async function downloadCommercial(
  project: ProjectDetails,
  items: ComplianceItem[],
  currency: Currency,
  exchangeRates: Partial<Record<Currency, number>>,
  filename = "TECS-Commercial-Quotation",
) {
  try {
    return await parsed<{ saved: boolean; filename: string; path: string }>(
      await fetch(`${BASE_URL}/commercial/xlsx/save?filename=${encodeURIComponent(filename)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, items, currency, exchange_rates: exchangeRates }),
      }),
    );
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("The local TECS service stopped responding. Restart the application and export again.");
    }
    throw error;
  }
}

export async function openExternalUrl(url: string) {
  if (!url) return;
  if (isTauri()) {
    await open(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
