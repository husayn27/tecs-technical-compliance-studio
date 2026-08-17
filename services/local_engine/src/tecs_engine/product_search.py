from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date
from urllib.parse import urlparse

import keyring
from openai import APITimeoutError, OpenAI

from .brand_research import BRAND_RESEARCH_PROFILES, canonical_brand
from .models import (
    ApiUsage,
    CriterionResult,
    ProductMatch,
    ProductSearchRequest,
    ProductSearchResponse,
    ProductSpecifications,
)

KEYRING_SERVICE = "TECS Lighting Quotation"
KEYRING_USER = "openai-api-key"


def _anonymous_requirement(request: ProductSearchRequest) -> dict:
    fixture = request.fixture
    return {
        "fixture_type": fixture.fixture_type,
        "mounting": fixture.mounting,
        "mounting_height_mm": fixture.mounting_height_mm,
        "wattage": fixture.wattage,
        "wattage_options": fixture.wattage_options,
        "lumens": fixture.lumens,
        "lumen_options": fixture.lumen_options,
        "lumens_is_minimum": fixture.lumens_is_minimum,
        "cct": fixture.cct,
        "cct_options": fixture.cct_options,
        "cct_min": fixture.cct_min,
        "cct_max": fixture.cct_max,
        "cri": fixture.cri,
        "ip_rating": fixture.ip_rating,
        "ik_rating": fixture.ik_rating,
        "ugr": fixture.ugr,
        "dimensions": fixture.dimensions,
        "construction": fixture.construction,
        "optical_details": fixture.optical_details,
        "voltage": fixture.voltage,
        "beam_angle": fixture.beam_angle,
        "waterproof": fixture.waterproof,
        "emergency_hours": fixture.emergency_hours,
        "controls": fixture.controls,
    }


def _criterion(
    name: str,
    required: float | str | bool,
    offered: float | str | bool | None,
    status: str,
    unit: str = "",
) -> CriterionResult:
    def display(value) -> str:
        if value is None:
            return "Not published"
        if isinstance(value, bool):
            return "Compatible" if value else "Not compatible"
        return f"{value:,}{unit}" if isinstance(value, (float, int)) else str(value)

    return CriterionResult(
        criterion=name,
        required=display(required),
        offered=display(offered),
        status=status,
    )


def _target_status(required: float, offered: float | None, tolerance: float) -> str:
    if offered is None:
        return "unknown"
    difference = abs(offered - required) / required * 100 if required else 0
    if difference <= 2:
        return "match"
    return "tolerance" if difference <= tolerance else "mismatch"


def _minimum_status(required: float, offered: float | None, tolerance: float = 0) -> str:
    if offered is None:
        return "unknown"
    if offered >= required:
        return "match"
    shortfall = (required - offered) / required * 100 if required else 0
    return "tolerance" if shortfall <= tolerance else "mismatch"


def _options_status(options: list[float] | list[int], offered: float | None, tolerance: float = 0) -> str:
    if offered is None:
        return "unknown"
    statuses = [_target_status(float(required), float(offered), tolerance) for required in options]
    return "match" if "match" in statuses else "tolerance" if "tolerance" in statuses else "mismatch"


def _required_height(dimensions: str | None) -> float | None:
    if not dimensions or "height" not in dimensions.lower():
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*mm", dimensions, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _normalized_words(value: str | None) -> set[str]:
    if not value:
        return set()
    aliases = {
        "dowlinght": "downlight",
        "down light": "downlight",
        "recessed mounted": "recessed",
        "surface mounted": "surface",
        "fixed output": "fixed",
        "on/off": "fixed",
    }
    normalized = value.lower()
    for source, target in aliases.items():
        normalized = normalized.replace(source, target)
    return set(re.findall(r"[a-z0-9]+", normalized))


def _category_status(required: str, offered: str | None) -> str:
    if not offered:
        return "unknown"
    required_words = _normalized_words(required)
    offered_words = _normalized_words(offered)
    categories = (
        {"downlight", "spotlight"},
        {"panel", "troffer"},
        {"floodlight", "projector"},
        {"bollard"},
        {"streetlight", "road", "lantern"},
        {"linear", "batten", "trunking"},
        {"highbay", "lowbay"},
        {"wall", "sconce"},
        {"track"},
        {"strip", "tape"},
    )
    required_category = next((group for group in categories if required_words & group), None)
    offered_category = next((group for group in categories if offered_words & group), None)
    if required_category and offered_category:
        return "match" if required_category is offered_category else "mismatch"
    return "match" if required_words & offered_words else "unknown"


def _text_status(required: str, offered: str | None) -> str:
    if not offered:
        return "unknown"
    required_words = _normalized_words(required)
    offered_words = _normalized_words(offered)
    return "match" if required_words <= offered_words or required_words & offered_words else "mismatch"


def _controls_status(required: list[str], offered: list[str]) -> str:
    if not offered:
        return "unknown"
    offered_words = _normalized_words(" ".join(offered))
    return "match" if all(_normalized_words(value) & offered_words for value in required) else "mismatch"


def _score_product(
    request: ProductSearchRequest, specs: ProductSpecifications
) -> tuple[float, list[CriterionResult]]:
    fixture = request.fixture
    criteria: list[tuple[CriterionResult, int]] = []

    type_status = _category_status(fixture.fixture_type, specs.product_type)
    criteria.append(
        (_criterion("Fixture type", fixture.fixture_type, specs.product_type, type_status), 22)
    )
    if fixture.mounting:
        mounting_status = _text_status(fixture.mounting, specs.mounting)
        criteria.append(
            (_criterion("Mounting", fixture.mounting, specs.mounting, mounting_status), 14)
        )
    if fixture.wattage_options:
        status = _options_status(fixture.wattage_options, specs.wattage, request.tolerances.wattage_percent)
        required = "/".join(str(value).removesuffix(".0") for value in fixture.wattage_options) + " W"
        criteria.append((_criterion("Wattage options", required, specs.wattage, status, " W"), 16))
    elif fixture.wattage is not None:
        status = _target_status(fixture.wattage, specs.wattage, request.tolerances.wattage_percent)
        criteria.append((_criterion("Wattage", fixture.wattage, specs.wattage, status, " W"), 16))
    if fixture.lumen_options:
        status = _options_status(fixture.lumen_options, specs.lumens, request.tolerances.lumens_percent)
        required = "/".join(str(value) for value in fixture.lumen_options) + " lm"
        criteria.append((_criterion("Lumen options", required, specs.lumens, status, " lm"), 18))
    elif fixture.lumens is not None:
        status = (
            _minimum_status(
                fixture.lumens, specs.lumens, request.tolerances.lumens_percent
            )
            if fixture.lumens_is_minimum
            else _target_status(
                fixture.lumens, specs.lumens, request.tolerances.lumens_percent
            )
        )
        label = "Lumens (minimum)" if fixture.lumens_is_minimum else "Lumens"
        criteria.append((_criterion(label, fixture.lumens, specs.lumens, status, " lm"), 18))
    if fixture.cct_min is not None and fixture.cct_max is not None:
        status = "unknown" if specs.cct is None else "match" if fixture.cct_min <= specs.cct <= fixture.cct_max else "mismatch"
        criteria.append((_criterion("CCT range", f"{fixture.cct_min}–{fixture.cct_max} K", specs.cct, status, " K"), 10))
    elif fixture.cct_options:
        status = "unknown" if specs.cct is None else "match" if specs.cct in fixture.cct_options else "mismatch"
        criteria.append((_criterion("CCT options", "/".join(str(value) for value in fixture.cct_options) + " K", specs.cct, status, " K"), 10))
    elif fixture.cct is not None:
        status = "unknown" if specs.cct is None else (
            "match" if specs.cct == fixture.cct else "mismatch"
        )
        criteria.append((_criterion("CCT", fixture.cct, specs.cct, status, " K"), 10))
    if fixture.cri is not None:
        status = _minimum_status(fixture.cri, specs.cri)
        criteria.append((_criterion("CRI (minimum)", fixture.cri, specs.cri, status), 6))
    if fixture.ip_rating is not None:
        status = _minimum_status(fixture.ip_rating, specs.ip_rating)
        criteria.append((_criterion("IP rating", fixture.ip_rating, specs.ip_rating, status, ""), 10))
    if fixture.ik_rating is not None:
        status = _minimum_status(fixture.ik_rating, specs.ik_rating)
        criteria.append((_criterion("IK rating", fixture.ik_rating, specs.ik_rating, status, ""), 6))
    if fixture.ugr is not None:
        status = "unknown" if specs.ugr is None else (
            "match" if specs.ugr <= fixture.ugr else "mismatch"
        )
        criteria.append((_criterion("UGR (maximum)", fixture.ugr, specs.ugr, status), 8))
    if fixture.emergency_hours is not None:
        status = _minimum_status(fixture.emergency_hours, specs.emergency_hours)
        criteria.append(
            (
                _criterion(
                    "Emergency duration",
                    fixture.emergency_hours,
                    specs.emergency_hours,
                    status,
                    " h",
                ),
                12,
            )
        )
    required_height = _required_height(fixture.dimensions)
    if required_height is not None:
        status = _target_status(
            required_height, specs.height_mm, request.tolerances.dimensions_percent
        )
        criteria.append((_criterion("Height", required_height, specs.height_mm, status, " mm"), 14))
    elif fixture.dimensions:
        status = _text_status(fixture.dimensions, specs.dimensions)
        criteria.append(
            (_criterion("Dimensions", fixture.dimensions, specs.dimensions, status), 10)
        )
    if fixture.construction:
        status = _text_status(fixture.construction, specs.construction)
        criteria.append(
            (_criterion("Construction", fixture.construction, specs.construction, status), 6)
        )
    if fixture.voltage:
        status = "unknown" if specs.voltage is None else "match" if fixture.voltage.lower() in specs.voltage.lower() or specs.voltage.lower() in fixture.voltage.lower() else "mismatch"
        criteria.append((_criterion("Voltage", fixture.voltage, specs.voltage, status), 6))
    if fixture.beam_angle:
        status = "unknown" if specs.beam_angle is None else "match" if fixture.beam_angle == specs.beam_angle else "mismatch"
        criteria.append((_criterion("Beam angle", fixture.beam_angle, specs.beam_angle, status), 6))
    if fixture.waterproof is not None:
        status = "unknown" if specs.waterproof is None else "match" if fixture.waterproof == specs.waterproof else "mismatch"
        criteria.append((_criterion("Waterproof", fixture.waterproof, specs.waterproof, status), 5))
    if fixture.optical_details:
        status = _text_status(fixture.optical_details, specs.optical_details)
        criteria.append(
            (_criterion("Diffuser / reflector", fixture.optical_details, specs.optical_details, status), 8)
        )
    if fixture.controls:
        status = _controls_status(fixture.controls, specs.controls)
        criteria.append(
            (
                _criterion(
                    "Controls",
                    ", ".join(fixture.controls),
                    ", ".join(specs.controls) if specs.controls else None,
                    status,
                ),
                10,
            )
        )

    factors = {"match": 1.0, "tolerance": 0.72, "unknown": 0.35, "mismatch": 0.0}
    total_weight = sum(weight for _, weight in criteria)
    score = sum(factors[item.status] * weight for item, weight in criteria) / total_weight * 100
    if any(
        item.status == "mismatch"
        and item.criterion
        in {
            "Fixture type",
            "Mounting",
            "CCT",
            "CRI (minimum)",
            "IP rating",
            "IK rating",
            "UGR (maximum)",
            "Emergency duration",
            "Controls",
            "Construction",
            "Diffuser / reflector",
        }
        for item, _ in criteria
    ):
        score = min(score, 64)
    return round(score, 1), [item for item, _ in criteria]


def _official_url(
    value: str | None,
    domain: str,
    trusted_asset_domains: tuple[str, ...] = (),
) -> str | None:
    if not value:
        return None
    hostname = (urlparse(value).hostname or "").lower()
    allowed_domains = (domain, *trusted_asset_domains)
    for candidate in allowed_domains:
        allowed = candidate.lower().removeprefix("www.")
        if hostname == allowed or hostname.endswith(f".{allowed}"):
            return value
    return None


def save_api_key(api_key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, api_key)


def delete_api_key() -> bool:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except keyring.errors.PasswordDeleteError:
        pass
    return bool(os.getenv("OPENAI_API_KEY"))


def has_api_key() -> bool:
    if os.getenv("OPENAI_API_KEY"):
        return True
    try:
        return bool(keyring.get_password(KEYRING_SERVICE, KEYRING_USER))
    except keyring.errors.KeyringError:
        return False


def _api_key() -> str:
    key = os.getenv("OPENAI_API_KEY") or keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if not key:
        raise RuntimeError("OpenAI API key has not been configured.")
    return key


def _product_schema(max_items: int = 5) -> dict:
    return {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "properties": {
                        "brand": {"type": "string"},
                        "product_name": {"type": "string"},
                        "product_code": {"type": ["string", "null"]},
                        "model_number": {"type": ["string", "null"]},
                        "product_url": {"type": "string"},
                        "datasheet_url": {"type": ["string", "null"]},
                        "image_url": {"type": ["string", "null"]},
                        "manufacturer_updated_at": {"type": ["string", "null"]},
                        "evidence_urls": {
                            "type": "array",
                            "maxItems": 6,
                            "items": {"type": "string"},
                        },
                        "description": {"type": "string"},
                        "specifications": {
                            "type": "object",
                            "properties": {
                                "product_type": {"type": ["string", "null"]},
                                "country_of_origin": {"type": ["string", "null"]},
                                "mounting": {"type": ["string", "null"]},
                                "mounting_height_mm": {"type": ["number", "null"]},
                                "wattage": {"type": ["number", "null"]},
                                "lumens": {"type": ["integer", "null"]},
                                "cct": {"type": ["integer", "null"]},
                                "cri": {"type": ["integer", "null"]},
                                "ip_rating": {"type": ["integer", "null"]},
                                "ik_rating": {"type": ["integer", "null"]},
                                "ugr": {"type": ["number", "null"]},
                                "emergency_hours": {"type": ["number", "null"]},
                                "height_mm": {"type": ["number", "null"]},
                                "dimensions": {"type": ["string", "null"]},
                                "construction": {"type": ["string", "null"]},
                                "optical_details": {"type": ["string", "null"]},
                                "voltage": {"type": ["string", "null"]},
                                "beam_angle": {"type": ["string", "null"]},
                                "efficacy_lm_w": {"type": ["number", "null"]},
                                "control_gear": {"type": ["string", "null"]},
                                "emergency_details": {"type": ["string", "null"]},
                                "led_life": {"type": ["string", "null"]},
                                "finish": {"type": ["string", "null"]},
                                "waterproof": {"type": ["boolean", "null"]},
                                "controls": {"type": "array", "items": {"type": "string"}},
                                "type_compatible": {"type": ["boolean", "null"]},
                                "mounting_compatible": {"type": ["boolean", "null"]},
                                "dimensions_compatible": {"type": ["boolean", "null"]},
                                "construction_compatible": {"type": ["boolean", "null"]},
                                "optical_details_compatible": {"type": ["boolean", "null"]},
                                "controls_compatible": {"type": ["boolean", "null"]},
                            },
                            "required": [
                                "product_type", "country_of_origin", "mounting", "mounting_height_mm",
                                "wattage", "lumens", "cct", "cri", "ip_rating", "ik_rating", "ugr",
                                "emergency_hours", "height_mm", "dimensions", "construction",
                                "optical_details", "voltage", "beam_angle", "efficacy_lm_w",
                                "control_gear", "emergency_details", "led_life", "finish", "waterproof",
                                "controls", "type_compatible", "mounting_compatible",
                                "dimensions_compatible", "construction_compatible",
                                "optical_details_compatible", "controls_compatible",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "required": [
                        "brand", "product_name", "product_code", "model_number", "product_url", "datasheet_url",
                        "image_url", "manufacturer_updated_at", "evidence_urls", "description", "specifications",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }


def _discovery_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "properties": {
                        "product_name": {"type": "string"},
                        "product_code": {"type": ["string", "null"]},
                        "product_url": {"type": "string"},
                        "datasheet_url": {"type": ["string", "null"]},
                        "evidence_urls": {
                            "type": "array",
                            "maxItems": 6,
                            "items": {"type": "string"},
                        },
                        "publication_status": {
                            "type": "string",
                            "enum": ["current", "unknown", "discontinued"],
                        },
                    },
                    "required": [
                        "product_name", "product_code", "product_url", "datasheet_url",
                        "evidence_urls", "publication_status",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["candidates"],
        "additionalProperties": False,
    }


def _valid_official_urls(
    values: list[str | None], domain: str, trusted_asset_domains: tuple[str, ...]
) -> list[str]:
    valid: list[str] = []
    for value in values:
        official = _official_url(value, domain, trusted_asset_domains)
        if official and official not in valid:
            valid.append(official)
    return valid


def _verification_level(product_url: str, datasheet_url: str | None, evidence: list[str]) -> str:
    if datasheet_url or any(urlparse(url).path.lower().endswith(".pdf") for url in evidence):
        return "datasheet"
    return "multi_source" if len(evidence) >= 2 else "product_page"


def _manufacturer_date(value: str | None) -> str | None:
    """Accept only explicit ISO calendar dates returned from official evidence."""
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _research_client() -> OpenAI:
    # Deep verification can include several manufacturer PDFs plus live catalog
    # searches. 150 seconds was too short for otherwise successful responses on
    # slower catalog sites (notably Wix-hosted technical documents).
    timeout = float(os.getenv("TECS_PRODUCT_SEARCH_TIMEOUT_SECONDS", "240"))
    return OpenAI(api_key=_api_key(), timeout=timeout, max_retries=0)


def _response_usage(response) -> ApiUsage:
    usage = getattr(response, "usage", None)
    output_details = getattr(usage, "output_tokens_details", None) if usage else None
    output_items = getattr(response, "output", []) or []
    return ApiUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        reasoning_tokens=getattr(output_details, "reasoning_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        web_search_calls=sum(
            1 for item in output_items if getattr(item, "type", None) == "web_search_call"
        ),
    )


def _combine_usage(*values: ApiUsage) -> ApiUsage:
    return ApiUsage(
        input_tokens=sum(value.input_tokens for value in values),
        output_tokens=sum(value.output_tokens for value in values),
        reasoning_tokens=sum(value.reasoning_tokens for value in values),
        total_tokens=sum(value.total_tokens for value in values),
        web_search_calls=sum(value.web_search_calls for value in values),
    )


def search_products(request: ProductSearchRequest) -> ProductSearchResponse:
    request = request.model_copy(update={"brand": canonical_brand(request.brand)})
    profile = BRAND_RESEARCH_PROFILES.get(request.brand)
    if not profile:
        raise ValueError("The selected manufacturer is not approved.")
    approved_domain = profile.domain
    trusted_asset_domains = profile.trusted_asset_domains
    search_domains = [approved_domain, *trusted_asset_domains]
    client = _research_client()
    fixture = _anonymous_requirement(request)
    tolerance = request.tolerances.model_dump(mode="json")

    discovery_prompt = f"""
Perform a broad first-pass catalog investigation of {profile.official_name}. Find up to
24 distinct, current or possibly current product configurations in the same functional
category as the requirement. Search product families, product finders, configuration
pages, downloads, catalogs, and exact order-code pages. Do not filter candidates out
because a wattage or lumen value is absent from a search snippet; this pass discovers
the range and its technical evidence for a second verification pass.

Requirement JSON:
{json.dumps(fixture, indent=2)}

Official catalog starting pages:
{json.dumps(profile.catalog_pages, indent=2)}

Manufacturer-specific research instructions:
{profile.research_notes}

Rules:
- Use only the official domain and the approved manufacturer document domains.
- Follow links from catalog/category pages into individual products and downloads.
- Preserve distinct order codes/configurations. A family page is a lead, not proof of
  one exact configuration.
- Mark clearly discontinued products as discontinued; do not silently mix them with
  the active range.
- Return evidence URLs actually inspected for each candidate.
""".strip()

    discovery_usage = ApiUsage()
    discovery_warning: str | None = None
    try:
        discovery_response = client.with_options(
            timeout=min(client.timeout, 120.0)
        ).responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.6"),
            reasoning={"effort": "low"},
            tools=[{
                "type": "web_search",
                "filters": {"allowed_domains": search_domains},
                "search_context_size": "medium",
            }],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            input=discovery_prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "lighting_catalog_discovery",
                    "strict": True,
                    "schema": _discovery_schema(),
                }
            },
            store=False,
        )
        discovery_payload = json.loads(discovery_response.output_text)
        discovery_usage = _response_usage(discovery_response)
    except APITimeoutError:
        # Discovery is useful but not a prerequisite. Continue from the brand's
        # curated official catalogue pages/PDFs so one slow web-search pass can
        # never throw away the entire catalogue refresh.
        discovery_payload = {"candidates": []}
        discovery_warning = (
            "The broad discovery pass was slow; verification continued from the "
            "approved official catalogue pages and documents."
        )
    discovered: list[dict] = []
    discovered_pdfs = list(profile.verified_product_pdfs)
    for candidate in discovery_payload.get("candidates", []):
        if candidate.get("publication_status") == "discontinued":
            continue
        product_url = _official_url(candidate.get("product_url"), approved_domain, trusted_asset_domains)
        if not product_url:
            continue
        datasheet_url = _official_url(candidate.get("datasheet_url"), approved_domain, trusted_asset_domains)
        evidence = _valid_official_urls(
            [*candidate.get("evidence_urls", []), product_url, datasheet_url],
            approved_domain,
            trusted_asset_domains,
        )
        discovered.append({
            "product_name": candidate["product_name"],
            "product_code": candidate.get("product_code"),
            "product_url": product_url,
            "datasheet_url": datasheet_url,
            "evidence_urls": evidence,
        })
        for url in evidence:
            if urlparse(url).path.lower().endswith(".pdf") and url not in discovered_pdfs:
                discovered_pdfs.append(url)
    if not discovered:
        discovered = [
            {
                "product_name": f"{profile.official_name} official catalogue lead",
                "product_code": None,
                "product_url": url,
                "datasheet_url": None,
                "evidence_urls": [url],
            }
            for url in profile.catalog_pages
        ]

    verification_prompt = f"""
Complete a second-pass, evidence-based technical verification for {profile.official_name}.
Deeply inspect the official product pages, configuration/order tables, linked technical
downloads, and PDFs for the discovered candidates. Also continue searching the official
catalog in case the discovery pass missed a better category-compatible option. Return up
to {request.max_results} distinct, current, orderable configurations and extract variant-level specifications.

Requirement JSON:
{json.dumps(fixture, indent=2)}

Permitted tolerances (ranking is still performed locally):
{json.dumps(tolerance, indent=2)}

Official catalog starting pages:
{json.dumps(profile.catalog_pages, indent=2)}

Discovered candidates and evidence:
{json.dumps(discovered, indent=2)}

Manufacturer-specific research instructions:
{profile.research_notes}

Verification rules:
- Every returned option must have an official product page, exact official datasheet,
  official order table, or another official technical source supporting its identity.
- Do not copy one family-level value into every configuration. Match wattage, lumens,
  CCT, optics, controls, emergency, protection, and dimensions to the exact order code.
- If an exact variant cannot be identified, product_code must be null and uncertain
  specification fields must be null. Never assemble a fictional order code.
- model_number is the manufacturer's human-readable model/configuration designation. For
  Signify, it MUST be the exact value labelled "Order product name" (or "Full product name")
  in the official datasheet, never the numeric Order code, 12NC, EAN, or material number.
  Keep the numeric Signify Order code in product_code so catalogue identity remains stable.
- Prefer product_url as the exact product/configuration page. Put the technical PDF in
  datasheet_url and list every official page actually used in evidence_urls.
- manufacturer_updated_at is the most recent explicit manufacturer-issued product-page
  update date, datasheet revision date, or technical-document issue date, in ISO YYYY-MM-DD
  format. Use null when no official source explicitly publishes a reliable date. Do not use
  today's research date, a search-engine date, a download timestamp, or infer a date.
- Do not return discontinued/phased-out products unless the current manufacturer page
  explicitly shows they remain orderable; normally omit them.
- Return null rather than infer values from unrelated variants, marketing text, images,
  search snippets, or typical industry values.
- Extract country of origin, driver/control gear, efficacy, emergency details,
  LED lifetime/lumen maintenance and finish whenever the official source publishes them.
- type_compatible and mounting_compatible compare the published product to the requirement.
- dimensions_compatible, construction_compatible and optical_details_compatible compare every
  stated requirement with the official published specification. Mounting height describes the
  project installation position and is context, not a required physical product dimension.
- controls_compatible means the product
  natively supports, or is officially documented as compatible with, every required control.
- Use null whenever an official page does not publish a value. Never infer a value from a
  family code unless the official product page or datasheet explicitly defines it.
- Do not invent specifications. Use unknown when the official page does not state a value.
- Keep different order codes when they represent genuinely different configurations.
- Do not return the same order code or product page more than once.
- Return no more than {request.max_results} distinct products.
""".strip()

    input_content: list[dict] = [{"type": "input_text", "text": verification_prompt}]
    for pdf_url in discovered_pdfs[:4]:
        input_content.append({"type": "input_file", "file_url": pdf_url, "detail": "low"})
    has_official_pdf_input = any(
        item.get("type") == "input_file" for item in input_content
    )

    verification_timed_out = False
    verification_usage = ApiUsage()
    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.6"),
            reasoning={"effort": "medium"},
            tools=[{
                "type": "web_search",
                "filters": {"allowed_domains": search_domains},
                "search_context_size": "high",
            }],
            # Discovery has already searched the official sites. When official
            # PDFs are attached, let verification answer from those documents
            # without forcing another slow web-search round trip. Web search
            # remains available when the PDF does not contain enough evidence.
            tool_choice="auto" if has_official_pdf_input else "required",
            include=["web_search_call.action.sources"],
            input=[{"role": "user", "content": input_content}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "lighting_product_matches",
                    "strict": True,
                    "schema": _product_schema(request.max_results),
                }
            },
            store=False,
        )
        verification_usage = _response_usage(response)
        payload = json.loads(response.output_text)
    except APITimeoutError:
        verification_timed_out = True
        payload = {"matches": []}
    matches: list[ProductMatch] = []
    seen_products: set[str] = set()
    warnings: list[str] = []
    if discovery_warning:
        warnings.append(discovery_warning)
    if verification_timed_out:
        warnings.append(
            "The detailed verification pass timed out. No unverified discovery-only "
            "results were shown; retry the search to complete official-source verification."
        )
    for item in payload.get("matches", []):
        product_url = _official_url(
            item.get("product_url"), approved_domain, trusted_asset_domains
        )
        if not product_url:
            warnings.append("Skipped a result whose product URL was not on the official domain.")
            continue
        identity = (item.get("product_code") or product_url or item["product_name"]).strip().lower()
        if identity in seen_products:
            continue
        seen_products.add(identity)
        datasheet_url = _official_url(
            item.get("datasheet_url"), approved_domain, trusted_asset_domains
        )
        evidence_urls = _valid_official_urls(
            [*item.get("evidence_urls", []), product_url, datasheet_url],
            approved_domain,
            trusted_asset_domains,
        )
        specifications = ProductSpecifications(**item["specifications"])
        score, criteria = _score_product(request, specifications)
        matches.append(
            ProductMatch(
                id=str(uuid.uuid4()),
                brand=item["brand"],
                product_name=item["product_name"],
                product_code=item.get("product_code"),
                model_number=item.get("model_number"),
                product_url=product_url,
                datasheet_url=datasheet_url,
                image_url=_official_url(
                    item.get("image_url"), approved_domain, trusted_asset_domains
                ),
                description=item["description"],
                evidence_urls=evidence_urls,
                verification_level=_verification_level(
                    product_url, datasheet_url, evidence_urls
                ),
                manufacturer_updated_at=_manufacturer_date(item.get("manufacturer_updated_at")),
                specifications=specifications,
                score=score,
                criteria=criteria,
            )
        )
    matches.sort(key=lambda product: product.score, reverse=True)
    return ProductSearchResponse(
        matches=matches[:request.max_results],
        searched_domain=approved_domain,
        warnings=warnings,
        usage=_combine_usage(discovery_usage, verification_usage),
    )
