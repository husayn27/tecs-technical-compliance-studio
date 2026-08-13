from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Annotated

import pymupdf
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .extractor import extract_pdf_detailed
from .local_ai import extract_pdf_with_local_ai
from .local_ai import runtime as local_ai_runtime
from .models import (
    ApiKeyRequest,
    ExtractionResponse,
    FixtureApprovalRequest,
    ProductSearchRequest,
    QuoteRequest,
    TechnicalSheetRequest,
)
from .compliance import build_compliance_pdf, build_compliance_xlsx
from .product_search import delete_api_key, has_api_key, save_api_key, search_products
from .quote import build_pdf, build_xlsx
from .storage import KnowledgeStore

app = FastAPI(title="TECS Lighting Engine", version="0.1.0")
knowledge = KnowledgeStore()


def _save_export(content: bytes, filename: str, extension: str) -> dict[str, str | bool]:
    safe_stem = Path(filename).stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", safe_stem).strip(" .-")
    safe_stem = safe_stem[:120] or "TECS-Technical-Compliance"
    export_dir = Path(os.getenv("TECS_EXPORT_DIR", Path.home() / "Downloads"))
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


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "api_key_configured": has_api_key(),
        "local_ai": local_ai_runtime.status().model_dump(),
    }


@app.get("/api/local-ai/status")
def local_ai_status() -> dict:
    return local_ai_runtime.status().model_dump()


@app.get("/api/learning/stats")
def learning_stats() -> dict:
    return knowledge.learning_stats()


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
def products(request: ProductSearchRequest):
    try:
        return search_products(request)
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


def run() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    run()
