"""TDS regression against the real JIGEESHA AUTO SERVICES export (git-ignored, confidential;
skipped when the files are not on disk). Figures are this tool's output, cross-checked
against the 'TDS on Purchase of Goods' / 'TDS on Rent' ledgers in the client's own books."""

from decimal import Decimal as D

import pytest

from app.repositories import client_repo, figures_repo, profile_repo
from app.services import import_json_service as js
from app.services import tds_engine as te
from app.services import tds_service
from tests.test_real_data_regression import FILES, TARGETS

KEY = ("JIGEESHA AUTO SERVICES", "25-26")
MASTER, TRANSACTIONS = FILES[KEY]

pytestmark = pytest.mark.skipif(
    not (MASTER.exists() and TRANSACTIONS.exists()),
    reason="real client files not present in sample_data/real/ (confidential, not committed)",
)


@pytest.fixture(scope="module")
def jigeesha(session_factory_module):
    db = session_factory_module()
    client = client_repo.create_client(db, "Regression - Jigeesha")
    profile_repo.save_profile(db, client.id, {"gstin": "27ABFPL5804R1Z4"})
    verified_24_25 = D(TARGETS[("JIGEESHA AUTO SERVICES", "24-25")]["sales"])
    figures_repo.upsert_figures(db, client.id, "2024-25", {"turnover": verified_24_25})
    files = [(MASTER.name, MASTER.open("rb")), (TRANSACTIONS.name, TRANSACTIONS.open("rb"))]
    try:
        js.import_json(db, client.id, files)
    finally:
        for _, handle in files:
            handle.close()
    yield db, client.id, tds_service.analyze(db, client.id, "2025-26")
    db.close()


def _row(analysis, party, section):
    return next(r for r in analysis.rows if r.party.name == party and r.section_key == section)


def test_turnover_unchanged_by_the_tds_capture(jigeesha) -> None:
    db, client_id, _ = jigeesha
    figures = figures_repo.get_figures(db, client_id, "2025-26")
    assert figures.turnover == D(TARGETS[KEY]["sales"])
    assert figures.purchases == D(TARGETS[KEY]["purchases"])


def test_194q_applies_from_last_years_turnover(jigeesha) -> None:
    payer = jigeesha[2].payer
    assert payer.s194q.applies is True
    assert payer.previous_turnover == D("289956842.26")
    assert payer.constitution == "Individual"  # PAN ABFPL5804R: 4th character P
    assert payer.others.applies is True and not payer.others.needs_confirmation  # > Rs 10 Cr


def test_hpcl_crosses_194q_and_matches_the_tds_ledger(jigeesha) -> None:
    analysis = jigeesha[2]
    crossed = sorted(r.party.name for r in analysis.rows
                     if r.section_key == "194Q" and r.evaluation.crossed and r.party.identified)  # fmt: skip
    assert crossed == ["HPCL"]
    hpcl = _row(analysis, "HPCL", "194Q")
    ev = hpcl.evaluation
    assert hpcl.party.pan == "AAACH1118B" and hpcl.party.pan_source == "from GSTIN"
    assert (str(ev.crossed_on), ev.crossed_voucher_no) == ("2025-04-07", "1")
    assert ev.aggregate == D("281081763.53")
    assert ev.base == D("276081763.53")  # only the excess over Rs 50 lakh
    assert ev.tds == D(276082)
    # all purchases are HPCL's except Rs 2,565 of battery water from another seller
    assert D(TARGETS[KEY]["purchases"]) - ev.aggregate == D("2565.00")
    # the client books 194Q TDS in monthly journals without a party: Rs 2,80,895 in the year
    pool = analysis.pools["194Q"]
    assert pool.amount == D(280895)
    # HPCL is the only seller over ₹50 lakh, so the party-less 194Q TDS is all its own...
    assert hpcl.deducted_attributed == D(280895) and pool.unallocated == 0
    # ...and it is ₹4,813 MORE than due: 0.1% of the full ₹28.11 Cr, without the ₹50 lakh relief
    assert hpcl.excess == D(4813) and hpcl.status == "tds_excess"
    cause = te.likely_cause(ev, hpcl.deducted, analysis.rules)
    assert cause.startswith("TDS appears to have been deducted on the full value without")


def test_hpcl_rent_194i_matches_the_tds_on_rent_ledger(jigeesha) -> None:
    analysis = jigeesha[2]
    rent = _row(analysis, "HPCL", "194I(b)")
    assert rent.evaluation.aggregate == D("1184421.64")
    assert rent.evaluation.tds == D(118442)
    assert analysis.pools["194I(b)"].amount == D(118447)
    assert rent.status == "tds_ok"


def test_professional_fees_deducted_in_the_partys_own_vouchers(jigeesha) -> None:
    fees = _row(jigeesha[2], "RMH ADVISORS PVT LTD", "194J(b)")
    assert fees.evaluation.tds == D(16000) and fees.deducted_in_books == D(16000)
    assert fees.status == "tds_ok"


def test_hpcl_rent_is_flagged_for_an_explicit_194i_choice(jigeesha) -> None:
    db, client_id, analysis = jigeesha
    assert "Rent Paid Expenses @ 18%" in analysis.pending_choice
    assert _row(analysis, "HPCL", "194I(b)").provisional
    rows = {r["name"]: r for r in tds_service.mapping_view(db, client_id)}
    hint = rows["Rent Paid Expenses @ 18%"]["hint"]
    assert "HPCL" in hint and "10.0% of all rent" in hint and "194I(a) at 2%" in hint


def test_hpcl_rent_choice_figures_on_the_mapping_screen(jigeesha) -> None:
    db, client_id, _ = jigeesha
    rows = {r["name"]: r for r in tds_service.mapping_view(db, client_id)}
    by = {c["section"]: c for c in rows["Rent Paid Expenses @ 18%"]["rent_comparison"]}
    assert (D(by["194I(a)"]["tds_due"]), D(by["194I(b)"]["tds_due"])) == (D(23688), D(118442))
    assert D(by["194I(a)"]["deducted"]) == D(118447)
    assert D(by["194I(a)"]["difference"]) == D(94759)  # over-deducted if it is 194I(a)
    assert D(by["194I(b)"]["difference"]) == D(5)
