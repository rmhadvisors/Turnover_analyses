"""Tally JSON export: parsing rules and the import API (synthetic exports, no real data)."""

import json
from decimal import Decimal

import pytest

from app.services import tally_json as tj
from app.services.tally_importer import ImportFormatError
from tests.test_api import make_client


def export(records: list[dict]) -> bytes:
    """A Tally-style JSON export: UTF-16 with a top-level 'tallymessage' array."""
    return json.dumps({"tallymessage": records}, indent=2).encode("utf-16")


def group(name, parent=None):
    return {"metadata": {"type": "Group", "name": name}, **({"parent": parent} if parent else {})}


def ledger(name, parent):
    return {"metadata": {"type": "Ledger", "name": name}, "parent": parent}


MASTER = [
    group("Sales Accounts"),
    group("Purchase Accounts"),
    group("Duties & Taxes"),
    group("Sundry Debtors"),
    group("Cash-in-Hand"),
    group("GST", "Duties & Taxes"),  # custom sub-group of Duties & Taxes
    group("URD Purchases", "Purchase Accounts"),  # custom sub-group of Purchase Accounts
    ledger("Sales 18%", "Sales Accounts"),
    ledger("Old Stock Sales", "Sales Accounts"),
    ledger("Purchase 18%", "URD Purchases"),
    ledger("Output CGST", "GST"),
    ledger("Output SGST", "GST"),
    ledger("Input CGST", "GST"),
    ledger("Acme Traders", "Sundry Debtors"),
    ledger("Cash", "Cash-in-Hand"),
    ledger("Supplier Ltd\r\n", "Sundry Debtors"),  # export can carry stray line breaks
    ledger("Bank", "Cash-in-Hand"),
]


def entry(name, amount, party=False):
    return {"ledgername": name, "amount": str(amount), "ispartyledger": party}


def voucher(vtype, number, day, entries, nested=(), **flags):
    body = {
        "metadata": {"type": "Voucher", "vchtype": vtype},
        "date": f"202603{day:02d}",
        "vouchertypename": vtype,
        "vouchernumber": number,
        "partyledgername": entries[0]["ledgername"] if entries else "",
        "cmpgstin": "27AAAAA0000A1Z5",
        "ledgerentries": entries,
        **flags,
    }
    if nested:  # item invoice: the sales ledger sits inside the inventory line
        body["allinventoryentries"] = [
            {"stockitemname": "Widget", "accountingallocations": list(nested)}
        ]
    return body


def parse(vouchers, master=MASTER):
    files = [tj.read_records(export(vouchers), "Transactions.json")]
    return tj.parse_transactions(
        files, tj.build_master([tj.read_records(export(master), "Master.json")])
    )


# ------------------------------------------------------------------ reading


def test_reads_utf16_and_utf8_and_reports_kind() -> None:
    records = [ledger("Cash", "Cash-in-Hand")]
    for payload in (export(records), json.dumps({"tallymessage": records}).encode("utf-8-sig")):
        result = tj.read_records(payload, "Master.json")
        assert len(result.records) == 1 and result.truncated is False
        assert tj.kind_of(result.records) == "master"
    assert (
        tj.kind_of(tj.read_records(export([voucher("Sales", "1", 1, [])]), "t").records)
        == "transactions"
    )


def test_truncated_file_keeps_complete_records_and_reports_the_cut() -> None:
    full = export([voucher("Sales", str(i), 1, [entry("Cash", -10, True)]) for i in range(5)])
    text = full.decode("utf-16")
    cut = text[: text.rindex('"vouchernumber"') + 20]  # chop the last voucher in half
    result = tj.read_records(cut.encode("utf-16"), "Transactions.json")
    assert result.truncated is True
    assert len(result.records) == 4  # the half-written fifth voucher is dropped


def test_not_a_tally_json_file() -> None:
    with pytest.raises(ImportFormatError, match="not a Tally JSON export"):
        tj.read_records(json.dumps({"hello": 1}).encode("utf-8"), "x.json")


# ------------------------------------------------------------------ turnover


def test_item_invoice_sales_with_nested_sales_ledger_and_gst() -> None:
    v = voucher(
        "Sales",
        "S-1",
        5,
        [
            entry("Acme Traders", -118000, party=True),
            entry("Output CGST", 9000),
            entry("Output SGST", 9000),
        ],
        nested=[entry("Sales 18%", 100000)],
    )
    result = parse([v])
    (sale,) = result.vouchers
    assert sale.voucher_type == "sales"
    assert (sale.taxable_value, sale.tax_value, sale.total_value) == (
        Decimal("100000.00"),
        Decimal("18000.00"),
        Decimal("118000.00"),
    )
    assert sale.party == "Acme Traders" and sale.fy == "2025-26"
    assert result.unbalanced == 0 and not result.unknown_ledgers


def test_credit_note_and_debit_note_are_detected_from_the_ledger_direction() -> None:
    credit = voucher(
        "Credit Note", "CN-1", 6,
        [entry("Acme Traders", 11800, party=True), entry("Output CGST", -900), entry("Output SGST", -900)],
        nested=[entry("Sales 18%", -10000)],
    )  # fmt: skip
    debit = voucher(
        "Debit Note", "DN-1", 7,
        [entry("Supplier Ltd", -5000, party=True), entry("Input CGST", 0)],
        nested=[entry("Purchase 18%", 5000)],
    )  # fmt: skip
    result = parse([credit, debit])
    kinds = {v.voucher_no: v for v in result.vouchers}
    assert kinds["CN-1"].voucher_type == "credit_note" and kinds["CN-1"].taxable_value == Decimal(
        "10000.00"
    )
    assert kinds["CN-1"].tax_value == Decimal("1800.00")
    assert (
        kinds["DN-1"].voucher_type == "debit_note"
    )  # credit to a purchase ledger = purchase return
    plain = voucher("Debit Note", "DN-2", 8,
                       [entry("Supplier Ltd", 2000, party=True)], nested=[entry("Purchase 18%", -2000)])  # fmt: skip
    assert parse([plain]).vouchers[0].voucher_type == "purchase"  # debit to a purchase ledger


def test_custom_voucher_type_names_work_because_ledger_groups_decide() -> None:
    purchase = voucher(
        "Purchase New", "P-1", 9,
        [entry("Supplier Ltd", 11800, party=True), entry("Input CGST", -900)],
        nested=[entry("Purchase 18%", -10000)],
    )  # fmt: skip
    (record,) = parse([purchase]).vouchers
    assert record.voucher_type == "purchase" and record.taxable_value == Decimal("10000.00")
    assert record.party == "Supplier Ltd"  # stray CR/LF stripped


def test_cash_purchase_paid_by_payment_voucher_still_counts() -> None:
    payment = voucher(
        "Payment", "PM-1", 10, [entry("Cash", 3000, True), entry("Purchase 18%", -3000)]
    )
    (record,) = parse([payment]).vouchers
    assert record.voucher_type == "purchase" and record.total_value == Decimal("3000.00")


def test_excluded_vouchers_and_non_trading_vouchers() -> None:
    sale = [entry("Acme Traders", -100, True), entry("Sales 18%", 100)]
    vouchers = [
        voucher("Sales", "S-1", 1, sale),
        voucher("Sales", "S-2", 2, sale, isdeleted=True),
        voucher("Sales", "S-3", 3, sale, iscancelled=True),
        voucher("Sales", "S-4", 4, sale, isoptional=True),
        voucher("Receipt", "R-1", 5, [entry("Bank", -100, True), entry("Acme Traders", 100)]),
    ]
    result = parse(vouchers)
    assert [v.voucher_no for v in result.vouchers] == ["S-1"]
    assert dict(result.excluded) == {"deleted": 1, "cancelled": 1, "optional (not posted)": 1}
    assert result.other_vouchers == 1 and result.total_vouchers == 5


def test_unknown_ledger_unbalanced_and_date_range_are_reported() -> None:
    odd = voucher("Sales", "S-1", 9, [entry("Ghost Ledger", -100, True), entry("Sales 18%", 90)])
    result = parse(
        [odd, voucher("Sales", "S-2", 2, [entry("Cash", -5, True), entry("Sales 18%", 5)])]
    )
    assert result.unknown_ledgers == {"Ghost Ledger"}
    assert result.unbalanced == 1
    assert (result.first_date.day, result.last_date.day) == (2, 9)


def test_warnings_explain_truncation_and_part_year() -> None:
    file = tj.read_records(
        export([voucher("Sales", "S-1", 1, [entry("Cash", -5, True), entry("Sales 18%", 5)])]),
        "T.json",
    )
    file.truncated = True
    master = tj.build_master([tj.read_records(export(MASTER), "M.json")])
    result = tj.parse_transactions([file], master)
    text = " ".join(
        tj.describe_warnings(result, master, {"M.json": "master", "T.json": "transactions"})
    )
    assert "cut off" in text and "part-year" in text


def test_summary_nets_credit_notes_by_year() -> None:
    sale = voucher("Sales", "S-1", 1, [entry("Cash", -1000, True), entry("Sales 18%", 1000)])
    note = voucher("Credit Note", "CN-1", 2, [entry("Cash", 100, True), entry("Sales 18%", -100)])
    totals = tj.summarize(parse([sale, note]).vouchers)
    assert totals["2025-26"]["sales"] == Decimal("900.00") and totals["2025-26"]["sales_count"] == 2


# ----------------------------------------------------------------------- API


def upload_json(api, client_id, files, endpoint="confirm"):
    return api.post(
        f"/imports/tally-json/{endpoint}",
        data={"client_id": client_id},
        files=[("files", (name, content)) for name, content in files],
    )


def sample_files():
    sale = voucher("Sales", "S-1", 1, [entry("Acme Traders", -118, True), entry("Output CGST", 9),
                                       entry("Output SGST", 9)], nested=[entry("Sales 18%", 100)])  # fmt: skip
    buy = voucher("Purchase New", "P-1", 2, [entry("Supplier Ltd", 59, True), entry("Input CGST", -4.5),
                                             entry("Input CGST", -4.5)], nested=[entry("Purchase 18%", -50)])  # fmt: skip
    return [("Master.json", export(MASTER)), ("Transactions.json", export([sale, buy]))]


def test_api_preview_import_and_duplicate_skipping(api) -> None:
    client_id = make_client(api, "Json Co")
    files = sample_files()

    preview = upload_json(api, client_id, files, "preview").json()
    assert preview["would_import"] == 2 and preview["duplicates"] == 0
    assert preview["sales_records"] == 1 and preview["purchase_records"] == 1
    assert preview["company_gstins"] == ["27AAAAA0000A1Z5"]
    assert {f["kind"] for f in preview["files"]} == {"master", "transactions"}
    assert any("part-year" in w for w in preview["warnings"])

    first = upload_json(api, client_id, files).json()
    assert first["imported"] == 2 and first["duplicates_skipped"] == 0
    assert first["fys_affected"] == ["2025-26"] and len(first["import_log_ids"]) == 2
    figures = api.get(f"/entries/{client_id}").json()[0]
    assert Decimal(figures["turnover"]) == Decimal("100.00")
    assert Decimal(figures["purchases"]) == Decimal("50.00")

    second = upload_json(api, client_id, list(reversed(files))).json()  # file order does not matter
    assert second["imported"] == 0 and second["duplicates_skipped"] == 2
    log = api.get("/imports/log", params={"client_id": client_id}).json()
    assert len(log) == 4 and {row["report_type"] for row in log} == {
        "sales_register",
        "purchase_register",
    }


def test_api_identical_vouchers_inside_one_file_are_imported_once(api) -> None:
    client_id = make_client(api)
    sale = voucher("Sales", "S-1", 1, [entry("Cash", -100, True), entry("Sales 18%", 100)])
    files = [("Master.json", export(MASTER)), ("Transactions.json", export([sale, sale]))]
    result = upload_json(api, client_id, files).json()
    assert result["imported"] == 1 and result["duplicates_skipped"] == 1


def test_api_rejects_missing_master_missing_transactions_and_junk(api) -> None:
    client_id = make_client(api)
    master, transactions = sample_files()
    assert "Master file is missing" in upload_json(api, client_id, [transactions]).json()["detail"]
    assert "Transactions file is missing" in upload_json(api, client_id, [master]).json()["detail"]
    junk = ("x.json", json.dumps({"tallymessage": [{"hello": 1}]}).encode())
    assert upload_json(api, client_id, [master, junk]).status_code == 422
    assert upload_json(api, client_id, [("bad.json", b"not json at all")]).status_code == 422
    assert upload_json(api, 999, [master, transactions]).status_code == 404


def test_blank_voucher_number_disambiguated_by_party_not_merged() -> None:
    """Two different cash customers paying the same round amount on the same day
    must not collide, even though their voucher numbers are both blank."""
    a = voucher("Sales", "", 1, [entry("Alpha Traders", -100, True), entry("Sales 18%", 100)])
    b = voucher("Sales", "", 1, [entry("Beta Traders", -100, True), entry("Sales 18%", 100)])
    result = parse([a, b])
    assert len(result.vouchers) == 2
    assert len({v.dedup_key for v in result.vouchers}) == 2


def test_blank_voucher_number_same_party_still_dedupes_on_reimport() -> None:
    a = voucher("Sales", "", 1, [entry("Alpha Traders", -100, True), entry("Sales 18%", 100)])
    again = voucher("Sales", "", 1, [entry("Alpha Traders", -100, True), entry("Sales 18%", 100)])
    result = parse([a, again])
    assert len({v.dedup_key for v in result.vouchers}) == 1


def test_guid_reused_across_periods_does_not_leak_into_the_dedup_key() -> None:
    """Tally's JSON guid suffix is a per-period internal sequence number and can
    name two unrelated vouchers in *different* period exports (each parsed in its
    own call, as the importer always does - one company+year per import). The
    stored dedup_key must not collide just because the raw guid was reused."""
    shared_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee-00000001"
    a = voucher("Sales", "S-1", 1, [entry("Alpha", -100, True), entry("Sales 18%", 100)])
    b = voucher("Sales", "S-2", 2, [entry("Beta", -200, True), entry("Sales 18%", 200)])
    a["guid"] = b["guid"] = shared_guid
    key_a = parse([a]).vouchers[0].dedup_key  # simulates the 24-25 import call
    key_b = parse([b]).vouchers[0].dedup_key  # simulates the separate 25-26 import call
    assert key_a != key_b


def test_same_guid_within_one_call_is_still_treated_as_a_repeat() -> None:
    """Within a single call (one company + one year, matching how the importer is
    actually used), a repeated guid is still trusted as 'the same voucher again' -
    this is the multi-file same-period merge STEP 3 asks for."""
    shared_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee-00000001"
    a = voucher("Sales", "S-1", 1, [entry("Alpha", -100, True), entry("Sales 18%", 100)])
    b = voucher("Sales", "S-2", 2, [entry("Beta", -200, True), entry("Sales 18%", 200)])
    a["guid"] = b["guid"] = shared_guid
    result = parse([a, b])
    assert len(result.vouchers) == 1
    assert result.duplicate_guid_vouchers == 1
