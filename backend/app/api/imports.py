import json
from typing import IO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories import client_repo, import_repo
from app.schemas.imports import (
    ImportLogRead,
    ImportPreview,
    ImportProgress,
    ImportResult,
    JsonImportResult,
    JsonPreview,
    MappingBody,
)
from app.services import import_json_service, import_service
from app.services.fy_utils import is_valid_fy
from app.services.tally_importer import REPORT_TYPES, ImportFormatError

router = APIRouter(prefix="/imports", tags=["imports"])


def _check_request(db: Session, client_id: int, report_type: str) -> None:
    if client_repo.get_client(db, client_id) is None:
        raise HTTPException(404, "Client not found")
    if report_type not in REPORT_TYPES:
        raise HTTPException(422, f"report_type must be one of {', '.join(REPORT_TYPES)}")


def _parse_mapping(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "mapping must be valid JSON") from exc
    if not isinstance(mapping, dict):
        raise HTTPException(422, "mapping must be a JSON object")
    return mapping


@router.post("/preview", response_model=ImportPreview)
async def preview_import(
    client_id: int = Form(...),
    report_type: str = Form(...),
    mapping: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _check_request(db, client_id, report_type)
    content = await file.read()
    try:
        return import_service.preview_file(
            db,
            client_id,
            report_type,
            file.filename or "upload",
            content,
            _parse_mapping(mapping),
        )
    except ImportFormatError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/confirm", response_model=ImportResult)
async def confirm_import(
    client_id: int = Form(...),
    report_type: str = Form(...),
    mapping: str | None = Form(None),
    fy: str | None = Form(None),
    save_mapping: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _check_request(db, client_id, report_type)
    if fy and not is_valid_fy(fy):
        raise HTTPException(422, "fy must look like 2025-26")
    content = await file.read()
    try:
        return import_service.import_file(
            db,
            client_id,
            report_type,
            file.filename or "upload",
            content,
            _parse_mapping(mapping),
            fy,
            save_mapping,
        )
    except ImportFormatError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/log", response_model=list[ImportLogRead])
def import_log(client_id: int | None = None, db: Session = Depends(get_db)):
    return import_repo.list_logs(db, client_id)


@router.get("/mapping/{client_id}/{report_type}", response_model=MappingBody | None)
def get_mapping(client_id: int, report_type: str, db: Session = Depends(get_db)):
    _check_request(db, client_id, report_type)
    mapping = import_repo.get_mapping(db, client_id, report_type)
    return MappingBody(mapping=mapping) if mapping else None


@router.put("/mapping/{client_id}/{report_type}", response_model=MappingBody)
def save_mapping(
    client_id: int, report_type: str, body: MappingBody, db: Session = Depends(get_db)
):
    _check_request(db, client_id, report_type)
    import_repo.save_mapping(db, client_id, report_type, body.mapping)
    db.commit()
    return body


def _uploads(files: list[UploadFile]) -> list[tuple[str, IO[bytes]]]:
    """The uploaded files as on-disk streams (Starlette spools large uploads to disk),
    so a 500+ MB export is parsed without being read into memory."""
    return [(f.filename or "upload.json", f.file) for f in files]


def _fy_set(text: str) -> set[str]:
    fys = {part.strip() for part in text.split(",") if part.strip()}
    bad = [fy for fy in fys if not is_valid_fy(fy)]
    if bad:
        raise HTTPException(422, f"Not a financial year: {bad[0]} (expected e.g. 2025-26)")
    return fys


# These are plain `def` routes: FastAPI runs them in a worker thread, so a long parse
# does not block the progress endpoint below.
@router.post("/tally-json/preview", response_model=JsonPreview)
def preview_tally_json(
    client_id: int = Form(...),
    files: list[UploadFile] = File(...),
    progress_token: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Dry run for Tally's JSON export: send the Master and Transactions files together.
    Poll /imports/tally-json/progress/{progress_token} meanwhile for a progress bar."""
    if client_repo.get_client(db, client_id) is None:
        raise HTTPException(404, "Client not found")
    try:
        return import_json_service.preview_json(db, client_id, _uploads(files), progress_token)
    except ImportFormatError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/tally-json/progress/{token}", response_model=ImportProgress)
def tally_json_progress(token: str):
    progress = import_json_service.progress_for(token)
    if progress is None:
        raise HTTPException(404, "No preview is running with this token")
    return progress


@router.post("/tally-json/confirm", response_model=JsonImportResult)
def confirm_tally_json(
    client_id: int = Form(...),
    files: list[UploadFile] | None = File(None),
    preview_token: str | None = Form(None),
    replace_fys: str = Form(""),
    skip_fys: str = Form(""),
    db: Session = Depends(get_db),
):
    """Import Tally's JSON export (sales and purchases), skipping vouchers already imported.
    Send the `preview_token` from the preview, or the files again. `replace_fys` /
    `skip_fys` are comma-separated FYs (e.g. "2025-26") to replace or leave untouched."""
    if client_repo.get_client(db, client_id) is None:
        raise HTTPException(404, "Client not found")
    if not preview_token and not files:
        raise HTTPException(422, "Send the files or the preview_token of a preview.")
    try:
        return import_json_service.import_json(
            db,
            client_id,
            _uploads(files or []),
            preview_token=preview_token,
            replace_fys=_fy_set(replace_fys),
            skip_fys=_fy_set(skip_fys),
        )
    except ImportFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
