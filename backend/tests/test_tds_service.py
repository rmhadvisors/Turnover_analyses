"""TDS ledger mapping, party-wise aggregation, deducted-vs-computed and alerts, through the
real import path with a synthetic Tally JSON export (no real client data)."""

import json
from decimal import Decimal as D

import pytest

from app.models import Alert
from app.repositories import client_repo, figures_repo, profile_repo, tds_repo
from app.services import import_json_service as js
from app.services import tds_service
from app.services.tds_engine import TdsStatus


def export(records: list[dict]) -> bytes:
    return json.dumps({"tallymessage": records}).encode("utf-16")


def group(name, parent=None, **extra):
    return {
        "metadata": {"type": "Group", "name": name},
        **({"parent": parent} if parent else {}),
        **extra,
    }


def ledger(name, parent, guid=None, gstin=None, **extra):
    record = {
        "metadata": {"type": "Ledger", "name": name},
        "parent": parent,
        "guid": guid or f"g-{name}",
    }
    if gstin:
        record["ledgstregdetails"] = [{"gstin": gstin}]
    record.update(extra)
    return record


MASTER = [
    group("Sales Accounts"), group("Purchase Accounts"), group("Duties & Taxes"),
    group("Sundry Creditors"), group("Bank Accounts"), group("Capital Account"),
    group("Indirect Expenses", isrevenue=True), group("Direct Expenses", isrevenue=True, affectsgrossprofit=True),
    group("TDS", "Duties & Taxes"),
    ledger("Sales", "Sales Accounts"),
    ledger("Purchase 18%", "Purchase Accounts"),
    ledger("Contract Labour", "Direct Expenses"),
    ledger("Professional Fees", "Indirect Expenses"),
    ledger("Remuneration to Partners", "Indirect Expenses"),
    ledger("Mystery Expenses", "Indirect Expenses"),
    ledger("TDS on Contract", "TDS", taxtype="TDS"),
    ledger("TDS on Purchase of Goods", "TDS", taxtype="TDS"),
    ledger("Input IGST", "Duties & Taxes"),
    ledger("Bank", "Bank Accounts"),
    ledger("Steel Seller", "Sundry Creditors", guid="guid-seller", gstin="27AAACS1234A1Z5"),
    ledger("Short Contractor", "Sundry Creditors", guid="guid-short", gstin="27ABCPS1234A1Z5"),
    ledger("Silent Contractor", "Sundry Creditors", guid="guid-silent"),
    ledger("Good Contractor", "Sundry Creditors", guid="guid-good", gstin="27AAAFG1234A1Z5"),
    ledger("Truck Owner", "Sundry Creditors", guid="guid-truck", incometaxnumber="ABCPT1234K"),
    ledger("CA Firm", "Sundry Creditors", guid="guid-ca", gstin="27AAAFC1234A1Z5"),
]  # fmt: skip


def e(name, amount, party=False):
    return {"ledgername": name, "amount": str(amount), "ispartyledger": party}


def v(vtype, number, date, entries):
    return {
        "metadata": {"type": "Voucher"},
        "date": date,
        "vouchertypename": vtype,
        "vouchernumber": number,
        "guid": f"vg-{vtype}-{number}",
        "partyledgername": next((x["ledgername"] for x in entries if x["ispartyledger"]), ""),
        "cmpgstin": "27AAAFZ9999Z1Z5",
        "ledgerentries": entries,
    }


def expense(no, date, party, ledger_name, amount, tds=0, tds_ledger="TDS on Contract"):
    """Dr expense, Cr party (net of TDS), Cr TDS."""
    entries = [e(ledger_name, -amount), e(party, amount - tds, True)]
    if tds:
        entries.append(e(tds_ledger, tds))
    return v("Journal", no, date, entries)


TRANSACTIONS = [
    v("Sales", "S1", "20250410", [e("Bank", -500, True), e("Sales", 500)]),
    # 194Q: Rs 62 lakh of goods from one seller (GST in its own ledger)
    v("Purchase", "P1", "20250415", [e("Steel Seller", 3540000, True), e("Purchase 18%", -3000000), e("Input IGST", -540000)]),
    v("Purchase", "P2", "20250610", [e("Steel Seller", 3776000, True), e("Purchase 18%", -3200000), e("Input IGST", -576000)]),
    # monthly lump-sum TDS journal with no party (as some clients book it)
    v("Journal", "J-TDSQ", "20250630", [e("Bank", -1200), e("TDS on Purchase of Goods", 1200)]),
    # 194C: short (deducted on one bill only), not deducted, correctly deducted
    expense("C1", "20250501", "Short Contractor", "Contract Labour", 60000, tds=600),
    expense("C2", "20250701", "Short Contractor", "Contract Labour", 60000),
    expense("C3", "20250502", "Silent Contractor", "Contract Labour", 35000),
    expense("C4", "20250503", "Good Contractor", "Contract Labour", 150000, tds=3000),
    expense("C5", "20250504", "Truck Owner", "Contract Labour", 120000),
    # 194J(b) below threshold, partners' remuneration excluded, an unmapped ledger
    expense("F1", "20250505", "CA Firm", "Professional Fees", 45000),
    v("Journal", "R1", "20250506", [e("Remuneration to Partners", -900000), e("Bank", 900000)]),
    v("Journal", "M1", "20250507", [e("Mystery Expenses", -70000), e("Bank", 70000)]),
]  # fmt: skip

FILES = [("Master.json", export(MASTER)), ("Transactions.json", export(TRANSACTIONS))]


@pytest.fixture()
def client_id(db_session):
    client = client_repo.create_client(db_session, "Synthetic TDS Firm")
    profile_repo.save_profile(db_session, client.id, {"gstin": "27AAAFZ9999Z1Z5"})  # F = firm
    figures_repo.upsert_figures(db_session, client.id, "2024-25", {"turnover": D(150000000)})
    js.import_json(db_session, client.id, FILES)
    return client.id


def _row(analysis, party, section):
    row = next(
        (r for r in analysis.rows if r.party.name == party and r.section_key == section), None
    )
    assert row is not None, (party, section)
    return row


def test_import_captures_lines_and_proposes_a_mapping(db_session, client_id) -> None:
    rows = {r["name"]: r for r in tds_service.mapping_view(db_session, client_id)}
    assert (rows["Contract Labour"]["role"], rows["Contract Labour"]["section_key"]) == (
        "base",
        "194C",
    )
    assert (rows["Purchase 18%"]["role"], rows["Purchase 18%"]["section_key"]) == ("base", "194Q")
    assert (rows["Professional Fees"]["role"], rows["Professional Fees"]["section_key"]) == (
        "base",
        "194J(b)",
    )
    assert rows["Remuneration to Partners"]["role"] == "excluded"
    assert (rows["TDS on Contract"]["role"], rows["TDS on Contract"]["section_key"]) == (
        "tds",
        "194C",
    )
    assert rows["TDS on Purchase of Goods"]["section_key"] == "194Q"
    assert rows["Mystery Expenses"]["role"] == "unmapped"
    assert rows["Contract Labour"]["amounts"]["2025-26"] == D(425000)


def test_turnover_figures_are_unchanged_by_the_tds_capture(db_session, client_id) -> None:
    figures = figures_repo.get_figures(db_session, client_id, "2025-26")
    assert figures.turnover == D(500)
    assert figures.purchases == D(6200000)


def test_unmapped_ledger_with_payments_is_listed(db_session, client_id) -> None:
    analysis = tds_service.analyze(db_session, client_id, "2025-26")
    assert [(u.name, u.amount) for u in analysis.unmapped] == [("Mystery Expenses", D(70000))]


def test_party_wise_classification(db_session, client_id) -> None:
    analysis = tds_service.analyze(db_session, client_id, "2025-26")
    assert analysis.payer.s194q.applies is True and analysis.payer.others.applies is True

    seller = _row(analysis, "Steel Seller", "194Q")
    assert seller.party.key == "guid-seller"
    assert (seller.party.pan, seller.party.pan_source) == ("AAACS1234A", "from GSTIN")
    assert seller.evaluation.aggregate == D(6200000)  # GST excluded
    assert seller.evaluation.base == D(1200000)
    assert seller.evaluation.tds == D(1200)
    assert seller.deducted_in_books == 0 and seller.deducted_attributed == D(
        1200
    )  # lump-sum journal
    assert seller.status == TdsStatus.OK.value

    short = _row(analysis, "Short Contractor", "194C")
    assert short.party.payee_type == "individual_huf"  # PAN ABCPS... 4th char P
    assert short.evaluation.tds == D(1200) and short.deducted == D(600)
    assert short.status == TdsStatus.SHORT.value and short.shortfall == D(600)

    silent = _row(analysis, "Silent Contractor", "194C")
    assert silent.party.pan is None and silent.party.pan_source == "not available"
    assert silent.evaluation.crossing_test.value == "single payment"
    assert silent.evaluation.tds == D(7000)  # no PAN: 20% of 35,000
    assert silent.status == TdsStatus.NOT_DEDUCTED.value

    good = _row(analysis, "Good Contractor", "194C")
    assert good.evaluation.tds == D(3000) and good.status == TdsStatus.OK.value

    truck = _row(analysis, "Truck Owner", "194C")
    assert truck.party.pan_source == "from Tally ledger PAN field"
    assert truck.status == TdsStatus.NOT_DEDUCTED.value

    fees = _row(analysis, "CA Firm", "194J(b)")
    assert not fees.evaluation.crossed and fees.status == TdsStatus.APPROACHING.value


def test_no_alerts_until_the_mapping_is_approved_then_only_on_change(db_session, client_id) -> None:
    def tds_alerts():
        return [a for a in db_session.query(Alert).filter(Alert.client_id == client_id)
                if a.metric.startswith("tds:")]  # fmt: skip

    assert tds_alerts() == []
    tds_service.approve_mapping(db_session, client_id, "CA")
    raised = tds_service.recheck_client(db_session, client_id)
    statuses = sorted((a.party, a.new_status) for a in raised)
    assert ("Silent Contractor", "tds_not_deducted") in statuses
    assert ("Short Contractor", "tds_short_deducted") in statuses
    assert ("CA Firm", "tds_approaching") in statuses
    silent = next(a for a in raised if a.party == "Silent Contractor")
    assert (silent.section, silent.crossed_voucher_no, str(silent.crossed_on)) == (
        "194C",
        "C3",
        "2025-05-02",
    )
    assert (silent.tds_computed, silent.tds_deducted, silent.shortfall) == (D(7000), D(0), D(7000))
    assert silent.threshold == D(100000) and silent.aggregate == D(35000)

    # re-importing the same files raises nothing new
    js.import_json(db_session, client_id, FILES)
    assert tds_service.recheck_client(db_session, client_id) == []
    assert len(tds_alerts()) == len(raised)


def test_flags_and_resolutions_change_the_outcome(db_session, client_id) -> None:
    tds_service.approve_mapping(db_session, client_id, "CA")
    tds_service.recheck_client(db_session, client_id)

    tds_repo.save_party(db_session, client_id, "guid-truck", {"transporter_declaration": True})
    tds_repo.set_resolution(db_session, client_id, "2025-26", "guid-silent", "194C",
                            {"note": "Deducted in March, booked outside Tally", "marked_by": "CA"})  # fmt: skip
    raised = tds_service.recheck_client(db_session, client_id)
    changed = {(a.party, a.new_status) for a in raised}
    assert ("Truck Owner", "tds_nil") in changed
    assert ("Silent Contractor", "tds_ok") in changed
    analysis = tds_service.analyze(db_session, client_id, "2025-26")
    assert _row(analysis, "Silent Contractor", "194C").deducted_outside == D(7000)


def test_206c1h_voucher_flag_leaves_the_voucher_out_of_194q(db_session, client_id) -> None:
    analysis = tds_service.analyze(db_session, client_id, "2025-26")
    first = _row(analysis, "Steel Seller", "194Q").evaluation.lines[0].payment.voucher_key
    tds_repo.set_voucher_flag(db_session, client_id, first, True)
    seller = _row(tds_service.analyze(db_session, client_id, "2025-26"), "Steel Seller", "194Q")
    assert seller.evaluation.aggregate == D(3200000) and not seller.evaluation.crossed
    assert len(seller.excluded_payments) == 1


def test_194q_not_applicable_when_last_year_turnover_was_8_crore(db_session, client_id) -> None:
    figures_repo.upsert_figures(db_session, client_id, "2024-25", {"turnover": D(80000000)})
    analysis = tds_service.analyze(db_session, client_id, "2025-26")
    assert analysis.payer.s194q.applies is False
    assert _row(analysis, "Steel Seller", "194Q").status == "tds_not_applicable"
    assert _row(analysis, "Silent Contractor", "194C").status == TdsStatus.NOT_DEDUCTED.value


def test_mapping_change_by_user_is_kept_and_reevaluated(db_session, client_id) -> None:
    tds_service.change_mapping(db_session, client_id, [
        {"match_type": "ledger", "name": "Mystery Expenses", "role": "base", "section_key": "194J(a)"},
    ])  # fmt: skip
    rows = {r["name"]: r for r in tds_service.mapping_view(db_session, client_id)}
    assert (rows["Mystery Expenses"]["source"], rows["Mystery Expenses"]["section_key"]) == (
        "user",
        "194J(a)",
    )
    analysis = tds_service.analyze(db_session, client_id, "2025-26")
    assert analysis.unmapped == []
    # paid straight from the bank: no party ledger, so listed but not worked out per payee
    no_party = _row(analysis, tds_service.NO_PARTY_NAME, "194J(a)")
    # over the 194J(a) threshold in total, payee unknown: high-severity 'cannot determine';
    # the TDS shown is an indication (one payee, PAN available) and never counted as a shortfall
    assert no_party.status == "tds_unidentified" and no_party.evaluation.tds == D(1400)
    assert no_party.shortfall == 0
    with pytest.raises(ValueError):
        tds_service.change_mapping(db_session, client_id, [
            {"match_type": "ledger", "name": "Mystery Expenses", "role": "base", "section_key": None},
        ])  # fmt: skip


def test_group_rule_maps_every_ledger_below_it(db_session, client_id) -> None:
    tds_service.change_mapping(db_session, client_id, [
        {"match_type": "ledger", "name": "Mystery Expenses", "role": "unmapped", "section_key": None},
        {"match_type": "group", "name": "Indirect Expenses", "role": "excluded", "section_key": None},
    ])  # fmt: skip
    analysis = tds_service.analyze(db_session, client_id, "2025-26")
    assert analysis.unmapped == []  # Mystery Expenses now excluded by its group
