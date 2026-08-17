from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .brand_research import BRAND_RESEARCH_PROFILES, canonical_brand
from .models import (
    ApiUsage,
    FixtureRequirement,
    ProductMatch,
    ProductSearchRequest,
    ProductSearchResponse,
    Tolerances,
)
from .product_search import _score_product, search_products
from .storage import KnowledgeStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _scope_key(brand: str, fixture: FixtureRequirement) -> str:
    """Stable catalogue scope: broad enough to reuse, narrow enough to stay relevant."""
    normalized = fixture_family(fixture)
    mounting = " ".join((fixture.mounting or "").lower().split())
    payload = f"{canonical_brand(brand).lower()}|{normalized}|{mounting}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def product_family(text: str) -> str:
    value = " ".join((text or "").lower().replace("×", "x").split())
    families = (
        ("Panel lights", ("panel", "backlit", "600 x 600", "595 x 595", "600x600", "595x595")),
        ("Downlights", ("downlight", "down light")),
        ("Linear fittings", ("linear", "batten", "trunking", "strip light", "line of light")),
        ("Spotlights", ("spotlight", "spot light", "track light")),
        ("Floodlights", ("floodlight", "flood light")),
        ("High-bay fittings", ("highbay", "high bay", "lowbay", "low bay")),
        ("Street and area lighting", ("streetlight", "street light", "road light", "area light")),
        ("Bollards", ("bollard",)),
        ("Wall lights", ("wall light", "wall mounted", "wall-mounted")),
        ("Emergency fittings", ("emergency", "exit sign")),
    )
    for family, terms in families:
        if any(term in value for term in terms):
            return family
    return "Other products"


def fixture_family(fixture: FixtureRequirement) -> str:
    return product_family(f"{fixture.fixture_type} {fixture.description}")


def _facet_text(value: str | None) -> str | None:
    """Keep catalogue-provided labels readable while deduplicating trivial variations."""
    cleaned = " ".join((value or "").strip().split())
    return cleaned or None


class CatalogService:
    def __init__(
        self,
        store: KnowledgeStore,
        researcher: Callable[[ProductSearchRequest], ProductSearchResponse] = search_products,
        on_catalog_updated: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.researcher = researcher
        self.on_catalog_updated = on_catalog_updated
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._scheduler_started = False
        self._refresh_slots = threading.Semaphore(
            max(1, int(os.getenv("TECS_CATALOG_MAX_CONCURRENT_REFRESHES", "1")))
        )

    @property
    def freshness_days(self) -> int:
        return max(1, int(os.getenv("TECS_CATALOG_FRESHNESS_DAYS", "90")))

    def _is_stale(self, verified_at: str | None) -> bool:
        if not verified_at:
            return True
        try:
            timestamp = datetime.fromisoformat(verified_at)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            return timestamp < _utc_now() - timedelta(days=self.freshness_days)
        except ValueError:
            return True

    def search(self, request: ProductSearchRequest, refresh: bool = False) -> ProductSearchResponse:
        request = request.model_copy(update={"brand": canonical_brand(request.brand)})
        profile = BRAND_RESEARCH_PROFILES.get(request.brand)
        if not profile:
            raise ValueError("The selected manufacturer is not approved.")
        scope = _scope_key(request.brand, request.fixture)
        snapshot = self.store.catalog_snapshot(scope)
        scope_products = snapshot.get("products", [])
        brand_products = self.store.catalog_products_for_brand(request.brand)
        matches = self._rank([*scope_products, *brand_products], request)
        last_verified_at = snapshot.get("last_verified_at")
        stale = self._is_stale(last_verified_at)
        # Criteria always rerank the brand catalogue locally. A category-specific
        # enrichment runs only when that category has never completed or is stale.
        should_refresh = refresh
        last_attempt_at = snapshot.get("last_attempt_at")
        if not refresh and not matches and snapshot.get("status") == "failed":
            try:
                last_attempt = datetime.fromisoformat(last_attempt_at)
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=UTC)
                should_refresh = last_attempt < _utc_now() - timedelta(hours=6)
            except (TypeError, ValueError):
                pass
        if should_refresh:
            self._start_refresh(scope, request)
        refreshing = self.is_refreshing(scope)
        # A fast test/failure can finish between starting the worker and checking
        # its state. Reload so callers never receive an empty obsolete snapshot.
        if should_refresh and not refreshing:
            snapshot = self.store.catalog_snapshot(scope)
            scope_products = snapshot.get("products", [])
            brand_products = self.store.catalog_products_for_brand(request.brand)
            matches = self._rank([*scope_products, *brand_products], request)
            last_verified_at = snapshot.get("last_verified_at")
            stale = self._is_stale(last_verified_at)
        warnings: list[str] = []
        if refreshing:
            warnings.append(
                "Official catalogue refresh is running in the background. "
                "Stored verified options are shown now and will update automatically."
            )
        if stale and matches and last_verified_at:
            warnings.append(
                f"Stored catalogue evidence is older than {self.freshness_days} days; "
                "use it provisionally while the official sources are refreshed."
            )
        last_error = snapshot.get("last_error")
        if last_error and not refreshing:
            if matches:
                warnings.append(
                    "This category could not be refreshed. Existing verified brand products "
                    "remain available and are ranked against the new criteria."
                )
            else:
                warnings.append(
                    "No new verified options were confirmed during this background refresh. "
                    "Your project remains available and the refresh can be retried later."
                )
        return ProductSearchResponse(
            matches=matches[:request.max_results],
            searched_domain=profile.domain,
            warnings=warnings,
            source="catalog",
            refreshing=refreshing,
            stale=stale,
            last_verified_at=last_verified_at,
            usage=ApiUsage(**snapshot["usage"]) if snapshot.get("usage") else None,
        )

    def browse(self, request: ProductSearchRequest) -> dict:
        """Return every saved product for a brand without starting API research."""
        request = request.model_copy(update={"brand": canonical_brand(request.brand)})
        if request.brand not in BRAND_RESEARCH_PROFILES:
            raise ValueError("The selected manufacturer is not approved.")
        ranked: list[dict] = []
        for record in self.store.catalog_records_for_brand(request.brand):
            try:
                product = ProductMatch.model_validate(record["product"])
                score, criteria = _score_product(request, product.specifications)
                product = product.model_copy(update={"score": score, "criteria": criteria})
                verified_type = _facet_text(product.specifications.product_type)
                family = product_family(verified_type or "") if verified_type else product_family(
                    f"{product.product_name} {product.description}"
                )
                # Known product types use broad, stable families. When research
                # discovers a genuinely new type, retain its verified catalogue
                # label so the UI gains that category without an app update.
                if family == "Other products":
                    family = verified_type or family
                missing_details = not product.product_code or not product.datasheet_url
                ranked.append(
                    {
                        **product.model_dump(mode="json"),
                        "catalog_family": family,
                        "verified_at": record["verified_at"],
                        "freshness": "outdated"
                        if self._is_stale(record["verified_at"])
                        else "incomplete"
                        if missing_details
                        else "current",
                    }
                )
            except (TypeError, ValueError):
                continue
        ranked.sort(key=lambda item: (-item["score"], item["catalog_family"], item["product_name"]))
        families = sorted({item["catalog_family"] for item in ranked})
        mounting = sorted(
            {
                value
                for item in ranked
                if (value := _facet_text(item["specifications"].get("mounting")))
            },
            key=str.casefold,
        )
        cct = sorted(
            {
                int(value)
                for item in ranked
                if (value := item["specifications"].get("cct")) is not None
            }
        )
        controls = sorted(
            {
                value
                for item in ranked
                for candidate in [
                    item["specifications"].get("control_gear"),
                    *(item["specifications"].get("controls") or []),
                ]
                if (value := _facet_text(candidate))
            },
            key=str.casefold,
        )
        return {
            "products": ranked,
            "families": families,
            "facets": {
                "families": families,
                "mounting": mounting,
                "cct": cct,
                "controls": controls,
            },
            "requirement_family": fixture_family(request.fixture),
            "freshness_days": self.freshness_days,
        }

    def _rank(self, records: list[dict], request: ProductSearchRequest) -> list[ProductMatch]:
        ranked: list[ProductMatch] = []
        seen: set[str] = set()
        for record in records:
            try:
                product = ProductMatch.model_validate(record)
                identity = str(product.product_code or product.product_url).strip().lower()
                if identity in seen:
                    continue
                seen.add(identity)
                score, criteria = _score_product(request, product.specifications)
                ranked.append(product.model_copy(update={"score": score, "criteria": criteria}))
            except (TypeError, ValueError):
                continue
        ranked.sort(key=lambda product: product.score, reverse=True)
        return ranked

    def _start_refresh(self, scope: str, request: ProductSearchRequest) -> bool:
        with self._lock:
            if scope in self._running:
                return False
            self._running.add(scope)
        self.store.catalog_refresh_started(scope, request.brand, request.fixture)
        thread = threading.Thread(
            target=self._refresh,
            args=(scope, request.model_copy(deep=True)),
            daemon=True,
            name=f"tecs-catalog-{scope}",
        )
        thread.start()
        return True

    def _refresh(self, scope: str, request: ProductSearchRequest) -> None:
        with self._refresh_slots:
            try:
                # Broad tolerances prevent a catalogue refresh from discarding useful
                # configurations; the engineer's tolerances are applied locally later.
                research_request = request.model_copy(
                    update={"tolerances": Tolerances(lumens_percent=100, wattage_percent=100)}
                )
                response = self.researcher(research_request)
                if not response.matches:
                    raise RuntimeError(
                        response.warnings[0]
                        if response.warnings
                        else "No verified products returned."
                    )
                self.store.replace_catalog_scope(
                    scope=scope,
                    brand=request.brand,
                    fixture=request.fixture,
                    products=response.matches,
                    verified_at=_iso_now(),
                    usage=response.usage,
                )
                if self.on_catalog_updated:
                    self.on_catalog_updated()
            except Exception as error:  # noqa: BLE001 - preserve old catalogue on refresh failure
                self.store.catalog_refresh_failed(scope, str(error))
            finally:
                with self._lock:
                    self._running.discard(scope)

    def is_refreshing(self, scope: str) -> bool:
        with self._lock:
            return scope in self._running

    def status(self) -> dict:
        stats = self.store.catalog_stats()
        with self._lock:
            stats["active_refreshes"] = len(self._running)
        stats["freshness_days"] = self.freshness_days
        return stats

    def scope_status(self, request: ProductSearchRequest) -> dict:
        request = request.model_copy(update={"brand": canonical_brand(request.brand)})
        profile = BRAND_RESEARCH_PROFILES.get(request.brand)
        if not profile:
            raise ValueError("The selected manufacturer is not approved.")
        scope = _scope_key(request.brand, request.fixture)
        snapshot = self.store.catalog_snapshot(scope)
        return {
            "refreshing": self.is_refreshing(scope),
            "status": snapshot.get("status", "not_started"),
            "last_attempt_at": snapshot.get("last_attempt_at"),
            "last_verified_at": snapshot.get("last_verified_at"),
            "last_error": snapshot.get("last_error"),
            "products": len(snapshot.get("products", [])),
        }

    def refresh_brand(self, brand: str) -> int:
        brand = canonical_brand(brand)
        if brand not in BRAND_RESEARCH_PROFILES:
            raise ValueError("The selected manufacturer is not approved.")
        scopes = self.store.catalog_scopes_for_brand(brand)
        started = 0
        for row in scopes:
            fixture = FixtureRequirement.model_validate(json.loads(row["fixture_json"]))
            request = ProductSearchRequest(fixture=fixture, brand=brand)
            started += int(self._start_refresh(row["scope_key"], request))
        return started

    def start_scheduler(self) -> None:
        with self._lock:
            if self._scheduler_started:
                return
            self._scheduler_started = True
        threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="tecs-catalog-scheduler",
        ).start()

    def _scheduler_loop(self) -> None:
        initial_delay = max(1, int(os.getenv("TECS_CATALOG_INITIAL_REFRESH_DELAY_SECONDS", "30")))
        interval = max(300, int(os.getenv("TECS_CATALOG_CHECK_INTERVAL_SECONDS", "21600")))
        time.sleep(initial_delay)
        while True:
            for row in self.store.catalog_scopes():
                if self._is_stale(row["last_verified_at"]):
                    fixture = FixtureRequirement.model_validate(json.loads(row["fixture_json"]))
                    brand = canonical_brand(row["brand"])
                    self._start_refresh(
                        row["scope_key"],
                        ProductSearchRequest(fixture=fixture, brand=brand),
                    )
            time.sleep(interval)
