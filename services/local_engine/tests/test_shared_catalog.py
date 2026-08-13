from __future__ import annotations

import httpx
from test_catalog import fixture, product

from tecs_engine.shared_catalog import SharedCatalogService
from tecs_engine.storage import KnowledgeStore


def test_shared_catalog_syncs_without_user_login(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    store.replace_catalog_scope(
        "scope-one", "LuxeLED", fixture(), [product()], "2026-08-13T10:00:00+00:00", None
    )
    cloud_rows: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Bearer sb_publishable_")
        assert request.url.path == "/rest/v1/tecs_catalog_products"
        if request.method == "POST":
            cloud_rows.extend(__import__("json").loads(request.content))
            return httpx.Response(201)
        return httpx.Response(200, json=cloud_rows)

    service = SharedCatalogService(
        store,
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    response = service.sync()

    assert response["uploaded"] == 1
    assert response["downloaded"] == 1
    assert service.status()["configured"] is True
    assert store.catalog_stats()["shared_products"] == 1
