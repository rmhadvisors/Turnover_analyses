"""Checks that a Master and Transactions file belong together (and to the client) before a
Tally JSON import, plus the review facts the Add-client panel shows (synthetic exports)."""

from decimal import Decimal

import pytest

from app.models import Client
from app.services import import_json_service as js
from app.services.tally_importer import ImportFormatError
from tests.test_api import make_client
from tests.test_tally_json import MASTER, entry, export, voucher

COMPANY_A = "2a1d1dde-741a-4be9-b634-65b0569c42ff"
COMPANY_B = "116eb1a3-865d-421c-bf93-b064d19035d7"
GSTIN = "27AAAAA0000A1Z5"  # the cmpgstin of the synthetic vouchers


def stamped(records, company, start=1):
    """Give every record a Tally guid '<company GUID>-<object number>'."""
    return [{**r, "guid": f"{company}-{start + i:08x}"} for i, r in enumerate(records)]


def sale(number, when, amount=100000):
    v = voucher("Sales", number, 1, [entry("Acme Traders", -amount, True), entry("Sales 18%", amount)])
    return {**v, "date": when}


SALES = [sale("1", "20250410"), sale("2", "20250515"), sale("3", "20250620")]


def files(master_company=COMPANY_A, tx_company=COMPANY_A, vouchers=SALES):
    return [
        ("Master.json", export(stamped(MASTER, master_company))),
        ("Transactions.json", export(stamped(vouchers, tx_company, 1000))),
    ]


def upload(pairs):
    return [("files", (name, content)) for name, content in pairs]


def test_master_and_transactions_from_different_companies_are_refused(db_session) -> None:
    db_session.add(Client(name="JIGEESHA", tally_company_id=COMPANY_A))
    db_session.add(Client(name="HOTEL KINARA", tally_company_id=COMPANY_B))
    db_session.commit()
    preview = js.preview_json(db_session, None, files(COMPANY_A, COMPANY_B))
    assert preview["can_import"] is False
    (blocker,) = [b for b in preview["blockers"] if "different Tally companies" in b]
    assert "Master file: JIGEESHA" in blocker and "Transactions file: HOTEL KINARA" in blocker
    # the "Master looks older" message is not added on top of the company mismatch
    assert not any("looks older" in b for b in preview["blockers"])
    assert preview["unmatched_ledgers"] == []  # same ledger names in this synthetic pair
    with pytest.raises(ImportFormatError, match="different Tally companies"):
        js.import_json(db_session, make_db_client(db_session), files(COMPANY_A, COMPANY_B))


def make_db_client(db, name="New Co") -> int:
    client = Client(name=name)
    db.add(client)
    db.commit()
    return client.id


def test_the_api_refuses_a_mismatched_pair_at_preview_and_at_confirm(api) -> None:
    client_id = make_client(api)
    preview = api.post(
        "/imports/tally-json/preview", data={"client_id": client_id}, files=upload(files(COMPANY_A, COMPANY_B))
    ).json()
    assert preview["can_import"] is False and preview["blockers"]
    confirm = api.post(
        "/imports/tally-json/confirm", data={"client_id": client_id, "preview_token": preview["preview_token"]}
    )
    assert confirm.status_code == 422 and "different Tally companies" in confirm.json()["detail"]
    assert api.get("/imports/log", params={"client_id": client_id}).json() == []


def test_a_master_missing_ledgers_with_over_2pct_of_the_value_is_refused(api) -> None:
    ghost = voucher("Payment", "P1", 2, [entry("Ghost Ledger", -50000, True), entry("Cash", 50000)])
    pair = files(vouchers=[*SALES, {**ghost, "date": "20250411"}])
    client_id = make_client(api)
    preview = api.post("/imports/tally-json/preview", data={"client_id": client_id}, files=upload(pair)).json()
    assert preview["can_import"] is False
    assert any(b.startswith("This Master looks older than the Transactions file — re-export the Master")
               for b in preview["blockers"])  # fmt: skip
    assert preview["unmatched_ledgers"] == [{"name": "Ghost Ledger", "lines": 1, "amount": "50000.00"}]
    assert Decimal(preview["unmatched_share_pct"]) == Decimal("7.14")  # 50,000 of 7,00,000 line value
    assert Decimal(preview["unmatched_limit_pct"]) == Decimal(2)

    # the 2% is a setting
    api.put("/thresholds/import-settings", json={"max_unmatched_ledger_pct": "10"})
    assert Decimal(api.get("/thresholds/settings").json()["max_unmatched_ledger_pct"]) == Decimal(10)
    preview = api.post("/imports/tally-json/preview", data={"client_id": client_id}, files=upload(pair)).json()
    assert preview["can_import"] is True and preview["unmatched_ledgers"]  # still listed
    confirm = api.post(
        "/imports/tally-json/confirm", data={"client_id": client_id, "preview_token": preview["preview_token"]}
    )
    assert confirm.status_code == 200, confirm.text


def test_gstin_and_pan_are_checked_against_the_client(api) -> None:
    preview = api.post(
        "/imports/tally-json/preview", data={"expected_gstin": "27AYWPK3086E1ZY"}, files=upload(files())
    ).json()
    assert any(f"GSTIN {GSTIN}" in b and "27AYWPK3086E1ZY" in b for b in preview["blockers"])
    preview = api.post(
        "/imports/tally-json/preview", data={"expected_pan": "AYWPK3086E"}, files=upload(files())
    ).json()
    assert any("PAN AAAAA0000A" in b for b in preview["blockers"])

    client_id = make_client(api)
    api.put(f"/clients/{client_id}/profile", json={"gstin": "27AYWPK3086E1ZY"})
    preview = api.post("/imports/tally-json/preview", data={"client_id": client_id}, files=upload(files())).json()
    assert preview["can_import"] is False and "Acme Traders has GSTIN 27AYWPK3086E1ZY" in preview["blockers"][0]


def test_add_client_flow_previews_before_the_client_exists(api) -> None:
    """Preview without a client, create the client, then confirm with the token; the
    client is linked to the Tally company, so later files from another company are refused."""
    preview = api.post(
        "/imports/tally-json/preview", data={"expected_gstin": GSTIN}, files=upload(files())
    ).json()
    assert preview["can_import"] is True and preview["blockers"] == []
    assert preview["detected_fy"] == "2025-26" and preview["fy_vouchers"] == {"2025-26": 3}
    assert (preview["first_date"], preview["last_date"]) == ("2025-04-10", "2025-06-20")
    assert preview["company"] == {"tally_company_id": COMPANY_A, "master_company_id": COMPANY_A,
                                  "gstin": GSTIN, "client_name": None}  # fmt: skip
    client_id = make_client(api, "Brand New Co")
    confirm = api.post(
        "/imports/tally-json/confirm", data={"client_id": client_id, "preview_token": preview["preview_token"]}
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["imported"] == 3

    # same company again, as a new client: a warning naming the existing client
    again = api.post("/imports/tally-json/preview", files=upload(files())).json()
    assert again["company"]["client_name"] == "Brand New Co"
    assert any("already imported for Brand New Co" in w for w in again["warnings"])
    # another (unknown) company's files for this client: flagged
    other = api.post(
        "/imports/tally-json/preview", data={"client_id": client_id}, files=upload(files(COMPANY_B, COMPANY_B))
    ).json()
    assert any("different Tally company (116eb1a3" in w for w in other["warnings"])
    # this company's files for another client: refused
    second = make_client(api, "Someone Else")
    wrong = api.post("/imports/tally-json/preview", data={"client_id": second}, files=upload(files())).json()
    assert any("already imported for Brand New Co, not Someone Else" in b for b in wrong["blockers"])


def test_quarterly_transactions_files_are_merged_and_months_reported(db_session) -> None:
    q1 = stamped([sale("1", "20250410"), sale("2", "20250515"), sale("3", "20250620")], COMPANY_A, 1000)
    q3 = stamped([sale("7", "20251010"), sale("8", "20251215")], COMPANY_A, 2000)
    overlap = [q1[2]]  # the same voucher (same guid) exported again
    pair = [
        ("Master.json", export(stamped(MASTER, COMPANY_A))),
        ("Q1.json", export(q1)),
        ("Q3.json", export([*q3, *overlap])),
    ]
    preview = js.preview_json(db_session, None, pair)
    assert preview["can_import"] is True
    assert preview["would_import"] == 5 and preview["by_fy"][0]["sales_count"] == 5
    assert any("appeared in more than one file" in w for w in preview["warnings"])
    (months,) = preview["months"]
    assert months["covered"] == ["Apr-2025", "May-2025", "Jun-2025", "Oct-2025", "Dec-2025"]
    assert months["missing"][:4] == ["Jul-2025", "Aug-2025", "Sep-2025", "Nov-2025"]
