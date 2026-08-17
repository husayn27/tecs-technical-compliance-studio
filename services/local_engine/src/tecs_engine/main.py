from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import pymupdf
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import __version__
from .catalog import CatalogService
from .commercial import build_commercial_xlsx
from .compliance import build_compliance_pdf, build_compliance_xlsx
from .extractor import extract_pdf_detailed
from .local_ai import extract_pdf_with_local_ai
from .local_ai import runtime as local_ai_runtime
from .models import (
    ApiKeyRequest,
    CommercialQuotationRequest,
    ExtractionResponse,
    FixtureApprovalRequest,
    ProductSearchRequest,
    QuoteRequest,
    TeamProjectSaveRequest,
    TeamWorkspaceKeyRequest,
    TechnicalSheetRequest,
)
from .product_search import delete_api_key, has_api_key, save_api_key
from .quote import build_pdf, build_xlsx
from .shared_catalog import SharedCatalogService
from .storage import KnowledgeStore
from .team_projects import TeamProjectError, TeamProjectService

app = FastAPI(title="TECS Lighting Engine", version=__version__)
knowledge = KnowledgeStore()
shared_catalog = SharedCatalogService(knowledge)
catalog = CatalogService(knowledge, on_catalog_updated=shared_catalog.product_saved)
team_projects = TeamProjectService()


def _settings_path() -> Path:
    return knowledge.root / "settings.json"


def _read_local_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}


def _write_local_settings(settings: dict) -> None:
    target = _settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    temporary.replace(target)


def _export_directory() -> Path:
    environment = os.getenv("TECS_EXPORT_DIR")
    if environment:
        return Path(environment).expanduser()
    configured = _read_local_settings().get("export_directory")
    return Path(configured).expanduser() if configured else Path.home() / "Downloads"


def _choose_export_directory() -> Path | None:
    current = _export_directory()
    if sys.platform == "win32":
        escaped_current = str(current).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f"$dialog.SelectedPath = '{escaped_current}'; "
            "$dialog.Description = 'Choose where TECS exports will be saved'; "
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($dialog.SelectedPath) }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    elif sys.platform == "darwin":
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt "Choose where TECS exports will be saved")',
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    else:
        raise RuntimeError("Folder selection is supported in the Windows and macOS apps.")
    selected = result.stdout.strip()
    return Path(selected) if result.returncode == 0 and selected else None


@app.on_event("startup")
def start_catalog_scheduler() -> None:
    catalog.start_scheduler()
    shared_catalog.start_scheduler()


def _save_export(content: bytes, filename: str, extension: str) -> dict[str, str | bool]:
    safe_stem = Path(filename).stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", safe_stem).strip(" .-")
    safe_stem = safe_stem[:120] or "TECS-Technical-Compliance"
    export_dir = _export_directory()
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"{safe_stem}.{extension}"
    copy_number = 2
    while target.exists():
        target = export_dir / f"{safe_stem} ({copy_number}).{extension}"
        copy_number += 1
    target.write_bytes(content)
    return {"saved": True, "filename": target.name, "path": str(target)}
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
        "tauri://localhost",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/settings/export-folder")
def export_folder() -> dict[str, str]:
    return {"path": str(_export_directory())}


@app.post("/api/settings/export-folder/choose")
def choose_export_folder() -> dict[str, str | bool]:
    try:
        selected = _choose_export_directory()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    if selected is None:
        return {"selected": False, "path": str(_export_directory())}
    selected.mkdir(parents=True, exist_ok=True)
    settings = _read_local_settings()
    settings["export_directory"] = str(selected)
    _write_local_settings(settings)
    return {"selected": True, "path": str(selected)}


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "engine_version": app.version,
        "catalog_api": True,
        "api_key_configured": has_api_key(),
        "team_projects_configured": team_projects.configured(),
        "local_ai": local_ai_runtime.status().model_dump(),
    }


@app.get("/api/local-ai/status")
def local_ai_status() -> dict:
    return local_ai_runtime.status().model_dump()


@app.get("/api/learning/stats")
def learning_stats() -> dict:
    return knowledge.learning_stats()


@app.get("/api/catalog/status")
def catalog_status() -> dict:
    return {**catalog.status(), "team_catalog": shared_catalog.status()}


@app.get("/api/settings/team-catalog")
def team_catalog_status() -> dict:
    return shared_catalog.status()


@app.post("/api/catalog/team-sync")
def sync_team_catalog() -> dict:
    try:
        return shared_catalog.sync()
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _team_project_call(callback):
    try:
        return callback()
    except TeamProjectError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.get("/api/settings/team-projects")
def team_projects_status() -> dict:
    return team_projects.status()


@app.post("/api/settings/team-projects")
def configure_team_projects(request: TeamWorkspaceKeyRequest) -> dict:
    return _team_project_call(lambda: team_projects.configure(request.workspace_key))


@app.delete("/api/settings/team-projects")
def remove_team_projects_key() -> dict:
    return _team_project_call(team_projects.remove_key)


@app.get("/api/team-projects")
def list_team_projects() -> list[dict]:
    return _team_project_call(team_projects.list_projects)


@app.get("/api/team-projects/{project_id}")
def get_team_project(project_id: str) -> dict:
    return _team_project_call(lambda: team_projects.get_project(project_id))


@app.post("/api/team-projects")
def save_team_project(request: TeamProjectSaveRequest) -> dict:
    return _team_project_call(lambda: team_projects.save_project(request.model_dump()))


@app.post("/api/catalog/search-status")
def catalog_search_status(request: ProductSearchRequest) -> dict:
    try:
        return catalog.scope_status(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/catalog/browse")
def browse_catalog(request: ProductSearchRequest) -> dict:
    try:
        return catalog.browse(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/catalog/brands/{brand}/refresh")
def refresh_catalog_brand(brand: str) -> dict:
    try:
        return {"started": catalog.refresh_brand(brand)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/settings/api-key")
def configure_api_key(request: ApiKeyRequest) -> dict:
    save_api_key(request.api_key)
    return {"saved": True}


@app.delete("/api/settings/api-key")
def remove_api_key() -> dict:
    return {"removed": True, "api_key_configured": delete_api_key()}


@app.post("/api/extract", response_model=ExtractionResponse)
async def extract(
    project_name: str,
    files: Annotated[list[UploadFile], File()],
) -> ExtractionResponse:
    project_id = knowledge.create_project(project_name)
    all_fixtures = []
    warnings: list[str] = []
    used_local_ai = False
    with tempfile.TemporaryDirectory(prefix="tecs-lighting-") as directory:
        for uploaded in files:
            if not uploaded.filename or not uploaded.filename.lower().endswith(".pdf"):
                warnings.append(f"Skipped unsupported file: {uploaded.filename or 'unnamed file'}")
                continue
            target = Path(directory) / Path(uploaded.filename).name
            target.write_bytes(await uploaded.read())
            if local_ai_runtime.status().available:
                try:
                    result = extract_pdf_with_local_ai(target)
                    used_local_ai = True
                except Exception as error:  # noqa: BLE001 - a safe private fallback is required
                    warnings.append(
                        f"{uploaded.filename}: local AI analysis failed ({error}); "
                        "the private rule extractor was used instead."
                    )
                    result = extract_pdf_detailed(target)
            else:
                result = extract_pdf_detailed(target)
                warnings.append(
                    f"{uploaded.filename}: local AI is not installed yet; "
                    "the private rule extractor was used."
                )
            result_profile = getattr(result, "profile", None)
            if result_profile:
                profile_id = result_profile.profile_id
                profile_score = result_profile.score
            else:
                profile_id, profile_score = knowledge.resolve_learned_family(
                    result.document_text, uploaded.filename
                )
                for fixture in result.fixtures:
                    fixture.profile_id = profile_id
                    fixture.profile_score = profile_score
            learned = knowledge.apply_learned_corrections(profile_id, result.fixtures)
            if learned:
                summary = ", ".join(
                    f"{symbol} ({len(fields)} fields)" for symbol, fields in learned.items()
                )
                warnings.append(
                    f"{uploaded.filename}: applied approved local corrections to {summary}."
                )
            document_id = knowledge.retain_document(
                project_id,
                target,
                uploaded.filename,
                profile_id,
                profile_score,
            )
            for fixture in result.fixtures:
                fixture.document_id = document_id
                fixture.evidence_url = (
                    f"/api/projects/{project_id}/documents/{document_id}/pages/"
                    f"{fixture.source_page}/preview"
                )
            knowledge.save_fixtures(
                project_id,
                document_id,
                profile_id,
                result.fixtures,
            )
            all_fixtures.extend(result.fixtures)
            warnings.extend(result.warnings)
    if not all_fixtures:
        warnings.append("No fixture schedule was detected automatically. Add fixtures manually or enable OCR.")
    elif used_local_ai:
        stated_quantities = [fixture for fixture in all_fixtures if fixture.quantity > 0]
        missing_quantities = [fixture for fixture in all_fixtures if fixture.quantity <= 0]
        if missing_quantities and stated_quantities:
            warnings.append(
                "Printed schedule quantities were extracted where stated. Enter a quantity "
                "only for the remaining rows; plan symbols were not counted."
            )
        elif missing_quantities:
            warnings.append(
                "Fixture specifications were extracted, but this legend does not state "
                "fixture quantities. Enter them manually; plan symbols were not counted."
            )
        else:
            warnings.append(
                "Fixture specifications and printed schedule quantities were extracted "
                "without counting symbols from the plan."
            )
    return ExtractionResponse(
        project_id=project_id,
        project_name=project_name,
        fixtures=all_fixtures,
        warnings=warnings,
        analysis_engine="local_ai" if used_local_ai else "rules",
    )


@app.post("/api/projects/{project_id}/fixtures/approve")
def approve_fixtures(project_id: str, request: FixtureApprovalRequest) -> dict:
    return {"approved": knowledge.approve_fixtures(project_id, request.fixtures)}


@app.get("/api/projects/{project_id}/documents/{document_id}/pages/{page}/preview")
def document_preview(project_id: str, document_id: str, page: int) -> Response:
    path = knowledge.document_path(project_id, document_id)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Source drawing was not found.")
    document = pymupdf.open(path)
    try:
        if page < 1 or page > document.page_count:
            raise HTTPException(status_code=404, detail="Source page was not found.")
        pixmap = document[page - 1].get_pixmap(
            matrix=pymupdf.Matrix(1.3, 1.3), alpha=False
        )
        return Response(content=pixmap.tobytes("png"), media_type="image/png")
    finally:
        document.close()


@app.post("/api/products/search")
def products(request: ProductSearchRequest, refresh: bool = False):
    try:
        return catalog.search(request, refresh=refresh)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/quote/xlsx")
def quote_xlsx(request: QuoteRequest) -> Response:
    return Response(
        content=build_xlsx(request),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="TECS-Lighting-Quotation.xlsx"'},
    )


@app.post("/api/quote/pdf")
def quote_pdf(request: QuoteRequest) -> Response:
    return Response(
        content=build_pdf(request),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="TECS-Lighting-Quotation.pdf"'},
    )


@app.post("/api/compliance/xlsx")
def compliance_xlsx(request: TechnicalSheetRequest) -> Response:
    try:
        content = build_compliance_xlsx(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="TECS-Technical-Compliance.xlsx"'},
    )


@app.post("/api/compliance/xlsx/save")
def save_compliance_xlsx(
    request: TechnicalSheetRequest,
    filename: str = "TECS-Technical-Compliance",
) -> dict[str, str | bool]:
    try:
        return _save_export(build_compliance_xlsx(request), filename, "xlsx")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/compliance/pdf")
def compliance_pdf(request: TechnicalSheetRequest) -> Response:
    try:
        content = build_compliance_pdf(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="TECS-Technical-Compliance.pdf"'},
    )


@app.post("/api/compliance/pdf/save")
def save_compliance_pdf(
    request: TechnicalSheetRequest,
    filename: str = "TECS-Technical-Compliance",
) -> dict[str, str | bool]:
    try:
        return _save_export(build_compliance_pdf(request), filename, "pdf")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/commercial/xlsx/save")
def save_commercial_xlsx(
    request: CommercialQuotationRequest,
    filename: str = "TECS-Commercial-Quotation",
) -> dict[str, str | bool]:
    try:
        return _save_export(build_commercial_xlsx(request), filename, "xlsx")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def run() -> None:
    port = int(os.getenv("TECS_ENGINE_PORT", "8765"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    run()
