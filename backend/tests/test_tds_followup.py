"""Follow-up rules: over-deduction, s.194-IB, unidentified payees, rent choice, the TDS
analysis year, pool attribution and the PAN data-quality check."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.models import Alert
from app.repositories import client_repo, figures_repo, profile_repo, tds_repo
from app.repositories.tds_seed import default_rows
from app.services import import_json_service as js
from app.services import tds_detail, tds_service
from app.services import tds_engine as te
from app.services.tds_engine import OTHER, Payment, TdsStatus
from tests.test_tds_service import e, export, group, ledger, v

RULES = [te.SectionRule(**row) for row in default_rows()]
FY26 = date(2025, 4, 1)


def pay(day, amount, no="1"):
    return Payment(day, no, "Payment", "Expense", D(amount), f"k{no}")


# ------------------------------------------------------------------ engine


def test_194c_single_payment_test_sums_every_payment_above_30000() -> None:
    payments = [pay(date(2025, 5, 1), 35000, "1"), pay(date(2025, 6, 1), 32000, "2"),
                pay(date(2025, 7, 1), 20000, "3")]  # fmt: skip
    result = te.evaluate_payments(RULES, "194C", FY26, payments, OTHER, True)
    assert result.aggregate == D(87000)
    assert result.crossed_by == te.CrossedBy.SINGLE
    assert result.base == D(67000)  # not 35,000 (largest) and not 87,000 (aggregate)
    assert result.tds == D(1340)


def test_tolerance_is_capped_so_real_differences_are_reported() -> None:
    assert te.tolerance(5) == D(10)
    assert te.tolerance(150) == D(75)
    assert te.tolerance(10_000) == D(100)


def test_194q_full_value_deduction_is_excess_with_its_likely_cause() -> None:
    payments = [pay(FY26, 3000000, "1"), pay(date(2025, 6, 1), 3200000, "2")]
    ev = te.evaluate_payments(RULES, "194Q", FY26, payments, OTHER, True)
    assert ev.tds == D(1200)
    deducted = D(6200)  # 0.1% of the full ₹62 lakh, ignoring the ₹50 lakh relief
    assert te.classify(ev, deducted) == TdsStatus.EXCESS
    cause = te.likely_cause(ev, deducted, RULES)
    assert "full value without the ₹50,00,000 relief" in cause


def test_wrong_rate_is_named_as_the_likely_cause() -> None:
    ev = te.evaluate_payments(RULES, "194I(a)", FY26, [pay(FY26, 700000)], OTHER, True)
    assert ev.tds == D(14000)  # 2%
    cause = te.likely_cause(ev, D(70000), RULES)  # deducted at 10%
    assert "about 10%" in cause and te.classify(ev, D(70000)) == TdsStatus.EXCESS


def test_rate_master_has_194ib() -> None:
    rule = te.rule_on(RULES, "194-IB", FY26)
    assert (rule.single_threshold, rule.rate_individual, rule.rate_no_pan, rule.base) == (
        D(50000), D(2), D(20), "monthly",
    )  # fmt: skip
    assert te.rule_on(RULES, "194-IB", date(2024, 6, 1)).rate_other == D(5)


def _rent(amount_per_month, months=12):
    return [pay(date(2025 + (4 + i > 12), (3 + i) % 12 + 1, 5), amount_per_month, str(i))
            for i in range(months)]  # fmt: skip


def test_194ib_monthly_rent_above_50000() -> None:
    ev = te.evaluate_payments(RULES, "194-IB", FY26, _rent(53000), OTHER, True)
    assert ev.aggregate == D(636000) and ev.crossed_by == te.CrossedBy.MONTHLY
    assert ev.tds == D(12720)  # 2% of the year's rent, once
    assert ev.deduct_on == date(2026, 3, 5)  # the last month


def test_194ib_not_crossed_at_exactly_50000_a_month() -> None:
    ev = te.evaluate_payments(RULES, "194-IB", FY26, _rent(50000), OTHER, True)
    assert not ev.crossed and ev.tds == 0


def test_194ib_no_pan_is_capped_at_the_last_months_rent() -> None:
    ev = te.evaluate_payments(RULES, "194-IB", FY26, _rent(53000), OTHER, False)
    assert ev.tds == D(53000)  # 20% would be 1,27,200


def test_pan_name_mismatch() -> None:
    assert "implies a Company" in te.pan_name_mismatch("RMH ADVISORS PVT LTD", "AAPFR5417D")
    assert te.pan_name_mismatch("RM Hasnani & Associates", "AAPFR5417D") is None
    assert "implies a Firm" in te.pan_name_mismatch("Acme LLP", "ABCPA1234K")
    assert te.pan_name_mismatch("Ravi Traders", "ABCPA1234K") is None


# --------------------------------------------- a Shirke-like individual client

MASTER = [
    group("Sales Accounts"), group("Indirect Expenses", isrevenue=True),
    group("Direct Expenses", isrevenue=True, affectsgrossprofit=True),
    group("Sundry Creditors"), group("Bank Accounts"), group("Duties & Taxes"),
    group("TDS", "Duties & Taxes"),
    ledger("Sales", "Sales Accounts"),
    ledger("Rent Paid", "Indirect Expenses"),
    ledger("Contract Labour", "Direct Expenses"),
    ledger("TDS on Contract", "TDS", taxtype="TDS"),
    ledger("Bank", "Bank Accounts"),
    ledger("Old Contractor", "Sundry Creditors", guid="guid-old"),
    ledger("Contractor A", "Sundry Creditors", guid="guid-a", gstin="27AAAFA1234A1Z5"),
    ledger("Contractor B", "Sundry Creditors", guid="guid-b", gstin="27AAAFB1234B1Z5"),
    ledger("XYZ ADVISORS PVT LTD", "Sundry Creditors", guid="guid-xyz", gstin="27AAPFX1234D1Z3"),
]  # fmt: skip
RENT = [
    v("Payment", f"R{i}", f"{2025 + (i > 8)}{(3 + i) % 12 + 1:02d}05",
      [e("Rent Paid", -53000), e("Bank", 53000, True)])
    for i in range(12)
]  # fmt: skip
TRANSACTIONS = [
    v("Sales", "S1", "20250410", [e("Bank", -500, True), e("Sales", 500)]),
    # FY 2024-25: a big contract payment that must never count in 2025-26
    v("Journal", "OLD", "20250320", [e("Contract Labour", -150000), e("Old Contractor", 150000, True)]),
    # two contractors over 194C, TDS booked only in one party-less lump-sum journal
    v("Journal", "A1", "20250601", [e("Contract Labour", -120000), e("Contractor A", 120000, True)]),
    v("Journal", "B1", "20250602", [e("Contract Labour", -110000), e("Contractor B", 110000, True)]),
    v("Journal", "TDSJ", "20250630", [e("Bank", -4600), e("TDS on Contract", 4600)]),
    v("Journal", "X1", "20250605", [e("Contract Labour", -10000), e("XYZ ADVISORS PVT LTD", 10000, True)]),
    *RENT,
]  # fmt: skip
FILES = [("Master.json", export(MASTER)), ("Transactions.json", export(TRANSACTIONS))]
FY = "2025-26"


@pytest.fixture()
def client_id(db_session):
    client = client_repo.create_client(db_session, "Individual Client")
    profile_repo.save_profile(db_session, client.id, {"gstin": "27BBIPS6830E1ZG"})  # P = individual
    figures_repo.upsert_figures(db_session, client.id, "2024-25", {"turnover": D(11969243)})
    js.import_json(db_session, client.id, FILES)
    return client.id


def _row(analysis, party, section):
    return next(r for r in analysis.rows if r.party.name == party and r.section_key == section)


def _tds_alerts(db, client_id):
    return [
        a
        for a in db.query(Alert).filter(Alert.client_id == client_id)
        if a.metric.startswith("tds:")
    ]


def test_rent_ledgers_need_an_explicit_choice_before_approval(db_session, client_id) -> None:
    rows = {r["name"]: r for r in tds_service.mapping_view(db_session, client_id)}
    rent = rows["Rent Paid"]
    assert rent["requires_choice"] and rent["choice_pending"]
    assert "choose 194I(a)" in rent["reason"]
    assert "₹6,36,000" in rent["hint"]
    with pytest.raises(ValueError, match="Rent Paid"):
        tds_service.approve_mapping(db_session, client_id, "CA")
    tds_service.change_mapping(db_session, client_id, [
        {"match_type": "ledger", "name": "Rent Paid", "role": "base", "section_key": "194I(b)"},
    ])  # fmt: skip
    tds_service.approve_mapping(db_session, client_id, "CA")


def test_unidentified_rent_is_a_high_severity_alert_with_both_sections(
    db_session, client_id
) -> None:
    tds_service.change_mapping(db_session, client_id, [
        {"match_type": "ledger", "name": "Rent Paid", "role": "base", "section_key": "194I(b)"},
    ])  # fmt: skip
    tds_service.approve_mapping(db_session, client_id, "CA")
    raised = tds_service.recheck_client(db_session, client_id)
    rent_alert = next(a for a in raised if a.section == "194I(b)")
    assert rent_alert.new_status == "tds_unidentified"
    assert rent_alert.party == tds_service.NO_PARTY_NAME
    analysis = tds_service.analyze(db_session, client_id, FY)
    row = _row(analysis, tds_service.NO_PARTY_NAME, "194I(b)")
    alternatives = {a.section_key: a.tds for a in row.alternatives}
    assert alternatives == {"194I(b)": D(63600), "194-IB": D(12720)}
    detail = tds_detail.build(analysis, row)
    assert detail["unidentified"]["disallowance"] == "190800"
    assert "₹6,36,000" in detail["unidentified"]["message"]


def test_assigning_a_landlord_and_194ib_for_a_payer_not_liable_to_audit(
    db_session, client_id
) -> None:
    tds_repo.set_payer_year(db_session, client_id, FY, False)  # not liable to audit in 24-25
    tds_repo.set_assignment(db_session, client_id, FY, "Rent Paid",
                            {"payee_name": "Mr Landlord", "pan": "ABCPL1234K"})  # fmt: skip
    analysis = tds_service.analyze(db_session, client_id, FY)
    assert analysis.payer.rent_under_194ib
    row = _row(analysis, "Mr Landlord", "194-IB")
    assert row.mapped_section == "194I(b)" and row.party.assigned
    assert row.evaluation.tds == D(12720) and row.status == TdsStatus.NOT_DEDUCTED.value
    # the payer is not liable to audit, so 194C does not apply to it at all
    assert _row(analysis, "Contractor A", "194C").status == "tds_not_applicable"


def test_lump_sum_tds_shared_by_several_parties_never_turns_red_green(
    db_session, client_id
) -> None:
    analysis = tds_service.analyze(db_session, client_id, FY)
    a, b = _row(analysis, "Contractor A", "194C"), _row(analysis, "Contractor B", "194C")
    assert a.evaluation.tds == D(2400) and b.evaluation.tds == D(2200)
    assert a.deducted_allocated + b.deducted_allocated == D(4600)  # shown...
    assert a.deducted == 0 and b.deducted == 0  # ...but not counted
    assert a.status == b.status == TdsStatus.NOT_DEDUCTED.value
    pool = analysis.pools["194C"]
    assert pool.amount == D(4600) and pool.unallocated == D(4600)


def test_2024_25_payments_never_enter_the_2025_26_aggregate(db_session, client_id) -> None:
    analysis = tds_service.analyze(db_session, client_id, FY)
    assert not any(r.party.name == "Old Contractor" for r in analysis.rows)


def test_tds_alerts_only_for_the_analysis_year(db_session, client_id) -> None:
    tds_service.change_mapping(db_session, client_id, [
        {"match_type": "ledger", "name": "Rent Paid", "role": "base", "section_key": "194I(b)"},
    ])  # fmt: skip
    tds_service.approve_mapping(db_session, client_id, "CA")
    tds_service.recheck_client(db_session, client_id, {"2024-25", "2025-26"})
    assert {a.fy for a in _tds_alerts(db_session, client_id)} == {FY}
    assert tds_service.evaluate_fy(db_session, client_id, "2024-25") == []


def test_moving_the_analysis_year_closes_the_old_years_alerts(db_session, client_id) -> None:
    from app.repositories import threshold_repo

    tds_service.change_mapping(db_session, client_id, [
        {"match_type": "ledger", "name": "Rent Paid", "role": "base", "section_key": "194I(b)"},
    ])  # fmt: skip
    tds_service.approve_mapping(db_session, client_id, "CA")
    tds_service.recheck_client(db_session, client_id)
    threshold_repo.get_settings(db_session).tds_analysis_fy = "2026-27"
    tds_service.recheck_client(db_session, client_id)
    assert all(a.acknowledged for a in _tds_alerts(db_session, client_id))


def test_data_quality_lists_a_company_name_with_a_firms_pan(db_session, client_id) -> None:
    found = tds_service.data_quality(db_session, client_id)
    assert [(r["party"], r["pan"]) for r in found] == [("XYZ ADVISORS PVT LTD", "AAPFX1234D")]
    assert "20%" in found[0]["consequence"]


# ------------------------------------------------------------------ API


def test_api_refuses_other_years_and_assigns_a_payee(api) -> None:
    client_id = api.post("/clients", json={"name": "Individual Client"}).json()["id"]
    api.put(f"/clients/{client_id}/profile", json={"gstin": "27BBIPS6830E1ZG"})
    files = [("files", (name, content)) for name, content in FILES]
    token = api.post(
        "/imports/tally-json/preview", data={"client_id": str(client_id)}, files=files
    ).json()["preview_token"]
    api.post(
        "/imports/tally-json/confirm", data={"client_id": str(client_id), "preview_token": token}
    )

    old = api.get(f"/tds/clients/{client_id}/report", params={"fy": "2024-25"})
    assert old.status_code == 409 and "FY 2025-26 only" in old.json()["detail"]
    assert api.get(f"/reports/client/{client_id}", params={"fy": "2024-25"}).json()["tds"] is None
    settings = api.get("/tds/settings").json()
    assert settings["analysis_fy"] == "2025-26"

    saved = api.put(f"/tds/clients/{client_id}/assignment", json={
        "fy": FY, "ledger": "Rent Paid", "payee_name": "Mr Landlord", "pan": "ABCPL1234K"})  # fmt: skip
    assert saved.status_code == 200
    report = api.get(f"/tds/clients/{client_id}/report", params={"fy": FY}).json()
    parties = {r["party"] for s in report["sections"] for r in s["rows"]}
    assert "Mr Landlord" in parties
    assert report["pending_choice"] == ["Rent Paid"]
    assert D(report["totals"]["unallocated"]) == D(4600)
    approve = api.post(f"/tds/clients/{client_id}/mapping/approve", json={"approved_by": "CA"})
    assert approve.status_code == 422 and "Rent Paid" in approve.json()["detail"]
    dq = api.get("/tds/data-quality").json()
    assert dq[0]["party"] == "XYZ ADVISORS PVT LTD"


# --------------------------------------- materiality, money at stake, estimates


def test_interest_is_worked_per_voucher_not_on_the_whole_year() -> None:
    from app.services import tds_compliance as tc

    today = date(2026, 10, 6)
    items = [(date(2025, 5, 2), D(3500)), (date(2025, 11, 10), D(3500))]
    e = tc.exposure(D(7000), "TDS not deducted", items, D(35000), "194C", today)
    assert [(line.months, line.interest_1) for line in e.lines] == [(18, D(630)), (12, D(420))]
    assert e.interest == D(1050)  # not ₹1,260 (₹7,000 x 1% x 18 months from the first date)
    assert e.interest_15 == D(945) + D(630)
    assert [line.deposit_due for line in e.lines] == [date(2025, 6, 7), date(2025, 12, 7)]
    assert e.at_stake == D(7000) + D(10500) + D(1050)
    # a partial shortfall is spread over the vouchers in proportion to their TDS
    half = tc.exposure(D(3500), "x", items, D(0), "194C", today)
    assert [line.tds for line in half.lines] == [D(1750), D(1750)]


def test_234e_is_once_per_return_capped_at_the_returns_tds() -> None:
    from app.services import tds_compliance as tc

    today = date(2026, 10, 6)
    items = [
        (date(2026, 3, 31), "194C", D(24312), D(24312)),  # party 1, Q4
        (date(2026, 2, 10), "194J(b)", D(6750), D(6750)),  # party 2, Q4
        (date(2026, 3, 31), "194C", D(559), D(559)),  # party 3, Q4
        (date(2025, 6, 1), "194C", D(1000), D(0)),  # Q1
    ]
    fees = tc.return_fees(items, today)
    q4 = next(f for f in fees if f.label.startswith("Q4"))
    assert (q4.due, q4.tds, q4.days) == (date(2026, 5, 31), D(31621), 128)
    assert q4.fee == D(25600)  # 128 days x ₹200, ONE fee for the return, under the ₹31,621 cap
    q1 = next(f for f in fees if f.label.startswith("Q1"))
    assert q1.fee == D(1000)  # capped at that return's ₹1,000


def test_no_party_payments_below_the_materiality_cut_off_are_listed_not_alerted(
    db_session, client_id
) -> None:
    from app.repositories import threshold_repo

    threshold_repo.get_settings(db_session).tds_unidentified_min = D(700000)
    analysis = tds_service.analyze(db_session, client_id, FY)
    rent = _row(analysis, tds_service.NO_PARTY_NAME, "194I(b)")
    assert rent.status == TdsStatus.BELOW.value  # ₹6,36,000 is below the ₹7 lakh cut-off
    threshold_repo.get_settings(db_session).tds_unidentified_min = D(30000)
    analysis = tds_service.analyze(db_session, client_id, FY)
    assert _row(analysis, tds_service.NO_PARTY_NAME, "194I(b)").status == "tds_unidentified"


def test_alerts_and_report_rank_by_money_at_stake(db_session, client_id) -> None:
    tds_service.change_mapping(db_session, client_id, [
        {"match_type": "ledger", "name": "Rent Paid", "role": "base", "section_key": "194I(b)"},
    ])  # fmt: skip
    tds_service.approve_mapping(db_session, client_id, "CA")
    tds_service.recheck_client(db_session, client_id)
    analysis = tds_service.analyze(db_session, client_id, FY, today=date(2026, 10, 6))
    rent = _row(analysis, tds_service.NO_PARTY_NAME, "194I(b)")
    a = _row(analysis, "Contractor A", "194C")
    # unidentified rent: indicative ₹63,600 + 30% of ₹6,36,000 + interest; contractor: ₹2,400 + ...
    assert rent.at_stake > a.at_stake > 0
    assert rent.exposure.disallowance == D(190800)
    from app.services import tds_report

    report = tds_report.build_report(analysis)
    ranked = [(r["party"], r["section"]) for r in report["ranked"]]
    assert ranked[0] == (tds_service.NO_PARTY_NAME, "194I(b)")
    assert report["sections"][0]["key"] == "194I(b)"  # the section with the most at stake first
    from app.repositories import alert_repo

    alerts = alert_repo.list_alerts(db_session, client_id, FY, kind="tds", sort="at_stake")
    stakes = [a.money_at_stake for a in alerts]
    assert stakes == sorted(stakes, reverse=True) and alerts[0].section == "194I(b)"


def test_rent_choice_shows_both_amounts(db_session, client_id) -> None:
    rows = {r["name"]: r for r in tds_service.mapping_view(db_session, client_id)}
    comparison = {c["section"]: c for c in rows["Rent Paid"]["rent_comparison"]}
    assert D(comparison["194I(a)"]["tds_due"]) == D(12720)
    assert D(comparison["194I(b)"]["tds_due"]) == D(63600)
    assert D(comparison["194I(b)"]["deducted"]) == 0


def test_194ia_and_194ib_tds_ledgers() -> None:
    from app.services import tds_mapping as tm

    assert tm.propose("TDS (194 IA)", "tds", []).role == "excluded"  # immovable property
    assert tm.propose("TDS 194-IB", "tds", []).section_key == "194-IB"
    assert tm.propose("TDS on Rent", "tds", []).requires_choice


def test_excess_explained_by_a_bill_booked_under_another_section_and_moved(db_session) -> None:
    """Advance Power / RMH: a professional bill posted to a purchase ledger, TDS at 10%."""
    master = [
        group("Purchase Accounts"), group("Indirect Expenses", isrevenue=True),
        group("Sundry Creditors"), group("Bank Accounts"), group("Duties & Taxes"),
        group("TDS", "Duties & Taxes"),
        ledger("Purchase GST @ 18%", "Purchase Accounts"),
        ledger("Professional Fees", "Indirect Expenses"),
        ledger("TDS On Professional Fees 194J", "TDS", taxtype="TDS"),
        ledger("Bank", "Bank Accounts"),
        ledger("RMH ADVISORS PVT LTD", "Sundry Creditors", guid="g-rmh", gstin="27AAOCR4207R1ZN"),
    ]  # fmt: skip
    tx = [
        v("Journal", "435", "20260129", [e("Professional Fees", -150000), e("RMH ADVISORS PVT LTD", 135000, True),
                                         e("TDS On Professional Fees 194J", 15000)]),
        v("Journal", "557", "20260331", [e("Purchase GST @ 18%", -50000), e("RMH ADVISORS PVT LTD", 45000, True),
                                         e("TDS On Professional Fees 194J", 5000)]),
    ]  # fmt: skip
    client = client_repo.create_client(db_session, "Advance-like")
    profile_repo.save_profile(db_session, client.id, {"gstin": "27AAAFZ9999Z1Z5"})
    js.import_json(db_session, client.id, [("M.json", export(master)), ("T.json", export(tx))])
    analysis = tds_service.analyze(db_session, client.id, FY)
    row = _row(analysis, "RMH ADVISORS PVT LTD", "194J(b)")
    assert row.excess == D(5000) and row.status == TdsStatus.EXCESS.value
    cause = tds_service.cross_section_cause(row, analysis.rows)
    assert "₹50,000" in cause and "Purchase GST @ 18%" in cause and "wrong ledger" in cause
    detail = tds_detail.build(analysis, row)
    moved = next(x for x in detail["other_section_vouchers"] if x["voucher_no"] == "557")
    tds_repo.set_voucher_flag(
        db_session, client.id, moved["voucher_key"], True, flag="section:194J(b)"
    )
    row = _row(tds_service.analyze(db_session, client.id, FY), "RMH ADVISORS PVT LTD", "194J(b)")
    assert row.evaluation.aggregate == D(200000) and row.status == TdsStatus.OK.value


def test_aggregator_commission_is_194h_not_excluded_with_card_commission() -> None:
    from app.services import tds_mapping as tm

    assert tm.propose("Zomato Commission", "expense", []).section_key == "194H"
    assert tm.propose("Swiggy Commission Charges", "expense", []).section_key == "194H"
    assert tm.propose("Credit Card Commission", "expense", []).role == "excluded"


def test_completeness_accounts_for_every_expense_and_purchase_ledger(db_session, client_id) -> None:
    analysis = tds_service.analyze(db_session, client_id, FY)
    view = tds_service.completeness(db_session, client_id, analysis)
    rows = {r["ledger"]: r for r in view["rows"]}
    assert set(rows) == {"Rent Paid", "Contract Labour"}
    labour = rows["Contract Labour"]
    assert (
        labour["treatment"] == "Payments - 194C"
        and labour["any_crossed"]
        and labour["parties"] == 3
    )
    assert D(labour["total"]) == D(240000)  # 2025-26 only: the 24-25 bill is not here
    assert rows["Rent Paid"]["no_party"] and rows["Rent Paid"]["flag"]


def test_234e_fee_is_at_risk_only_where_no_tds_was_booked() -> None:
    from app.services import tds_compliance as tc

    items = [
        (date(2026, 3, 31), "194C", D(5000), D(0)),
        (date(2025, 6, 1), "194C", D(1000), D(1000)),
    ]
    booked = [(date(2026, 3, 31), "194C", D(5000))]
    fees = {f.label[:2]: f for f in tc.return_fees(items, date(2026, 10, 6), booked)}
    assert not fees["Q4"].likely_unfiled and fees["Q4"].deducted_in_books == D(5000)
    assert fees["Q1"].likely_unfiled
