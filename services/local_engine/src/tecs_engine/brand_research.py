from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrandResearchProfile:
    """Official research surfaces and manufacturer-specific search guidance."""

    official_name: str
    domain: str
    catalog_pages: tuple[str, ...]
    trusted_asset_domains: tuple[str, ...] = ()
    verified_product_pdfs: tuple[str, ...] = ()
    research_notes: str = ""


# Manufacturer catalogues and older projects sometimes use the product brand
# (Philips) while the approved research profile uses its parent company
# (Signify). Keep one canonical key for lookups without losing compatibility
# with records already saved under either label.
BRAND_ALIASES: dict[str, str] = {
    "signify": "Signify",
    "philips": "Signify",
    "philips lighting": "Signify",
    "signify / philips": "Signify",
    "signify/philips": "Signify",
}


def canonical_brand(value: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    return BRAND_ALIASES.get(cleaned.casefold(), cleaned)


def brand_variants(value: str) -> tuple[str, ...]:
    canonical = canonical_brand(value)
    if canonical == "Signify":
        return ("Signify", "Philips", "Philips Lighting", "Signify / Philips", "Signify/Philips")
    return (canonical,)


# These are intentionally manufacturer-specific. A document host is trusted only
# for the brand that publishes through it; generic file-hosting domains are never
# accepted globally.
BRAND_RESEARCH_PROFILES: dict[str, BrandResearchProfile] = {
    "Signify": BrandResearchProfile(
        "Signify / Philips professional lighting",
        "signify.com",
        ("https://www.signify.com/global/prof",),
        research_notes="Use the professional eCatalogue. Open the exact configured product and its Downloads tab; Signify order codes often identify variants more precisely than family pages.",
    ),
    "Modular Lighting": BrandResearchProfile(
        "Modular Lighting Instruments",
        "supermodular.com",
        ("https://www.supermodular.com/en/products/", "https://brochures.supermodular.com/catalogue/"),
        trusted_asset_domains=("brochures.supermodular.com", "media.supermodular.com"),
        research_notes="Search product designs and article numbers, then inspect the brochure, technical downloads, and configurator for the exact configuration.",
    ),
    "Colour Kinetics": BrandResearchProfile(
        "Color Kinetics",
        "colorkinetics.com",
        ("https://www.colorkinetics.com/global/products", "https://www.colorkinetics.com/global/products/navigator"),
        trusted_asset_domains=("docs.colorkinetics.com",),
        research_notes="Use Lighting Navigator and the product-family pages. Distinguish LED mix, environment, beam, voltage, and controller/power-supply requirements.",
    ),
    "Lite Magic": BrandResearchProfile(
        "LiteMagic",
        "litemagic.com",
        ("https://en.litemagic.com/products/",),
        research_notes="Search the full Middle East product range and follow the individual product downloads for exact BCP/BGC/BCS/BVP/BWS order families.",
    ),
    "LEDC4": BrandResearchProfile(
        "LedsC4",
        "ledsc4.com",
        ("https://ledsc4.com/en/",),
        research_notes="Use the Architectural, Outdoor, and Decorative product filters, then verify the exact reference in technical resources or a current catalog.",
    ),
    "LuxeLED": BrandResearchProfile(
        "LuxeLED",
        "luxeled.com",
        ("https://www.luxeled.com/product-page/mellow-iii",),
        trusted_asset_domains=("90b00135-5a72-4c01-b37e-1f0325f9da2e.usrfiles.com",),
        verified_product_pdfs=("https://90b00135-5a72-4c01-b37e-1f0325f9da2e.usrfiles.com/ugd/90b001_efb17c738c5d441cb8b5034f692dddb0.pdf",),
        research_notes="Product pages may link technical PDFs on the approved Wix user-files host. Read the complete ordering table, not only the visible web-page summary.",
    ),
    "Novolux": BrandResearchProfile(
        "Novolux Lighting",
        "novoluxlighting.com",
        ("https://www.novoluxlighting.com/en/products.html",),
        research_notes="Search both indoor and outdoor categories and inspect current product pages and catalogs for reference-level data.",
    ),
    "ATP": BrandResearchProfile(
        "ATP Iluminacion",
        "atpiluminacion.com",
        ("https://www.atpiluminacion.com/",),
        research_notes="Search the official luminaire ranges and technical catalogs. Verify the exact model, optical distribution, current, and driver configuration.",
    ),
    "Plux B": BrandResearchProfile(
        "PLUXB",
        "pluxb.com",
        ("https://pluxb.com/product/", "https://pluxb.com/product-categories/"),
        research_notes="Use product categories to identify the family, then open the exact variant page and its Datasheet download. Do not stop at category-card wattage ranges.",
    ),
    "Floz": BrandResearchProfile(
        "Flos",
        "flos.com",
        ("https://flos.com/en/us/shop-products/",),
        research_notes="Search the professional/product family range and verify the exact variant or item code; family marketing pages alone are insufficient.",
    ),
    "RELCO": BrandResearchProfile(
        "RELCO Group",
        "relcogroup.com",
        ("https://www.relcogroup.com/",),
        research_notes="Search RELCO Lighting product pages, current brochures, and scheda tecnica PDFs. Prefer a current technical sheet for the exact code.",
    ),
    "Unilamp": BrandResearchProfile(
        "Unilamp",
        "unilamp.co.th",
        ("https://unilamp.co.th/en/product", "https://unilamp.co.th/en/product/search"),
        research_notes="Use the product filter and open the exact ordering-code specification sheet; product-family names have many electrical and optical variants.",
    ),
    "Ligman": BrandResearchProfile(
        "LIGMAN",
        "ligman.com",
        ("https://www.ligman.com/products/",),
        research_notes="Search the full indoor/outdoor taxonomy, excluding the Discontinued category, and verify an orderable configuration in technical downloads.",
    ),
    "MP Illumination": BrandResearchProfile(
        "MP Illumination",
        "mpillumination.com",
        ("https://mpillumination.com/en/products/",),
        research_notes="Search all product categories and inspect the exact product technical files; custom capability is not evidence that a standard order code meets the requirement.",
    ),
    "Hepper": BrandResearchProfile(
        "HEPER Lighting",
        "heperlighting.com",
        ("https://heperlighting.com/product-finder/", "https://heperlighting.com/products/"),
        research_notes="Use Product Finder filters and the exact family/configuration page. Explicitly exclude products listed in Phased-out products.",
    ),
    "Faelluce": BrandResearchProfile(
        "FAEL LUCE",
        "faelluce.lighting",
        ("https://faelluce.lighting/",),
        research_notes="Search the official professional luminaire catalog and exact technical product sheets. Confirm orderability and variant-level values.",
    ),
    "Dialight": BrandResearchProfile(
        "Dialight",
        "dialight.com",
        ("https://www.dialight.com/product/products-solutions/",),
        research_notes="Navigate from the relevant industrial or hazardous family to its current spec sheet/configurator. Certification region and full part number are critical.",
    ),
    "Airfal": BrandResearchProfile(
        "Airfal",
        "airfal.com",
        ("https://www.airfal.com/en/",),
        research_notes="Search ATEX and high-specification product pages and current technical catalog. Verify zone, gas/dust group, temperature class, IP/IK, and emergency variants where required.",
    ),
    "3F Filippi": BrandResearchProfile(
        "3F Filippi",
        "3f-filippi.it",
        ("https://www.3f-filippi.it/en/",),
        research_notes="Search the official product catalog and open the exact code's technical documentation. Separate family capabilities from the selected configuration.",
    ),
    "Roger Pradier": BrandResearchProfile(
        "Roger Pradier",
        "roger-pradier.com",
        ("https://roger-pradier.com/",),
        research_notes="Search outdoor-lighting collections, then verify the exact reference in its product sheet or current international catalog.",
    ),
    "Francisconi": BrandResearchProfile(
        "Francesconi Architectural Light",
        "francesconi.it",
        ("https://www.francesconi.it/eng/catalogo.php", "https://www.francesconi.it/ita/prodotti.php"),
        research_notes="Search by collection/code and inspect the variant table. Verify real luminaire wattage and lumen output, not LED-package headline values.",
    ),
    "Whitecroft Lighting": BrandResearchProfile(
        "Whitecroft Lighting",
        "whitecroftlighting.com",
        ("https://www.whitecroftlighting.com/products/",),
        research_notes="Search and filter the full product range, then inspect the product page, downloadable datasheet, and exact order-code table (including output and control suffixes).",
    ),
}


APPROVED_BRANDS = {
    name: profile.domain for name, profile in BRAND_RESEARCH_PROFILES.items()
}
