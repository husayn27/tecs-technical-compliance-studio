from __future__ import annotations

import json
import os
import re
import uuid
from urllib.parse import urlparse

import keyring
from openai import OpenAI

from .models import (
    CriterionResult,
    ProductMatch,
    ProductSearchRequest,
    ProductSearchResponse,
    ProductSpecifications,
)

KEYRING_SERVICE = "TECS Lighting Quotation"
KEYRING_USER = "openai-api-key"
APPROVED_BRANDS = {
    "Signify": "signify.com",
    "Modular Lighting": "supermodular.com",
    "Colour Kinetics": "colorkinetics.com",
    "Lite Magic": "litemagic.com",
    "LEDC4": "ledsc4.com",
    "LuxeLED": "luxeled.com",
    "Novolux": "novoluxlighting.com",
    "ATP": "atpiluminacion.com",
    "Plux B": "pluxb.com",
    "Floz": "flos.com",
    "RELCO": "relcogroup.com",
    "Unilamp": "unilamp.co.th",
    "Ligman": "ligman.com",
    "MP Illumination": "mpillumination.com",
    "Hepper": "heperlighting.com",
    "Faelluce": "faelluce.lighting",
    "Dialight": "dialight.com",
    "Airfal": "airfal.com",
    "3F Filippi": "3f-filippi.it",
    "Roger Pradier": "roger-pradier.com",
    "Francisconi": "francesconi.it",
    "Whitecroft Lighting": "whitecroftlighting.com",
}

# Some manufacturers publish their own datasheets on a separately hosted CDN.
# Keep this allowlist brand-specific and host-specific: generic CDN domains are
# never trusted for every brand.
TRUSTED_ASSET_DOMAINS = {
    "LuxeLED": (
        "90b00135-5a72-4c01-b37e-1f0325f9da2e.usrfiles.com",
    ),
}

VERIFIED_RESEARCH_STARTING_POINTS = {
    "LuxeLED": (
        "https://www.luxeled.com/product-page/mellow-iii",
        "https://90b00135-5a72-4c01-b37e-1f0325f9da2e.usrfiles.com/ugd/90b001_efb17c738c5d441cb8b5034f692dddb0.pdf",
    ),
}


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


def _score_product(
    request: ProductSearchRequest, specs: ProductSpecifications
) -> tuple[float, list[CriterionResult]]:
    fixture = request.fixture
    criteria: list[tuple[CriterionResult, int]] = []

    type_status = (
        "unknown" if specs.type_compatible is None
        else "match" if specs.type_compatible else "mismatch"
    )
    criteria.append(
        (_criterion("Fixture type", fixture.fixture_type, specs.product_type, type_status), 22)
    )
    if fixture.mounting:
        mounting_status = (
            "unknown" if specs.mounting_compatible is None
            else "match" if specs.mounting_compatible else "mismatch"
        )
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
        status = (
            "unknown" if specs.dimensions_compatible is None
            else "match" if specs.dimensions_compatible else "mismatch"
        )
        criteria.append(
            (_criterion("Dimensions", fixture.dimensions, specs.dimensions, status), 10)
        )
    if fixture.construction:
        status = (
            "unknown" if specs.construction_compatible is None
            else "match" if specs.construction_compatible else "mismatch"
        )
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
        status = (
            "unknown" if specs.optical_details_compatible is None
            else "match" if specs.optical_details_compatible else "mismatch"
        )
        criteria.append(
            (_criterion("Diffuser / reflector", fixture.optical_details, specs.optical_details, status), 8)
        )
    if fixture.controls:
        status = (
            "unknown" if specs.controls_compatible is None
            else "match" if specs.controls_compatible else "mismatch"
        )
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


def search_products(request: ProductSearchRequest) -> ProductSearchResponse:
    approved_domain = APPROVED_BRANDS.get(request.brand)
    if not approved_domain:
        raise ValueError("The selected manufacturer is not approved.")
    trusted_asset_domains = TRUSTED_ASSET_DOMAINS.get(request.brand, ())
    search_domains = [approved_domain, *trusted_asset_domains]
    research_starting_points = VERIFIED_RESEARCH_STARTING_POINTS.get(request.brand, ())
    client = OpenAI(api_key=_api_key())
    fixture = _anonymous_requirement(request)
    tolerance = request.tolerances.model_dump(mode="json")
    prompt = f"""
Search the official {request.brand} website for all distinct currently published,
orderable lighting products in the same fitting category as this fixture. Return up
to fifteen credible category-compatible candidates. Do not return an empty list just
because an exact wattage, lumen value, or other specification is not visible in the
first page snippet: inspect the product page and its linked datasheet, and return null
for genuinely unpublished values. The application performs the final numerical
tolerance checks locally.

Requirement JSON:
{json.dumps(fixture, indent=2)}

Permitted tolerances:
{json.dumps(tolerance, indent=2)}

Verified official research starting points for this manufacturer:
{json.dumps(research_starting_points, indent=2)}

Rules:
- Use only official manufacturer product or datasheet pages.
- Follow datasheet links published on the official product page, including the
  approved manufacturer CDN domains: {", ".join(trusted_asset_domains) or "none"}.
- Prefer the official manufacturer product page as product_url and its linked PDF
  as datasheet_url. A manufacturer-CDN PDF may be used as product_url only when no
  dedicated official product page is available.
- Return exact orderable products when possible, not generic editorial pages.
- Extract the exact published technical values into specifications.
- Treat the verified starting points only as pages to investigate. Return a product
  only when the official sources support it, and continue searching for other relevant
  products in the same category.
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
- Return no more than fifteen distinct products.
""".strip()

    schema = {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "maxItems": 15,
                "items": {
                    "type": "object",
                    "properties": {
                        "brand": {"type": "string"},
                        "product_name": {"type": "string"},
                        "product_code": {"type": ["string", "null"]},
                        "product_url": {"type": "string"},
                        "datasheet_url": {"type": ["string", "null"]},
                        "image_url": {"type": ["string", "null"]},
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
                                "controls": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "type_compatible": {"type": ["boolean", "null"]},
                                "mounting_compatible": {"type": ["boolean", "null"]},
                                "dimensions_compatible": {"type": ["boolean", "null"]},
                                "construction_compatible": {"type": ["boolean", "null"]},
                                "optical_details_compatible": {"type": ["boolean", "null"]},
                                "controls_compatible": {"type": ["boolean", "null"]},
                            },
                            "required": [
                                "product_type", "country_of_origin", "mounting", "mounting_height_mm", "wattage", "lumens", "cct",
                                "cri", "ip_rating", "ik_rating", "ugr", "emergency_hours",
                                "height_mm", "dimensions", "construction", "optical_details", "voltage", "beam_angle",
                                "efficacy_lm_w", "control_gear", "emergency_details", "led_life", "finish",
                                "waterproof", "controls", "type_compatible",
                                "mounting_compatible", "dimensions_compatible",
                                "construction_compatible", "optical_details_compatible",
                                "controls_compatible"
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "required": [
                        "brand", "product_name", "product_code", "product_url",
                        "datasheet_url", "image_url", "description", "specifications"
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }

    input_content: list[dict] = [{"type": "input_text", "text": prompt}]
    for starting_point in research_starting_points:
        if urlparse(starting_point).path.lower().endswith(".pdf"):
            input_content.append(
                {
                    "type": "input_file",
                    "file_url": starting_point,
                    "detail": "low",
                }
            )

    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.6"),
        reasoning={"effort": "low"},
        tools=[{
            "type": "web_search",
            "filters": {"allowed_domains": search_domains},
            "search_context_size": "medium",
        }],
        tool_choice="required",
        include=["web_search_call.action.sources"],
        input=[{"role": "user", "content": input_content}],
        text={
            "format": {
                "type": "json_schema",
                "name": "lighting_product_matches",
                "strict": True,
                "schema": schema,
            }
        },
        store=False,
    )
    payload = json.loads(response.output_text)
    matches: list[ProductMatch] = []
    seen_products: set[str] = set()
    warnings: list[str] = []
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
        specifications = ProductSpecifications(**item["specifications"])
        score, criteria = _score_product(request, specifications)
        matches.append(
            ProductMatch(
                id=str(uuid.uuid4()),
                brand=item["brand"],
                product_name=item["product_name"],
                product_code=item.get("product_code"),
                product_url=product_url,
                datasheet_url=_official_url(
                    item.get("datasheet_url"), approved_domain, trusted_asset_domains
                ),
                image_url=_official_url(
                    item.get("image_url"), approved_domain, trusted_asset_domains
                ),
                description=item["description"],
                specifications=specifications,
                score=score,
                criteria=criteria,
            )
        )
    matches.sort(key=lambda product: product.score, reverse=True)
    return ProductSearchResponse(
        matches=matches[:15], searched_domain=approved_domain, warnings=warnings
    )
