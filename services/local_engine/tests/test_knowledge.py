from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tecs_engine.brand_research import APPROVED_BRANDS, BRAND_RESEARCH_PROFILES
from tecs_engine.knowledge import match_document_profile
from tecs_engine.models import (
    FixtureRequirement,
    ProductSearchRequest,
    ProductSpecifications,
)
from tecs_engine.product_search import (
    _anonymous_requirement,
    _discovery_schema,
    _official_url,
    _product_schema,
    _research_client,
    _score_product,
    _valid_official_urls,
    _verification_level,
    search_products,
)
from tecs_engine.storage import KnowledgeStore


def fixture(quantity: int = 4) -> FixtureRequirement:
    return FixtureRequirement(
        id="fixture-1",
        symbol="FL1",
        description="400W IP66 pole floodlight",
        quantity=quantity,
        fixture_type="pole floodlight",
        wattage=400,
        ip_rating=66,
        source_file="renamed.pdf",
    )


def test_matches_seed_profiles_from_content_not_filename() -> None:
    match = match_document_profile(
        "12M HEIGHT POLE LIGHT WITH 4 X 400W, 58070LM, "
        "1000MM HEIGHT 40W LED BOLLARD and standalone off-grid solar photovoltaic LED street lighting",
        "completely-renamed.pdf",
    )
    assert match is not None
    assert match.profile_id == "bst_site_lighting_v1"

    mod = match_document_profile(
        "DB-F1-Y1 DB-F2-B1 PIRDB-G1-R2SENSOR DB-G2-Y2 F A L S E C E I L I N G",
        "unknown.pdf",
    )
    assert mod is not None
    assert mod.profile_id == "mod_snco_v1"

    sample = match_document_profile(
        "COOL WHITE FLEXIBLE LED LIGHT STRIP 12VOLT 2X10W LED (2X1040LM) "
        "CIRCULAR SUSPENDED LIGHT FITTING 25/40W LED (3000LM/4800LM) "
        "5500-6500 K DAY LIGHT",
        "renamed-schedule.pdf",
    )
    assert sample is not None
    assert sample.profile_id == "lighting_sample_v1"


def test_approval_records_user_correction(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge")
    project_id = store.create_project("Test")
    source = tmp_path / "drawing.pdf"
    source.write_bytes(b"%PDF-test")
    document_id = store.retain_document(
        project_id, source, "drawing.pdf", "bst_site_lighting_v1", 0.8
    )
    original = fixture()
    store.save_fixtures(
        project_id, document_id, "bst_site_lighting_v1", [original]
    )

    corrected = fixture(quantity=8)
    assert store.approve_fixtures(project_id, [corrected]) == 1
    assert store.learning_stats()["approved_fixtures"] == 1
    assert store.learning_stats()["corrections"] == 1

    connection = sqlite3.connect(store.database_path)
    before, after = connection.execute(
        "SELECT before_json, after_json FROM corrections"
    ).fetchone()
    assert json.loads(before)["quantity"] == 4
    assert json.loads(after)["quantity"] == 8


def test_approved_specification_corrections_are_reused_but_quantity_is_recounted(
    tmp_path: Path,
) -> None:
    store = KnowledgeStore(tmp_path / "knowledge")
    project_id = store.create_project("Training project")
    source = tmp_path / "drawing.pdf"
    source.write_bytes(b"%PDF-test")
    profile_id = "bst_site_lighting_v1"
    document_id = store.retain_document(project_id, source, "drawing.pdf", profile_id, 1)
    original = fixture(quantity=4)
    original.profile_id = profile_id
    store.save_fixtures(project_id, document_id, profile_id, [original])

    corrected = original.model_copy(deep=True)
    corrected.wattage = 450
    corrected.mounting = "high-mast mounted"
    corrected.quantity = 8
    store.approve_fixtures(project_id, [corrected])

    new_drawing = fixture(quantity=3)
    new_drawing.profile_id = profile_id
    applied = store.apply_learned_corrections(profile_id, [new_drawing])

    assert new_drawing.wattage == 450
    assert new_drawing.mounting == "high-mast mounted"
    assert new_drawing.quantity == 3
    assert set(applied["FL1"]) == {"mounting", "wattage"}


def test_unknown_approved_layout_becomes_a_reusable_local_family(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge")
    first_text = (
        "LIGHTING FIXTURE SCHEDULE SYMBOL DESCRIPTION MOUNTING HEIGHT "
        "TYPE X LED DOWNLIGHT RECESSED MOUNTED ALUMINIUM BODY OPAL DIFFUSER "
        "GROUND FLOOR LIGHTING LAYOUT ELECTRICAL NOTES"
    )
    family_id, first_score = store.resolve_learned_family(first_text, "first.pdf")
    assert first_score == 0

    project_id = store.create_project("First unfamiliar layout")
    source = tmp_path / "first.pdf"
    source.write_bytes(b"%PDF-test")
    document_id = store.retain_document(project_id, source, "first.pdf", family_id, 0)
    learned_fixture = fixture()
    learned_fixture.profile_id = family_id
    store.save_fixtures(project_id, document_id, family_id, [learned_fixture])
    store.approve_fixtures(project_id, [learned_fixture])

    second_text = (
        "LIGHTING FIXTURE SCHEDULE SYMBOL DESCRIPTION MOUNTING HEIGHT "
        "TYPE Y LED DOWNLIGHT RECESSED MOUNTED ALUMINIUM BODY OPAL DIFFUSER "
        "FIRST FLOOR LIGHTING LAYOUT ELECTRICAL NOTES"
    )
    matched_id, score = store.resolve_learned_family(second_text, "second.pdf")

    assert matched_id == family_id
    assert score >= 0.52
    assert store.learning_stats()["learned_families"] == 1


def test_cloud_product_payload_excludes_local_project_data() -> None:
    local_fixture = fixture()
    local_fixture.document_id = "private-document-id"
    local_fixture.evidence_url = "/api/private-preview"
    request = ProductSearchRequest(
        fixture=local_fixture,
        brand="Signify",
        domain="signify.com",
    )

    payload = _anonymous_requirement(request)

    assert payload["wattage"] == 400
    assert "source_file" not in payload
    assert "document_id" not in payload
    assert "evidence_url" not in payload
    assert "description" not in payload


def test_product_search_request_accepts_brand_without_domain() -> None:
    request = ProductSearchRequest(fixture=fixture(), brand="Whitecroft Lighting")

    assert request.brand == "Whitecroft Lighting"
    assert request.domain is None


def test_local_product_scoring_applies_tolerances_and_minimums() -> None:
    required = fixture()
    required.lumens = 58070
    required.lumens_is_minimum = True
    required.dimensions = "1000 mm height"
    request = ProductSearchRequest(
        fixture=required,
        brand="Signify",
        domain="signify.com",
    )
    score, criteria = _score_product(
        request,
        ProductSpecifications(
            product_type="pole floodlight",
            mounting="pole mounted",
            wattage=400,
            lumens=60000,
            ip_rating=66,
            height_mm=848,
            type_compatible=True,
            mounting_compatible=True,
        ),
    )

    statuses = {criterion.criterion: criterion.status for criterion in criteria}
    assert statuses["Lumens (minimum)"] == "match"
    assert statuses["Height"] == "mismatch"
    assert 70 < score < 90


def test_only_official_product_urls_are_accepted() -> None:
    assert _official_url("https://www.signify.com/product", "signify.com")
    assert _official_url("https://cdn.signify.com/file.pdf", "signify.com")
    assert _official_url("https://example.com/fake", "signify.com") is None


def test_brand_specific_official_cdn_urls_are_accepted() -> None:
    luxeled_cdn = "90b00135-5a72-4c01-b37e-1f0325f9da2e.usrfiles.com"
    datasheet = f"https://{luxeled_cdn}/ugd/90b001_datasheet.pdf"

    assert _official_url(datasheet, "luxeled.com", (luxeled_cdn,)) == datasheet
    assert _official_url(datasheet, "signify.com") is None
    assert _official_url(
        "https://unrelated.usrfiles.com/ugd/fake.pdf",
        "luxeled.com",
        (luxeled_cdn,),
    ) is None


def test_every_approved_brand_has_a_deep_research_profile() -> None:
    assert len(BRAND_RESEARCH_PROFILES) == 22
    assert set(BRAND_RESEARCH_PROFILES) == set(APPROVED_BRANDS)
    for brand, profile in BRAND_RESEARCH_PROFILES.items():
        assert profile.catalog_pages, brand
        assert profile.research_notes, brand
        assert all(
            _official_url(url, profile.domain, profile.trusted_asset_domains)
            for url in profile.catalog_pages
        )


def test_every_trusted_document_domain_is_scoped_to_its_brand() -> None:
    luxeled = BRAND_RESEARCH_PROFILES["LuxeLED"]
    pdf = luxeled.verified_product_pdfs[0]
    assert _official_url(pdf, luxeled.domain, luxeled.trusted_asset_domains) == pdf
    for brand, profile in BRAND_RESEARCH_PROFILES.items():
        if brand != "LuxeLED":
            assert _official_url(pdf, profile.domain, profile.trusted_asset_domains) is None


def test_only_official_evidence_urls_survive_validation() -> None:
    evidence = _valid_official_urls(
        [
            "https://www.signify.com/global/prof",
            "https://www.signify.com/global/prof",
            "https://example.com/invented.pdf",
            None,
        ],
        "signify.com",
        (),
    )
    assert evidence == ["https://www.signify.com/global/prof"]


def test_verification_level_prefers_datasheet_then_multiple_sources() -> None:
    assert _verification_level(
        "https://signify.com/product", "https://signify.com/datasheet.pdf", []
    ) == "datasheet"
    assert _verification_level(
        "https://signify.com/product",
        None,
        ["https://signify.com/product", "https://signify.com/configuration"],
    ) == "multi_source"
    assert _verification_level(
        "https://signify.com/product", None, ["https://signify.com/product"]
    ) == "product_page"


def test_deep_search_schemas_require_evidence_for_every_candidate() -> None:
    discovery = _discovery_schema()["properties"]["candidates"]["items"]
    products = _product_schema()["properties"]["matches"]["items"]
    assert "evidence_urls" in discovery["required"]
    assert "publication_status" in discovery["required"]
    assert "evidence_urls" in products["required"]


def test_research_client_has_a_bounded_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key-value-long-enough")
    monkeypatch.setenv("TECS_PRODUCT_SEARCH_TIMEOUT_SECONDS", "42")
    client = _research_client()
    assert client.timeout == 42.0
    assert client.max_retries == 0


def test_product_search_runs_discovery_then_exact_verification(monkeypatch) -> None:
    calls: list[dict] = []
    discovery = {
        "candidates": [
            {
                "product_name": "Official candidate",
                "product_code": "CODE-1",
                "product_url": "https://www.signify.com/global/prof/product-one",
                "datasheet_url": "https://www.signify.com/api/assets/product-one.pdf",
                "evidence_urls": ["https://www.signify.com/global/prof/product-one"],
                "publication_status": "current",
            }
        ]
    }
    specifications = {
        "product_type": "pole floodlight",
        "country_of_origin": None,
        "mounting": "pole mounted",
        "mounting_height_mm": None,
        "wattage": 400,
        "lumens": 58070,
        "cct": None,
        "cri": None,
        "ip_rating": 66,
        "ik_rating": None,
        "ugr": None,
        "emergency_hours": None,
        "height_mm": None,
        "dimensions": None,
        "construction": None,
        "optical_details": None,
        "voltage": None,
        "beam_angle": None,
        "efficacy_lm_w": None,
        "control_gear": None,
        "emergency_details": None,
        "led_life": None,
        "finish": None,
        "waterproof": None,
        "controls": [],
        "type_compatible": True,
        "mounting_compatible": True,
        "dimensions_compatible": None,
        "construction_compatible": None,
        "optical_details_compatible": None,
        "controls_compatible": None,
    }
    verified = {
        "matches": [
            {
                "brand": "Signify",
                "product_name": "Verified exact configuration",
                "product_code": "CODE-1",
                "product_url": "https://www.signify.com/global/prof/product-one",
                "datasheet_url": "https://www.signify.com/api/assets/product-one.pdf",
                "image_url": None,
                "evidence_urls": [
                    "https://www.signify.com/global/prof/product-one",
                    "https://www.signify.com/api/assets/product-one.pdf",
                ],
                "description": "Official exact configured product",
                "specifications": specifications,
            }
        ]
    }

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            payload = discovery if len(calls) == 1 else verified
            return SimpleNamespace(output_text=json.dumps(payload))

    class FakeClient:
        timeout = 150.0

        def __init__(self):
            self.responses = FakeResponses()

        def with_options(self, **_kwargs):
            return self

    monkeypatch.setattr("tecs_engine.product_search._research_client", FakeClient)
    request = ProductSearchRequest(fixture=fixture(), brand="Signify")

    response = search_products(request)

    assert len(calls) == 2
    assert calls[0]["tools"][0]["search_context_size"] == "high"
    assert calls[1]["tools"][0]["search_context_size"] == "high"
    verification_content = calls[1]["input"][0]["content"]
    assert any(item.get("type") == "input_file" for item in verification_content)
    assert response.matches[0].product_code == "CODE-1"
    assert response.matches[0].verification_level == "datasheet"
    assert len(response.matches[0].evidence_urls) == 2
