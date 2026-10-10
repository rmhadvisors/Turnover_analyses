"""Regression tests against the real Tally exports in sample_data/real/.

These are the CA's own client files (git-ignored, confidential) rather than
committed fixtures, so every test here is skipped automatically when the
expected files are not present on disk - CI and other machines are unaffected.

The rupee targets are the CA's own independently-computed figures (optional and
cancelled vouchers excluded, GST excluded), cross-checked against this tool's
output. Each is a regression guard: if a change to the importer moves one of
these numbers, that is a bug until proven otherwise.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.services import import_json_service as js
from app.services import tally_json as tj

REAL = Path(__file__).resolve().parents[2] / "sample_data" / "real"
REQ = REAL / "Turnover Requirements"

# (company, fy) -> (master path, transactions path), relative to sample_data/real/
FILES = {
    ("ADVANCE POWER", "24-25"): (
        REQ / "ADVANCE POWER/24-25/Master.json",
        REAL / "extracted/TURNOVER REQ/TURNOVER REQ/ADVANCE POWER/24-25/Transactions.json",
    ),
    ("ADVANCE POWER", "25-26"): (
        REQ / "ADVANCE POWER/25-26/Master.json",
        REQ / "ADVANCE POWER/25-26/Transactions.json",
    ),
    ("JIGEESHA AUTO SERVICES", "24-25"): (
        REQ / "JIGEESHA AUTO SERVICES/24-25/Master.json",
        REAL / "extracted/TURNOVER REQ/TURNOVER REQ/JIGEESHA/24-25/Transactions.json",
    ),
    ("JIGEESHA AUTO SERVICES", "25-26"): (
        REQ / "JIGEESHA AUTO SERVICES/25-26/Master.json",
        REAL / "extracted/TURNOVER REQ/TURNOVER REQ/JIGEESHA/25-26/Transactions.json",
    ),
    ("SHIRKE FLEX INDUSTRIAL", "24-25"): (
        REQ / "SHIRKE FLEX/24-25/Master.json",
        REAL / "extracted/SHIRKE FLEX/SHIRKE FLEX/24-25/Transactions.json",
    ),
    ("SHIRKE FLEX INDUSTRIAL", "25-26"): (
        REQ / "SHIRKE FLEX/25-26/Master.json",
        REAL / "extracted/SHIRKE FLEX/SHIRKE FLEX/25-26/Transactions.json",
    ),
    ("HOTEL KINARA", "24-25"): (
        REQ / "HOTEL KINARA/24-25/Master.json",
        REAL / "extracted/TURNOVER REQ/TURNOVER REQ/HOTEL KINARA/24-25/Transactions.json",
    ),
    ("HOTEL KINARA", "25-26"): (
        REQ / "HOTEL KINARA/25-26/Master.json",
        REQ / "HOTEL KINARA/25-26/Transactions (8).json",
    ),
}

# Independently-computed targets (rupees, taxable value, optional/cancelled excluded).
TARGETS = {
    ("ADVANCE POWER", "24-25"): {"sales": "47963293.91", "purchases": "49693152.60"},
    ("ADVANCE POWER", "25-26"): {"sales": "103160065.61", "purchases": "92469137.24"},
    ("JIGEESHA AUTO SERVICES", "24-25"): {"sales": "289956842.26", "purchases": "284043866.77"},
    ("JIGEESHA AUTO SERVICES", "25-26"): {"sales": "289465353.64", "purchases": "281084328.53"},
    ("SHIRKE FLEX INDUSTRIAL", "24-25"): {"sales": "11969243.00", "purchases": "5916513.68"},
    ("SHIRKE FLEX INDUSTRIAL", "25-26"): {"sales": "12136927.15", "purchases": "6521598.21"},
    ("HOTEL KINARA", "24-25"): {"sales": "42722334.00", "purchases": "29656166.04"},
}
# SHIRKE FLEX total voucher record counts (all types), per the CA's own count of the file.
VOUCHER_COUNTS = {
    ("SHIRKE FLEX INDUSTRIAL", "24-25"): 2817,
    ("SHIRKE FLEX INDUSTRIAL", "25-26"): 2996,
}

pytestmark = pytest.mark.skipif(
    not all(m.exists() and t.exists() for m, t in FILES.values()),
    reason="real client files not present in sample_data/real/ (confidential, not committed)",
)


def _parse(company: str, fy: str) -> tj.JsonParseResult:
    master_path, tx_path = FILES[(company, fy)]
    master = tj.build_master([tj.read_records(master_path.read_bytes(), master_path.name)])
    return tj.parse_transactions([tj.read_records(tx_path.read_bytes(), tx_path.name)], master)


@pytest.mark.parametrize(("company", "fy"), sorted(TARGETS))
def test_real_company_year_matches_the_cas_figures(company, fy) -> None:
    result = _parse(company, fy)
    totals = tj.summarize(result.vouchers)
    target = TARGETS[(company, fy)]
    got_sales = sum((row["sales"] for row in totals.values()), Decimal(0))
    got_purchases = sum((row["purchases"] for row in totals.values()), Decimal(0))
    assert got_sales == Decimal(target["sales"]), f"{company} {fy} sales"
    assert got_purchases == Decimal(target["purchases"]), f"{company} {fy} purchases"


@pytest.mark.parametrize(("company", "fy"), sorted(VOUCHER_COUNTS))
def test_real_total_voucher_count_matches(company, fy) -> None:
    result = _parse(company, fy)
    assert result.total_vouchers == VOUCHER_COUNTS[(company, fy)]


def test_real_full_year_files_are_not_reported_as_cut_off() -> None:
    for (company, fy), (_master_path, tx_path) in FILES.items():
        rf = tj.read_records(tx_path.read_bytes(), tx_path.name)
        assert not rf.truncated, f"{company} {fy}: {tx_path.name} is reported cut off"


def test_real_advance_power_full_import_via_the_service_layer(db_session) -> None:
    """End-to-end through import_json_service and the stored yearly figures - the
    same path the API and the Client Report use - not just the bare parser."""
    from app.repositories import client_repo, figures_repo

    client = client_repo.create_client(db_session, "Regression - Advance Power")
    for fy in ("24-25", "25-26"):
        master_path, tx_path = FILES[("ADVANCE POWER", fy)]
        files = [
            (master_path.name, master_path.read_bytes()),
            (tx_path.name, tx_path.read_bytes()),
        ]
        result = js.import_json(db_session, client.id, files)
        assert result["duplicates_skipped"] == 0

    for fy, key in (("2024-25", "24-25"), ("2025-26", "25-26")):
        figures = figures_repo.get_figures(db_session, client.id, fy)
        target = TARGETS[("ADVANCE POWER", key)]
        assert figures.turnover == Decimal(target["sales"])
        assert figures.purchases == Decimal(target["purchases"])


# ---------------------------------------------- files that do not belong together

COMPANY_IDS = {  # each client's Tally company GUID, as carried by every guid in its exports
    "ADVANCE POWER": "2a1d1dde-741a-4be9-b634-65b0569c42ff",
    "JIGEESHA AUTO SERVICES": "bd267260-5ac4-4f1f-8a69-cf1d2efb1c19",
    "SHIRKE FLEX INDUSTRIAL": "710a0dec-9964-4ff4-ab30-04cf77247928",
    "HOTEL KINARA": "116eb1a3-865d-421c-bf93-b064d19035d7",
}


def test_real_jigeesha_master_with_hotel_kinara_transactions_is_refused(db_session) -> None:
    from app.models import Client, ClientProfile
    from app.services.tally_importer import ImportFormatError

    for name, gstin in (("JIGEESHA AUTO SERVICES", "27ABFPL5804R1Z4"), ("HOTEL KINARA", "27AAKFH4657G1Z4")):
        client = Client(name=name, tally_company_id=COMPANY_IDS[name])
        db_session.add(client)
        db_session.flush()
        db_session.add(ClientProfile(client_id=client.id, gstin=gstin))
    db_session.commit()
    master_path, _ = FILES[("JIGEESHA AUTO SERVICES", "24-25")]
    _, tx_path = FILES[("HOTEL KINARA", "24-25")]
    pair = [(master_path.name, master_path.read_bytes()), (tx_path.name, tx_path.read_bytes())]

    preview = js.preview_json(db_session, None, pair)
    assert preview["can_import"] is False
    (blocker,) = [b for b in preview["blockers"] if "different Tally companies" in b]
    assert "Master file: JIGEESHA AUTO SERVICES" in blocker
    assert "Transactions file: HOTEL KINARA (GSTIN 27AAKFH4657G1Z4" in blocker
    assert preview["unmatched_ledger_count"] > 100  # listed for the reviewer as well
    with pytest.raises(ImportFormatError, match="different Tally companies"):
        kinara = db_session.query(Client).filter_by(name="HOTEL KINARA").one()
        js.import_json(db_session, kinara.id, pair)


# share (%) of each genuine pair's voucher line value on ledgers missing from its Master
UNMATCHED_SHARE = {
    ("ADVANCE POWER", "24-25"): "0.00",
    ("ADVANCE POWER", "25-26"): "0.00",
    ("JIGEESHA AUTO SERVICES", "24-25"): "21.55",  # HPCL + PetroCard are not in that Master
    ("JIGEESHA AUTO SERVICES", "25-26"): "0.00",
    ("SHIRKE FLEX INDUSTRIAL", "24-25"): "0.00",
    ("SHIRKE FLEX INDUSTRIAL", "25-26"): "0.00",
    ("HOTEL KINARA", "24-25"): "0.10",
    ("HOTEL KINARA", "25-26"): "0.00",
}


@pytest.mark.parametrize(("company", "fy"), sorted(UNMATCHED_SHARE))
def test_real_pairs_share_one_company_and_their_unmatched_share(company, fy) -> None:
    master_path, tx_path = FILES[(company, fy)]
    master = tj.build_master([tj.read_records(master_path.read_bytes(), master_path.name)])
    result = tj.parse_transactions([tj.read_records(tx_path.read_bytes(), tx_path.name)], master)
    assert set(master.company_ids) == set(result.company_ids) == {COMPANY_IDS[company]}
    assert result.unknown_share_pct() == Decimal(UNMATCHED_SHARE[(company, fy)])
