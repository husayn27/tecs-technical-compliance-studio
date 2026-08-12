from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class FixtureRequirement(BaseModel):
    id: str
    symbol: str
    description: str
    quantity: int = Field(default=1, ge=0)
    drawing_quantity: int | None = Field(default=None, ge=0)
    units_per_assembly: int = Field(default=1, ge=1)
    fixture_type: str = "unspecified"
    mounting: str | None = None
    mounting_height_mm: float | None = None
    wattage: float | None = None
    wattage_options: list[float] = Field(default_factory=list)
    lumens: int | None = None
    lumen_options: list[int] = Field(default_factory=list)
    lumens_is_minimum: bool = False
    cct: int | None = None
    cct_options: list[int] = Field(default_factory=list)
    cct_min: int | None = None
    cct_max: int | None = None
    cri: int | None = None
    ip_rating: int | None = None
    ik_rating: int | None = None
    ugr: float | None = None
    dimensions: str | None = None
    construction: str | None = None
    optical_details: str | None = None
    voltage: str | None = None
    beam_angle: str | None = None
    led_density_per_m: int | None = None
    waterproof: bool | None = None
    emergency_hours: float | None = None
    controls: list[str] = Field(default_factory=list)
    source_file: str
    source_page: int = 1
    document_id: str | None = None
    profile_id: str | None = None
    profile_score: float | None = Field(default=None, ge=0, le=1)
    evidence_url: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: Literal["review", "confirmed"] = "review"


class LocalAIStatus(BaseModel):
    state: Literal["ready", "starting", "not_installed", "error"]
    available: bool = False
    model: str | None = None
    runtime: str | None = None
    message: str


class ExtractionResponse(BaseModel):
    project_id: str
    project_name: str
    fixtures: list[FixtureRequirement]
    warnings: list[str] = Field(default_factory=list)
    analysis_engine: Literal["local_ai", "rules"] = "rules"


class FixtureApprovalRequest(BaseModel):
    fixtures: list[FixtureRequirement]


class Tolerances(BaseModel):
    lumens_percent: float = Field(default=10, ge=0, le=100)
    wattage_percent: float = Field(default=15, ge=0, le=100)
    dimensions_percent: float = Field(default=15, ge=0, le=100)


class ProductSearchRequest(BaseModel):
    fixture: FixtureRequirement
    brand: str
    domain: str | None = None
    tolerances: Tolerances = Field(default_factory=Tolerances)


class CriterionResult(BaseModel):
    criterion: str
    required: str
    offered: str
    status: Literal["match", "tolerance", "mismatch", "unknown"]


class ProductSpecifications(BaseModel):
    product_type: str | None = None
    country_of_origin: str | None = None
    mounting: str | None = None
    mounting_height_mm: float | None = None
    wattage: float | None = None
    lumens: int | None = None
    cct: int | None = None
    cri: int | None = None
    ip_rating: int | None = None
    ik_rating: int | None = None
    ugr: float | None = None
    emergency_hours: float | None = None
    height_mm: float | None = None
    dimensions: str | None = None
    construction: str | None = None
    optical_details: str | None = None
    voltage: str | None = None
    beam_angle: str | None = None
    efficacy_lm_w: float | None = None
    control_gear: str | None = None
    emergency_details: str | None = None
    led_life: str | None = None
    finish: str | None = None
    waterproof: bool | None = None
    controls: list[str] = Field(default_factory=list)
    type_compatible: bool | None = None
    mounting_compatible: bool | None = None
    dimensions_compatible: bool | None = None
    construction_compatible: bool | None = None
    optical_details_compatible: bool | None = None
    controls_compatible: bool | None = None


class ProductMatch(BaseModel):
    id: str
    brand: str
    product_name: str
    product_code: str | None = None
    product_url: HttpUrl
    datasheet_url: HttpUrl | None = None
    image_url: HttpUrl | None = None
    description: str
    specifications: ProductSpecifications = Field(default_factory=ProductSpecifications)
    score: float = Field(ge=0, le=100)
    criteria: list[CriterionResult]


class ProductSearchResponse(BaseModel):
    matches: list[ProductMatch]
    searched_domain: str
    warnings: list[str] = Field(default_factory=list)


class SelectedLine(BaseModel):
    fixture: FixtureRequirement
    product: ProductMatch


class QuoteRequest(BaseModel):
    project_name: str
    customer_name: str | None = None
    reference: str | None = None
    lines: list[SelectedLine]


class ApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=20)


class ProjectDetails(BaseModel):
    project_name: str
    client: str | None = None
    consultant: str | None = None
    contractor: str | None = None
    reference: str | None = None


class ComplianceRow(BaseModel):
    parameter: str
    specified: str = ""
    proposed: str = ""
    status: Literal["complies", "deviation", "pending", "not_applicable"] = "pending"
    remarks: str = ""


class TechnicalItem(BaseModel):
    id: str
    fitting_type: str
    quantity: int = Field(default=1, ge=0)
    selected: bool = True
    brand: str = ""
    product_name: str = ""
    country_of_origin: str = ""
    model_no: str = ""
    product_url: str | None = None
    datasheet_url: str | None = None
    rows: list[ComplianceRow] = Field(default_factory=list)


class TechnicalSheetRequest(BaseModel):
    project: ProjectDetails
    items: list[TechnicalItem]
