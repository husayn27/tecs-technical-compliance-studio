from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .brand_research import brand_variants, canonical_brand
from .knowledge import SEED_PROFILES
from .models import ApiUsage, FixtureRequirement, ProductMatch

LEARNABLE_FIELDS = (
    "description",
    "fixture_type",
    "mounting",
    "mounting_height_mm",
    "wattage",
    "wattage_options",
    "lumens",
    "lumen_options",
    "lumens_is_minimum",
    "cct",
    "cct_options",
    "cct_min",
    "cct_max",
    "cri",
    "ip_rating",
    "ik_rating",
    "ugr",
    "dimensions",
    "construction",
    "optical_details",
    "voltage",
    "beam_angle",
    "led_density_per_m",
    "waterproof",
    "emergency_hours",
    "controls",
)


def _layout_features(text: str) -> set[str]:
    """Create a stable, non-sensitive signature for matching repeated drawing layouts."""
    normalized = re.sub(r"\d+(?:\.\d+)?", "#", text.lower())
    words = re.findall(r"[a-z]+|#", normalized)
    words = words[:2500]
    return {
        " ".join(words[index : index + 3])
        for index in range(max(0, len(words) - 2))
        if len({*words[index : index + 3]}) > 1
    }


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def data_directory() -> Path:
    override = os.environ.get("TECS_LIGHTING_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "TECS Lighting Quotation"


class KnowledgeStore:
    def __init__(self, root: Path | None = None):
        self.root = root or data_directory()
        self.documents_dir = self.root / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "knowledge.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    signatures_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    original_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    profile_id TEXT,
                    profile_score REAL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fixtures (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    document_id TEXT,
                    profile_id TEXT,
                    original_json TEXT NOT NULL,
                    current_json TEXT NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0,
                    rejected INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS corrections (
                    id TEXT PRIMARY KEY,
                    fixture_id TEXT NOT NULL REFERENCES fixtures(id),
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learned_families (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    signature_json TEXT NOT NULL,
                    approved_examples INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS family_approvals (
                    family_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (family_id, project_id)
                );
                CREATE TABLE IF NOT EXISTS catalog_scopes (
                    scope_key TEXT PRIMARY KEY,
                    brand TEXT NOT NULL,
                    fixture_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    last_attempt_at TEXT,
                    last_verified_at TEXT,
                    last_error TEXT,
                    usage_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalog_products (
                    scope_key TEXT NOT NULL REFERENCES catalog_scopes(scope_key) ON DELETE CASCADE,
                    identity TEXT NOT NULL,
                    product_json TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    PRIMARY KEY (scope_key, identity)
                );
                CREATE INDEX IF NOT EXISTS catalog_products_scope_idx
                    ON catalog_products(scope_key);
                CREATE TABLE IF NOT EXISTS shared_catalog_products (
                    identity TEXT PRIMARY KEY,
                    brand TEXT NOT NULL,
                    product_json TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS shared_catalog_products_brand_idx
                    ON shared_catalog_products(brand);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(fixtures)").fetchall()
            }
            if "rejected" not in columns:
                connection.execute(
                    "ALTER TABLE fixtures ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0"
                )
            for profile in SEED_PROFILES:
                connection.execute(
                    "INSERT OR IGNORE INTO profiles VALUES (?, ?, ?, ?)",
                    (profile.id, profile.name, json.dumps(profile.signatures), _now()),
                )
            # A process can be stopped during a background refresh. Such a job
            # cannot still be running after restart, so make it safely retryable.
            connection.execute(
                """
                UPDATE catalog_scopes
                SET status='interrupted',
                    last_error='The application closed before this catalogue refresh completed.',
                    updated_at=?
                WHERE status='refreshing'
                """,
                (_now(),),
            )

    def catalog_refresh_started(
        self, scope: str, brand: str, fixture: FixtureRequirement
    ) -> None:
        now = _now()
        brand = canonical_brand(brand)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO catalog_scopes
                    (scope_key, brand, fixture_json, status, last_attempt_at, updated_at)
                VALUES (?, ?, ?, 'refreshing', ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    brand=excluded.brand,
                    fixture_json=excluded.fixture_json,
                    status='refreshing',
                    last_attempt_at=excluded.last_attempt_at,
                    updated_at=excluded.updated_at
                """,
                (scope, brand, fixture.model_dump_json(), now, now),
            )

    def replace_catalog_scope(
        self,
        scope: str,
        brand: str,
        fixture: FixtureRequirement,
        products: list[ProductMatch],
        verified_at: str,
        usage: ApiUsage | None,
    ) -> None:
        brand = canonical_brand(brand)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO catalog_scopes
                    (scope_key, brand, fixture_json, status, last_attempt_at,
                     last_verified_at, last_error, usage_json, updated_at)
                VALUES (?, ?, ?, 'idle', ?, ?, NULL, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    brand=excluded.brand,
                    fixture_json=excluded.fixture_json,
                    status='idle',
                    last_attempt_at=excluded.last_attempt_at,
                    last_verified_at=excluded.last_verified_at,
                    last_error=NULL,
                    usage_json=excluded.usage_json,
                    updated_at=excluded.updated_at
                """,
                (
                    scope,
                    brand,
                    fixture.model_dump_json(),
                    verified_at,
                    verified_at,
                    usage.model_dump_json() if usage else None,
                    verified_at,
                ),
            )
            connection.execute("DELETE FROM catalog_products WHERE scope_key = ?", (scope,))
            for product in products:
                identity = str(product.product_code or product.product_url).strip().lower()
                connection.execute(
                    "INSERT INTO catalog_products VALUES (?, ?, ?, ?)",
                    (scope, identity, product.model_dump_json(), verified_at),
                )

    def catalog_refresh_failed(self, scope: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE catalog_scopes
                SET status='failed', last_error=?, updated_at=?
                WHERE scope_key=?
                """,
                (error[:1000], _now(), scope),
            )

    def catalog_snapshot(self, scope: str) -> dict:
        with self._connect() as connection:
            scope_row = connection.execute(
                "SELECT * FROM catalog_scopes WHERE scope_key=?", (scope,)
            ).fetchone()
            products = connection.execute(
                "SELECT product_json FROM catalog_products WHERE scope_key=?", (scope,)
            ).fetchall()
        if not scope_row:
            return {"products": []}
        return {
            "products": [json.loads(row["product_json"]) for row in products],
            "status": scope_row["status"],
            "last_attempt_at": scope_row["last_attempt_at"],
            "last_verified_at": scope_row["last_verified_at"],
            "last_error": scope_row["last_error"],
            "usage": json.loads(scope_row["usage_json"]) if scope_row["usage_json"] else None,
        }

    def catalog_products_for_brand(self, brand: str) -> list[dict]:
        """Return a de-duplicated union of all verified category scopes for a brand."""
        variants = tuple(value.casefold() for value in brand_variants(brand))
        placeholders = ", ".join("?" for _ in variants)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.identity, p.product_json, p.verified_at
                FROM catalog_products p
                JOIN catalog_scopes s ON s.scope_key = p.scope_key
                WHERE lower(s.brand) IN ({placeholders})
                ORDER BY p.verified_at DESC
                """,
                variants,
            ).fetchall()
            shared_rows = connection.execute(
                f"""
                SELECT identity, product_json, verified_at
                FROM shared_catalog_products
                WHERE lower(brand) IN ({placeholders})
                ORDER BY verified_at DESC
                """,
                variants,
            ).fetchall()
        products: list[dict] = []
        seen: set[str] = set()
        for row in [*rows, *shared_rows]:
            if row["identity"] in seen:
                continue
            seen.add(row["identity"])
            products.append(json.loads(row["product_json"]))
        return products

    def catalog_records_for_brand(self, brand: str) -> list[dict]:
        """Return de-duplicated saved products with their verification dates."""
        variants = tuple(value.casefold() for value in brand_variants(brand))
        placeholders = ", ".join("?" for _ in variants)
        with self._connect() as connection:
            local_rows = connection.execute(
                f"""
                SELECT p.identity, p.product_json, p.verified_at
                FROM catalog_products p
                JOIN catalog_scopes s ON s.scope_key = p.scope_key
                WHERE lower(s.brand) IN ({placeholders})
                ORDER BY p.verified_at DESC
                """,
                variants,
            ).fetchall()
            shared_rows = connection.execute(
                f"""
                SELECT identity, product_json, verified_at
                FROM shared_catalog_products
                WHERE lower(brand) IN ({placeholders})
                ORDER BY verified_at DESC
                """,
                variants,
            ).fetchall()
        records: list[dict] = []
        seen: set[str] = set()
        for row in [*local_rows, *shared_rows]:
            if row["identity"] in seen:
                continue
            seen.add(row["identity"])
            records.append(
                {"product": json.loads(row["product_json"]), "verified_at": row["verified_at"]}
            )
        return records

    def shared_catalog_replace(self, records: list[dict]) -> int:
        """Atomically replace the local mirror with authenticated cloud records."""
        now = _now()
        valid: list[tuple[str, str, str, str, str]] = []
        for record in records:
            try:
                product = ProductMatch.model_validate(record["product_json"])
                identity = str(record.get("identity") or product.product_code or product.product_url)
                brand = canonical_brand(str(record.get("brand") or product.brand))
                verified_at = str(record.get("verified_at") or now)
                valid.append(
                    (identity.strip().lower(), brand, product.model_dump_json(), verified_at, now)
                )
            except (KeyError, TypeError, ValueError):
                continue
        with self._connect() as connection:
            connection.execute("DELETE FROM shared_catalog_products")
            connection.executemany(
                "INSERT INTO shared_catalog_products VALUES (?, ?, ?, ?, ?)", valid
            )
        return len(valid)

    def catalog_products_for_sharing(self) -> list[dict]:
        """Return only reusable verified product data; no project or fixture data."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.identity, s.brand, p.product_json, p.verified_at
                FROM catalog_products p
                JOIN catalog_scopes s ON s.scope_key = p.scope_key
                ORDER BY p.verified_at DESC
                """
            ).fetchall()
        records: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            identity = row["identity"]
            if identity in seen:
                continue
            seen.add(identity)
            product = json.loads(row["product_json"])
            records.append(
                {
                    "identity": identity,
                    "brand": row["brand"],
                    "product_name": product.get("product_name", ""),
                    "product_code": product.get("product_code"),
                    "product_url": product.get("product_url", ""),
                    "product_json": product,
                    "verified_at": row["verified_at"],
                    "updated_at": _now(),
                }
            )
        return records

    def catalog_scopes_for_brand(self, brand: str) -> list[sqlite3.Row]:
        variants = tuple(value.casefold() for value in brand_variants(brand))
        placeholders = ", ".join("?" for _ in variants)
        with self._connect() as connection:
            return connection.execute(
                f"SELECT scope_key, fixture_json FROM catalog_scopes "
                f"WHERE lower(brand) IN ({placeholders})",
                variants,
            ).fetchall()

    def catalog_scopes(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT scope_key, brand, fixture_json, last_verified_at FROM catalog_scopes"
            ).fetchall()

    def catalog_stats(self) -> dict:
        with self._connect() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) scopes,
                       COUNT(DISTINCT brand) brands,
                       MAX(last_verified_at) last_verified_at
                FROM catalog_scopes
                """
            ).fetchone()
            products = connection.execute(
                """
                SELECT COUNT(DISTINCT identity) FROM (
                    SELECT identity FROM catalog_products
                    UNION ALL
                    SELECT identity FROM shared_catalog_products
                )
                """
            ).fetchone()[0]
            shared_products = connection.execute(
                "SELECT COUNT(*) FROM shared_catalog_products"
            ).fetchone()[0]
        return {
            "scopes": counts["scopes"],
            "brands": counts["brands"],
            "products": products,
            "shared_products": shared_products,
            "last_verified_at": counts["last_verified_at"],
        }
    def resolve_learned_family(self, text: str, filename: str) -> tuple[str, float]:
        features = _layout_features(text)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, signature_json FROM learned_families WHERE approved_examples > 0"
            ).fetchall()
            matches = [
                (row["id"], _similarity(features, set(json.loads(row["signature_json"]))))
                for row in rows
            ]
            if matches:
                family_id, score = max(matches, key=lambda item: item[1])
                if score >= 0.52:
                    return family_id, round(score, 3)
            family_id = f"learned_{uuid.uuid4().hex}"
            now = _now()
            connection.execute(
                "INSERT INTO learned_families VALUES (?, ?, ?, 0, ?, ?)",
                (
                    family_id,
                    f"Learned layout: {Path(filename).stem}",
                    json.dumps(sorted(features)),
                    now,
                    now,
                ),
            )
        return family_id, 0.0

    def apply_learned_corrections(
        self, profile_id: str | None, fixtures: list[FixtureRequirement]
    ) -> dict[str, list[str]]:
        """Apply field-level corrections learned for the same family and symbol."""
        if not profile_id:
            return {}
        applied: dict[str, list[str]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.before_json, c.after_json, c.created_at
                FROM corrections c
                JOIN fixtures f ON f.id = c.fixture_id
                WHERE f.profile_id = ? AND f.rejected = 0
                ORDER BY c.created_at
                """,
                (profile_id,),
            ).fetchall()
        by_symbol = {fixture.symbol.upper(): fixture for fixture in fixtures}
        votes: dict[tuple[str, str], list[tuple[str, object]]] = {}
        for row in rows:
            before = json.loads(row["before_json"])
            after = json.loads(row["after_json"])
            if after.get("deleted"):
                continue
            symbol = str(after.get("symbol") or before.get("symbol") or "").upper()
            if symbol not in by_symbol:
                continue
            for field in LEARNABLE_FIELDS:
                if before.get(field) != after.get(field):
                    serialized = json.dumps(after.get(field), sort_keys=True)
                    votes.setdefault((symbol, field), []).append((serialized, after.get(field)))

        for (symbol, field), values in votes.items():
            counts = Counter(serialized for serialized, _ in values)
            highest = max(counts.values())
            winner = next(
                value
                for serialized, value in reversed(values)
                if counts[serialized] == highest
            )
            fixture = by_symbol[symbol]
            if getattr(fixture, field) != winner:
                setattr(fixture, field, winner)
                applied.setdefault(symbol, []).append(field)
                fixture.confidence = max(fixture.confidence, 0.82)
        return applied

    def create_project(self, name: str) -> str:
        project_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?)", (project_id, name, _now())
            )
        return project_id

    def retain_document(
        self,
        project_id: str,
        source: Path,
        original_name: str,
        profile_id: str | None,
        profile_score: float | None,
    ) -> str:
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        document_id = str(uuid.uuid4())
        project_dir = self.documents_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        destination = project_dir / f"{document_id}.pdf"
        shutil.copyfile(source, destination)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    project_id,
                    original_name,
                    str(destination),
                    digest,
                    profile_id,
                    profile_score,
                    _now(),
                ),
            )
        return document_id

    def save_fixtures(
        self,
        project_id: str,
        document_id: str,
        profile_id: str | None,
        fixtures: list[FixtureRequirement],
    ) -> None:
        with self._connect() as connection:
            for fixture in fixtures:
                payload = fixture.model_dump_json()
                connection.execute(
                    """
                    INSERT OR REPLACE INTO fixtures
                    (id, project_id, document_id, profile_id, original_json, current_json,
                     approved, rejected, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        fixture.id,
                        project_id,
                        document_id,
                        profile_id,
                        payload,
                        payload,
                        _now(),
                        _now(),
                    ),
                )

    def approve_fixtures(self, project_id: str, fixtures: list[FixtureRequirement]) -> int:
        approved = 0
        submitted_ids = {fixture.id for fixture in fixtures}
        with self._connect() as connection:
            for fixture in fixtures:
                row = connection.execute(
                    "SELECT current_json FROM fixtures WHERE id = ? AND project_id = ?",
                    (fixture.id, project_id),
                ).fetchone()
                current = fixture.model_dump_json()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO fixtures
                        (id, project_id, document_id, profile_id, original_json, current_json,
                         approved, rejected, created_at, updated_at)
                        VALUES (?, ?, NULL, NULL, ?, ?, 1, 0, ?, ?)
                        """,
                        (fixture.id, project_id, current, current, _now(), _now()),
                    )
                else:
                    before = row["current_json"]
                    if json.loads(before) != json.loads(current):
                        connection.execute(
                            "INSERT INTO corrections VALUES (?, ?, ?, ?, ?)",
                            (str(uuid.uuid4()), fixture.id, before, current, _now()),
                        )
                    connection.execute(
                        """
                        UPDATE fixtures
                        SET current_json = ?, approved = 1, rejected = 0, updated_at = ?
                        WHERE id = ?
                        """,
                        (current, _now(), fixture.id),
                    )
                approved += 1
            removed = connection.execute(
                """
                SELECT id, current_json FROM fixtures
                WHERE project_id = ? AND rejected = 0
                """,
                (project_id,),
            ).fetchall()
            for row in removed:
                if row["id"] in submitted_ids:
                    continue
                connection.execute(
                    "INSERT INTO corrections VALUES (?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        row["id"],
                        row["current_json"],
                        json.dumps({"deleted": True}),
                        _now(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE fixtures
                    SET approved = 0, rejected = 1, updated_at = ? WHERE id = ?
                    """,
                    (_now(), row["id"]),
                )
            learned_profiles = {
                fixture.profile_id
                for fixture in fixtures
                if fixture.profile_id and fixture.profile_id.startswith("learned_")
            }
            for family_id in learned_profiles:
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO family_approvals VALUES (?, ?, ?)",
                    (family_id, project_id, _now()),
                )
                if inserted.rowcount:
                    connection.execute(
                        """
                        UPDATE learned_families
                        SET approved_examples = approved_examples + 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (_now(), family_id),
                    )
        return approved

    def learning_stats(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                "projects": connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "approved_fixtures": connection.execute(
                    "SELECT COUNT(*) FROM fixtures WHERE approved = 1"
                ).fetchone()[0],
                "corrections": connection.execute("SELECT COUNT(*) FROM corrections").fetchone()[0],
                "learned_families": connection.execute(
                    "SELECT COUNT(*) FROM learned_families WHERE approved_examples > 0"
                ).fetchone()[0],
            }

    def document_path(self, project_id: str, document_id: str) -> Path | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT stored_path FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
        return Path(row["stored_path"]) if row else None
