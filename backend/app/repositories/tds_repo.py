"""Database access for the TDS rate master and the user's TDS decisions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    TdsAssignment,
    TdsClientSettings,
    TdsParty,
    TdsPayerYear,
    TdsResolution,
    TdsSection,
    TdsVoucherFlag,
)
from app.repositories.tds_seed import default_rows
from app.services.tds_engine import SECTION_KEYS, SectionRule

SECTION_FIELDS = (
    "key",
    "section",
    "nature",
    "single_threshold",
    "aggregate_threshold",
    "rate_individual",
    "rate_other",
    "rate_no_pan",
    "base",
    "effective_from",
    "remarks",
)


def seed_sections(db: Session) -> None:
    """Fill the rate master: every default row on an empty table, and the default rows of a
    section the table does not have yet (a section added in a later version, e.g. 194-IB).
    Rows the CA already has are never touched."""
    have = set(db.scalars(select(TdsSection.key).distinct()))
    rows = [TdsSection(**row) for row in default_rows() if row["key"] not in have]
    if rows:
        db.add_all(rows)
        db.commit()


def list_sections(db: Session) -> list[TdsSection]:
    order = {key: i for i, key in enumerate(SECTION_KEYS)}
    rows = list(db.scalars(select(TdsSection)))
    return sorted(rows, key=lambda r: (order.get(r.key, 99), r.key, r.effective_from))


def get_section(db: Session, section_id: int) -> TdsSection | None:
    return db.get(TdsSection, section_id)


def to_rule(row: TdsSection) -> SectionRule:
    return SectionRule(
        key=row.key,
        section=row.section,
        nature=row.nature,
        single_threshold=row.single_threshold,
        aggregate_threshold=row.aggregate_threshold,
        rate_individual=row.rate_individual,
        rate_other=row.rate_other,
        rate_no_pan=row.rate_no_pan,
        base=row.base,
        effective_from=row.effective_from,
        remarks=row.remarks or "",
    )


def rules(db: Session) -> list[SectionRule]:
    return [to_rule(row) for row in list_sections(db)]


# ------------------------------------------------------- client decisions


def client_settings(db: Session, client_id: int) -> TdsClientSettings:
    row = db.get(TdsClientSettings, client_id)
    if row is None:
        row = TdsClientSettings(client_id=client_id)
        db.add(row)
        db.flush()
    return row


def payer_year(db: Session, client_id: int, fy: str) -> TdsPayerYear | None:
    return db.get(TdsPayerYear, (client_id, fy))


def set_payer_year(db: Session, client_id: int, fy: str, audit_liable: bool | None, note=None):
    row = payer_year(db, client_id, fy) or TdsPayerYear(client_id=client_id, fy=fy)
    row.audit_liable, row.note = audit_liable, note
    db.add(row)
    db.flush()
    return row


def parties(db: Session, client_id: int) -> dict[str, TdsParty]:
    query = select(TdsParty).where(TdsParty.client_id == client_id)
    return {p.party_key: p for p in db.scalars(query)}


def get_party(db: Session, client_id: int, party_key: str) -> TdsParty | None:
    return db.get(TdsParty, (client_id, party_key))


def save_party(db: Session, client_id: int, party_key: str, values: dict) -> TdsParty:
    row = get_party(db, client_id, party_key) or TdsParty(client_id=client_id, party_key=party_key)
    db.add(row)
    for name in (
        "party_name",
        "pan",
        "payee_type",
        "transporter_declaration",
        "tcs_206c1h",
        "note",
    ):
        if name in values:
            setattr(row, name, values[name])
    db.flush()
    return row


def voucher_flags(db: Session, client_id: int) -> dict[str, TdsVoucherFlag]:
    query = select(TdsVoucherFlag).where(TdsVoucherFlag.client_id == client_id)
    return {f.voucher_key: f for f in db.scalars(query)}


def set_voucher_flag(
    db: Session, client_id: int, voucher_key: str, on: bool, note=None, flag: str = "tcs_206c1h"
) -> None:
    """flag 'tcs_206c1h' (leave out of 194Q) or 'section:<key>' (count its payments under
    another section, e.g. a professional bill booked to a purchase ledger)."""
    row = db.get(TdsVoucherFlag, (client_id, voucher_key))
    if on:
        row = row or TdsVoucherFlag(client_id=client_id, voucher_key=voucher_key)
        row.flag, row.note = flag, note
        db.add(row)
    elif row is not None:
        db.delete(row)
    db.flush()


def assignments(db: Session, client_id: int, fy: str) -> dict[str, TdsAssignment]:
    query = select(TdsAssignment).where(
        TdsAssignment.client_id == client_id, TdsAssignment.fy == fy
    )
    return {a.ledger_key: a for a in db.scalars(query)}


def set_assignment(db: Session, client_id: int, fy: str, ledger: str, values: dict | None) -> None:
    """values None removes the assignment."""
    key = " ".join(ledger.split()).casefold()
    row = db.get(TdsAssignment, (client_id, fy, key))
    if values is None:
        if row is not None:
            db.delete(row)
    else:
        row = row or TdsAssignment(client_id=client_id, fy=fy, ledger_key=key)
        row.ledger = ledger
        for name in ("payee_name", "pan", "payee_type", "note"):
            setattr(row, name, values.get(name))
        db.add(row)
    db.flush()


def resolutions(db: Session, client_id: int, fy: str) -> dict[tuple[str, str], TdsResolution]:
    query = select(TdsResolution).where(
        TdsResolution.client_id == client_id, TdsResolution.fy == fy
    )
    return {(r.party_key, r.section_key): r for r in db.scalars(query)}


def set_resolution(
    db: Session, client_id: int, fy: str, party_key: str, section_key: str, values: dict | None
) -> None:
    """values None removes the 'deducted outside Tally' mark."""
    row = db.get(TdsResolution, (client_id, fy, party_key, section_key))
    if values is None:
        if row is not None:
            db.delete(row)
    else:
        row = row or TdsResolution(
            client_id=client_id, fy=fy, party_key=party_key, section_key=section_key
        )
        row.amount, row.note, row.marked_by = (
            values.get("amount"),
            values.get("note"),
            values.get("marked_by"),
        )
        db.add(row)
    db.flush()
