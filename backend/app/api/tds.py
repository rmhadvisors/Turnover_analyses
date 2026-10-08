"""TDS: rate master, ledger mapping, party analysis, alerts detail and reports."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.alerts import to_read as alert_to_read
from app.api.deps import get_client_or_404
from app.api.reports import XLSX, _attachment
from app.database import get_db
from app.models import Client, TdsSection
from app.repositories import alert_repo, client_repo, tds_repo, threshold_repo
from app.schemas.tds import (
    AcknowledgeTdsIn,
    ApproveIn,
    AssignmentIn,
    MappingOut,
    MappingUpdate,
    PartyIn,
    PayerIn,
    ResolutionIn,
    SectionBase,
    SectionRead,
    SectionsOut,
    TdsSettingsIO,
    VoucherFlagIn,
)
from app.services import tds_capture, tds_detail, tds_service
from app.services import tds_mapping as tm
from app.services import tds_report as tds_report_service
from app.services.fy_utils import is_valid_fy
from app.services.tds_engine import CONSTITUTIONS, SECTION_KEYS, valid_pan

router = APIRouter(prefix="/tds", tags=["tds"])


def _section_or_404(db: Session, section_id: int) -> TdsSection:
    row = tds_repo.get_section(db, section_id)
    if row is None:
        raise HTTPException(404, "Rate master row not found")
    return row


def _commit_master(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "That section already has a row with this effective-from date"
        ) from exc
    tds_service.recheck_all(db)  # rates / thresholds changed: re-evaluate every client


@router.get("/sections", response_model=SectionsOut)
def list_sections(db: Session = Depends(get_db)):
    return SectionsOut(sections=tds_repo.list_sections(db))


@router.post("/sections", response_model=SectionRead, status_code=status.HTTP_201_CREATED)
def create_section(body: SectionBase, db: Session = Depends(get_db)):
    """A new row for a section from a later date (a rate change); older years keep theirs."""
    row = TdsSection(section=body.key.split("(")[0], **body.model_dump())
    db.add(row)
    _commit_master(db)
    db.refresh(row)
    return row


@router.put("/sections/{section_id}", response_model=SectionRead)
def update_section(section_id: int, body: SectionBase, db: Session = Depends(get_db)):
    row = _section_or_404(db, section_id)
    for name, value in body.model_dump().items():
        setattr(row, name, value)
    row.section = body.key.split("(")[0]
    _commit_master(db)
    db.refresh(row)
    return row


@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_section(section_id: int, db: Session = Depends(get_db)):
    row = _section_or_404(db, section_id)
    if sum(r.key == row.key for r in tds_repo.list_sections(db)) <= 1:
        raise HTTPException(409, f"{row.key} needs at least one row in the rate master")
    db.delete(row)
    _commit_master(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------- mapping


@router.get("/clients/{client_id}/mapping", response_model=MappingOut)
def get_mapping(client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    settings = tds_repo.client_settings(db, client.id)
    db.commit()
    return MappingOut(
        client_id=client.id,
        approved_at=settings.mapping_approved_at,
        approved_by=settings.mapping_approved_by,
        rows=tds_service.mapping_view(db, client.id),
        roles=list(tm.ROLES),
        sections=list(SECTION_KEYS),
    )


@router.put("/clients/{client_id}/mapping", response_model=MappingOut)
def update_mapping(
    body: MappingUpdate, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    try:
        tds_service.change_mapping(db, client.id, [c.model_dump() for c in body.changes])
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    tds_service.recheck_client(db, client.id)
    db.commit()
    return get_mapping(client, db)


@router.post("/clients/{client_id}/mapping/approve", response_model=MappingOut)
def approve_mapping(
    body: ApproveIn, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    try:
        tds_service.approve_mapping(db, client.id, body.approved_by, body.approved)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    tds_service.recheck_client(db, client.id)
    db.commit()
    return get_mapping(client, db)


@router.post("/clients/{client_id}/mapping/refresh", response_model=MappingOut)
def refresh_proposals(client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    """Re-run the proposal rules on ledgers not yet edited or approved."""
    tds_capture.repropose(db, client.id)
    tds_service.recheck_client(db, client.id)
    db.commit()
    return get_mapping(client, db)


# ------------------------------------------------------- analysis and reports


def _fy(fy: str) -> str:
    if not is_valid_fy(fy):
        raise HTTPException(422, "fy must look like 2025-26")
    return fy


def _analysis_fy(db: Session, fy: str) -> str:
    """TDS is analysed for one year only (Settings -> TDS -> TDS analysis year)."""
    target = tds_service.analysis_fy(db)
    if _fy(fy) != target:
        raise HTTPException(409, f"TDS analysis is available for FY {target} only")
    return fy


def _row_or_404(analysis: tds_service.ClientTds, party_key: str, section: str):
    row = analysis.row(party_key, section)
    if row is None:
        raise HTTPException(404, "No payments for this party and section in the year")
    return row


@router.get("/clients/{client_id}/report")
def tds_report(fy: str, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    """'TDS Applicability': one row per party per section, subtotals and the total shortfall."""
    return tds_report_service.build_report(tds_service.analyze(db, client.id, _analysis_fy(db, fy)))


@router.get("/clients/{client_id}/report/export")
def export_tds_report(
    fy: str,
    format: str = Query("xlsx", pattern="^(xlsx|pdf)$"),
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    report = tds_report_service.build_report(
        tds_service.analyze(db, client.id, _analysis_fy(db, fy))
    )
    stem = f"TDS_Applicability_{client.name}_FY{fy}"
    if format == "pdf":
        return _attachment(tds_report_service.report_pdf(report), "application/pdf", f"{stem}.pdf")
    return _attachment(tds_report_service.report_xlsx(report), XLSX, f"{stem}.xlsx")


@router.get("/clients/{client_id}/summary")
def tds_summary(
    fy: str, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """The TDS block of the Client Report."""
    return tds_report_service.summary_block(
        tds_service.analyze(db, client.id, _analysis_fy(db, fy))
    )


@router.get("/clients/{client_id}/detail")
def party_detail(
    fy: str,
    party_key: str,
    section: str,
    as_of: date | None = None,
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    """Everything about one party under one section (the alert's detail panel)."""
    analysis = tds_service.analyze(db, client.id, _analysis_fy(db, fy), as_of)
    return tds_detail.build(analysis, _row_or_404(analysis, party_key, section), as_of)


@router.get("/clients/{client_id}/detail/export")
def export_party_detail(
    fy: str,
    party_key: str,
    section: str,
    format: str = Query("xlsx", pattern="^(xlsx|pdf)$"),
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    analysis = tds_service.analyze(db, client.id, _analysis_fy(db, fy))
    detail = tds_detail.build(analysis, _row_or_404(analysis, party_key, section))
    stem = f"TDS_{section}_{detail['party']['name']}_{client.name}_FY{fy}"
    if format == "pdf":
        return _attachment(tds_report_service.party_pdf(detail), "application/pdf", f"{stem}.pdf")
    return _attachment(tds_report_service.party_xlsx(detail), XLSX, f"{stem}.xlsx")


@router.get("/alerts/{alert_id}/detail")
def alert_detail(alert_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    """The detail panel of a TDS alert, with the alert itself."""
    alert = alert_repo.get_alert(db, alert_id)
    if alert is None or not alert.metric.startswith("tds:") or not alert.party_key:
        raise HTTPException(404, "TDS alert not found")
    analysis = tds_service.analyze(db, alert.client_id, alert.fy, as_of)
    row = analysis.row(alert.party_key, alert.section)
    if row is None:
        raise HTTPException(
            404, "This party no longer has payments under the section (was the mapping changed?)"
        )
    detail = tds_detail.build(analysis, row, as_of)
    detail["alert"] = alert_to_read(alert).model_dump(mode="json")
    return detail


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_tds_alert(alert_id: int, body: AcknowledgeTdsIn, db: Session = Depends(get_db)):
    """Acknowledge with a note (any alert; the note is kept on the alert)."""
    alert = alert_repo.get_alert(db, alert_id)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    return alert_to_read(alert_repo.acknowledge(db, alert, body.acknowledged_by, body.note))


@router.get("/alert-counts")
def tds_alert_counts(fy: str | None = None, db: Session = Depends(get_db)):
    """Unacknowledged TDS alerts by severity (dashboard)."""
    return alert_repo.count_open_tds(db, fy)


# -------------------------------------------------------- the CA's decisions


def _recheck(db: Session, client_id: int) -> None:
    tds_service.recheck_client(db, client_id)
    db.commit()


@router.put("/clients/{client_id}/party")
def save_party(
    body: PartyIn, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """PAN entered by the user, payee-type override and the special-case flags of a party."""
    values = body.model_dump(exclude={"party_key"})
    if body.pan:
        values["pan"] = valid_pan(body.pan)
        if values["pan"] is None:
            raise HTTPException(422, "PAN must look like ABCDE1234F")
    tds_repo.save_party(db, client.id, body.party_key, values)
    _recheck(db, client.id)
    return {"ok": True}


@router.put("/clients/{client_id}/resolution")
def save_resolution(
    body: ResolutionIn, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """Mark (or unmark) 'TDS deducted outside Tally' for a party and section."""
    values = None if body.remove else body.model_dump(include={"amount", "note", "marked_by"})
    tds_repo.set_resolution(db, client.id, _fy(body.fy), body.party_key, body.section_key, values)
    _recheck(db, client.id)
    return {"ok": True}


@router.put("/clients/{client_id}/assignment")
def save_assignment(
    body: AssignmentIn, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """Assign a payee (e.g. the landlord) to the party-less payments of a ledger."""
    if not body.remove and not (body.payee_name or "").strip():
        raise HTTPException(422, "Enter the payee's name")
    values = None
    if not body.remove:
        pan = valid_pan(body.pan) if body.pan else None
        if body.pan and pan is None:
            raise HTTPException(422, "PAN must look like ABCDE1234F")
        values = {
            "payee_name": body.payee_name.strip(),
            "pan": pan,
            "payee_type": body.payee_type,
            "note": body.note,
        }
    tds_repo.set_assignment(db, client.id, _fy(body.fy), body.ledger, values)
    _recheck(db, client.id)
    return {"ok": True}


@router.get("/clients/{client_id}/completeness")
def tds_completeness(
    fy: str, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """Every expense and purchase ledger with postings: section / excluded / unmapped,
    total, parties, whether any party crossed - proof that nothing was missed."""
    analysis = tds_service.analyze(db, client.id, _analysis_fy(db, fy))
    return tds_service.completeness(db, client.id, analysis)


@router.get("/clients/{client_id}/data-quality")
def client_data_quality(client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    """Parties whose PAN contradicts their name."""
    return tds_service.data_quality(db, client.id)


@router.get("/data-quality")
def all_data_quality(db: Session = Depends(get_db)):
    out = []
    for client in client_repo.list_clients(db):
        for row in tds_service.data_quality(db, client.id):
            out.append({"client_id": client.id, "client_name": client.name, **row})
    return out


@router.put("/clients/{client_id}/voucher-flag")
def save_voucher_flag(
    body: VoucherFlagIn, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """Leave one voucher out of 194Q (seller charged TCS u/s 206C(1H)), or count its
    payments under another section (a bill booked to the wrong ledger)."""
    if body.flag.startswith("section:") and body.flag.split(":", 1)[1] not in SECTION_KEYS:
        raise HTTPException(422, "Unknown section")
    tds_repo.set_voucher_flag(db, client.id, body.voucher_key, body.on, body.note, body.flag)
    _recheck(db, client.id)
    return {"ok": True}


@router.get("/clients/{client_id}/payer")
def get_payer(fy: str, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    """Who must deduct: 194Q from last year's turnover; the others from the constitution and,
    for an Individual/HUF, audit liability last year."""
    payer = tds_service.payer_for(db, client.id, _fy(fy))
    settings = tds_repo.client_settings(db, client.id)
    override = tds_repo.payer_year(db, client.id, fy)
    db.commit()
    return {
        "fy": fy,
        "previous_fy": payer.previous_fy,
        "previous_turnover": payer.previous_turnover,
        "constitution": payer.constitution,
        "constitution_source": payer.constitution_source,
        "constitution_override": settings.constitution,
        "audit_override": override.audit_liable if override is not None else None,
        "audit_liable": payer.audit_liable,
        "audit_source": payer.audit_source,
        "s194q": {"applies": payer.s194q.applies, "reason": payer.s194q.reason},
        "others": {
            "applies": payer.others.applies,
            "reason": payer.others.reason,
            "needs_confirmation": payer.others.needs_confirmation,
        },
        "constitutions": list(CONSTITUTIONS),
    }


@router.put("/clients/{client_id}/payer")
def save_payer(
    body: PayerIn, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    if body.constitution is not None and body.constitution not in CONSTITUTIONS:
        raise HTTPException(422, f"constitution must be one of {', '.join(CONSTITUTIONS)}")
    tds_repo.client_settings(db, client.id).constitution = body.constitution
    tds_repo.set_payer_year(db, client.id, _fy(body.fy), body.audit_liable, body.note)
    _recheck(db, client.id)
    return get_payer(body.fy, client, db)


@router.get("/settings", response_model=TdsSettingsIO)
def get_tds_settings(db: Session = Depends(get_db)):
    settings = threshold_repo.get_settings(db)
    return TdsSettingsIO(
        approaching_pct=settings.tds_approaching_pct,
        analysis_fy=tds_service.analysis_fy(db),
        unidentified_min=settings.tds_unidentified_min,
    )


@router.put("/settings", response_model=TdsSettingsIO)
def save_tds_settings(body: TdsSettingsIO, db: Session = Depends(get_db)):
    """Approaching % and the TDS analysis year (alerts outside it are closed)."""
    settings = threshold_repo.get_settings(db)
    settings.tds_approaching_pct, settings.tds_analysis_fy = body.approaching_pct, body.analysis_fy
    settings.tds_unidentified_min = body.unidentified_min
    db.commit()
    tds_service.recheck_all(db)
    return body
