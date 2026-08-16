import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ClipboardCheck,
  Copy,
  Download,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  LoaderCircle,
  KeyRound,
  Minus,
  Plus,
  RefreshCw,
  Search,
  Save,
  Settings,
  Users,
  ShieldCheck,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { approveFixtures, browseCatalog, catalogSearchStatus, catalogStatus, chooseExportFolder, downloadCommercial, downloadCompliance, extract, getExportFolder, health, initializeApiEndpoint, openExternalUrl, removeApiKey, saveApiKey, searchProducts, syncTeamCatalog, teamCatalogStatus } from "./api";
import tecsLogo from "./assets/tecs-logo.png";
import type { CatalogBrowseResponse, CatalogStatus, ComplianceItem, ComplianceRow, ComplianceStatus, Currency, Fixture, LegendRequirements, LocalAIStatus, Product, ProductSearchResponse, ProjectDetails, TeamCatalogStatus } from "./types";

const BRANDS = [
  { name: "Signify", domain: "signify.com" },
  { name: "Modular Lighting", domain: "supermodular.com" },
  { name: "Colour Kinetics", domain: "colorkinetics.com" },
  { name: "Lite Magic", domain: "litemagic.com" },
  { name: "LEDC4", domain: "ledsc4.com" },
  { name: "LuxeLED", domain: "luxeled.com" },
  { name: "Novolux", domain: "novoluxlighting.com" },
  { name: "ATP", domain: "atpiluminacion.com" },
  { name: "Plux B", domain: "pluxb.com" },
  { name: "Floz", domain: "flos.com" },
  { name: "RELCO", domain: "relcogroup.com" },
  { name: "Unilamp", domain: "unilamp.co.th" },
  { name: "Ligman", domain: "ligman.com" },
  { name: "MP Illumination", domain: "mpillumination.com" },
  { name: "Hepper", domain: "heperlighting.com" },
  { name: "Faelluce", domain: "faelluce.lighting" },
  { name: "Dialight", domain: "dialight.com" },
  { name: "Airfal", domain: "airfal.com" },
  { name: "3F Filippi", domain: "3f-filippi.it" },
  { name: "Roger Pradier", domain: "roger-pradier.com" },
  { name: "Francisconi", domain: "francesconi.it" },
  { name: "Whitecroft Lighting", domain: "whitecroftlighting.com" },
];

const BRAND_ALIASES: Record<string, string> = {
  signify: "Signify",
  philips: "Signify",
  "philips lighting": "Signify",
  "signify / philips": "Signify",
  "signify/philips": "Signify",
};

function canonicalBrand(value: string) {
  const cleaned = (value || "").trim().replace(/\s+/g, " ");
  return BRAND_ALIASES[cleaned.toLowerCase()] || cleaned;
}

function normalizeItemBrand(item: ComplianceItem): ComplianceItem {
  const brand = canonicalBrand(item.brand);
  if (brand === item.brand) return item;
  return {
    ...item,
    brand,
    rows: item.rows.map((row) => row.parameter === "Make"
      ? { ...row, proposed: canonicalBrand(row.proposed) }
      : row),
  };
}
const CURRENCIES: Currency[] = ["OMR", "AED", "USD", "GBP", "EUR"];
type ExchangeRateSets = Record<Currency, Partial<Record<Currency, number>>>;
type CatalogFilters = {
  family: string;
  mounting: string;
  cct: string;
  control: string;
};

const DEFAULT_CATALOG_FILTERS: CatalogFilters = {
  family: "All families",
  mounting: "All mounting types",
  cct: "All CCTs",
  control: "All controls",
};

function defaultExchangeRates(): ExchangeRateSets {
  return Object.fromEntries(CURRENCIES.map((offerCurrency) => [offerCurrency, { [offerCurrency]: 1 }])) as ExchangeRateSets;
}

const UI_ZOOM_KEY = "tecs-ui-zoom";
const PARAMETERS = [
  "Description",
  "Make",
  "Country of Origin",
  "Model No",
  "Mounting",
  "Housing / Construction",
  "Reflector / Optical System",
  "Control Gear / Ballast",
  "Lamp / Lumen / Color Temp / Efficacy",
  "Emergency",
  "CRI",
  "LED life",
  "IP Rating / IK Rating",
  "UGR",
  "Finish",
  "Location",
  "Remarks",
];

const STEPS = ["Project", "Requirements", "Offered products", "Technical sheets"];
const STORAGE_KEY = "tecs-compliance-draft-v2";
const RECENT_PROJECTS_KEY = "tecs-compliance-recent-projects-v1";

type SavedDraft = {
  step?: number;
  project: ProjectDetails;
  items: ComplianceItem[];
  activeId: string;
  priceCurrency: Currency;
  exchangeRateSets: ExchangeRateSets;
  catalogFilters: Record<string, CatalogFilters>;
  catalogViews: Record<string, "closed" | "saved" | "api">;
  selectedMatches: Record<string, string>;
  searchResults: Record<string, Product[]>;
  candidatePrices: Record<string, string>;
  candidateCurrencies: Record<string, Currency>;
  lastExportAt: string | null;
};

type RecentProject = {
  id: string;
  projectName: string;
  client: string;
  reference: string;
  completedAt: string;
  status?: "draft" | "completed";
  draft: SavedDraft;
};

function emptyProject(): ProjectDetails {
  return { project_name: "", client: "", consultant: "", contractor: "", reference: "" };
}

function normalizeSavedDraft(value: Partial<SavedDraft>): SavedDraft {
  return {
    step: value.step,
    project: value.project || emptyProject(),
    items: Array.isArray(value.items) ? value.items.map(normalizeItemBrand) : [],
    activeId: value.activeId || "",
    priceCurrency: CURRENCIES.includes(value.priceCurrency as Currency) ? value.priceCurrency as Currency : "OMR",
    exchangeRateSets: { ...defaultExchangeRates(), ...(value.exchangeRateSets || {}) },
    catalogFilters: value.catalogFilters || {},
    catalogViews: value.catalogViews || {},
    selectedMatches: value.selectedMatches || {},
    searchResults: value.searchResults || {},
    candidatePrices: value.candidatePrices || {},
    candidateCurrencies: value.candidateCurrencies || {},
    lastExportAt: value.lastExportAt || null,
  };
}

function draftHasProjectData(draft: SavedDraft) {
  return draft.items.length > 0 || Object.values(draft.project).some((value) => value.trim() !== "");
}

function savedRecentProjects(): RecentProject[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENT_PROJECTS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function filenamePart(value: string) {
  return value.trim().replace(/[<>:"/\\|?*\u0000-\u001f]+/g, "-").replace(/\s+/g, " ").replace(/[ .-]+$/g, "");
}

function projectExportName(project: ProjectDetails, documentType: string) {
  const identity = [project.reference, project.project_name, project.client]
    .map((value) => filenamePart(value).slice(0, 28).trim())
    .filter(Boolean);
  return [...identity, documentType].join(" - ") || `TECS - ${documentType}`;
}

function recentProjectTitle(recent: RecentProject) {
  return [recent.reference, recent.projectName].filter(Boolean).join(" — ") || "Untitled project";
}

function emptyRows(): ComplianceRow[] {
  return PARAMETERS.map((parameter) => ({ parameter, specified: "", proposed: "", status: "pending", remarks: "" }));
}

function emptyLegend(): LegendRequirements {
  return { light_type: "", description: "", mounting: "", wattage: null, lumens: null, cct: null, cri: null, ip_rating: null, ik_rating: null, ugr: null, controls: "" };
}

function legendDescription(legend: LegendRequirements) {
  return [legend.light_type, legend.description].filter(Boolean).join(". ");
}

function legendLamp(legend: LegendRequirements) {
  return [legend.wattage != null ? `${legend.wattage} W` : "", legend.lumens != null ? `${legend.lumens} lm` : "", legend.cct != null ? `${legend.cct} K` : ""].filter(Boolean).join(" / ");
}

function syncLegendRows(rows: ComplianceRow[], legend: LegendRequirements) {
  const specified: Record<string, string> = {
    Description: legendDescription(legend),
    Mounting: legend.mounting,
    "Control Gear / Ballast": legend.controls,
    "Lamp / Lumen / Color Temp / Efficacy": legendLamp(legend),
    CRI: legend.cri != null ? `CRI ${legend.cri}` : "",
    "IP Rating / IK Rating": [legend.ip_rating != null ? `IP${legend.ip_rating}` : "", legend.ik_rating != null ? `IK${legend.ik_rating}` : ""].filter(Boolean).join(" / "),
    UGR: legend.ugr != null ? `UGR < ${legend.ugr}` : "",
  };
  return rows.map((row) => specified[row.parameter] !== undefined ? { ...row, specified: specified[row.parameter] } : row);
}

function blankItem(index: number): ComplianceItem {
  const legend = emptyLegend();
  const rows = emptyRows().map((row) => row.parameter === "Make" ? { ...row, proposed: BRANDS[0].name, status: "complies" as ComplianceStatus } : row);
  return {
    id: crypto.randomUUID(),
    fitting_type: `F${index + 1}`,
    quantity: 0,
    selected: true,
    brand: BRANDS[0].name,
    product_name: "",
    country_of_origin: "",
    model_no: "",
    product_url: "",
    datasheet_url: "",
    unit_price: null,
    unit_price_currency: null,
    legend,
    rows,
  };
}

function displayOutput(fixture: Fixture) {
  const parts = [
    fixture.wattage != null ? `${fixture.wattage} W` : "",
    fixture.lumens != null ? `${fixture.lumens} lm` : "",
    fixture.cct != null ? `${fixture.cct} K` : "",
    fixture.cri != null ? `CRI ${fixture.cri}` : "",
  ];
  return parts.filter(Boolean).join(" / ");
}

function fixtureToItem(fixture: Fixture): ComplianceItem {
  const legend: LegendRequirements = {
    light_type: fixture.fixture_type === "unspecified" ? "" : fixture.fixture_type,
    description: fixture.description || "",
    mounting: fixture.mounting || "",
    wattage: fixture.wattage ?? null,
    lumens: fixture.lumens ?? null,
    cct: fixture.cct ?? null,
    cri: fixture.cri ?? null,
    ip_rating: fixture.ip_rating ?? null,
    ik_rating: fixture.ik_rating ?? null,
    ugr: fixture.ugr ?? null,
    controls: fixture.controls?.join(", ") || "",
  };
  const specified: Record<string, string> = {
    Description: fixture.description || fixture.fixture_type,
    Mounting: fixture.mounting || "",
    "Housing / Construction": fixture.construction || "",
    "Reflector / Optical System": fixture.optical_details || "",
    "Control Gear / Ballast": fixture.controls?.join(", ") || "",
    "Lamp / Lumen / Color Temp / Efficacy": displayOutput(fixture),
    Emergency: fixture.emergency_hours != null ? `${fixture.emergency_hours} hours` : "",
    CRI: fixture.cri != null ? `CRI ${fixture.cri}` : "",
    "IP Rating / IK Rating": [fixture.ip_rating != null ? `IP${fixture.ip_rating}` : "", fixture.ik_rating != null ? `IK${fixture.ik_rating}` : ""].filter(Boolean).join(" / "),
    UGR: fixture.ugr != null ? `UGR < ${fixture.ugr}` : "",
  };
  return {
    ...blankItem(0),
    id: fixture.id,
    fitting_type: fixture.symbol,
    quantity: fixture.quantity || 1,
    legend,
    rows: syncLegendRows(emptyRows().map((row) => ({ ...row, specified: specified[row.parameter] || "", proposed: row.parameter === "Make" ? BRANDS[0].name : "", status: row.parameter === "Make" ? "complies" : "pending" })), legend),
  };
}

function normalize(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function suggestedStatus(specified: string, proposed: string): ComplianceStatus {
  if (!specified.trim() || ["-", "_"].includes(specified.trim())) return proposed.trim() ? "complies" : "not_applicable";
  if (!proposed.trim()) return "pending";
  return normalize(specified) === normalize(proposed) ? "complies" : "deviation";
}

function setProposedIdentity(item: ComplianceItem, parameter: string, value: string) {
  return {
    ...item,
    rows: item.rows.map((row) => row.parameter === parameter ? { ...row, proposed: value, status: suggestedStatus(row.specified, value) } : row),
  };
}

function specifiedValue(item: ComplianceItem, parameter: string) {
  return item.rows.find((row) => row.parameter === parameter)?.specified || "";
}

function firstNumber(text: string, pattern: RegExp) {
  const match = pattern.exec(text);
  return match ? Number(match[1].replace(/,/g, "")) : null;
}

function itemToFixture(item: ComplianceItem): Fixture {
  const legend = item.legend || emptyLegend();
  const description = specifiedValue(item, "Description") || item.fitting_type;
  const lamp = specifiedValue(item, "Lamp / Lumen / Color Temp / Efficacy");
  const protection = specifiedValue(item, "IP Rating / IK Rating");
  const criText = specifiedValue(item, "CRI");
  const ugrText = specifiedValue(item, "UGR");
  const emergencyText = specifiedValue(item, "Emergency");
  const combined = `${description} ${lamp}`;
  const control = specifiedValue(item, "Control Gear / Ballast");
  return {
    id: item.id,
    symbol: item.fitting_type,
    description: legendDescription(legend) || description,
    quantity: item.quantity,
    fixture_type: legend.light_type || description,
    mounting: legend.mounting || specifiedValue(item, "Mounting") || null,
    mounting_height_mm: null,
    wattage: legend.wattage ?? firstNumber(combined, /(\d+(?:\.\d+)?)\s*W\b/i),
    wattage_options: [],
    lumens: legend.lumens ?? firstNumber(combined, /(\d[\d,]*(?:\.\d+)?)\s*(?:lm|lumen)/i),
    lumen_options: [],
    cct: legend.cct ?? firstNumber(combined, /(\d{3,5})\s*K\b/i),
    cct_options: [],
    cct_min: null,
    cct_max: null,
    cri: legend.cri ?? firstNumber(criText || combined, /(?:CRI|Ra)\s*[><=]*\s*(\d+)/i),
    ip_rating: legend.ip_rating ?? firstNumber(protection || description, /IP\s*X?\s*(\d{2})/i),
    ik_rating: legend.ik_rating ?? firstNumber(protection || description, /IK\s*(\d{2})/i),
    ugr: legend.ugr ?? firstNumber(ugrText || description, /UGR\s*[<=>]*\s*(\d+(?:\.\d+)?)/i),
    dimensions: null,
    construction: specifiedValue(item, "Housing / Construction") || null,
    optical_details: specifiedValue(item, "Reflector / Optical System") || null,
    voltage: null,
    beam_angle: null,
    led_density_per_m: null,
    waterproof: null,
    emergency_hours: null,
    controls: legend.controls ? legend.controls.split(",").map((value) => value.trim()).filter(Boolean) : control && !["-", "_"].includes(control.trim()) ? [control] : [],
    source_file: "Engineer master entry",
    source_page: 1,
    confidence: 1,
    status: "confirmed",
  };
}

function criterionStatus(product: Product, names: string[]): ComplianceStatus | null {
  const criteria = product.criteria.filter((criterion) => names.some((name) => criterion.criterion.startsWith(name)));
  if (!criteria.length) return null;
  if (criteria.some((criterion) => criterion.status === "mismatch" || criterion.status === "tolerance")) return "deviation";
  if (criteria.some((criterion) => criterion.status === "unknown")) return "pending";
  return "complies";
}

function criterionRemark(product: Product, names: string[], status: ComplianceStatus) {
  const criteria = product.criteria.filter((criterion) => names.some((name) => criterion.criterion.startsWith(name)));
  if (!criteria.length) return status === "pending" ? "Not published; engineer to confirm." : "";
  if (status === "complies") return "";
  if (status === "pending") return "Not published; engineer to confirm.";
  return criteria.filter((criterion) => criterion.status === "mismatch" || criterion.status === "tolerance").map((criterion) => `${criterion.criterion}: required ${criterion.required}; offered ${criterion.offered}.`).join(" ");
}

function formatLamp(product: Product) {
  const specs = product.specifications;
  return [
    specs.wattage != null ? `${specs.wattage} W` : "",
    specs.lumens != null ? `${specs.lumens.toLocaleString()} lm` : "",
    specs.cct != null ? `${specs.cct} K` : "",
    specs.efficacy_lm_w != null ? `${specs.efficacy_lm_w} lm/W` : "",
  ].filter(Boolean).join(" / ");
}

function applyProduct(item: ComplianceItem, product: Product): ComplianceItem {
  const specs = product.specifications;
  const brand = canonicalBrand(product.brand);
  const proposed: Record<string, string> = {
    Description: product.description,
    Make: brand,
    "Country of Origin": specs.country_of_origin || "",
    "Model No": product.product_code || "",
    Mounting: specs.mounting || "",
    "Housing / Construction": specs.construction || "",
    "Reflector / Optical System": specs.optical_details || "",
    "Control Gear / Ballast": specs.control_gear || specs.controls?.join(", ") || "",
    "Lamp / Lumen / Color Temp / Efficacy": formatLamp(product),
    Emergency: specs.emergency_details || (specs.emergency_hours != null ? `${specs.emergency_hours} hours` : ""),
    CRI: specs.cri != null ? `CRI ${specs.cri}` : "",
    "LED life": specs.led_life || "",
    "IP Rating / IK Rating": [specs.ip_rating != null ? `IP${specs.ip_rating}` : "", specs.ik_rating != null ? `IK${specs.ik_rating}` : ""].filter(Boolean).join(" / "),
    UGR: specs.ugr != null ? `UGR ${specs.ugr}` : "",
    Finish: specs.finish || "",
  };
  const criteriaMap: Record<string, string[]> = {
    Description: ["Fixture type"],
    Mounting: ["Mounting"],
    "Housing / Construction": ["Construction"],
    "Reflector / Optical System": ["Diffuser / reflector"],
    "Control Gear / Ballast": ["Controls"],
    "Lamp / Lumen / Color Temp / Efficacy": ["Wattage", "Lumens", "Lumen options", "CCT"],
    Emergency: ["Emergency duration"],
    CRI: ["CRI"],
    "IP Rating / IK Rating": ["IP rating", "IK rating"],
    UGR: ["UGR"],
  };
  return {
    ...item,
    brand,
    product_name: product.product_name,
    country_of_origin: specs.country_of_origin || "",
    model_no: product.product_code || "",
    product_url: product.product_url,
    datasheet_url: product.datasheet_url || "",
    rows: item.rows.map((row) => {
      if (["Location", "Remarks"].includes(row.parameter)) return row;
      const value = proposed[row.parameter] || "";
      const names = criteriaMap[row.parameter] || [];
      const status = criterionStatus(product, names) || suggestedStatus(row.specified, value);
      return { ...row, proposed: value, status, remarks: criterionRemark(product, names, status) };
    }),
  };
}

function matchSummary(product: Product) {
  const mismatches = product.criteria.filter((criterion) => criterion.status === "mismatch").length;
  const tolerances = product.criteria.filter((criterion) => criterion.status === "tolerance").length;
  const unknown = product.criteria.filter((criterion) => criterion.status === "unknown").length;
  if (mismatches) return `${mismatches} deviation${mismatches === 1 ? "" : "s"}`;
  if (tolerances) return `${tolerances} item${tolerances === 1 ? "" : "s"} within tolerance`;
  if (unknown) return `${unknown} published value${unknown === 1 ? "" : "s"} unknown`;
  return "Best verified match";
}

function productFacts(product: Product) {
  const specs = product.specifications;
  return [
    specs.wattage != null ? `${specs.wattage} W` : null,
    specs.lumens != null ? `${specs.lumens.toLocaleString()} lm` : null,
    specs.cct != null ? `${specs.cct} K` : null,
    specs.cri != null ? `CRI ${specs.cri}` : null,
    specs.ip_rating != null ? `IP${specs.ip_rating}` : null,
    specs.ik_rating != null ? `IK${specs.ik_rating}` : null,
    specs.ugr != null ? `UGR ${specs.ugr}` : null,
  ].filter(Boolean) as string[];
}

function verificationLabel(product: Product) {
  if (product.verification_level === "datasheet") return "Datasheet verified";
  if (product.verification_level === "multi_source") return "Multiple official sources";
  return "Official product page";
}

function displayManufacturerDate(value?: string | null) {
  if (!value) return "Not published";
  const parsed = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

export default function App() {
  const [step, setStep] = useState(0);
  const [project, setProject] = useState<ProjectDetails>(emptyProject);
  const [projectId, setProjectId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [sourceFixtures, setSourceFixtures] = useState<Fixture[]>([]);
  const [items, setItems] = useState<ComplianceItem[]>([]);
  const [activeId, setActiveId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [success, setSuccess] = useState("");
  const [localAI, setLocalAI] = useState<LocalAIStatus | null>(null);
  const [serviceStatus, setServiceStatus] = useState<"checking" | "ready" | "offline">("checking");
  const [catalogApiReady, setCatalogApiReady] = useState(false);
  const [apiReady, setApiReady] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [apiSettingsOpen, setApiSettingsOpen] = useState(false);
  const [apiSettingsMessage, setApiSettingsMessage] = useState("");
  const [settingsTab, setSettingsTab] = useState<"api" | "team">("api");
  const [teamCatalog, setTeamCatalog] = useState<TeamCatalogStatus | null>(null);
  const [teamMessage, setTeamMessage] = useState("");
  const [searching, setSearching] = useState("");
  const [searchResults, setSearchResults] = useState<Record<string, Product[]>>({});
  const [catalogBrowseInfo, setCatalogBrowseInfo] = useState<Record<string, CatalogBrowseResponse>>({});
  const [catalogFilters, setCatalogFilters] = useState<Record<string, CatalogFilters>>({});
  const [catalogViews, setCatalogViews] = useState<Record<string, "closed" | "saved" | "api">>({});
  const [browsingCatalog, setBrowsingCatalog] = useState("");
  const [selectedMatches, setSelectedMatches] = useState<Record<string, string>>({});
  const [failedSearches, setFailedSearches] = useState<Record<string, boolean>>({});
  const [catalogInfo, setCatalogInfo] = useState<CatalogStatus | null>(null);
  const [searchInfo, setSearchInfo] = useState<Record<string, ProductSearchResponse>>({});
  const [refreshingCatalogs, setRefreshingCatalogs] = useState<Record<string, boolean>>({});
  const catalogPolls = useRef(new Set<string>());
  const [searchTolerance, setSearchTolerance] = useState({ lumens_percent: 10, wattage_percent: 15 });
  const [candidatePrices, setCandidatePrices] = useState<Record<string, string>>({});
  const [candidateCurrencies, setCandidateCurrencies] = useState<Record<string, Currency>>({});
  const [priceCurrency, setPriceCurrency] = useState<Currency>("OMR");
  const [exchangeRateSets, setExchangeRateSets] = useState<ExchangeRateSets>(defaultExchangeRates);
  const [lastExportAt, setLastExportAt] = useState<string | null>(null);
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>(savedRecentProjects);
  const [exportFolder, setExportFolder] = useState("");
  const [restored, setRestored] = useState(false);
  const [uiZoom, setUiZoom] = useState(() => {
    const saved = Number(localStorage.getItem(UI_ZOOM_KEY));
    return saved >= 80 && saved <= 150 ? saved : 100;
  });

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const draft = normalizeSavedDraft(JSON.parse(raw));
        if (draftHasProjectData(draft)) {
          const recovered: RecentProject = {
            id: crypto.randomUUID(),
            projectName: draft.project.project_name.trim(),
            client: draft.project.client.trim(),
            reference: draft.project.reference.trim(),
            completedAt: new Date().toISOString(),
            status: "draft",
            draft,
          };
          const nextRecent = [recovered, ...savedRecentProjects()].slice(0, 12);
          localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(nextRecent));
          setRecentProjects(nextRecent);
          setSuccess("Your previous workspace was moved to Recent Projects. A new blank project is ready.");
        }
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
    setRestored(true);
    let cancelled = false;
    let retryTimer: number | undefined;
    let attempts = 0;
    const checkService = async () => {
      try {
        const value = await health();
        if (cancelled) return;
        setLocalAI(value.local_ai);
        setApiReady(value.api_key_configured);
        setServiceStatus("ready");
        setCatalogApiReady(value.catalog_api === true);
        if (value.catalog_api === true) void catalogStatus().then(setCatalogInfo).catch(() => undefined);
        void teamCatalogStatus().then(setTeamCatalog).catch(() => undefined);
        void getExportFolder().then((folder) => setExportFolder(folder.path)).catch(() => undefined);
      } catch {
        if (cancelled) return;
        attempts += 1;
        setLocalAI(null);
        if (attempts < 45) retryTimer = window.setTimeout(checkService, 1000);
        else setServiceStatus("offline");
      }
    };
    void initializeApiEndpoint().then(checkService).catch(() => {
      if (cancelled) return;
      setServiceStatus("offline");
      setError("The packaged TECS engine could not be started. Close the app completely and open it again.");
    });
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, []);

  useEffect(() => {
    document.body.style.zoom = `${uiZoom}%`;
    localStorage.setItem(UI_ZOOM_KEY, String(uiZoom));
  }, [uiZoom]);

  useEffect(() => {
    if (!restored) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ step, project, items, activeId, priceCurrency, exchangeRateSets, catalogFilters, catalogViews, selectedMatches, searchResults, candidatePrices, candidateCurrencies, lastExportAt }));
  }, [step, project, items, activeId, priceCurrency, exchangeRateSets, catalogFilters, catalogViews, selectedMatches, searchResults, candidatePrices, candidateCurrencies, lastExportAt, restored]);

  const activeItem = items.find((item) => item.id === activeId) || items[0];
  const selectedItems = items.filter((item) => item.selected);
  const activeCatalog = activeItem ? catalogBrowseInfo[activeItem.id] : undefined;
  const catalogView = activeItem ? catalogViews[activeItem.id] || "closed" : "closed";
  const activeCatalogFilters = activeItem ? catalogFilters[activeItem.id] || DEFAULT_CATALOG_FILTERS : DEFAULT_CATALOG_FILTERS;
  const catalogOptions = useMemo(() => {
    const products = activeCatalog?.products || [];
    const unique = (values: Array<string | null | undefined>) => [...new Set(values.filter((value): value is string => Boolean(value?.trim())).map((value) => value.trim()))].sort((left, right) => left.localeCompare(right, undefined, { numeric: true }));
    return {
      families: activeCatalog?.facets?.families || activeCatalog?.families || [],
      mounting: activeCatalog?.facets?.mounting || unique(products.map((product) => product.specifications.mounting)),
      cct: activeCatalog?.facets?.cct || [...new Set(products.map((product) => product.specifications.cct).filter((value): value is number => value != null))].sort((left, right) => left - right),
      controls: activeCatalog?.facets?.controls || unique(products.flatMap((product) => [product.specifications.control_gear, ...(product.specifications.controls || [])])),
    };
  }, [activeCatalog]);
  const catalogFilterActive = activeCatalogFilters.family !== "All families" || activeCatalogFilters.mounting !== "All mounting types" || activeCatalogFilters.cct !== "All CCTs" || activeCatalogFilters.control !== "All controls";
  const filteredCatalogProducts = useMemo(() => {
    if (!activeItem) return [];
    const products = catalogView === "api" ? (searchResults[activeItem.id] || []) : (activeCatalog?.products || []);
    if (catalogView === "api") return products;
    if (catalogView === "saved" && !catalogFilterActive) return [];
    const filtered = products.filter((product) => {
      const matchesFamily = activeCatalogFilters.family === "All families" || product.catalog_family === activeCatalogFilters.family;
      const matchesMounting = activeCatalogFilters.mounting === "All mounting types" || product.specifications.mounting === activeCatalogFilters.mounting;
      const matchesCct = activeCatalogFilters.cct === "All CCTs" || product.specifications.cct === Number(activeCatalogFilters.cct);
      const productControls = [product.specifications.control_gear, ...(product.specifications.controls || [])].filter(Boolean);
      const matchesControl = activeCatalogFilters.control === "All controls" || productControls.includes(activeCatalogFilters.control);
      return matchesFamily && matchesMounting && matchesCct && matchesControl;
    });
    return catalogView === "saved"
      ? [...filtered].sort((left, right) => left.product_name.localeCompare(right.product_name))
      : filtered;
  }, [activeCatalog, activeCatalogFilters, activeItem, catalogFilterActive, catalogView, searchResults]);
  const totals = useMemo(() => {
    const rows = items.flatMap((item) => item.rows);
    return {
      complies: rows.filter((row) => row.status === "complies").length,
      deviations: rows.filter((row) => row.status === "deviation").length,
      pending: rows.filter((row) => row.status === "pending").length,
    };
  }, [items]);

  useEffect(() => {
    if (step !== 2 || !catalogApiReady || !activeItem || catalogBrowseInfo[activeItem.id] || browsingCatalog === activeItem.id) return;
    void loadSavedCatalog(activeItem);
  }, [step, catalogApiReady, activeItem?.id, activeItem?.brand]);

  function updateProject(key: keyof ProjectDetails, value: string) {
    setProject((current) => ({ ...current, [key]: value }));
  }

  function setItemCatalogView(id: string, view: "closed" | "saved" | "api") {
    setCatalogViews((current) => ({ ...current, [id]: view }));
  }

  function updateCatalogFilter(id: string, key: keyof CatalogFilters, value: string) {
    setCatalogFilters((current) => ({
      ...current,
      [id]: { ...(current[id] || DEFAULT_CATALOG_FILTERS), [key]: value },
    }));
  }

  function resetCatalogFilters(id: string) {
    setCatalogFilters((current) => ({ ...current, [id]: { ...DEFAULT_CATALOG_FILTERS } }));
  }

  function updateItem(id: string, updater: (item: ComplianceItem) => ComplianceItem) {
    setItems((current) => current.map((item) => item.id === id ? updater(item) : item));
  }

  function updateLegend(id: string, key: keyof LegendRequirements, value: string | number | null) {
    updateItem(id, (item) => {
      const legend = { ...(item.legend || emptyLegend()), [key]: value };
      return { ...item, legend, rows: syncLegendRows(item.rows, legend) };
    });
  }

  function addItem() {
    const item = blankItem(items.length);
    setItems((current) => [...current, item]);
    setActiveId(item.id);
  }

  function duplicateItem(item: ComplianceItem) {
    const copyItem = { ...item, id: crypto.randomUUID(), fitting_type: `${item.fitting_type} copy`, rows: item.rows.map((row) => ({ ...row })) };
    setItems((current) => [...current, copyItem]);
    setActiveId(copyItem.id);
  }

  function removeItem(id: string) {
    setItems((current) => current.filter((item) => item.id !== id));
    if (activeId === id) setActiveId(items.find((item) => item.id !== id)?.id || "");
  }

  function startManual() {
    if (!items.length) {
      const item = blankItem(0);
      setItems([item]);
      setActiveId(item.id);
    }
    setStep(1);
  }

  async function processDrawings() {
    setBusy(true);
    setError("");
    try {
      const response = await extract(project.project_name.trim() || "Untitled Project", files);
      const converted = response.fixtures.map(fixtureToItem);
      setProjectId(response.project_id);
      setSourceFixtures(response.fixtures);
      setWarnings(response.warnings);
      setItems(converted.length ? converted : [blankItem(0)]);
      setActiveId(converted[0]?.id || "");
      setStep(1);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not process the drawings.");
    } finally {
      setBusy(false);
    }
  }

  async function continueToProducts() {
    if (projectId && sourceFixtures.length) {
      try { await approveFixtures(projectId, sourceFixtures); } catch { /* Manual compliance work can continue if learning is unavailable. */ }
    }
    setStep(2);
    const item = activeItem || items[0];
    if (item) void loadSavedCatalog(item);
  }

  async function loadSavedCatalog(item: ComplianceItem, brandName = item.brand) {
    if (!catalogApiReady) return;
    brandName = canonicalBrand(brandName);
    setBrowsingCatalog(item.id);
    setError("");
    try {
      const response = await browseCatalog(itemToFixture(item), brandName, searchTolerance);
      setCatalogBrowseInfo((current) => ({ ...current, [item.id]: response }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load the saved catalogue.");
    } finally {
      setBrowsingCatalog("");
    }
  }

  function updateIdentity(id: string, key: "brand" | "country_of_origin" | "model_no" | "product_name", value: string) {
    if (key === "brand") {
      value = canonicalBrand(value);
      setItemCatalogView(id, "closed");
      resetCatalogFilters(id);
      setCatalogBrowseInfo((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      setSearchResults((current) => ({ ...current, [id]: [] }));
      setSelectedMatches((current) => ({ ...current, [id]: "" }));
      updateItem(id, (item) => ({
        ...item,
        brand: value,
        product_name: "",
        country_of_origin: "",
        model_no: "",
        product_url: "",
        datasheet_url: "",
        rows: item.rows.map((row) => row.parameter === "Make"
          ? { ...row, proposed: value, status: "complies", remarks: "" }
          : { ...row, proposed: "", status: "pending", remarks: "" }),
      }));
      const current = items.find((item) => item.id === id);
      if (current) void loadSavedCatalog({ ...current, brand: value }, value);
      return;
    }
    if (key === "product_name") {
      updateItem(id, (item) => ({ ...item, product_name: value }));
      return;
    }
    const parameter = key === "country_of_origin" ? "Country of Origin" : "Model No";
    updateItem(id, (item) => setProposedIdentity({ ...item, [key]: value }, parameter, value));
  }

  function updateRow(id: string, rowIndex: number, key: keyof ComplianceRow, value: string) {
    updateItem(id, (item) => ({
      ...item,
      rows: item.rows.map((row, index) => {
        if (index !== rowIndex) return row;
        const next = { ...row, [key]: value } as ComplianceRow;
        if (key === "proposed") next.status = suggestedStatus(next.specified, value);
        return next;
      }),
    }));
  }

  async function configureApiKey() {
    if (!apiKey.trim() || serviceStatus !== "ready") return;
    setBusy(true);
    setError("");
    try {
      await saveApiKey(apiKey.trim());
      setApiKey("");
      setApiReady(true);
      setApiSettingsMessage("API key saved securely on this computer.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save the API key.");
    } finally {
      setBusy(false);
    }
  }

  async function clearApiKey() {
    setBusy(true);
    setError("");
    try {
      const response = await removeApiKey();
      setApiKey("");
      setApiReady(response.api_key_configured);
      setApiSettingsMessage(response.api_key_configured
        ? "The saved key was removed, but an environment-provided key is still active."
        : "API key removed from this computer.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not remove the API key.");
    } finally {
      setBusy(false);
    }
  }

  async function syncSharedCatalog() {
    setBusy(true); setTeamMessage("");
    try {
      const response = await syncTeamCatalog();
      setTeamMessage(`Sync complete: ${response.downloaded} downloaded, ${response.uploaded} uploaded.`);
      setTeamCatalog(await teamCatalogStatus());
      setCatalogInfo(await catalogStatus());
      if (activeItem) await loadSavedCatalog(activeItem);
    } catch (caught) { setTeamMessage(caught instanceof Error ? caught.message : "Team catalogue sync failed."); }
    finally { setBusy(false); }
  }


  async function findBestProducts(item: ComplianceItem) {
    if (!catalogApiReady) {
      setError("The catalogue engine has not restarted yet. Stop and restart the TECS app, then search again; no API search was started.");
      return;
    }
    const itemBrand = canonicalBrand(item.brand);
    const brand = BRANDS.find((candidate) => candidate.name === itemBrand) || BRANDS[0];
    setSearching(item.id);
    setError("");
    setWarnings([]);
    setSearchResults((current) => ({ ...current, [item.id]: [] }));
    setSelectedMatches((current) => ({ ...current, [item.id]: "" }));
    setFailedSearches((current) => ({ ...current, [item.id]: false }));
    try {
      const response = await searchProducts(itemToFixture(item), brand.name, searchTolerance, true);
      setSearchResults((current) => ({ ...current, [item.id]: response.matches }));
      setItemCatalogView(item.id, "api");
      setSearchInfo((current) => ({ ...current, [item.id]: response }));
      setRefreshingCatalogs((current) => ({ ...current, [item.id]: response.refreshing }));
      void catalogStatus().then(setCatalogInfo).catch(() => undefined);
      await loadSavedCatalog(item, brand.name);
      if (response.refreshing) void pollCatalog(item, brand.name);
      if (!response.matches.length && !response.refreshing) {
        setFailedSearches((current) => ({ ...current, [item.id]: true }));
        setWarnings(response.warnings.length ? response.warnings : [`No verified ${brand.name} options are catalogued for this category yet. You can continue working and retry the background refresh later.`]);
      } else if (response.warnings.length) setWarnings(response.warnings);
    } catch (caught) {
      setFailedSearches((current) => ({ ...current, [item.id]: true }));
      setError(caught instanceof Error ? caught.message : "The product search could not be completed.");
    } finally {
      setSearching("");
    }
  }

  async function pollCatalog(item: ComplianceItem, brandName: string) {
    brandName = canonicalBrand(brandName);
    if (catalogPolls.current.has(item.id)) return;
    catalogPolls.current.add(item.id);
    const startedAt = Date.now();
    try {
      let scopeStatus;
      do {
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
        scopeStatus = await catalogSearchStatus(itemToFixture(item), brandName);
      } while (scopeStatus.refreshing && Date.now() - startedAt < 5 * 60 * 1000);
      const response = await searchProducts(itemToFixture(item), brandName, searchTolerance);
      setSearchResults((current) => ({ ...current, [item.id]: response.matches }));
      setItemCatalogView(item.id, "api");
      setSearchInfo((current) => ({ ...current, [item.id]: response }));
      setRefreshingCatalogs((current) => ({ ...current, [item.id]: response.refreshing }));
      setWarnings(response.warnings);
      void catalogStatus().then(setCatalogInfo).catch(() => undefined);
      await loadSavedCatalog(item, brandName);
      if (!response.matches.length && !response.refreshing) {
        setFailedSearches((current) => ({ ...current, [item.id]: true }));
        setWarnings(response.warnings.length ? response.warnings : [`No verified ${brandName} options are catalogued for this category yet. Retry the background refresh later.`]);
      }
    } catch (caught) {
      setWarnings([caught instanceof Error ? `Catalogue status check paused: ${caught.message}` : "Catalogue status check paused. You can retry it later."]);
    } finally {
      setRefreshingCatalogs((current) => ({ ...current, [item.id]: false }));
      catalogPolls.current.delete(item.id);
    }
  }

  function finalizeProduct(item: ComplianceItem, product: Product) {
    const enteredPrice = candidatePrices[`${item.id}:${product.id}`]?.trim() || "";
    const unitPrice = enteredPrice === "" ? null : Number(enteredPrice);
    const unitPriceCurrency = unitPrice == null ? null : candidateCurrencies[`${item.id}:${product.id}`] || item.unit_price_currency || priceCurrency;
    updateItem(item.id, (current) => ({
      ...applyProduct(current, product),
      unit_price: unitPrice != null && Number.isFinite(unitPrice) ? unitPrice : null,
      unit_price_currency: unitPrice != null && Number.isFinite(unitPrice) ? unitPriceCurrency : null,
    }));
    setSelectedMatches((current) => ({ ...current, [item.id]: product.id }));
  }

  function currentDraft(): SavedDraft {
    return { step, project, items, activeId, priceCurrency, exchangeRateSets, catalogFilters, catalogViews, selectedMatches, searchResults, candidatePrices, candidateCurrencies, lastExportAt };
  }

  function restoreProject(recent: RecentProject) {
    const draft = normalizeSavedDraft(recent.draft);
    const nextRecent = recentProjects.filter((candidate) => candidate.id !== recent.id);
    localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(nextRecent));
    setRecentProjects(nextRecent);
    setProject(draft.project || emptyProject());
    setItems((draft.items || []).map(normalizeItemBrand));
    setActiveId(draft.activeId || draft.items?.[0]?.id || "");
    setPriceCurrency(draft.priceCurrency || "OMR");
    setExchangeRateSets({ ...defaultExchangeRates(), ...(draft.exchangeRateSets || {}) });
    setCatalogFilters(draft.catalogFilters || {});
    setCatalogViews(draft.catalogViews || {});
    setSelectedMatches(draft.selectedMatches || {});
    setSearchResults(draft.searchResults || {});
    setCandidatePrices(draft.candidatePrices || {});
    setCandidateCurrencies(draft.candidateCurrencies || {});
    setLastExportAt(draft.lastExportAt || null);
    setCatalogBrowseInfo({});
    setSearchInfo({});
    setWarnings([]);
    setError("");
    setSuccess("");
    const fallbackStep = recent.status !== "draft" ? 3 : draft.items.length ? 1 : 0;
    setStep(Math.max(0, Math.min(3, draft.step ?? fallbackStep)));
  }

  function finishProjectAndStartNew() {
    if (!lastExportAt || !project.project_name.trim()) return;
    const completedAt = new Date().toISOString();
    const archive: RecentProject = {
      id: crypto.randomUUID(),
      projectName: project.project_name.trim(),
      client: project.client.trim(),
      reference: project.reference.trim(),
      completedAt,
      status: "completed",
      // Finalized product details live in `items`; transient catalogue cards can
      // be reloaded from the database and would make the local archive needlessly large.
      draft: { ...currentDraft(), catalogViews: {}, selectedMatches: {}, searchResults: {}, candidatePrices: {}, candidateCurrencies: {}, lastExportAt },
    };
    const nextRecent = [archive, ...recentProjects].slice(0, 12);
    localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(nextRecent));
    localStorage.removeItem(STORAGE_KEY);
    setRecentProjects(nextRecent);
    setStep(0);
    setProject(emptyProject());
    setProjectId("");
    setFiles([]);
    setSourceFixtures([]);
    setItems([]);
    setActiveId("");
    setCatalogBrowseInfo({});
    setCatalogFilters({});
    setCatalogViews({});
    setSearchResults({});
    setSelectedMatches({});
    setCandidatePrices({});
    setCandidateCurrencies({});
    setSearchInfo({});
    setRefreshingCatalogs({});
    setFailedSearches({});
    setExchangeRateSets(defaultExchangeRates());
    setPriceCurrency("OMR");
    setLastExportAt(null);
    setWarnings([]);
    setError("");
    setSuccess("Previous project archived. A new blank project is ready.");
  }

  async function selectExportFolder() {
    setBusy(true);
    setError("");
    try {
      await health();
      const result = await chooseExportFolder();
      setExportFolder(result.path);
      if (result.selected) setSuccess(`Exports will now be saved to ${result.path}`);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "";
      if (caught instanceof TypeError) setServiceStatus("offline");
      setError(caught instanceof TypeError || /not found/i.test(message)
        ? "The local TECS engine needs to be restarted for folder selection. Close TECS, run “Run TECS Lighting.command” again, then choose the folder."
        : message || "Could not select the export folder.");
    } finally {
      setBusy(false);
    }
  }

  async function exportSheets(format: "xlsx" | "pdf", only?: ComplianceItem) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const exportItems = only ? [{ ...only, selected: true }] : items;
      const suffix = projectExportName(project, only ? "Individual Technical Compliance" : "Technical Compliance");
      const result = await downloadCompliance(format, project, exportItems, suffix);
      setLastExportAt(new Date().toISOString());
      setSuccess(`Saved ${result.filename} to ${result.path}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the technical sheets.");
    } finally {
      setBusy(false);
    }
  }

  async function exportCommercialQuotation() {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const result = await downloadCommercial(project, selectedItems, priceCurrency, exchangeRateSets[priceCurrency], projectExportName(project, "Commercial Quotation"));
      setLastExportAt(new Date().toISOString());
      setSuccess(`Saved ${result.filename} to ${result.path}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the commercial quotation.");
    } finally {
      setBusy(false);
    }
  }

  async function openOfficialLink(url: string) {
    setError("");
    try {
      await openExternalUrl(url);
    } catch {
      setError("Windows could not open this link in the default browser. Check the default browser setting and try again.");
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <img className="tecs-logo" src={tecsLogo} alt="TECS Technical Supplies" />
        <div className="app-name">Technical Compliance Studio</div>
        <nav>
          {STEPS.map((label, index) => (
            <button key={label} className={`nav-step ${index === step ? "active" : ""} ${index < step ? "complete" : ""}`} onClick={() => index <= step && setStep(index)}>
              <span>{index < step ? <Check size={13} /> : index + 1}</span>{label}
            </button>
          ))}
        </nav>
        <div className="autosave"><Save size={16} /><div><strong>Draft autosaved</strong><small>Stored privately on this computer</small></div></div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div><span className="eyebrow">TECS LIGHTING</span><h1>{STEPS[step]}</h1></div>
          <div className="top-statuses"><div className="zoom-controls" aria-label="Interface zoom"><button disabled={uiZoom <= 80} onClick={() => setUiZoom((value) => Math.max(80, value - 10))} title="Make interface smaller"><Minus size={13} /></button><button className="zoom-value" onClick={() => setUiZoom(100)} title="Reset interface size">{uiZoom}%</button><button disabled={uiZoom >= 150} onClick={() => setUiZoom((value) => Math.min(150, value + 10))} title="Make interface larger"><Plus size={13} /></button></div><div className={`engine-chip ${localAI?.available ? "ready" : ""}`}><ShieldCheck size={14} />{localAI?.available ? "Drawing AI ready" : "Manual mode ready"}</div><button className={`engine-chip api-settings-trigger ${apiReady ? "ready" : ""} ${serviceStatus === "offline" ? "offline" : ""}`} onClick={() => { setApiSettingsMessage(serviceStatus === "offline" ? "The local TECS service is not running. Restart the application, then try again." : ""); setApiSettingsOpen(true); }}><Settings size={14} />{serviceStatus === "checking" ? "Connecting to local service…" : serviceStatus === "offline" ? "Local service offline" : apiReady ? "API settings" : "Set up product API"}</button></div>
        </header>
        {apiSettingsOpen && <div className="settings-overlay" onMouseDown={(event) => event.target === event.currentTarget && setApiSettingsOpen(false)}><section className="settings-panel settings-panel-wide" role="dialog" aria-modal="true" aria-labelledby="api-settings-title"><div className="settings-header"><div className="settings-icon">{settingsTab === "api" ? <KeyRound size={20} /> : <Users size={20} />}</div><div><span className="section-kicker">SETTINGS</span><h2 id="api-settings-title">{settingsTab === "api" ? "OpenAI API" : "Shared catalogue"}</h2></div><button className="settings-close" onClick={() => setApiSettingsOpen(false)} aria-label="Close settings">×</button></div><div className="settings-tabs"><button className={settingsTab === "api" ? "active" : ""} onClick={() => setSettingsTab("api")}><KeyRound size={14} />Product API</button><button className={settingsTab === "team" ? "active" : ""} onClick={() => setSettingsTab("team")}><Users size={14} />Shared catalogue</button></div>{settingsTab === "api" ? <><p className="settings-description">The key is stored securely on this computer and is used only for official manufacturer product searches.</p><div className={`api-key-status ${serviceStatus === "offline" ? "offline" : apiReady ? "configured" : "missing"}`}><span></span><strong>{serviceStatus === "checking" ? "Connecting to the local TECS service…" : serviceStatus === "offline" ? "Local TECS service is offline" : apiReady ? "API key configured" : "No API key configured"}</strong></div><label><span>{apiReady ? "Enter a replacement key" : "OpenAI API key"}</span><input type="password" autoComplete="off" disabled={serviceStatus !== "ready"} value={apiKey} onChange={(event) => { setApiKey(event.target.value); setApiSettingsMessage(""); }} /></label>{apiSettingsMessage && <div className={`settings-message ${serviceStatus === "offline" ? "error" : ""}`}>{apiSettingsMessage}</div>}<div className="settings-actions">{apiReady && <button className="remove-key" disabled={busy || serviceStatus !== "ready"} onClick={clearApiKey}><Trash2 size={15} />Remove key</button>}<button className="primary" disabled={!apiKey.trim() || busy || serviceStatus !== "ready"} onClick={configureApiKey}>{busy ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}{apiReady ? "Save replacement" : "Save API key"}</button></div></> : <><p className="settings-description">The shared product catalogue works automatically. No account, email, password, or setup is required.</p><div className="api-key-status configured"><span></span><strong>{teamCatalog?.syncing ? "Syncing shared products…" : `Ready · ${teamCatalog?.shared_products || 0} shared products`}</strong></div><div className="team-privacy-note"><ShieldCheck size={16} /><span>Only reusable manufacturer product details are shared. Projects, customer files, prices, and OpenAI keys stay local.</span></div>{teamMessage && <div className={`settings-message ${/failed|could not|invalid|error/i.test(teamMessage) ? "error" : ""}`}>{teamMessage}</div>}{teamCatalog?.last_error && <div className="settings-message error">Last automatic sync: {teamCatalog.last_error}</div>}<div className="settings-actions"><button className="primary" disabled={busy || teamCatalog?.syncing} onClick={syncSharedCatalog}>{busy || teamCatalog?.syncing ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}Sync now</button></div></>}</section></div>}
        {error && <div className="error-banner">{error}<button onClick={() => setError("")}>Dismiss</button></div>}
        {success && <div className="success-banner">{success}<button onClick={() => setSuccess("")}>Dismiss</button></div>}

        {step === 0 && (
          <section className="content project-step">
            <div className="intro"><span className="section-kicker">NEW SUBMISSION</span><h2>Set up the project</h2><p>Enter the header details once. They will appear on every individual technical data sheet.</p></div>
            <div className="project-grid">
              <label className="wide-field"><span>Project name</span><textarea rows={2} value={project.project_name} onChange={(event) => updateProject("project_name", event.target.value)} placeholder="Project title and location" autoFocus /></label>
              <label><span>Client</span><input value={project.client} onChange={(event) => updateProject("client", event.target.value)} placeholder="Client / developer" /></label>
              <label><span>Consultant</span><input value={project.consultant} onChange={(event) => updateProject("consultant", event.target.value)} placeholder="Consultant" /></label>
              <label><span>Contractor</span><input value={project.contractor} onChange={(event) => updateProject("contractor", event.target.value)} placeholder="Main contractor" /></label>
              <label><span>Reference</span><input value={project.reference} onChange={(event) => updateProject("reference", event.target.value)} placeholder="Submission reference" /></label>
            </div>
            <div className="start-options">
              <div className="start-card">
                <div className="start-icon"><ClipboardCheck size={22} /></div>
                <div><h3>Enter requirements manually</h3><p>Build each fitting from the master-sheet parameters.</p></div>
                <button className="primary" disabled={!project.project_name.trim()} onClick={startManual}>Start manual entry <ArrowRight size={17} /></button>
              </div>
              <div className="start-card">
                <div className="start-icon muted"><UploadCloud size={22} /></div>
                <div><h3>Start from drawings</h3><p>Extract a first draft, then review every master field manually.</p></div>
                <label className="file-picker"><FolderOpen size={16} />{files.length ? `${files.length} PDF selected` : "Choose PDF drawings"}<input type="file" accept=".pdf,application/pdf" multiple onChange={(event) => setFiles(Array.from(event.target.files || []))} /></label>
                <button className="secondary" disabled={!files.length || busy} onClick={processDrawings}>{busy ? <LoaderCircle className="spin" size={17} /> : <ArrowRight size={17} />}Extract first draft</button>
              </div>
            </div>
            {recentProjects.length > 0 && <div className="recent-projects"><div className="recent-projects-heading"><div><span className="section-kicker">RECENT PROJECTS</span><h3>Reopen previous work</h3></div><small>Stored privately on this computer</small></div><div className="recent-project-list">{recentProjects.map((recent) => <article key={recent.id}><div><strong>{recentProjectTitle(recent)}</strong><span>{[recent.client ? `Client: ${recent.client}` : "", recent.reference ? `Reference: ${recent.reference}` : ""].filter(Boolean).join(" · ") || "No client or reference"}</span><small>{recent.draft.items.length} product{recent.draft.items.length === 1 ? "" : "s"} · {recent.status === "draft" ? "Saved" : "Completed"} {new Date(recent.completedAt).toLocaleString()}</small></div><button className="secondary" onClick={() => restoreProject(recent)}>Reopen</button></article>)}</div></div>}
          </section>
        )}

        {step === 1 && (
          <section className="content workspace-step">
            <div className="section-heading"><div><span className="section-kicker">LIGHTING LEGEND</span><h2>Enter the basic fitting requirements</h2><p>Capture only what the engineer would normally read from the lighting legend. The full compliance sheet is prepared after a product is selected.</p></div><button className="outline" onClick={addItem}><Plus size={16} />Add fitting</button></div>
            {warnings.length > 0 && <div className="warning-box">{warnings.map((warning) => <div key={warning}>{warning}</div>)}</div>}
            <div className="split-workspace">
              <div className="item-list">
                {items.map((item) => (
                  <button key={item.id} className={`item-tab ${activeItem?.id === item.id ? "active" : ""}`} onClick={() => setActiveId(item.id)}>
                    <span className="type-badge">{item.fitting_type || "NEW"}</span><span><strong>{item.legend?.light_type || "Untitled fitting"}</strong><small>{[item.legend?.wattage != null ? `${item.legend.wattage} W` : "", item.legend?.lumens != null ? `${item.legend.lumens} lm` : "", item.quantity > 0 ? `${item.quantity} unit${item.quantity === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ")}</small></span>
                  </button>
                ))}
              </div>
              {activeItem && <div className="editor-card">
                <div className="editor-toolbar">
                  <label><span>Fitting type</span><input value={activeItem.fitting_type} onChange={(event) => updateItem(activeItem.id, (item) => ({ ...item, fitting_type: event.target.value }))} /></label>
                  <label className="qty"><span>Quantity</span><input type="number" min="1" value={activeItem.quantity || ""} onChange={(event) => updateItem(activeItem.id, (item) => ({ ...item, quantity: event.target.value === "" ? 0 : Number(event.target.value) }))} /></label>
                  <button title="Duplicate fitting" onClick={() => duplicateItem(activeItem)}><Copy size={16} /></button>
                  <button title="Delete fitting" className="danger" onClick={() => removeItem(activeItem.id)}><Trash2 size={16} /></button>
                </div>
                <div className="legend-form">
                  <div className="legend-section"><h3>Fixture identity</h3><div className="legend-grid">
                    <label className="span-2"><span>Type of light</span><input value={activeItem.legend?.light_type || ""} onChange={(event) => updateLegend(activeItem.id, "light_type", event.target.value)} placeholder="e.g. recessed downlight, linear batten, floodlight" /></label>
                    <label className="span-2"><span>Mounting</span><input value={activeItem.legend?.mounting || ""} onChange={(event) => updateLegend(activeItem.id, "mounting", event.target.value)} placeholder="Recessed / surface / suspended" /></label>
                    <label className="span-4"><span>Legend description / important notes</span><textarea rows={3} value={activeItem.legend?.description || ""} onChange={(event) => updateLegend(activeItem.id, "description", event.target.value)} placeholder="Any additional description printed in the legend" /></label>
                  </div></div>
                  <div className="legend-section"><h3>Light output and electrical</h3><div className="legend-grid numeric-grid">
                    <label><span>Wattage (W)</span><input type="number" value={activeItem.legend?.wattage ?? ""} onChange={(event) => updateLegend(activeItem.id, "wattage", event.target.value === "" ? null : Number(event.target.value))} /></label>
                    <label><span>Lumens (lm)</span><input type="number" value={activeItem.legend?.lumens ?? ""} onChange={(event) => updateLegend(activeItem.id, "lumens", event.target.value === "" ? null : Number(event.target.value))} /></label>
                    <label><span>CCT (K)</span><input type="number" value={activeItem.legend?.cct ?? ""} onChange={(event) => updateLegend(activeItem.id, "cct", event.target.value === "" ? null : Number(event.target.value))} /></label>
                    <label><span>CRI minimum</span><input type="number" value={activeItem.legend?.cri ?? ""} onChange={(event) => updateLegend(activeItem.id, "cri", event.target.value === "" ? null : Number(event.target.value))} /></label>
                  </div></div>
                  <div className="legend-section"><h3>Protection and options</h3><div className="legend-grid numeric-grid">
                    <label><span>IP rating</span><input type="number" value={activeItem.legend?.ip_rating ?? ""} onChange={(event) => updateLegend(activeItem.id, "ip_rating", event.target.value === "" ? null : Number(event.target.value))} /></label>
                    <label><span>IK rating</span><input type="number" value={activeItem.legend?.ik_rating ?? ""} onChange={(event) => updateLegend(activeItem.id, "ik_rating", event.target.value === "" ? null : Number(event.target.value))} /></label>
                    <label><span>UGR maximum</span><input type="number" value={activeItem.legend?.ugr ?? ""} onChange={(event) => updateLegend(activeItem.id, "ugr", event.target.value === "" ? null : Number(event.target.value))} /></label>
                    <label className="span-4"><span>Controls / driver</span><input value={activeItem.legend?.controls || ""} onChange={(event) => updateLegend(activeItem.id, "controls", event.target.value)} placeholder="e.g. DALI, fixed output, 1-10V" /></label>
                  </div></div>
                </div>
              </div>}
            </div>
            <div className="page-actions"><button className="secondary" onClick={() => setStep(0)}><ArrowLeft size={17} />Project</button><button className="primary" disabled={!items.length} onClick={continueToProducts}>Enter offered products <ArrowRight size={17} /></button></div>
          </section>
        )}

        {step === 2 && (
          <section className="content workspace-step">
            <div className="section-heading"><div><span className="section-kicker">AI PRODUCT SELECTION</span><h2>Find and finalize the best product</h2><p>Stored verified catalogues return immediately. Official sources refresh in the background without discarding the last successful results.</p></div><span className={`count-pill ${catalogApiReady ? "" : "catalog-offline"}`}>{catalogApiReady ? `${catalogInfo?.products || 0} catalogued products · ${BRANDS.length} brands` : "Restart required for catalogue"}</span></div>
            {warnings.length > 0 && <div className="warning-box">{warnings.map((warning) => <div key={warning}>{warning}</div>)}</div>}
            <div className="split-workspace products-workspace">
              <div className="item-list">
                {items.map((item) => <button key={item.id} className={`item-tab ${activeItem?.id === item.id ? "active" : ""}`} onClick={() => { setActiveId(item.id); if (!catalogBrowseInfo[item.id]) void loadSavedCatalog(item); }}><span className="type-badge">{item.fitting_type}</span><span><strong>{item.product_name || "Product not entered"}</strong><small>{item.brand} · {item.model_no || "model pending"}</small></span></button>)}
              </div>
              {activeItem && <div className="editor-card product-editor">
                <div className="ai-search-panel">
                  <div className="search-panel-copy"><div className="ai-icon"><Search size={18} /></div><div><strong>Official {activeItem.brand} product research</strong><span>Use this only when the saved catalogue does not contain the product you need or its information is outdated.</span></div></div>
                  <div className="search-controls"><label className="tolerance-field"><span>Lumens ±</span><input type="number" min="0" max="100" value={searchTolerance.lumens_percent} onChange={(event) => setSearchTolerance((current) => ({ ...current, lumens_percent: Number(event.target.value) }))} /><small>%</small></label><label className="tolerance-field"><span>Watts ±</span><input type="number" min="0" max="100" value={searchTolerance.wattage_percent} onChange={(event) => setSearchTolerance((current) => ({ ...current, wattage_percent: Number(event.target.value) }))} /><small>%</small></label>{!apiReady ? <button className="outline api-settings-shortcut" onClick={() => setApiSettingsOpen(true)}><Settings size={15} />Configure API</button> : <button className="primary" disabled={!catalogApiReady || searching === activeItem.id || refreshingCatalogs[activeItem.id]} onClick={() => findBestProducts(activeItem)}>{searching === activeItem.id || refreshingCatalogs[activeItem.id] ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{refreshingCatalogs[activeItem.id] ? "Researching official sources…" : activeCatalog?.products.some((product) => product.freshness === "outdated") ? "Refresh outdated products" : "Search official catalogue"}</button>}</div>
                </div>
                {searchInfo[activeItem.id]?.last_verified_at && <div className="catalog-evidence"><ShieldCheck size={13} /><span>Catalogue verified {new Date(searchInfo[activeItem.id].last_verified_at!).toLocaleDateString()}{searchInfo[activeItem.id].stale ? " · refresh in progress" : ""}</span>{searchInfo[activeItem.id].usage && <small>{searchInfo[activeItem.id].usage!.total_tokens.toLocaleString()} API tokens used for that catalogue refresh</small>}</div>}
                <div className="product-identity">
                  <label><span>Brand</span><select value={activeItem.brand} disabled={browsingCatalog === activeItem.id} onChange={(event) => { updateIdentity(activeItem.id, "brand", event.target.value); setItemCatalogView(activeItem.id, "saved"); }}>{BRANDS.map((brand) => <option key={brand.name}>{brand.name}</option>)}</select></label>
                  <label><span>Product / family</span><input className="search-output" value={activeItem.product_name} readOnly /></label>
                  <label><span>Model number</span><input className="search-output" value={activeItem.model_no} readOnly /></label>
                  <label><span>Country of origin</span><input className="search-output" value={activeItem.country_of_origin} readOnly /></label>
                  <label><span>Product URL</span><input className="search-output" value={activeItem.product_url} readOnly /></label>
                  <label><span>Datasheet URL</span><input className="search-output" value={activeItem.datasheet_url} readOnly /></label>
                </div>
                <div className="catalog-browser-summary">
                  <div><strong>Saved {activeItem.brand} catalogue</strong><span>{browsingCatalog === activeItem.id ? "Loading catalogue…" : `${activeCatalog?.products.length || 0} products available`}</span></div>
                  <div className="catalog-summary-actions">
                    {catalogView !== "api" && (searchResults[activeItem.id]?.length || 0) > 0 && <button className="catalog-results-open" onClick={() => setItemCatalogView(activeItem.id, "api")}>View {searchResults[activeItem.id].length} search results</button>}
                    {catalogView !== "saved" && <button className="catalog-open" disabled={browsingCatalog === activeItem.id || !activeCatalog?.products.length} onClick={() => { setItemCatalogView(activeItem.id, "saved"); void loadSavedCatalog(activeItem); }}>Browse saved products</button>}
                    {catalogView !== "closed" && <button className="catalog-close" onClick={() => setItemCatalogView(activeItem.id, "closed")}><X size={14} />Close</button>}
                  </div>
                </div>
                {catalogView === "saved" && <div className="catalog-browser-toolbar">
                  <label><span>Brand</span><select value={activeItem.brand} onChange={(event) => updateIdentity(activeItem.id, "brand", event.target.value)}>{BRANDS.map((brand) => <option key={brand.name}>{brand.name}</option>)}</select></label>
                  <label><span>Product family</span><select value={activeCatalogFilters.family} onChange={(event) => updateCatalogFilter(activeItem.id, "family", event.target.value)}><option>All families</option>{catalogOptions.families.map((family) => <option key={family}>{family}</option>)}</select></label>
                  <label><span>Mounting</span><select value={activeCatalogFilters.mounting} onChange={(event) => updateCatalogFilter(activeItem.id, "mounting", event.target.value)}><option>All mounting types</option>{catalogOptions.mounting.map((mounting) => <option key={mounting}>{mounting}</option>)}</select></label>
                  <label><span>CCT</span><select value={activeCatalogFilters.cct} onChange={(event) => updateCatalogFilter(activeItem.id, "cct", event.target.value)}><option>All CCTs</option>{catalogOptions.cct.map((cct) => <option key={cct} value={cct}>{cct} K</option>)}</select></label>
                  <label><span>Control / driver</span><select value={activeCatalogFilters.control} onChange={(event) => updateCatalogFilter(activeItem.id, "control", event.target.value)}><option>All controls</option>{catalogOptions.controls.map((control) => <option key={control}>{control}</option>)}</select></label>
                </div>}
                {!browsingCatalog && activeCatalog && activeCatalog.products.length === 0 && <div className="empty-catalog"><strong>No saved {activeItem.brand} products yet.</strong><span>Run “Search official catalogue” to add verified products.</span></div>}
                {catalogView === "saved" && !catalogFilterActive && <div className="catalog-filter-prompt"><Search size={16} /><span><strong>Choose a configuration to begin.</strong> Select a family, mounting type, CCT, or control option to display matching products.</span></div>}
                {catalogView === "saved" && catalogFilterActive && filteredCatalogProducts.length === 0 && <div className="catalog-filter-prompt"><span><strong>No saved products match these filters.</strong> Close the catalogue and use the official API search above.</span></div>}
                {failedSearches[activeItem.id] && activeItem.product_name && <div className="stale-product-notice"><ShieldCheck size={14} /><span><strong>Previous finalized product retained.</strong> The latest search did not return a verified shortlist, so the product details below were not replaced.</span></div>}
                {catalogView !== "closed" && filteredCatalogProducts.length > 0 && <div className="api-results">
                  <div className="results-heading"><strong>{catalogView === "api" ? "Official API search results" : `Saved products matching your filters (${filteredCatalogProducts.length})`}</strong><span>{catalogView === "api" ? "Review the verified results and select the product to use." : "Review the filtered options and select the product to use."}</span></div>
                  <div className="result-grid">{filteredCatalogProducts.map((product, index) => {
                    const priceKey = `${activeItem.id}:${product.id}`;
                    const isSelectedProduct = selectedMatches[activeItem.id] === product.id;
                    const priceValue = candidatePrices[priceKey] ?? (isSelectedProduct && activeItem.unit_price != null ? String(activeItem.unit_price) : "");
                    const unitCurrency = candidateCurrencies[priceKey] || (isSelectedProduct && activeItem.unit_price_currency) || priceCurrency;
                    const unitPrice = Number(priceValue);
                    const hasMismatch = product.criteria.some((criterion) => criterion.status === "mismatch");
                    const hasUnknown = product.criteria.some((criterion) => criterion.status === "unknown");
                    return <article className={`api-product ${catalogView === "saved" ? "manual-catalog-product" : ""} ${selectedMatches[activeItem.id] === product.id ? "selected" : ""}`} key={product.id}>
                      <div className="result-rank">{catalogView === "api" ? `#${index + 1}` : <Search size={12} />}</div><div className="result-main"><strong>{product.product_name}</strong><span>{product.product_code || "Order code not published"} · {product.catalog_family || "Other products"}{catalogView === "api" ? ` · ${matchSummary(product)}` : ""}</span><div className="verification-row"><span className={`freshness-badge ${product.freshness || "current"}`}>{product.freshness === "outdated" ? "Outdated" : product.freshness === "incomplete" ? "Missing details" : "Current"}</span><span className={`verification-badge ${product.verification_level || "product_page"}`}><ShieldCheck size={11} />{verificationLabel(product)}</span>{product.verified_at && <small>Verified {new Date(product.verified_at).toLocaleDateString()}</small>}<small className={`manufacturer-date ${product.manufacturer_updated_at ? "published" : "unknown"}`}>Manufacturer updated: {displayManufacturerDate(product.manufacturer_updated_at)}</small></div><div className="product-facts">{productFacts(product).map((fact) => <small key={fact}>{fact}</small>)}</div><div className="result-links"><button onClick={() => openOfficialLink(product.product_url)}>Official product <ExternalLink size={11} /></button>{product.datasheet_url && <button onClick={() => openOfficialLink(product.datasheet_url!)}>Datasheet <ExternalLink size={11} /></button>}</div></div>{catalogView === "api" && <div className={`result-score ${product.score >= 80 ? "high" : ""}`}>{product.score}%<small>match</small></div>}<button className={selectedMatches[activeItem.id] === product.id ? "selected-product" : "select-product"} onClick={() => finalizeProduct(activeItem, product)}>{selectedMatches[activeItem.id] === product.id ? <><Check size={14} />Finalized</> : "Use product"}</button>
                      <div className="commercial-row"><span className={`tolerance-badge ${hasMismatch ? "outside" : hasUnknown ? "verify" : "inside"}`}>{hasMismatch ? "Outside one or more limits" : hasUnknown ? "Within known limits · verify gaps" : "Within selected tolerance"}</span><label><span>Supplier unit price</span><select className="currency-select" value={unitCurrency} onChange={(event) => { const currency = event.target.value as Currency; setCandidateCurrencies((current) => ({ ...current, [priceKey]: currency })); if (isSelectedProduct) updateItem(activeItem.id, (current) => ({ ...current, unit_price_currency: currency })); }}>{CURRENCIES.map((currency) => <option key={currency}>{currency}</option>)}</select><input type="number" min="0" step="0.001" value={priceValue} onChange={(event) => { const value = event.target.value; setCandidatePrices((current) => ({ ...current, [priceKey]: value })); if (isSelectedProduct) updateItem(activeItem.id, (current) => ({ ...current, unit_price: value === "" ? null : Number(value), unit_price_currency: value === "" ? null : unitCurrency })); }} placeholder="Optional" /></label>{unitPrice > 0 && activeItem.quantity > 0 && <strong>Supplier total: {unitCurrency} {(unitPrice * activeItem.quantity).toLocaleString(undefined, { maximumFractionDigits: 3 })}</strong>}</div>
                      <details className="criteria-details"><summary>View requirement comparison ({product.criteria.length})</summary><div className="criteria-grid"><strong>Category</strong><strong>Required</strong><strong>Offered</strong><strong>Result</strong>{product.criteria.map((criterion) => <div className="criterion-row" key={criterion.criterion}><span>{criterion.criterion}</span><span>{criterion.required}</span><span>{criterion.offered}</span><span className={`criterion-status ${criterion.status}`}>{criterion.status}</span></div>)}</div></details>
                    </article>;
                  })}</div>
                </div>}
                <div className="comparison-table comparison-head"><span>Parameter</span><span>Specified</span><span>Proposed</span><span>Status</span><span>Remarks / deviation</span></div>
                <div className="parameter-scroll comparison-scroll">
                  {activeItem.rows.map((row, index) => <div className="comparison-table" key={row.parameter}>
                    <strong>{row.parameter}</strong><div className="specified-readonly">{row.specified || "Not specified"}</div><textarea rows={2} value={row.proposed} onChange={(event) => updateRow(activeItem.id, index, "proposed", event.target.value)} placeholder="Offered value" /><select className={`status-select ${row.status}`} value={row.status} onChange={(event) => updateRow(activeItem.id, index, "status", event.target.value)}><option value="complies">Complies</option><option value="deviation">Deviation</option><option value="pending">Pending</option><option value="not_applicable">N/A</option></select><textarea rows={2} value={row.remarks} onChange={(event) => updateRow(activeItem.id, index, "remarks", event.target.value)} placeholder={row.status === "deviation" ? "Explain the deviation and impact" : "Optional engineer note"} />
                  </div>)}
                </div>
              </div>}
            </div>
            <div className="page-actions"><button className="secondary" onClick={() => setStep(1)}><ArrowLeft size={17} />Requirements</button><button className="primary" disabled={items.some((item) => !item.product_name.trim())} onClick={() => setStep(3)}>Prepare quotations <ArrowRight size={17} /></button></div>
          </section>
        )}

        {step === 3 && (
          <section className="content sheets-step">
            <div className="section-heading"><div><span className="section-kicker">SUBMISSION PACKAGE</span><h2>Technical and commercial quotations</h2><p>Select the products to include. Export the individual technical sheets or the commercial costing and offer workbook.</p></div><div className="summary-pills"><span className="good">{totals.complies} compliant</span><span className="bad">{totals.deviations} deviations</span><span>{totals.pending} pending</span></div></div>
            <div className="sheet-list">
              {items.map((item) => {
                const deviations = item.rows.filter((row) => row.status === "deviation").length;
                const pending = item.rows.filter((row) => row.status === "pending").length;
                return <article className={`sheet-card ${item.selected ? "selected" : ""}`} key={item.id}>
                  <label className="select-sheet"><input type="checkbox" checked={item.selected} onChange={(event) => updateItem(item.id, (current) => ({ ...current, selected: event.target.checked }))} /><span className="type-badge">{item.fitting_type}</span></label>
                  <div className="sheet-product"><strong>{item.product_name || "Product name pending"}</strong><span>{item.brand} · {item.model_no || "Model pending"}</span>{item.unit_price != null && <label className="sheet-price-currency"><span>Supplier price</span><select value={item.unit_price_currency || priceCurrency} onChange={(event) => updateItem(item.id, (current) => ({ ...current, unit_price_currency: event.target.value as Currency }))}>{CURRENCIES.map((currency) => <option key={currency}>{currency}</option>)}</select><strong>{item.unit_price.toLocaleString(undefined, { maximumFractionDigits: 3 })}</strong></label>}</div>
                  <div className="sheet-status">{deviations ? <span className="deviation-dot">{deviations} deviation{deviations === 1 ? "" : "s"}</span> : <span className="complies-dot"><CheckCircle2 size={14} />No deviations</span>}{pending > 0 && <small>{pending} pending</small>}</div>
                  <div className="sheet-actions"><button onClick={() => exportSheets("xlsx", item)}><FileSpreadsheet size={15} />Excel</button><button onClick={() => exportSheets("pdf", item)}><FileText size={15} />PDF</button></div>
                </article>;
              })}
            </div>
            <div className="exchange-rate-panel"><div className="exchange-rate-copy"><strong>Offer currency and exchange rates</strong><span>Enter how much one unit of each supplier currency equals in the selected offer currency.</span></div><label className="offer-currency"><span>Offer currency</span><select className="currency-select" value={priceCurrency} onChange={(event) => setPriceCurrency(event.target.value as Currency)}>{CURRENCIES.map((currency) => <option key={currency}>{currency}</option>)}</select></label><div className="exchange-rate-grid">{CURRENCIES.map((currency) => <label key={currency} className={currency === priceCurrency ? "base-rate" : ""}><span>1 {currency} =</span><input type="number" min="0" step="0.000001" disabled={currency === priceCurrency} value={currency === priceCurrency ? 1 : exchangeRateSets[priceCurrency][currency] ?? ""} onChange={(event) => { const value = event.target.value; setExchangeRateSets((current) => ({ ...current, [priceCurrency]: { ...current[priceCurrency], [currency]: value === "" ? undefined : Number(value) } })); }} placeholder="Rate" /><small>{priceCurrency}</small></label>)}</div></div>
            <div className="export-destination"><div><FolderOpen size={17} /><span><strong>Export folder</strong><small title={exportFolder}>{exportFolder || "Downloads"}</small></span></div><button className="secondary" disabled={busy || serviceStatus !== "ready"} onClick={selectExportFolder}>Choose folder</button></div>
            <div className="export-panel"><div><Download size={22} /><span><strong>Export selected package</strong><small>{selectedItems.length} product{selectedItems.length === 1 ? "" : "s"} selected · filenames use project, client, and reference</small></span></div><button className="secondary" disabled={!selectedItems.length || busy} onClick={exportCommercialQuotation}>{busy ? <LoaderCircle className="spin" size={17} /> : <FileSpreadsheet size={17} />}Commercial Excel</button><button className="secondary" disabled={!selectedItems.length || busy} onClick={() => exportSheets("xlsx")}>{busy ? <LoaderCircle className="spin" size={17} /> : <FileSpreadsheet size={17} />}Technical Excel</button><button className="primary" disabled={!selectedItems.length || busy} onClick={() => exportSheets("pdf")}>{busy ? <LoaderCircle className="spin" size={17} /> : <FileText size={17} />}Technical PDF</button></div>
            {lastExportAt && <div className="finish-project-panel"><div><CheckCircle2 size={20} /><span><strong>Exports completed?</strong><small>Archive this project and clear the workspace when you are ready to begin another.</small></span></div><button className="primary" disabled={busy} onClick={finishProjectAndStartNew}>Finish project and start new <ArrowRight size={16} /></button></div>}
            <div className="page-actions"><button className="secondary" onClick={() => setStep(2)}><ArrowLeft size={17} />Offered products</button></div>
          </section>
        )}
      </main>
    </div>
  );
}
