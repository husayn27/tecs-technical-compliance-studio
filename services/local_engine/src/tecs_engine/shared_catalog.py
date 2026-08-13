from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime

import httpx

from .storage import KnowledgeStore

# Temporary shared catalogue. Environment overrides make moving the database later simple.
DEFAULT_PROJECT_URL = "https://apyengsruimahzfxbzaf.supabase.co"
DEFAULT_PUBLISHABLE_KEY = "sb_publishable_YRdeeDaSO_Y9RB2Yi6sDkA_Av_otTH-"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SharedCatalogService:
    """Anonymous Supabase mirror containing reusable manufacturer products only."""

    def __init__(self, store: KnowledgeStore, client_factory=httpx.Client) -> None:
        self.store = store
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._syncing = False
        self._scheduler_started = False
        self._last_sync_at: str | None = None
        self._last_error: str | None = None

    @property
    def project_url(self) -> str:
        return os.getenv("TECS_SUPABASE_URL", DEFAULT_PROJECT_URL).rstrip("/")

    @property
    def publishable_key(self) -> str:
        return os.getenv("TECS_SUPABASE_PUBLISHABLE_KEY", DEFAULT_PUBLISHABLE_KEY)

    @staticmethod
    def _raise(response: httpx.Response, prefix: str) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
            detail = payload.get("message") or payload.get("error_description")
        except ValueError:
            detail = response.text
        raise RuntimeError(f"{prefix}: {detail or response.reason_phrase}")

    def sync(self) -> dict:
        with self._lock:
            if self._syncing:
                return {"started": False, "message": "A shared catalogue sync is already running."}
            self._syncing = True
        try:
            key = self.publishable_key
            headers = {"apikey": key, "Authorization": f"Bearer {key}"}
            local = self.store.catalog_products_for_sharing()
            with self.client_factory(timeout=35.0) as client:
                if local:
                    response = client.post(
                        f"{self.project_url}/rest/v1/tecs_catalog_products",
                        params={"on_conflict": "identity"},
                        headers={
                            **headers,
                            "Content-Type": "application/json",
                            "Prefer": "resolution=merge-duplicates,return=minimal",
                        },
                        json=local,
                    )
                    self._raise(response, "Could not upload catalogue records")
                response = client.get(
                    f"{self.project_url}/rest/v1/tecs_catalog_products",
                    params={
                        "select": "identity,brand,product_json,verified_at",
                        "order": "verified_at.desc",
                        "limit": "5000",
                    },
                    headers=headers,
                )
                self._raise(response, "Could not download the shared catalogue")
                downloaded = self.store.shared_catalog_replace(response.json())
            self._last_sync_at = _now()
            self._last_error = None
            return {"started": True, "uploaded": len(local), "downloaded": downloaded}
        except Exception as error:
            self._last_error = str(error)
            raise
        finally:
            with self._lock:
                self._syncing = False

    def sync_background(self) -> bool:
        with self._lock:
            if self._syncing:
                return False
        threading.Thread(target=self._safe_sync, daemon=True, name="tecs-shared-catalog-sync").start()
        return True

    def product_saved(self) -> None:
        self.sync_background()

    def _safe_sync(self) -> None:
        try:
            self.sync()
        except (httpx.HTTPError, RuntimeError, ValueError) as error:
            self._last_error = str(error)

    def status(self) -> dict:
        with self._lock:
            syncing = self._syncing
        return {
            "configured": True,
            "syncing": syncing,
            "last_sync_at": self._last_sync_at,
            "last_error": self._last_error,
            "shared_products": self.store.catalog_stats().get("shared_products", 0),
        }

    def start_scheduler(self) -> None:
        with self._lock:
            if self._scheduler_started:
                return
            self._scheduler_started = True
        threading.Thread(target=self._scheduler, daemon=True, name="tecs-shared-sync-scheduler").start()

    def _scheduler(self) -> None:
        time.sleep(5)
        while True:
            self._safe_sync()
            time.sleep(15 * 60)
