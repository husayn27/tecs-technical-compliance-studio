from __future__ import annotations

from datetime import UTC, datetime

from tecs_engine.catalog import CatalogService, _scope_key, product_family
from tecs_engine.models import (
    ApiUsage,
    FixtureRequirement,
    ProductMatch,
    ProductSearchRequest,
    ProductSearchResponse,
    ProductSpecifications,
)
from tecs_engine.storage import KnowledgeStore


def fixture(lumens: int = 4500) -> FixtureRequirement:
    return FixtureRequirement(
        id="f1",
        symbol="F1",
        description="600 x 600 recessed panel",
        quantity=8,
        fixture_type="600 x 600 panel light",
        mounting="recessed",
        wattage=40,
        lumens=lumens,
        cct=4000,
        cri=80,
        source_file="manual",
    )


def product() -> ProductMatch:
    return ProductMatch(
        id="catalog-product-1",
        brand="LuxeLED",
        product_name="Mellow III Backlit Panel",
        product_code="MEL-III-4K-40W",
        product_url="https://www.luxeled.com/product-page/mellow-iii",
        datasheet_url=(
            "https://90b00135-5a72-4c01-b37e-1f0325f9da2e.usrfiles.com/"
            "ugd/90b001_efb17c738c5d441cb8b5034f692dddb0.pdf"
        ),
        evidence_urls=["https://www.luxeled.com/product-page/mellow-iii"],
        description="Official recessed backlit panel configuration.",
        verification_level="datasheet",
        specifications=ProductSpecifications(
            product_type="600 x 600 panel light",
            mounting="recessed",
            wattage=40,
            lumens=4500,
            cct=4000,
            cri=80,
            control_gear="DALI",
            controls=["DALI", "Fixed output"],
            type_compatible=True,
            mounting_compatible=True,
        ),
        score=0,
        criteria=[],
    )


def test_catalog_persists_and_reranks_without_research(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    request = ProductSearchRequest(fixture=fixture(), brand="LuxeLED")
    scope = _scope_key(request.brand, request.fixture)
    verified_at = datetime.now(UTC).isoformat()
    store.replace_catalog_scope(
        scope,
        request.brand,
        request.fixture,
        [product()],
        verified_at,
        ApiUsage(input_tokens=1000, output_tokens=200, total_tokens=1200),
    )

    def unexpected_research(_request):
        raise AssertionError("A fresh catalogue search must not call the API.")

    service = CatalogService(KnowledgeStore(tmp_path), researcher=unexpected_research)
    response = service.search(request)

    assert response.source == "catalog"
    assert response.refreshing is False
    assert response.last_verified_at == verified_at
    assert response.usage and response.usage.total_tokens == 1200
    assert response.matches[0].product_code == "MEL-III-4K-40W"
    assert response.matches[0].score > 90


def test_catalog_respects_requested_per_brand_result_limit(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    request = ProductSearchRequest(fixture=fixture(), brand="LuxeLED", max_results=2)
    scope = _scope_key(request.brand, request.fixture)
    products = [
        product().model_copy(update={"id": f"product-{index}", "product_code": f"MODEL-{index}"})
        for index in range(5)
    ]
    store.replace_catalog_scope(
        scope,
        request.brand,
        request.fixture,
        products,
        datetime.now(UTC).isoformat(),
        None,
    )

    response = CatalogService(store).search(request)

    assert len(response.matches) == 2


def test_product_keeps_order_code_and_human_readable_model_separately() -> None:
    signify = product().model_copy(update={
        "brand": "Signify",
        "product_code": "911401897484",
        "model_number": "RC035B LED40S/840 120-277 W60L60 LA",
    })

    assert signify.product_code == "911401897484"
    assert signify.model_number == "RC035B LED40S/840 120-277 W60L60 LA"


def test_catalogue_default_freshness_is_ninety_days(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TECS_CATALOG_FRESHNESS_DAYS", raising=False)
    assert CatalogService(KnowledgeStore(tmp_path)).freshness_days == 90


def test_failed_refresh_preserves_last_verified_catalog(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    request = ProductSearchRequest(fixture=fixture(), brand="LuxeLED")
    scope = _scope_key(request.brand, request.fixture)
    store.replace_catalog_scope(
        scope,
        request.brand,
        request.fixture,
        [product()],
        datetime.now(UTC).isoformat(),
        None,
    )

    def failed_research(_request):
        raise TimeoutError("simulated official-source timeout")

    service = CatalogService(store, researcher=failed_research)
    service._refresh(scope, request)
    response = service.search(request)

    assert len(response.matches) == 1
    assert response.matches[0].product_name == "Mellow III Backlit Panel"
    assert any("existing verified brand products" in warning.lower() for warning in response.warnings)


def test_different_criteria_reuse_existing_brand_catalog(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    panel_request = ProductSearchRequest(fixture=fixture(), brand="LuxeLED")
    panel_scope = _scope_key(panel_request.brand, panel_request.fixture)
    store.replace_catalog_scope(
        panel_scope,
        panel_request.brand,
        panel_request.fixture,
        [product()],
        datetime.now(UTC).isoformat(),
        None,
    )
    downlight = fixture(lumens=1100).model_copy(
        update={"id": "f2", "fixture_type": "recessed downlight", "wattage": 10}
    )

    def failed_enrichment(_request):
        raise TimeoutError("simulated category enrichment timeout")

    service = CatalogService(store, researcher=failed_enrichment)
    response = service.search(ProductSearchRequest(fixture=downlight, brand="LuxeLED"))

    assert response.refreshing is False
    assert len(response.matches) == 1
    assert response.matches[0].product_name == "Mellow III Backlit Panel"


def test_catalog_browser_returns_all_saved_products_without_research(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    request = ProductSearchRequest(fixture=fixture(), brand="LuxeLED")
    store.replace_catalog_scope(
        _scope_key(request.brand, request.fixture),
        request.brand,
        request.fixture,
        [product()],
        datetime.now(UTC).isoformat(),
        None,
    )

    def unexpected_research(_request):
        raise AssertionError("Browsing saved products must never call the API.")

    response = CatalogService(store, researcher=unexpected_research).browse(request)

    assert response["requirement_family"] == "Panel lights"
    assert response["families"] == ["Panel lights"]
    assert response["facets"] == {
        "families": ["Panel lights"],
        "mounting": ["recessed"],
        "cct": [4000],
        "controls": ["DALI", "Fixed output"],
    }
    assert response["products"][0]["catalog_family"] == "Panel lights"
    assert response["products"][0]["freshness"] == "current"
    assert response["products"][0]["score"] > 90


def test_signify_and_philips_share_one_saved_catalogue(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    request = ProductSearchRequest(fixture=fixture(), brand="Philips")
    philips_product = product().model_copy(
        update={
            "brand": "Philips",
            "product_name": "CoreLine Panel",
            "product_code": "RC132V",
            "product_url": "https://www.signify.com/global/prof/coreline-panel",
            "datasheet_url": "https://www.signify.com/api/assets/coreline-panel.pdf",
        }
    )
    # Simulate records created by an older app version before aliases were
    # canonicalized on write.
    store.replace_catalog_scope(
        "legacy-philips-scope",
        "Philips",
        request.fixture,
        [philips_product],
        datetime.now(UTC).isoformat(),
        None,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE catalog_scopes SET brand='Philips' WHERE scope_key='legacy-philips-scope'"
        )

    service = CatalogService(store)
    signify_response = service.browse(
        ProductSearchRequest(fixture=fixture(), brand="Signify")
    )
    philips_response = service.browse(request)

    assert [item["product_code"] for item in signify_response["products"]] == ["RC132V"]
    assert [item["product_code"] for item in philips_response["products"]] == ["RC132V"]


def test_family_normalization_ignores_minor_wording_and_dimensions() -> None:
    assert product_family("600 x 600 recessed panel light") == "Panel lights"
    assert product_family("595x595 backlit LED fitting") == "Panel lights"
    assert product_family("Recessed down light 12 W") == "Downlights"


def test_catalog_browser_adds_new_verified_product_types_as_families(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    request = ProductSearchRequest(fixture=fixture(), brand="LuxeLED")
    new_type = product().model_copy(
        update={
            "id": "catalog-product-new-type",
            "product_name": "Architectural Light Tile",
            "product_code": "ALT-01",
            "product_url": "https://www.luxeled.com/product-page/architectural-light-tile",
            "specifications": product().specifications.model_copy(
                update={"product_type": "Architectural light tile"}
            ),
        }
    )
    store.replace_catalog_scope(
        _scope_key(request.brand, request.fixture),
        request.brand,
        request.fixture,
        [new_type],
        datetime.now(UTC).isoformat(),
        None,
    )

    response = CatalogService(store).browse(request)

    assert "Architectural light tile" in response["facets"]["families"]


def test_successful_refresh_atomically_replaces_catalog(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    request = ProductSearchRequest(fixture=fixture(), brand="LuxeLED")

    def successful_research(_request):
        return ProductSearchResponse(
            matches=[product()],
            searched_domain="luxeled.com",
            usage=ApiUsage(total_tokens=4321, web_search_calls=2),
        )

    service = CatalogService(store, researcher=successful_research)
    scope = _scope_key(request.brand, request.fixture)
    service._refresh(scope, request)

    snapshot = store.catalog_snapshot(scope)
    assert len(snapshot["products"]) == 1
    assert snapshot["last_error"] is None
    assert snapshot["usage"]["total_tokens"] == 4321
