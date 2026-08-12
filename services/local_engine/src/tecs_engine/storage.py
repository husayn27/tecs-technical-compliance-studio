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

from .knowledge import SEED_PROFILES
from .models import FixtureRequirement

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
