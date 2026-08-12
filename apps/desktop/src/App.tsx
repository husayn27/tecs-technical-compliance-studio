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
  Plus,
  Search,
  Save,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { approveFixtures, downloadCompliance, extract, health, saveApiKey, searchProducts } from "./api";
import tecsLogo from "./assets/tecs-logo.png";
import type { ComplianceItem, ComplianceRow, ComplianceStatus, Fixture, LegendRequirements, LocalAIStatus, Product, ProjectDetails } from "./types";

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
  const proposed: Record<string, string> = {
    Description: product.description,
    Make: product.brand,
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
    brand: product.brand,
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

export default function App() {
  const [step, setStep] = useState(0);
  const [project, setProject] = useState<ProjectDetails>({ project_name: "", client: "", consultant: "", contractor: "", reference: "" });
  const [projectId, setProjectId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [sourceFixtures, setSourceFixtures] = useState<Fixture[]>([]);
  const [items, setItems] = useState<ComplianceItem[]>([]);
  const [activeId, setActiveId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [localAI, setLocalAI] = useState<LocalAIStatus | null>(null);
  const [apiReady, setApiReady] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [searching, setSearching] = useState("");
  const [searchResults, setSearchResults] = useState<Record<string, Product[]>>({});
  const [selectedMatches, setSelectedMatches] = useState<Record<string, string>>({});
  const [searchTolerance, setSearchTolerance] = useState({ lumens_percent: 10, wattage_percent: 15 });
  const [candidatePrices, setCandidatePrices] = useState<Record<string, string>>({});
  const [priceCurrency, setPriceCurrency] = useState<"OMR" | "AED" | "USD">("OMR");
  const [restored, setRestored] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const draft = JSON.parse(raw);
        if (draft.project) setProject(draft.project);
        if (Array.isArray(draft.items)) setItems(draft.items);
        if (draft.activeId) setActiveId(draft.activeId);
      }
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
    setRestored(true);
    health().then((value) => { setLocalAI(value.local_ai); setApiReady(value.api_key_configured); }).catch(() => setLocalAI(null));
  }, []);

  useEffect(() => {
    if (!restored) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ project, items, activeId }));
  }, [project, items, activeId, restored]);

  const activeItem = items.find((item) => item.id === activeId) || items[0];
  const selectedItems = items.filter((item) => item.selected);
  const totals = useMemo(() => {
    const rows = items.flatMap((item) => item.rows);
    return {
      complies: rows.filter((row) => row.status === "complies").length,
      deviations: rows.filter((row) => row.status === "deviation").length,
      pending: rows.filter((row) => row.status === "pending").length,
    };
  }, [items]);

  function updateProject(key: keyof ProjectDetails, value: string) {
    setProject((current) => ({ ...current, [key]: value }));
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
  }

  function updateIdentity(id: string, key: "brand" | "country_of_origin" | "model_no" | "product_name", value: string) {
    if (key === "brand") {
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
    if (!apiKey.trim()) return;
    setBusy(true);
    setError("");
    try {
      await saveApiKey(apiKey.trim());
      setApiKey("");
      setApiReady(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save the API key.");
    } finally {
      setBusy(false);
    }
  }

  async function findBestProducts(item: ComplianceItem) {
    const brand = BRANDS.find((candidate) => candidate.name === item.brand) || BRANDS[0];
    setSearching(item.id);
    setError("");
    try {
      const response = await searchProducts(itemToFixture(item), brand.name, searchTolerance);
      setSearchResults((current) => ({ ...current, [item.id]: response.matches }));
      if (!response.matches.length) setError(`No verified ${brand.name} products were returned. Review the requirements or increase the lumen and wattage tolerances.`);
      else if (response.warnings.length) setWarnings(response.warnings);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The product search could not be completed.");
    } finally {
      setSearching("");
    }
  }

  function finalizeProduct(item: ComplianceItem, product: Product) {
    updateItem(item.id, (current) => applyProduct(current, product));
    setSelectedMatches((current) => ({ ...current, [item.id]: product.id }));
  }

  async function exportSheets(format: "xlsx" | "pdf", only?: ComplianceItem) {
    setBusy(true);
    setError("");
    try {
      const exportItems = only ? [{ ...only, selected: true }] : items;
      const suffix = only ? `${only.fitting_type.replace(/[^a-z0-9]+/gi, "-")}-Technical-Compliance` : "TECS-Technical-Compliance";
      await downloadCompliance(format, project, exportItems, suffix);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the technical sheets.");
    } finally {
      setBusy(false);
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
          <div className="top-statuses"><div className={`engine-chip ${localAI?.available ? "ready" : ""}`}><ShieldCheck size={14} />{localAI?.available ? "Drawing AI ready" : "Manual mode ready"}</div><div className={`engine-chip ${apiReady ? "ready" : ""}`}><Search size={14} />{apiReady ? "Product API ready" : "Product API setup required"}</div></div>
        </header>
        {error && <div className="error-banner">{error}<button onClick={() => setError("")}>Dismiss</button></div>}

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
            <div className="section-heading"><div><span className="section-kicker">AI PRODUCT SELECTION</span><h2>Find and finalize the best product</h2><p>Select a brand. The API searches its official website, ranks verified options, and prepares the comparison for engineer approval.</p></div><span className="count-pill">{BRANDS.length} brands available</span></div>
            <div className="split-workspace products-workspace">
              <div className="item-list">
                {items.map((item) => <button key={item.id} className={`item-tab ${activeItem?.id === item.id ? "active" : ""}`} onClick={() => setActiveId(item.id)}><span className="type-badge">{item.fitting_type}</span><span><strong>{item.product_name || "Product not entered"}</strong><small>{item.brand} · {item.model_no || "model pending"}</small></span></button>)}
              </div>
              {activeItem && <div className="editor-card product-editor">
                <div className="ai-search-panel">
                  <div className="search-panel-copy"><div className="ai-icon"><Search size={18} /></div><div><strong>Official {activeItem.brand} product search</strong><span>Only the approved manufacturer domain is searched. Results are scored against the engineer-entered requirement.</span></div></div>
                  {!apiReady ? <div className="api-key-setup"><KeyRound size={16} /><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Enter OpenAI API key" /><button className="primary" disabled={!apiKey.trim() || busy} onClick={configureApiKey}>Save API key</button></div> : <div className="search-controls"><label className="tolerance-field"><span>Lumens ±</span><input type="number" min="0" max="100" value={searchTolerance.lumens_percent} onChange={(event) => setSearchTolerance((current) => ({ ...current, lumens_percent: Number(event.target.value) }))} /><small>%</small></label><label className="tolerance-field"><span>Watts ±</span><input type="number" min="0" max="100" value={searchTolerance.wattage_percent} onChange={(event) => setSearchTolerance((current) => ({ ...current, wattage_percent: Number(event.target.value) }))} /><small>%</small></label><button className="primary" disabled={searching === activeItem.id} onClick={() => findBestProducts(activeItem)}>{searching === activeItem.id ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}Search matching options</button></div>}
                </div>
                <div className="product-identity">
                  <label><span>Brand</span><select value={activeItem.brand} onChange={(event) => updateIdentity(activeItem.id, "brand", event.target.value)}>{BRANDS.map((brand) => <option key={brand.name}>{brand.name}</option>)}</select></label>
                  <label><span>Product / family</span><input className="search-output" value={activeItem.product_name} readOnly /></label>
                  <label><span>Model number</span><input className="search-output" value={activeItem.model_no} readOnly /></label>
                  <label><span>Country of origin</span><input className="search-output" value={activeItem.country_of_origin} readOnly /></label>
                  <label><span>Product URL</span><input className="search-output" value={activeItem.product_url} readOnly /></label>
                  <label><span>Datasheet URL</span><input className="search-output" value={activeItem.datasheet_url} readOnly /></label>
                </div>
                {(searchResults[activeItem.id]?.length ?? 0) > 0 && <div className="api-results">
                  <div className="results-heading"><strong>Technical shortlist · {searchResults[activeItem.id].length} distinct option{searchResults[activeItem.id].length === 1 ? "" : "s"}</strong><span>Compare compliance and enter quoted prices before finalizing the commercial choice.</span></div>
                  <div className="result-grid">{searchResults[activeItem.id].map((product, index) => {
                    const priceKey = `${activeItem.id}:${product.id}`;
                    const unitPrice = Number(candidatePrices[priceKey]);
                    const hasMismatch = product.criteria.some((criterion) => criterion.status === "mismatch");
                    const hasUnknown = product.criteria.some((criterion) => criterion.status === "unknown");
                    return <article className={`api-product ${selectedMatches[activeItem.id] === product.id ? "selected" : ""}`} key={product.id}>
                      <div className="result-rank">#{index + 1}</div><div className="result-main"><strong>{product.product_name}</strong><span>{product.product_code || "Order code not published"} · {matchSummary(product)}</span><div className="product-facts">{productFacts(product).map((fact) => <small key={fact}>{fact}</small>)}</div><div className="result-links"><a href={product.product_url} target="_blank" rel="noreferrer">Official product <ExternalLink size={11} /></a>{product.datasheet_url && <a href={product.datasheet_url} target="_blank" rel="noreferrer">Datasheet <ExternalLink size={11} /></a>}</div></div><div className={`result-score ${product.score >= 80 ? "high" : ""}`}>{product.score}%<small>match</small></div><button className={selectedMatches[activeItem.id] === product.id ? "selected-product" : "select-product"} onClick={() => finalizeProduct(activeItem, product)}>{selectedMatches[activeItem.id] === product.id ? <><Check size={14} />Finalized</> : "Use product"}</button>
                      <div className="commercial-row"><span className={`tolerance-badge ${hasMismatch ? "outside" : hasUnknown ? "verify" : "inside"}`}>{hasMismatch ? "Outside one or more limits" : hasUnknown ? "Within known limits · verify gaps" : "Within selected tolerance"}</span><label><span>Quoted unit price</span><select className="currency-select" value={priceCurrency} onChange={(event) => setPriceCurrency(event.target.value as "OMR" | "AED" | "USD")}><option value="OMR">OMR</option><option value="AED">AED</option><option value="USD">USD</option></select><input type="number" min="0" step="0.001" value={candidatePrices[priceKey] || ""} onChange={(event) => setCandidatePrices((current) => ({ ...current, [priceKey]: event.target.value }))} placeholder="Optional" /></label>{unitPrice > 0 && activeItem.quantity > 0 && <strong>Total: {priceCurrency} {(unitPrice * activeItem.quantity).toLocaleString(undefined, { maximumFractionDigits: 3 })}</strong>}</div>
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
            <div className="page-actions"><button className="secondary" onClick={() => setStep(1)}><ArrowLeft size={17} />Requirements</button><button className="primary" disabled={items.some((item) => !item.product_name.trim())} onClick={() => setStep(3)}>Prepare technical sheets <ArrowRight size={17} /></button></div>
          </section>
        )}

        {step === 3 && (
          <section className="content sheets-step">
            <div className="section-heading"><div><span className="section-kicker">SUBMISSION PACKAGE</span><h2>Individual technical data sheets</h2><p>Select the products to include. Each fitting exports as its own formatted sheet or PDF page.</p></div><div className="summary-pills"><span className="good">{totals.complies} compliant</span><span className="bad">{totals.deviations} deviations</span><span>{totals.pending} pending</span></div></div>
            <div className="sheet-list">
              {items.map((item) => {
                const deviations = item.rows.filter((row) => row.status === "deviation").length;
                const pending = item.rows.filter((row) => row.status === "pending").length;
                return <article className={`sheet-card ${item.selected ? "selected" : ""}`} key={item.id}>
                  <label className="select-sheet"><input type="checkbox" checked={item.selected} onChange={(event) => updateItem(item.id, (current) => ({ ...current, selected: event.target.checked }))} /><span className="type-badge">{item.fitting_type}</span></label>
                  <div className="sheet-product"><strong>{item.product_name || "Product name pending"}</strong><span>{item.brand} · {item.model_no || "Model pending"}</span></div>
                  <div className="sheet-status">{deviations ? <span className="deviation-dot">{deviations} deviation{deviations === 1 ? "" : "s"}</span> : <span className="complies-dot"><CheckCircle2 size={14} />No deviations</span>}{pending > 0 && <small>{pending} pending</small>}</div>
                  <div className="sheet-actions"><button onClick={() => exportSheets("xlsx", item)}><FileSpreadsheet size={15} />Excel</button><button onClick={() => exportSheets("pdf", item)}><FileText size={15} />PDF</button></div>
                </article>;
              })}
            </div>
            <div className="export-panel"><div><Download size={22} /><span><strong>Export selected package</strong><small>{selectedItems.length} product{selectedItems.length === 1 ? "" : "s"} selected</small></span></div><button className="secondary" disabled={!selectedItems.length || busy} onClick={() => exportSheets("xlsx")}>{busy ? <LoaderCircle className="spin" size={17} /> : <FileSpreadsheet size={17} />}Excel workbook</button><button className="primary" disabled={!selectedItems.length || busy} onClick={() => exportSheets("pdf")}>{busy ? <LoaderCircle className="spin" size={17} /> : <FileText size={17} />}Combined PDF</button></div>
            <div className="page-actions"><button className="secondary" onClick={() => setStep(2)}><ArrowLeft size={17} />Offered products</button></div>
          </section>
        )}
      </main>
    </div>
  );
}
