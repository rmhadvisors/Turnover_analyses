import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories import client_repo, import_repo
from app.schemas.imports import (
    ImportLogRead,
    ImportPreview,
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


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    return [(f.filename or "upload.json", await f.read()) for f in files]


@router.post("/tally-json/preview", response_model=JsonPreview)
async def preview_tally_json(
    client_id: int = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Dry run for Tally's JSON export: send the Master and Transactions files together."""
    if client_repo.get_client(db, client_id) is None:
        raise HTTPException(404, "Client not found")
    try:
        return import_json_service.preview_json(db, client_id, await _read_uploads(files))
    except ImportFormatError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/tally-json/confirm", response_model=JsonImportResult)
async def confirm_tally_json(
    client_id: int = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Import Tally's JSON export (sales and purchases), skipping vouchers already imported."""
    if client_repo.get_client(db, client_id) is None:
        raise HTTPException(404, "Client not found")
    try:
        return import_json_service.import_json(db, client_id, await _read_uploads(files))
    except ImportFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
