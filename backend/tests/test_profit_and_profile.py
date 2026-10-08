"""Gross / net profit derived from Tally exports, client profiles deciding which limits
apply, and the per-seller 194Q check (synthetic data only)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import SYSTEM_PER_SELLER, upgrade
from app.models import AbsoluteLimit, AbsoluteLimitMetric, Alert, Client
from app.repositories import figures_repo, threshold_repo
from app.services import applicability as ap
from app.services import tally_json as tj
from app.services.recheck_service import SYSTEM_NOT_APPLICABLE
from app.services.turnover import VoucherAmount, purchase_entries_by_seller
from tests.test_api import make_client
from tests.test_tally_json import entry, export, group, ledger

# ------------------------------------------------------------ derived profit


def gp_group(name):
    return {**group(name), "affectsgrossprofit": True, "isrevenue": True}


def rev_group(name):
    return {**group(name), "isrevenue": True}


PL_MASTER = [
    gp_group("Sales Accounts"),
    gp_group("Purchase Accounts"),
    gp_group("Direct Expenses"),
    rev_group("Indirect Expenses"),
    rev_group("Indirect Incomes"),
    group("Current Assets"),
    group("Stock-in-Hand", "Current Assets"),
    group("Sundry Debtors", "Current Assets"),
    ledger("Sales", "Sales Accounts"),
    ledger("Purchases", "Purchase Accounts"),
    ledger("Wages", "Direct Expenses"),
    ledger("Rent", "Indirect Expenses"),
    ledger("Interest Received", "Indirect Incomes"),
    ledger("Cash", "Current Assets"),
    {
        **ledger("Stock", "Stock-in-Hand"),
        "openingbalance": "-1000.00",
        "ledgerclosingvalues": [{"date": "20260331", "amount": "-1500.00"}],
    },
]


def month_voucher(month: int, entries: list[dict]) -> dict:
    year = 2025 if month >= 4 else 2026
    return {
        "metadata": {"type": "Voucher"},
        "date": f"{year}{month:02d}10",
        "vouchertypename": "Journal",
        "vouchernumber": f"J-{month}",
        "ledgerentries": entries,
    }


def year_of_vouchers(extra: list[dict] | None = None) -> list[dict]:
    """Twelve months: sales 10,000 and purchases 6,000 a month, plus other P&L lines."""
    vouchers = [
        month_voucher(m, [entry("Cash", -10000), entry("Sales", 10000)])
        for m in (*range(4, 13), 1, 2, 3)
    ]
    vouchers += [
        month_voucher(m, [entry("Purchases", -6000), entry("Cash", 6000)])
        for m in (*range(4, 13), 1, 2, 3)
    ]
    vouchers.append(month_voucher(5, [entry("Wages", -2000), entry("Cash", 2000)]))
    vouchers.append(month_voucher(6, [entry("Rent", -12000), entry("Cash", 12000)]))
    vouchers.append(month_voucher(7, [entry("Interest Received", 500), entry("Cash", -500)]))
    return vouchers + (extra or [])


def derive(vouchers, master=PL_MASTER, today=date(2026, 9, 1)):
    built = tj.build_master([tj.read_records(export(master), "M.json")])
    result = tj.parse_transactions([tj.read_records(export(vouchers), "T.json")], built)
    return tj.derive_profits(result, built, today)


def test_gross_and_net_profit_follow_tallys_pl() -> None:
    (profit,) = derive(year_of_vouchers())
    # GP = 120,000 sales - 72,000 purchases - 2,000 wages + 1,500 closing - 1,000 opening
    assert profit.fy == "2025-26" and profit.gross_profit == Decimal("46500.00")
    # NP = GP - 12,000 rent + 500 interest
    assert profit.net_profit == Decimal("35000.00")
    assert (profit.opening_stock, profit.closing_stock) == (Decimal("1000.00"), Decimal("1500.00"))
    assert profit.notes == []


def test_profit_is_not_derived_for_unfinished_or_part_years_or_missing_stock() -> None:
    (running,) = derive(year_of_vouchers(), today=date(2026, 3, 1))
    assert running.gross_profit is None and "not finished" in running.notes[0]

    short = [v for v in year_of_vouchers() if v["date"][4:6] in ("04", "05", "06")]
    (part,) = derive(short)
    assert part.gross_profit is None and "not a full year" in part.notes[0]

    no_closing = [r for r in PL_MASTER if r["metadata"]["name"] != "Stock"]
    no_closing.append({**ledger("Stock", "Stock-in-Hand"), "openingbalance": "-1000.00"})
    (missing,) = derive(year_of_vouchers(), master=no_closing)
    assert missing.gross_profit is None and "closing stock" in missing.notes[0]

    without_stock = [r for r in PL_MASTER if r["metadata"]["name"] != "Stock"]
    (no_stock,) = derive(year_of_vouchers(), master=without_stock)
    assert no_stock.gross_profit == Decimal("46000.00")  # no stock adjustment
    assert "No stock ledger" in no_stock.notes[0]


def test_net_profit_held_back_when_ledgers_are_missing_from_the_master() -> None:
    gap = month_voucher(8, [entry("Mystery Expense", -5000), entry("Cash", 5000)])
    (profit,) = derive(year_of_vouchers([gap]))
    assert profit.gross_profit == Decimal("46500.00") and profit.net_profit is None
    assert "missing from the Master" in profit.notes[-1]

    small = month_voucher(8, [entry("Mystery Expense", -100), entry("Cash", 100)])
    (ok,) = derive(year_of_vouchers([small]))  # 100 is under 5% of NP 35,000
    assert ok.net_profit == Decimal("35000.00")


def test_derived_profit_never_replaces_manual_or_pl_import_figures(db_session) -> None:
    client = Client(name="Profit Co")
    db_session.add(client)
    db_session.flush()
    assert figures_repo.store_derived_profit(db_session, client.id, "2025-26", Decimal(1), Decimal(2))
    row = figures_repo.get_figures(db_session, client.id, "2025-26")
    assert (row.gross_profit, row.net_profit, row.profit_source) == (1, 2, "tally_json")

    row.gross_profit, row.profit_source = Decimal(9), "manual"
    assert not figures_repo.store_derived_profit(db_session, client.id, "2025-26", Decimal(3), None)
    assert row.gross_profit == Decimal(9)


def test_manual_entry_marks_profit_as_manual(api) -> None:
    client_id = make_client(api, "Manual Profit Co")
    body = {"client_id": client_id, "previous_fy": "2024-25", "current_fy": "2025-26",
            "current_gross_profit": "100", "current_turnover": "1000"}  # fmt: skip
    assert api.post("/entries", json=body).status_code < 300
    from app.database import get_db  # the test database behind `api`
    from app.main import app

    db = next(app.dependency_overrides[get_db]())
    assert figures_repo.get_figures(db, client_id, "2025-26").profit_source == "manual"


# -------------------------------------------------------------- applicability


def test_rules_unknown_fields_never_exclude_a_limit() -> None:
    rule = [{"gst_registered": [False], "supplies": ["goods", "both"]}]
    assert ap.applies(rule, ap.context(None, None))  # nothing known: applies
    known = type("P", (), {"gst_registered": True, "supplies": "goods"})()
    assert not ap.applies(rule, ap.context(known, None))
    assert ap.applies(None, ap.context(known, None)) and ap.applies([], {})
    assert ap.describe(rule) == "not GST-registered · supplies: goods/both"


def test_tds_rule_uses_entity_and_previous_year_audit() -> None:
    rule = ap.DEFAULT_RULES["TDS u/s 194C - contractors"]
    person = type("P", (), {"entity_type": "individual", "nature": "business"})()
    assert not ap.applies(rule, ap.context(person, Decimal(5_000_000)))  # not audited last year
    assert ap.applies(rule, ap.context(person, Decimal(20_000_000)))  # audited u/s 44AB
    firm = type("P", (), {"entity_type": "firm"})()
    assert ap.applies(rule, ap.context(firm, Decimal(1)))
    llp_rule = ap.DEFAULT_RULES["LLP audit"]
    assert not ap.applies(llp_rule, ap.context(firm, None))
    assert ap.applies(llp_rule, ap.context(type("P", (), {"entity_type": "llp"})(), None))


def test_gstin_gives_state_registration_and_entity() -> None:
    facts = ap.parse_gstin("27aakfh4657g1z4")
    assert (facts.state_code, facts.state_name, facts.special_category) == ("27", "Maharashtra", False)
    assert facts.entity_type is None and "firm or an LLP" in facts.hint  # PAN type F
    assert ap.parse_gstin("27AYWPK3086E1ZY").entity_type == "individual"
    assert ap.parse_gstin("14ABCDE1234F1Z5").special_category  # Manipur
    assert ap.parse_gstin("bad") is None


def test_profile_api_closes_alerts_of_limits_that_no_longer_apply(api) -> None:
    client_id = make_client(api, "Profile Co")
    body = {"client_id": client_id, "previous_fy": "2024-25", "current_fy": "2025-26",
            "previous_turnover": "5000000", "current_turnover": "9000000"}  # fmt: skip
    api.post("/entries", json=body)
    gst_open = [
        a for a in api.get("/alerts", params={"client_id": client_id, "unacknowledged_only": "true"}).json()
        if "GST registration" in a["metric_label"]
    ]
    assert gst_open  # 90 L is over the GST registration limits while the profile is empty

    empty = api.get(f"/clients/{client_id}/profile").json()
    assert empty["gstin"] is None and "entity_type" in empty["unknown_fields"]

    saved = api.put(
        f"/clients/{client_id}/profile",
        json={"gstin": "27AAKFH4657G1Z4", "gst_registered": True, "entity_type": "firm",
              "nature": "business", "supplies": "services", "presumptive": "none",
              "cash_within_5pct": False},
    )  # fmt: skip
    assert saved.status_code == 200, saved.text
    profile = saved.json()
    assert profile["state_name"] == "Maharashtra" and profile["unknown_fields"] == ["special_category"]

    alerts = api.get("/alerts", params={"client_id": client_id}).json()
    for alert in alerts:
        if "GST registration" in alert["metric_label"]:
            assert alert["acknowledged_by"] == SYSTEM_NOT_APPLICABLE
    limits = api.get(f"/reports/comparison/{client_id}", params={"fy": "2025-26"}).json()["limits"]
    names = {limit["name"] for limit in limits}
    assert not any("GST registration" in n or "LLP" in n or "44ADA" in n for n in names)
    assert "Tax audit u/s 44AB - business (standard)" in names

    bad = api.put(f"/clients/{client_id}/profile", json={"gstin": "123"})
    assert bad.status_code == 422


def test_limits_show_who_they_apply_to_and_updates_keep_the_rule(api) -> None:
    limits = api.get("/thresholds/limits").json()
    llp = next(limit for limit in limits if limit["name"] == "LLP audit")
    assert llp["applies_to"] == "entity type: llp"
    body = {k: llp[k] for k in ("name", "metric", "amount", "approaching_pct", "fy_scope", "description", "is_enabled")}
    body["amount"] = "4000000"
    updated = api.put(f"/thresholds/limits/{llp['id']}", json=body).json()
    assert updated["applies_when"] == llp["applies_when"] and updated["amount"] == "4000000.00"


# ------------------------------------------------------------------ 194Q


def test_194q_counts_purchases_per_seller_without_cash() -> None:
    d = date(2025, 6, 1)
    vouchers = [
        VoucherAmount("purchase", d, "P1", Decimal(3_000_000), Decimal(0), "Seller A"),
        VoucherAmount("purchase", d, "P2", Decimal(3_000_000), Decimal(0), "Seller B"),
        VoucherAmount("purchase", d, "P3", Decimal(9_000_000), Decimal(0), "Cash"),
        VoucherAmount("debit_note", d, "D1", Decimal(500_000), Decimal(0), "Seller A"),
        VoucherAmount("purchase", d, "P4", Decimal(1), Decimal(0), None),
    ]
    by_seller = purchase_entries_by_seller(vouchers)
    assert set(by_seller) == {"Seller A", "Seller B"}
    assert sum(value for *_, value in by_seller["Seller A"]) == Decimal(2_500_000)


# ------------------------------------------------------------------ migration


def test_upgrade_adds_columns_rules_and_moves_194q_to_per_seller(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        threshold_repo.seed_defaults(db)
        q = db.query(AbsoluteLimit).filter_by(name="TDS u/s 194Q - purchases of goods").one()
        q.metric = AbsoluteLimitMetric.PURCHASE_TURNOVER  # as in a database from before
        db.add(Client(id=1, name="Old Co"))
        db.flush()
        db.add(Alert(client_id=1, fy="2025-26", metric=f"limit:{q.id}:purchase_turnover",
                     new_status="crossed", value=Decimal(1)))  # fmt: skip
        db.commit()
    with engine.begin() as connection:  # simulate the old schema
        connection.execute(text("ALTER TABLE yearly_figures DROP COLUMN profit_source"))
        connection.execute(text("ALTER TABLE absolute_limits DROP COLUMN applies_when"))

    upgrade(engine)
    upgrade(engine)  # idempotent
    columns = {c["name"] for c in inspect(engine).get_columns("absolute_limits")}
    assert "applies_when" in columns
    with Session(engine) as db:
        q = db.query(AbsoluteLimit).filter_by(name="TDS u/s 194Q - purchases of goods").one()
        assert q.metric == AbsoluteLimitMetric.PURCHASE_PER_SELLER
        assert q.applies_when == ap.DEFAULT_RULES[q.name]
        old = db.query(Alert).one()
        assert old.acknowledged and old.acknowledged_by == SYSTEM_PER_SELLER
