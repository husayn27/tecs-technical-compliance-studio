export type Fixture = {
  id: string;
  symbol: string;
  description: string;
  quantity: number;
  drawing_quantity?: number | null;
  units_per_assembly?: number;
  fixture_type: string;
  mounting?: string | null;
  mounting_height_mm?: number | null;
  wattage?: number | null;
  wattage_options: number[];
  lumens?: number | null;
  lumen_options: number[];
  lumens_is_minimum?: boolean;
  cct?: number | null;
  cct_options: number[];
  cct_min?: number | null;
  cct_max?: number | null;
  cri?: number | null;
  ip_rating?: number | null;
  ik_rating?: number | null;
  ugr?: number | null;
  dimensions?: string | null;
  construction?: string | null;
  optical_details?: string | null;
  voltage?: string | null;
  beam_angle?: string | null;
  led_density_per_m?: number | null;
  waterproof?: boolean | null;
  emergency_hours?: number | null;
  controls: string[];
  source_file: string;
  source_page: number;
  document_id?: string | null;
  profile_id?: string | null;
  profile_score?: number | null;
  evidence_url?: string | null;
  confidence: number;
  status: "review" | "confirmed";
};

export type LocalAIStatus = {
  state: "ready" | "starting" | "not_installed" | "error";
  available: boolean;
  model?: string | null;
  runtime?: string | null;
  message: string;
};

export type Criterion = {
  criterion: string;
  required: string;
  offered: string;
  status: "match" | "tolerance" | "mismatch" | "unknown";
};

export type Product = {
  id: string;
  brand: string;
  product_name: string;
  product_code?: string | null;
  model_number?: string | null;
  product_url: string;
  datasheet_url?: string | null;
  image_url?: string | null;
  description: string;
  evidence_urls?: string[];
  verification_level?: "datasheet" | "multi_source" | "product_page";
  manufacturer_updated_at?: string | null;
  specifications: {
    product_type?: string | null;
    country_of_origin?: string | null;
    mounting?: string | null;
    mounting_height_mm?: number | null;
    wattage?: number | null;
    lumens?: number | null;
    cct?: number | null;
    cri?: number | null;
    ip_rating?: number | null;
    ik_rating?: number | null;
    ugr?: number | null;
    emergency_hours?: number | null;
    height_mm?: number | null;
    dimensions?: string | null;
    construction?: string | null;
    optical_details?: string | null;
    voltage?: string | null;
    beam_angle?: string | null;
    efficacy_lm_w?: number | null;
    control_gear?: string | null;
    emergency_details?: string | null;
    led_life?: string | null;
    finish?: string | null;
    waterproof?: boolean | null;
    controls?: string[];
    type_compatible?: boolean | null;
    mounting_compatible?: boolean | null;
    dimensions_compatible?: boolean | null;
    construction_compatible?: boolean | null;
    optical_details_compatible?: boolean | null;
    controls_compatible?: boolean | null;
  };
  score: number;
  criteria: Criterion[];
  catalog_family?: string;
  verified_at?: string;
  freshness?: "current" | "outdated" | "incomplete";
};

export type CatalogBrowseResponse = {
  products: Product[];
  families: string[];
  facets?: {
    families: string[];
    mounting: string[];
    cct: number[];
    controls: string[];
  };
  requirement_family: string;
  freshness_days: number;
};

export type ApiUsage = {
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  web_search_calls: number;
};

export type ProductSearchResponse = {
  matches: Product[];
  warnings: string[];
  searched_domain: string;
  source: "live" | "catalog";
  refreshing: boolean;
  stale: boolean;
  last_verified_at?: string | null;
  usage?: ApiUsage | null;
};

export type CatalogStatus = {
  scopes: number;
  brands: number;
  products: number;
  active_refreshes: number;
  freshness_days: number;
  last_verified_at?: string | null;
  shared_products?: number;
  team_catalog?: TeamCatalogStatus;
};

export type TeamCatalogStatus = {
  configured: boolean;
  syncing: boolean;
  last_sync_at?: string | null;
  last_error?: string | null;
  shared_products: number;
};

export type QuoteLine = { fixture: Fixture; product: Product };

export type ProjectDetails = {
  project_name: string;
  client: string;
  consultant: string;
  contractor: string;
  reference: string;
};

export type ComplianceStatus = "complies" | "deviation" | "pending" | "not_applicable";

export type Currency = "OMR" | "AED" | "USD" | "GBP" | "EUR";

export type ComplianceRow = {
  parameter: string;
  specified: string;
  proposed: string;
  status: ComplianceStatus;
  remarks: string;
};

export type LegendRequirements = {
  light_type: string;
  description: string;
  mounting: string;
  wattage: number | null;
  lumens: number | null;
  cct: number | null;
  cri: number | null;
  ip_rating: number | null;
  ik_rating: number | null;
  ugr: number | null;
  controls: string;
};

export type ComplianceItem = {
  id: string;
  fitting_type: string;
  quantity: number;
  selected: boolean;
  brand: string;
  product_name: string;
  country_of_origin: string;
  model_no: string;
  product_url: string;
  datasheet_url: string;
  unit_price: number | null;
  unit_price_currency?: Currency | null;
  legend: LegendRequirements;
  rows: ComplianceRow[];
};
