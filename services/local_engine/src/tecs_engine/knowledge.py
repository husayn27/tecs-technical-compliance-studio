from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentProfile:
    id: str
    name: str
    signatures: tuple[str, ...]


@dataclass(frozen=True)
class ProfileMatch:
    profile_id: str
    profile_name: str
    score: float
    matched_signatures: tuple[str, ...]


SEED_PROFILES = (
    DocumentProfile(
        id="bst_site_lighting_v1",
        name="BST outdoor lighting legend",
        signatures=(
            "standalone off grid solar photovoltaic led street lighting",
            "7200lm output ip66 rated",
            "1000mm height 40w led bollard",
            "12m height pole light with 4 x 400w",
            "58070lm",
        ),
    ),
    DocumentProfile(
        id="data_center_lighting_v1",
        name="Data center lighting schedule",
        signatures=(
            "1449mmx60mm",
            "1168mmx60mm",
            "6400lm",
            "4530lm",
            "1100mmx92mm",
        ),
    ),
    DocumentProfile(
        id="el101_multifloor_v1",
        name="EL-101 multi-floor lighting schedule",
        signatures=(
            "lighting fixture schedule",
            "grand total 89",
            "1425lum",
            "7000lum",
            "non maintained suspended emergency exit",
        ),
    ),
    DocumentProfile(
        id="mod_snco_v1",
        name="MOD SNCO accommodation schedule",
        signatures=(
            "db f1 y1",
            "db f2 b1",
            "pirdb g1 r2sensor",
            "db g2 y2",
            "f a l s e c e i l i n g",
        ),
    ),
    DocumentProfile(
        id="villa_lighting_v1",
        name="Villa lighting schedule",
        signatures=(
            "13.5w led ceiling recessed fixed down light",
            "pendant light provision only",
            "12w led 1000lm",
            "8w meter 700lm",
            "luxury villas attached villas",
        ),
    ),
    DocumentProfile(
        id="lighting_sample_v1",
        name="Lighting Drawings sample schedule",
        signatures=(
            "cool white flexible led light strip 12volt",
            "2x10w led 2x1040lm",
            "circular suspended light fitting",
            "25 40w led 3000lm 4800lm",
            "5500 6500 k day light",
        ),
    ),
)


def normalize_document_text(value: str) -> str:
    value = value.lower().replace("\u00a0", " ").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9.]+", " ", value).strip()


def _contains(normalized_text: str, signature: str) -> bool:
    normalized_signature = normalize_document_text(signature)
    if normalized_signature in normalized_text:
        return True
    compact_text = normalized_text.replace(" ", "")
    return normalized_signature.replace(" ", "") in compact_text


def match_document_profile(text: str, filename: str = "") -> ProfileMatch | None:
    normalized = normalize_document_text(f"{text} {filename}")
    candidates: list[ProfileMatch] = []
    for profile in SEED_PROFILES:
        matches = tuple(token for token in profile.signatures if _contains(normalized, token))
        score = len(matches) / len(profile.signatures)
        candidates.append(ProfileMatch(profile.id, profile.name, score, matches))
    best = max(candidates, key=lambda candidate: candidate.score)
    return best if best.score >= 0.4 else None
