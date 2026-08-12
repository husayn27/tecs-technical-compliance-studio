from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from .extractor import verified_schedule_fixtures
from .knowledge import ProfileMatch, match_document_profile
from .models import FixtureRequirement, LocalAIStatus
from .storage import data_directory

LOCAL_AI_HOST = "127.0.0.1"
LOCAL_AI_PORT = 11435
DEFAULT_MODEL_REPOSITORY = "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF"
SCHEDULE_TERMS = (
    "lighting fixture schedule",
    "luminaire schedule",
    "lighting schedule",
    "fixture legend",
    "lighting legend",
    "legend",
)


@dataclass(frozen=True)
class LocalAIExtraction:
    fixtures: list[FixtureRequirement]
    warnings: list[str]
    document_text: str
    profile: ProfileMatch | None = None


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def _candidate_runtime_paths() -> list[Path]:
    executable = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    configured = os.environ.get("TECS_LLAMA_SERVER_PATH")
    paths = [
        Path(configured) if configured else None,
        _application_root() / "runtime" / executable,
        Path(sys.executable).parent / executable,
        Path("/opt/homebrew/bin/llama-server"),
        Path("/usr/local/bin/llama-server"),
    ]
    return [path for path in paths if path is not None]


def _candidate_model_paths() -> list[Path]:
    configured = os.environ.get("TECS_VISION_MODEL_PATH")
    roots = [
        Path(configured) if configured else None,
        data_directory() / "models",
        _application_root() / "models",
    ]
    candidates: list[Path] = []
    for root in roots:
        if root is None:
            continue
        if root.is_file():
            candidates.append(root)
        elif root.is_dir():
            candidates.extend(
                path
                for path in sorted(root.glob("*.gguf"))
                if "mmproj" not in path.name.lower()
            )
    return candidates


def _find_projector(model: Path) -> Path | None:
    configured = os.environ.get("TECS_VISION_PROJECTOR_PATH")
    if configured and Path(configured).is_file():
        return Path(configured)
    candidates = sorted(model.parent.glob("*mmproj*.gguf"))
    return candidates[0] if candidates else None


def _model_repository() -> str | None:
    """A repository is used only after explicit setup; it is cached for offline reuse."""
    configured = os.environ.get("TECS_VISION_MODEL_REPOSITORY", "").strip()
    return configured or None


def _http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 4,
    api_key: str | None = None,
) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class LocalVisionRuntime:
    """Own a loopback-only llama.cpp process used for private drawing analysis."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._error: str | None = None
        self._starting = False
        self._api_key = secrets.token_urlsafe(32)

    @property
    def base_url(self) -> str:
        return os.environ.get(
            "TECS_LOCAL_AI_URL", f"http://{LOCAL_AI_HOST}:{LOCAL_AI_PORT}"
        ).rstrip("/")

    def _server_ready(self) -> bool:
        try:
            _http_json(f"{self.base_url}/health", timeout=0.7, api_key=self._api_key)
            return True
        except Exception:  # noqa: BLE001 - health probes treat every connection failure as unavailable
            return False

    def _runtime_and_model(self) -> tuple[Path | None, Path | None]:
        runtime = next((path for path in _candidate_runtime_paths() if path.is_file()), None)
        model = next((path for path in _candidate_model_paths() if path.is_file()), None)
        return runtime, model

    def ensure_started(self) -> None:
        if self._server_ready() or self._starting:
            return
        runtime, model = self._runtime_and_model()
        repository = _model_repository()
        if runtime is None or (model is None and repository is None):
            return
        with self._lock:
            if self._server_ready() or self._starting:
                return
            self._starting = True
            try:
                command = [
                    str(runtime),
                    "--host",
                    LOCAL_AI_HOST,
                    "--port",
                    str(LOCAL_AI_PORT),
                    "--ctx-size",
                    os.environ.get("TECS_LOCAL_AI_CONTEXT", "16384"),
                    "--n-gpu-layers",
                    "999",
                    "--image-min-tokens",
                    "1024",
                    "--jinja",
                    "--api-key",
                    self._api_key,
                    "--cors-origins",
                    "http://127.0.0.1:8765",
                    "--no-cors-credentials",
                ]
                if model is not None:
                    command.extend(["--model", str(model)])
                    projector = _find_projector(model)
                    if projector:
                        command.extend(["--mmproj", str(projector)])
                else:
                    command.extend(["--hf-repo", repository])
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
                self._error = None
            except OSError as error:
                self._error = str(error)
            finally:
                self._starting = False

    def status(self, start: bool = True) -> LocalAIStatus:
        if start:
            self.ensure_started()
        runtime, model = self._runtime_and_model()
        repository = _model_repository()
        model_name = model.name if model else repository
        if self._server_ready():
            return LocalAIStatus(
                state="ready",
                available=True,
                model=model_name or "local vision model",
                runtime=runtime.name if runtime else "local server",
                message="Local drawing AI is ready. Documents remain on this computer.",
            )
        if self._error:
            return LocalAIStatus(
                state="error",
                message=f"The local drawing AI could not start: {self._error}",
                model=model_name,
                runtime=runtime.name if runtime else None,
            )
        missing = []
        if runtime is None:
            missing.append("runtime")
        if model is None and repository is None:
            missing.append("vision model")
        if missing:
            return LocalAIStatus(
                state="not_installed",
                message=f"Local AI setup required ({' and '.join(missing)} missing).",
                model=model_name,
                runtime=runtime.name if runtime else None,
            )
        return LocalAIStatus(
            state="starting",
            message="Local drawing AI is starting. This can take up to a minute.",
            model=model_name,
            runtime=runtime.name,
        )

    def wait_until_ready(self, timeout: float = 75) -> bool:
        self.ensure_started()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server_ready():
                return True
            if self._process is not None and self._process.poll() is not None:
                self._error = "The local model process exited during startup."
                return False
            time.sleep(0.5)
        self._error = "The local model took too long to start."
        return False

    def analyse(self, prompt: str, images: list[bytes], timeout: float = 600) -> dict:
        if not self.wait_until_ready():
            raise RuntimeError(self.status(start=False).message)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )
        response = _http_json(
            f"{self.base_url}/v1/chat/completions",
            {
                "model": "local-vision",
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.0,
                "max_tokens": 10000,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
            api_key=self._api_key,
        )
        raw = _response_text(response)
        try:
            return _parse_model_json(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            # Small local models occasionally omit a comma in otherwise useful
            # structured output. Give the same private model one text-only repair
            # pass before falling back to deterministic extraction.
            repaired = _http_json(
                f"{self.base_url}/v1/chat/completions",
                {
                    "model": "local-vision",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Repair the supplied JSON. Return one valid JSON object only. "
                                "Do not add, remove, infer, or rewrite fixture values."
                            ),
                        },
                        {"role": "user", "content": raw},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 10000,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout,
                api_key=self._api_key,
            )
            return _parse_model_json(_response_text(repaired))


def _response_text(response: dict[str, Any]) -> str:
    raw = response["choices"][0]["message"]["content"]
    if isinstance(raw, list):
        return "".join(item.get("text", "") for item in raw if isinstance(item, dict))
    return str(raw)


def _parse_model_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("The local model did not return structured fixture data.")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise TypeError("The local model returned an invalid fixture payload.")
    return value


runtime = LocalVisionRuntime()


def _legend_regions(page: pymupdf.Page, blocks: list[tuple] | None = None) -> list[pymupdf.Rect]:
    """Locate every separate legend/schedule table on a drawing page."""
    blocks = blocks or page.get_text("blocks", sort=True)
    anchors = [
        block
        for block in blocks
        if any(term in str(block[4]).lower() for term in SCHEDULE_TERMS)
    ]
    if not anchors:
        return []

    anchors = sorted(anchors, key=lambda block: (block[0], block[1]))
    if len(anchors) == 1:
        anchor = anchors[0]
        right_stops = [
            block[0]
            for block in blocks
            if block[0] > anchor[2]
            and any(term in str(block[4]).lower() for term in ("general notes", "notes:"))
        ]
        x1 = min(right_stops) if right_stops else page.rect.width
        relevant = [
            block
            for block in blocks
            if block[1] >= anchor[1] and block[0] < x1 and block[2] > anchor[0]
        ]
        y1 = max((block[3] for block in relevant), default=page.rect.height)
        return [
            pymupdf.Rect(
                max(0, anchor[0] - page.rect.width * 0.015),
                max(0, anchor[1] - page.rect.height * 0.015),
                min(page.rect.width, x1 + page.rect.width * 0.01),
                min(page.rect.height, y1 + page.rect.height * 0.025),
            )
        ]

    # Side-by-side legends are common on large site drawings. Use the spacing
    # between headings as the table width so each block reaches the model at a
    # readable scale instead of becoming one tiny page-wide crop.
    regions: list[pymupdf.Rect] = []
    for index, anchor in enumerate(anchors):
        left_gap = anchor[0] - anchors[index - 1][0] if index else None
        right_gap = anchors[index + 1][0] - anchor[0] if index + 1 < len(anchors) else None
        inferred_width = right_gap or left_gap or page.rect.width * 0.35
        x0 = max(0, anchor[0] - page.rect.width * 0.015)
        x1 = min(page.rect.width, anchor[0] + inferred_width * 1.02)
        relevant = [
            block
            for block in blocks
            if block[1] >= anchor[1] and block[0] < x1 and block[2] > x0
        ]
        y1 = max((block[3] for block in relevant), default=page.rect.height)
        regions.append(
            pymupdf.Rect(
                x0,
                max(0, anchor[1] - page.rect.height * 0.015),
                x1,
                min(page.rect.height, y1 + page.rect.height * 0.025),
            )
        )
    return regions


def _page_images(page: pymupdf.Page) -> list[bytes]:
    """Return one overview and detailed tiles without writing drawing images to disk."""
    longest = max(page.rect.width, page.rect.height)
    overview_scale = min(2.0, 1500 / longest)
    overview = page.get_pixmap(
        matrix=pymupdf.Matrix(overview_scale, overview_scale), alpha=False
    ).tobytes("png")

    # Native PDF text helps find the schedule; the crop preserves small legend details.
    blocks = page.get_text("blocks", sort=True)
    regions = _legend_regions(page, blocks)
    images = [overview]
    if regions:
        for crop in regions[:6]:
            scale = min(3.2, 2600 / max(crop.width, crop.height))
            images.append(
                page.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale), clip=crop, alpha=False
                ).tobytes("png")
            )
    else:
        # Unknown drawing layouts are divided into four readable quadrants.
        mid_x, mid_y = page.rect.width / 2, page.rect.height / 2
        for crop in (
            pymupdf.Rect(0, 0, mid_x, mid_y),
            pymupdf.Rect(mid_x, 0, page.rect.width, mid_y),
            pymupdf.Rect(0, mid_y, mid_x, page.rect.height),
            pymupdf.Rect(mid_x, mid_y, page.rect.width, page.rect.height),
        ):
            scale = min(3.0, 1800 / max(crop.width, crop.height))
            images.append(
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=crop, alpha=False).tobytes("png")
            )
    return images


def _prompt(filename: str, page_number: int, native_text: str) -> str:
    text = native_text[:30000]
    return f"""You are the private TECS lighting drawing extraction engine. Analyse page {page_number} of {filename}.

The first image is a page overview. Remaining images are detailed schedule crops or drawing quadrants. Extract LIGHT FIXTURES only. Do not return switches, sockets, sensors, distribution boards, notes, cable labels, or generic electrical symbols unless they are explicitly part of a luminaire assembly.

Read every relevant requirement exactly as stated, including symbol/type, complete description, fixture type, mounting, mounting height, wattage, lumens, CCT, CRI, IP, IK, UGR, dimensions, voltage, construction/body material, optical diffuser/reflector, beam angle, LED density, waterproof status, emergency duration, controls and accessories. The fixture symbol must be copied exactly from the legend's code/type column (for example, preserve LS-01; never rename rows A1, A2, etc.). Populate every structured field whose value appears anywhere in that row, even when it is already included in description. Never copy a value from one legend row into another. Preserve every slash-separated option and range. Use null only for an unstated specification. Do not count fixtures from the plan. Extract drawing_quantity only when a quantity, QTY, NOS or PCS value is explicitly printed in that legend/schedule row; otherwise use null. For an assembly such as four luminaires per pole, set units_per_assembly to 4 and quantity to drawing_quantity multiplied by units_per_assembly.

Return JSON only with this shape:
{{"fixtures":[{{"symbol":"exact legend code","description":"complete requirement","fixture_type":"string","drawing_quantity":"integer or null","units_per_assembly":"integer or null","quantity":"integer or null","mounting":"string or null","mounting_height_mm":"number or null","wattage":"number or null","wattage_options":[],"lumens":"integer or null","lumen_options":[],"lumens_is_minimum":"boolean","cct":"integer or null","cct_options":[],"cct_min":"integer or null","cct_max":"integer or null","cri":"integer or null","ip_rating":"integer or null","ik_rating":"integer or null","ugr":"number or null","dimensions":"string or null","construction":"string or null","optical_details":"string or null","voltage":"string or null","beam_angle":"string or null","led_density_per_m":"integer or null","waterproof":"boolean or null","emergency_hours":"number or null","controls":[],"confidence":"0 to 1"}}],"warnings":[]}}

Native PDF text, which may be out of visual order:
---
{text}
---"""


def _number(value: Any, kind: type[int | float]) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        return kind(float(value))
    except (TypeError, ValueError):
        return None


def _number_list(value: Any, kind: type[int | float]) -> list[int] | list[float]:
    if not isinstance(value, list):
        return []
    values = [_number(item, kind) for item in value]
    return list(dict.fromkeys(item for item in values if item is not None))


def _slash_numbers(value: str, kind: type[int | float]) -> list[int] | list[float]:
    values = [_number(item, kind) for item in re.split(r"\s*/\s*", value)]
    return list(dict.fromkeys(item for item in values if item is not None))


def _enrich_from_description(
    fixture: FixtureRequirement, source_text: str | None = None
) -> None:
    """Recover typed values a small VLM sometimes leaves inside its own description."""
    text = source_text or fixture.description
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = text.replace(",", " ")
    explicit = re.search(
        r"\bWATTAGE\s*:\s*((?:\d+(?:\.\d+)?\s*/\s*)*\d+(?:\.\d+)?)\s*(?:W|WATTS?)\b",
        text,
        re.IGNORECASE,
    )
    multiplied_watts = re.search(
        r"\b(\d+)\s*[X×]\s*(\d+(?:\.\d+)?)\s*W\b", text, re.IGNORECASE
    )
    pole_assembly = bool(
        multiplied_watts
        and re.search(r"\bPOLE\s+LIGHT\b", text, re.IGNORECASE)
        and re.search(r"\bINDIVIDUAL\s+LUMINAIRE\b", text, re.IGNORECASE)
    )
    if pole_assembly and multiplied_watts:
        fixture.units_per_assembly = int(multiplied_watts.group(1))
        fixture.wattage = float(multiplied_watts.group(2))
        fixture.wattage_options = [fixture.wattage]
    elif multiplied_watts:
        fixture.wattage = int(multiplied_watts.group(1)) * float(multiplied_watts.group(2))
        fixture.wattage_options = [fixture.wattage]
    elif explicit:
        fixture.wattage_options = _slash_numbers(explicit.group(1), float)
        fixture.wattage = fixture.wattage_options[-1]
    elif fixture.wattage is None or fixture.wattage > 1000:
        values = re.findall(r"(?<![A-Z0-9])(\d+(?:\.\d+)?)\s*W\b", text, re.IGNORECASE)
        if values:
            fixture.wattage = float(values[-1])
        else:
            fixture.wattage = None
    generic_watt_options = re.search(
        r"\b((?:\d+(?:\.\d+)?\s*/\s*)+\d+(?:\.\d+)?)\s*W\b",
        text,
        re.IGNORECASE,
    )
    if generic_watt_options:
        fixture.wattage_options = _slash_numbers(generic_watt_options.group(1), float)
        fixture.wattage = fixture.wattage_options[-1]
    all_watts = [
        float(value)
        for value in re.findall(r"(?<![A-Z0-9])(?:\d+\s*[X×]\s*)?(\d+(?:\.\d+)?)\s*W\b", text, re.IGNORECASE)
    ]
    if not multiplied_watts:
        fixture.wattage_options = list(
            dict.fromkeys([*fixture.wattage_options, *all_watts])
        )
    multiplied = re.search(r"\b(\d+)\s*[X×]\s*(\d+)\s*LM\b", text, re.IGNORECASE)
    regular = re.search(r"\b(\d{3,7})\s*LM\b", text, re.IGNORECASE)
    flux = re.search(
        r"\bLUMINOUS\s+FLUX\s*:\s*(\d{3,7})\s*LM?\b", text, re.IGNORECASE
    )
    if flux:
        fixture.lumens = int(flux.group(1))
    elif multiplied:
        fixture.lumens = int(multiplied.group(1)) * int(multiplied.group(2))
    elif (fixture.lumens is None or fixture.lumens > 1_000_000) and regular:
        fixture.lumens = int(regular.group(1))
    lumen_option_match = re.search(
        r"\b((?:\d{3,7}\s*LM\s*/\s*)+\d{3,7}\s*LM)\b", text, re.IGNORECASE
    ) or re.search(
        r"\b((?:\d{3,7}\s*/\s*)+\d{3,7})\s*LM\b", text, re.IGNORECASE
    )
    if lumen_option_match:
        fixture.lumen_options = [
            int(value) for value in re.findall(r"\d{3,7}", lumen_option_match.group(1))
        ]
        fixture.lumens = fixture.lumen_options[-1]
    elif fixture.lumens is not None:
        fixture.lumen_options = [fixture.lumens]
    if re.search(
        r"\b(?:MIN(?:IMUM)?\.?\s+)?LUMEN\s+OUTPUT\s+SHALL\s+BE\s+MIN\b|"
        r"\bINDIVIDUAL\s+LUMINAIRE\s+LUMEN\s+OUTPUT\s+SHALL\s+BE\s+MIN\b",
        text,
        re.IGNORECASE,
    ):
        fixture.lumens_is_minimum = True
    cct_range = re.search(
        r"\b([2-6]\d{3})\s*[-–]\s*([2-6]\d{3})\s*K\b", text, re.IGNORECASE
    )
    if cct_range:
        fixture.cct_min = int(cct_range.group(1))
        fixture.cct_max = int(cct_range.group(2))
        fixture.cct = round((fixture.cct_min + fixture.cct_max) / 2)
    else:
        cct_option_match = re.search(
            r"\b((?:[2-6]\d{3}\s*/\s*)+[2-6]\d{3})\s*K\b", text, re.IGNORECASE
        )
        cct_values = [
            int(value)
            for value in re.findall(r"\b([2-6]\d{3})\s*K\b", text, re.IGNORECASE)
        ]
        fixture.cct_options = (
            _slash_numbers(cct_option_match.group(1), int)
            if cct_option_match
            else list(dict.fromkeys(cct_values))
        )
        if fixture.cct_options:
            fixture.cct = fixture.cct_options[0] if len(fixture.cct_options) == 1 else fixture.cct_options[-1]
    if fixture.cri is None:
        cri = re.search(r"\bCRI\s*[>:]?\s*(\d{2,3})\b", text, re.IGNORECASE)
        if cri:
            fixture.cri = int(cri.group(1))
    if fixture.ip_rating is None:
        ip = re.search(r"\bIP\s*:?-?\s*(\d{2})\b", text, re.IGNORECASE)
        if ip:
            fixture.ip_rating = int(ip.group(1))
    if fixture.ik_rating is None:
        ik = re.search(r"\bIK\s*:?-?\s*(\d{2})\b", text, re.IGNORECASE)
        if ik:
            fixture.ik_rating = int(ik.group(1))
    if fixture.dimensions is None:
        dimension = re.search(
            r"\b(\d{2,4}(?:\.\d+)?)\s*MM\s*[X×]\s*"
            r"(\d{2,4}(?:\.\d+)?)\s*MM"
            r"(?:\s*[X×]\s*(\d{2,4}(?:\.\d+)?)\s*MM)?\b",
            text,
            re.IGNORECASE,
        ) or re.search(
            r"\b(\d{2,4}(?:\.\d+)?)\s*[X×]\s*(\d{2,4}(?:\.\d+)?)"
            r"(?:\s*[X×]\s*(\d{2,4}(?:\.\d+)?))?\s*MM\b",
            text,
            re.IGNORECASE,
        )
        if dimension:
            fixture.dimensions = " x ".join(
                value for value in dimension.groups() if value
            ) + " mm"
        else:
            diameter = re.search(
                r"\b(\d{2,4}(?:\s*/\s*\d{2,4})+)\s*MM\s*(?:DIA|DIAMETER)\b",
                text,
                re.IGNORECASE,
            )
            if diameter:
                height = re.search(r"\b(\d{2,4})\s*MM\s*(?:HEIGHT|HIGH)\b", text, re.IGNORECASE)
                if not height:
                    height = re.search(r"\bDIA\s*[-–]\s*(\d{2,4})\s*MM\b", text, re.IGNORECASE)
                fixture.dimensions = re.sub(r"\s+", "", diameter.group(1)) + " mm dia"
                if height:
                    fixture.dimensions += f" x {height.group(1)} mm"
    mounting_height = re.search(
        r"\bMOUNTING\s+HEIGHT\s*:?\s*\+?\s*(\d{3,6})\s*MM\b",
        text,
        re.IGNORECASE,
    ) or re.search(r"\+\s*(\d{3,6})\s*MM\b", text, re.IGNORECASE)
    if mounting_height:
        fixture.mounting_height_mm = float(mounting_height.group(1))
    if fixture.mounting is None:
        lower = text.lower()
        for token, value in (
            ("recessed", "recessed"),
            ("suspended", "suspended"),
            ("pendant", "suspended"),
            ("wall mounted", "wall mounted"),
            ("surface mounted", "surface mounted"),
        ):
            if token in lower:
                fixture.mounting = value
                break
    lower = text.lower()
    if "led strip" in lower or "ribbon light" in lower or ("ribbon" in lower and "strip" in lower):
        fixture.fixture_type = "LED strip"
    elif (
        "panel fitting" in lower
        or "panel luminaire" in lower
        or "square type" in lower
        or "600mmx600mm" in lower.replace(" ", "")
    ):
        fixture.fixture_type = "panel luminaire"
    elif "circular" in lower and "suspend" in lower:
        fixture.fixture_type = "circular suspended luminaire"
    elif "linear" in lower or "pendant light" in lower:
        fixture.fixture_type = "linear luminaire"
    elif "downlight" in lower or "down light" in lower:
        fixture.fixture_type = "downlight"
    elif "luminaire" in lower and "suspend" in lower:
        fixture.fixture_type = "linear luminaire"
    if fixture.construction is None:
        body = re.search(
            r"\b(POWDER\s+COATED\s+ALUMINI?UM(?:\s+BLACK)?\s+RIM|"
            r"COATED(?:\s*SUSPENDED)?\s*STEEL\s+BODY|"
            r"EXTRUDED\s+ALUMINI?UM\s+CURVED\s+PROFILE|"
            r"DIE\s+CAST\s+ALUMINI?UM(?:\s+\w+){0,3}\s+BODY|"
            r"STEEL\s+SHEET\s+HOUSING\s+WITH\s+ALUMINI?UM\s+FRAME)\b",
            text,
            re.IGNORECASE,
        ) or re.search(r"\bBODY\s*:\s*([^.;]+)", text, re.IGNORECASE)
        if not body:
            body = re.search(
                r"\b((?:(?:COATED|SHEET)\s+STEEL|ALUMINI?UM|POLYCARBONATE)[^,;.]{0,45}(?:BODY|CANOPY|PROFILE|HOUSING|FRAME|RIM))\b",
                text,
                re.IGNORECASE,
            )
        if body:
            construction = re.sub(r"\s+", " ", body.group(1)).strip()
            construction = re.sub(
                r"\b(ALUMINI?UM)\s+[A-Z]\s*-\s*LED\s+(BODY)\b",
                r"\1 \2",
                construction,
                flags=re.IGNORECASE,
            )
            fixture.construction = construction.replace("COATED SUSPENDED STEEL", "COATED STEEL")
        elif (
            re.search(r"\bCOATED\b", text, re.IGNORECASE)
            and re.search(r"\bSTEEL\b", text, re.IGNORECASE)
            and re.search(r"\bBODY\b", text, re.IGNORECASE)
        ):
            fixture.construction = "coated steel body"
    if fixture.optical_details is None:
        optical = re.search(
            r"\b((?:POLYCARBONATE\s+MICRO\s+PRISMATIC|SATIN\s+OPAL|OPAL|PRISMATIC|FROSTED)[^.;]{0,100}(?:DIFFUSER|COVER))",
            text,
            re.IGNORECASE,
        ) or re.search(r"\b(SATIN\s+RITE\s+REFLECTOR)\b", text, re.IGNORECASE)
        if optical:
            optical_text = re.sub(r"\s+", " ", optical.group(1)).strip()
            optical_text = re.sub(r"\+\s*\d+(?:\.\d+)?\s*MM", "", optical_text, flags=re.IGNORECASE)
            optical_text = re.sub(r"\bOPAL\s*SUSPENDED\s*ACRYLIC\b", "OPAL ACRYLIC", optical_text, flags=re.IGNORECASE)
            fixture.optical_details = re.sub(r"\s+", " ", optical_text).strip()
    voltage = re.search(
        r"\b(?:INPUT\s+)?(?:VOLTAGE\s*:\s*)?((?:AC\s*)?\d+\s*V(?:OLT)?S?(?:\s*[-–]\s*\d+\s*V(?:OLT)?S?)?)\b",
        text,
        re.IGNORECASE,
    )
    if voltage:
        fixture.voltage = re.sub(r"\s+", " ", voltage.group(1)).strip()
    compact_voltage_range = re.search(
        r"\b(AC\s*)?(\d+)\s*V?\s*[-–]\s*(\d+)\s*V(?:OLT)?S?\b",
        text,
        re.IGNORECASE,
    )
    if compact_voltage_range:
        prefix = "AC " if compact_voltage_range.group(1) else ""
        fixture.voltage = (
            f"{prefix}{compact_voltage_range.group(2)}V – "
            f"{compact_voltage_range.group(3)}V"
        )
    voltage_values = re.findall(r"\b(\d+)\s*V(?:OLT)?S?\b", text, re.IGNORECASE)
    if "AC" in text.upper() and len(voltage_values) >= 2:
        fixture.voltage = f"AC {voltage_values[0]}V – {voltage_values[1]}V"
    beam = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(?:°|DEGREES?|DEG)\s*BEAM\s*(?:ANGLE)?",
        text,
        re.IGNORECASE,
    ) or re.search(
        r"\bBEAM\s*ANGLE\s*:?\s*(\d+(?:\.\d+)?)\s*(?:°|DEGREES?|DEG)?",
        text,
        re.IGNORECASE,
    ) or re.search(
        r"\b(\d+(?:\.\d+)?)\s*(?:°|DEGREES?|DEG)\b",
        text,
        re.IGNORECASE,
    )
    if beam:
        fixture.beam_angle = f"{beam.group(1)}°"
    density = re.search(
        r"\b(\d+)\s*LEDS?\s*(?:/|PER\s*)?\s*M(?:ETER)?\b", text, re.IGNORECASE
    )
    if density:
        fixture.led_density_per_m = int(density.group(1))
    if re.search(
        r"\bNON(?:[-\s]+\w+){0,2}[-\s]+WATERPROOF\b", text, re.IGNORECASE
    ):
        fixture.waterproof = False
    elif re.search(r"\bWATERPROOF\b", text, re.IGNORECASE):
        fixture.waterproof = True
    if fixture.ugr is None:
        ugr = re.search(r"\bUGR\s*[<:]?\s*(\d+(?:\.\d+)?)\b", text, re.IGNORECASE)
        if ugr:
            fixture.ugr = float(ugr.group(1))
    if fixture.emergency_hours is None:
        emergency = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(?:H|HR|HRS|HOURS?)\b[^.;]{0,35}\bEMERGENCY\b|\bEMERGENCY\b[^.;]{0,35}\b(\d+(?:\.\d+)?)\s*(?:H|HR|HRS|HOURS?)\b",
            text,
            re.IGNORECASE,
        )
        if emergency:
            fixture.emergency_hours = float(emergency.group(1) or emergency.group(2))
    known_controls = [
        label
        for pattern, label in (
            (r"\bDALI\b", "DALI"),
            (r"\b0\s*[-–]\s*10\s*V\b", "0-10V"),
            (r"\bMOTION\s+SENSOR\b", "motion sensor"),
            (r"\bOCCUPANCY\s+SENSOR\b", "occupancy sensor"),
        )
        if re.search(pattern, text, re.IGNORECASE)
    ]
    fixture.controls = list(dict.fromkeys([*fixture.controls, *known_controls]))
    fixture.wattage_options = sorted(set(fixture.wattage_options))
    fixture.lumen_options = sorted(set(fixture.lumen_options))
    fixture.cct_options = sorted(set(fixture.cct_options))

    populated = sum(
        value is not None
        for value in (
            fixture.wattage,
            fixture.lumens,
            fixture.cct,
            fixture.cri,
            fixture.ip_rating,
            fixture.mounting,
            fixture.dimensions,
            fixture.construction,
            fixture.optical_details,
            fixture.voltage,
            fixture.beam_angle,
        )
    )
    fixture.confidence = min(0.9, max(fixture.confidence, 0.48 + populated * 0.045))
    if fixture.quantity <= 0:
        fixture.status = "review"


def _validate_against_legend_rows(
    page: pymupdf.Page, fixtures: list[FixtureRequirement]
) -> None:
    """Use the PDF text layer as evidence for values interpreted by the vision model."""
    blocks = page.get_text("blocks", sort=True)
    anchors = [
        block
        for block in blocks
        if any(term in str(block[4]).lower() for term in SCHEDULE_TERMS)
    ]
    if not anchors:
        return
    anchor = anchors[0]
    right_stops = [
        block[0]
        for block in blocks
        if block[0] > anchor[2]
        and any(term in str(block[4]).lower() for term in ("general notes", "notes:"))
    ]
    table_x1 = min(right_stops) if right_stops else page.rect.width
    words = page.get_text("words", sort=True)
    fixture_matches: dict[str, list[tuple]] = {}
    for fixture in fixtures:
        fixture_matches[fixture.id] = [
            word
            for word in words
            if str(word[4]).strip().upper() == fixture.symbol.strip().upper()
            and word[0] >= anchor[0]
            and word[2] <= table_x1
        ]
    reliable_data_rows = [
        float(word[1])
        for fixture in fixtures
        if len(fixture.symbol.strip()) >= 2
        for word in fixture_matches[fixture.id]
    ]
    data_y_floor = (
        min(reliable_data_rows) - page.rect.height * 0.003
        if reliable_data_rows
        else anchor[3]
    )
    positions: list[tuple[FixtureRequirement, float]] = []
    for fixture in fixtures:
        matches = [
            word
            for word in fixture_matches[fixture.id]
            if word[1] >= data_y_floor
        ]
        if matches:
            # A PDF can split LEGEND or SYMBOL into one-letter tokens. Establish
            # the first data row from multi-character codes, then take the first
            # exact occurrence of every schedule code below that header.
            code_word = min(matches, key=lambda word: (word[1], word[0]))
            positions.append((fixture, float(code_word[1])))
    positions.sort(key=lambda value: value[1])
    if not positions:
        return

    for index, (fixture, center_y) in enumerate(positions):
        top_padding = max(2, page.rect.height * 0.003)
        y0 = max(anchor[3], center_y - top_padding)
        if index + 1 < len(positions):
            next_y = positions[index + 1][1]
            bottom_padding = max(0.5, page.rect.height * 0.0007)
            y1 = next_y - bottom_padding
        else:
            next_rows = [
                word[1]
                for word in words
                if word[0] >= anchor[0]
                and word[0] < anchor[0] + page.rect.width * 0.04
                and word[1] > center_y
                and re.fullmatch(r"0?[7-9]|1\d", word[4])
            ]
            y1 = min(next_rows) if next_rows else min(
                page.rect.height, center_y + page.rect.height * 0.12
            )
        clip = pymupdf.Rect(anchor[0], y0, table_x1, y1)
        native_row_text = page.get_text("text", clip=clip, sort=True)
        spaced_row_text = " ".join(
            str(word[4])
            for word in words
            if word[0] >= anchor[0]
            and word[2] <= table_x1
            and word[1] >= y0
            and word[3] <= y1
        )
        row_text = f"{native_row_text} {spaced_row_text}"
        if len(row_text.strip()) < 20:
            continue
        # Numeric/model fields must be supported by the source row, not just plausible.
        fixture.wattage = None
        fixture.wattage_options = []
        fixture.lumens = None
        fixture.lumen_options = []
        fixture.cct = None
        fixture.cct_options = []
        fixture.cct_min = None
        fixture.cct_max = None
        fixture.cri = None
        fixture.ip_rating = None
        fixture.ik_rating = None
        fixture.ugr = None
        fixture.dimensions = None
        fixture.mounting = None
        fixture.mounting_height_mm = None
        fixture.construction = None
        fixture.optical_details = None
        fixture.voltage = None
        fixture.beam_angle = None
        fixture.led_density_per_m = None
        fixture.waterproof = None
        fixture.emergency_hours = None
        fixture.controls = []
        fixture.quantity = 0
        fixture.drawing_quantity = None
        _enrich_from_description(fixture, row_text)
        explicit_quantity = re.search(
            r"\b0*(\d+)\s*(?:NOS?\.?|PCS?\.?)\b", row_text, re.IGNORECASE
        )
        if explicit_quantity:
            fixture.drawing_quantity = int(explicit_quantity.group(1))
            fixture.quantity = fixture.drawing_quantity * fixture.units_per_assembly


def _fixture_from_ai(item: dict[str, Any], filename: str, page: int) -> FixtureRequirement | None:
    symbol = str(item.get("symbol") or "").strip().upper()
    description = str(item.get("description") or "").strip()
    if not symbol or not description:
        return None
    # Quantities are accepted only when the model has read an explicit schedule
    # value. The model is instructed never to count repeated plan symbols.
    units = _number(item.get("units_per_assembly"), int) or 1
    drawing_quantity = _number(item.get("drawing_quantity"), int)
    quantity = drawing_quantity * units if drawing_quantity is not None else 0
    confidence = _number(item.get("confidence"), float)
    confidence = min(0.98, max(0.05, confidence if confidence is not None else 0.55))
    controls = item.get("controls")
    fixture = FixtureRequirement(
        id=str(uuid.uuid4()),
        symbol=symbol,
        description=description,
        quantity=max(0, quantity or 0),
        drawing_quantity=max(0, drawing_quantity) if drawing_quantity is not None else None,
        units_per_assembly=max(1, units),
        fixture_type=str(item.get("fixture_type") or "unspecified").strip().lower(),
        mounting=str(item["mounting"]).strip().lower() if item.get("mounting") else None,
        mounting_height_mm=_number(item.get("mounting_height_mm"), float),
        wattage=_number(item.get("wattage"), float),
        wattage_options=_number_list(item.get("wattage_options"), float),
        lumens=_number(item.get("lumens"), int),
        lumen_options=_number_list(item.get("lumen_options"), int),
        lumens_is_minimum=bool(item.get("lumens_is_minimum", False)),
        cct=_number(item.get("cct"), int),
        cct_options=_number_list(item.get("cct_options"), int),
        cct_min=_number(item.get("cct_min"), int),
        cct_max=_number(item.get("cct_max"), int),
        cri=_number(item.get("cri"), int),
        ip_rating=_number(item.get("ip_rating"), int),
        ik_rating=_number(item.get("ik_rating"), int),
        ugr=_number(item.get("ugr"), float),
        dimensions=str(item["dimensions"]).strip() if item.get("dimensions") else None,
        construction=str(item["construction"]).strip() if item.get("construction") else None,
        optical_details=str(item["optical_details"]).strip() if item.get("optical_details") else None,
        voltage=str(item["voltage"]).strip() if item.get("voltage") else None,
        beam_angle=str(item["beam_angle"]).strip() if item.get("beam_angle") else None,
        led_density_per_m=_number(item.get("led_density_per_m"), int),
        waterproof=item.get("waterproof") if isinstance(item.get("waterproof"), bool) else None,
        emergency_hours=_number(item.get("emergency_hours"), float),
        controls=[str(value).strip() for value in controls] if isinstance(controls, list) else [],
        source_file=filename,
        source_page=page,
        confidence=confidence,
        status="review",
    )
    _enrich_from_description(fixture)
    return fixture


def extract_pdf_with_local_ai(path: Path) -> LocalAIExtraction:
    if not runtime.wait_until_ready():
        raise RuntimeError(runtime.status(start=False).message)
    document = pymupdf.open(path)
    fixtures: list[FixtureRequirement] = []
    warnings: list[str] = []
    document_text = "\n".join(page.get_text("text", sort=True) for page in document)
    profile = match_document_profile(document_text, path.name)
    try:
        likely_pages = [
            index
            for index, page in enumerate(document)
            if any(term in page.get_text("text", sort=True).lower() for term in SCHEDULE_TERMS)
        ]
        page_indexes = likely_pages or list(range(min(document.page_count, 8)))
        if len(page_indexes) > 8:
            page_indexes = page_indexes[:8]
            warnings.append("Only the first eight likely lighting schedule pages were analysed locally.")
        for page_index in page_indexes:
            page = document[page_index]
            native_text = page.get_text("text", sort=True)
            result = runtime.analyse(
                _prompt(path.name, page_index + 1, native_text), _page_images(page)
            )
            page_fixtures: list[FixtureRequirement] = []
            for item in result.get("fixtures", []):
                if isinstance(item, dict):
                    fixture = _fixture_from_ai(item, path.name, page_index + 1)
                    if fixture:
                        page_fixtures.append(fixture)
            warnings.extend(str(value) for value in result.get("warnings", []) if value)
            if page_fixtures:
                _validate_against_legend_rows(page, page_fixtures)
            verified = verified_schedule_fixtures(
                page,
                path.name,
                page_index + 1,
                profile.profile_id if profile else None,
            )
            if verified:
                # The vision model discovers the rows. For the five seed drawing
                # families, deterministic PDF evidence acts as a guardrail: it
                # replaces malformed labels, omitted rows, duplicates and
                # unsupported model values with one authoritative row per symbol.
                for fixture in verified:
                    # BST quantities are explicitly printed as NOS in the two
                    # legends. Other seed parsers may count plan symbols, so
                    # those remain manual until schedule evidence is available.
                    if not profile or profile.profile_id != "bst_site_lighting_v1":
                        fixture.quantity = 0
                        fixture.drawing_quantity = None
                    fixture.status = "review" if fixture.quantity <= 0 else "confirmed"
                page_fixtures = verified
            fixtures.extend(page_fixtures)
    finally:
        document.close()

    # Consolidate repeated schedule rows, preferring the most complete/high-confidence result.
    unique: dict[str, FixtureRequirement] = {}
    for fixture in fixtures:
        key = fixture.symbol.upper()
        current = unique.get(key)
        completeness = sum(
            value is not None
            for value in (
                fixture.wattage,
                fixture.lumens,
                fixture.cct,
                fixture.ip_rating,
                fixture.mounting,
                fixture.dimensions,
                fixture.construction,
                fixture.optical_details,
            )
        )
        current_completeness = -1 if current is None else sum(
            value is not None
            for value in (
                current.wattage,
                current.lumens,
                current.cct,
                current.ip_rating,
                current.mounting,
                current.dimensions,
                current.construction,
                current.optical_details,
            )
        )
        if current is None or (completeness, fixture.confidence) > (
            current_completeness,
            current.confidence,
        ):
            unique[key] = fixture
    for fixture in unique.values():
        fixture.profile_id = profile.profile_id if profile else "local_ai_general"
        fixture.profile_score = profile.score if profile else fixture.confidence
    if not unique:
        warnings.append("The local AI did not find a verifiable lighting fixture schedule.")
    return LocalAIExtraction(list(unique.values()), warnings, document_text, profile)
