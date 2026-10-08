"""One-call endpoints used by the frontend (sidebar, dashboard, client report) and
the preview-token import flow."""

from app.services import tally_json as tj
from tests.test_api import make_client
from tests.test_tally_json import MASTER, export, sample_files, upload_json


def test_workspace_dashboard_and_client_report(api) -> None:
    client_id = make_client(api, "Batch Co")
    other_id = make_client(api, "No Data Co")
    upload_json(api, client_id, sample_files())

    workspace = api.get("/workspace").json()
    assert {c["name"] for c in workspace["clients"]} == {"Batch Co", "No Data Co"}
    assert workspace["fys"] == api.get("/reports/fys").json() == ["2025-26"]
    assert workspace["unacknowledged_alerts"] == api.get("/alerts/count").json()["unacknowledged"]

    dashboard = api.get("/reports/dashboard", params={"fy": "2025-26"}).json()
    assert dashboard["rows"] == api.get("/reports/summary", params={"fy": "2025-26"}).json()
    assert {r["client_id"] for r in dashboard["rows"]} == {client_id, other_id}
    open_alerts = api.get("/alerts", params={"fy": "2025-26", "unacknowledged_only": "true"}).json()
    by_client = {row["client_id"]: row for row in dashboard["open_alerts"]}
    for alert in open_alerts:
        assert by_client[alert["client_id"]][alert["severity"]] >= 1
    total = sum(r["critical"] + r["warning"] + r["info"] for r in dashboard["open_alerts"])
    assert total == len(open_alerts)

    report = api.get(f"/reports/client/{client_id}", params={"fy": "2025-26"}).json()
    params = {"fy": "2025-26"}
    assert report["comparison"] == api.get(f"/reports/comparison/{client_id}", params=params).json()
    assert report["monthly"] == api.get(f"/reports/monthly/{client_id}", params=params).json()
    assert report["alerts"] == api.get("/alerts", params={"client_id": client_id, **params}).json()
    assert api.get("/reports/client/999", params=params).status_code == 404


def test_confirm_with_preview_token_needs_no_reupload(api) -> None:
    client_id = make_client(api, "Token Co")
    preview = upload_json(api, client_id, sample_files(), "preview").json()
    assert preview["preview_token"] and preview["fys_already_imported"] == []

    wrong_client = make_client(api, "Other Co")
    refused = api.post(
        "/imports/tally-json/confirm",
        data={"client_id": wrong_client, "preview_token": preview["preview_token"]},
    )
    assert refused.status_code == 422

    preview = upload_json(api, client_id, sample_files(), "preview").json()
    done = api.post(
        "/imports/tally-json/confirm",
        data={"client_id": client_id, "preview_token": preview["preview_token"]},
    ).json()
    assert done["imported"] == 2
    reused = api.post(  # a token is used once
        "/imports/tally-json/confirm",
        data={"client_id": client_id, "preview_token": preview["preview_token"]},
    )
    assert reused.status_code == 422 and "expired" in reused.json()["detail"]

    again = upload_json(api, client_id, sample_files(), "preview").json()
    assert again["fys_already_imported"] == ["2025-26"] and again["would_import"] == 0
    assert api.post("/imports/tally-json/confirm", data={"client_id": client_id}).status_code == 422
    assert api.get("/imports/tally-json/progress/unknown").status_code == 404


def test_broken_trailer_after_complete_record_list_is_not_a_cut_off() -> None:
    text = export(MASTER).decode("utf-16").rstrip()
    assert text.endswith("}") and text[:-1].rstrip().endswith("]")
    result = tj.read_records(text[:-1].encode("utf-8"), "Master.json")  # closing '}' lost
    assert not result.truncated and len(result.records) == len(MASTER)


def test_coverage_and_replace_or_skip_a_year(api) -> None:
    client_id = make_client(api, "Cover Co")
    assert [r for r in api.get("/coverage").json() if r["client_id"] == client_id] == []
    upload_json(api, client_id, sample_files())
    (row,) = [r for r in api.get("/coverage").json() if r["client_id"] == client_id]
    assert row == {
        "client_id": client_id, "fy": "2025-26", "sales_vouchers": 1, "purchase_vouchers": 1,
        "figures": "imported", "has_profit": False, "profit_source": None,
    }  # fmt: skip

    preview = upload_json(api, client_id, sample_files(), "preview").json()
    assert preview["existing_vouchers"] == {"2025-26": 2}

    skipped = api.post(
        "/imports/tally-json/confirm",
        data={"client_id": client_id, "preview_token": preview["preview_token"], "skip_fys": "2025-26"},
    ).json()
    assert skipped["imported"] == 0 and skipped["skipped_fys"] == ["2025-26"]
    assert skipped["import_log_ids"] == []

    preview = upload_json(api, client_id, sample_files(), "preview").json()
    replaced = api.post(
        "/imports/tally-json/confirm",
        data={"client_id": client_id, "preview_token": preview["preview_token"], "replace_fys": "2025-26"},
    ).json()
    assert replaced["replaced"] == {"2025-26": 2}
    assert replaced["imported"] == 2 and replaced["duplicates_skipped"] == 0
    (row,) = [r for r in api.get("/coverage").json() if r["client_id"] == client_id]
    assert (row["sales_vouchers"], row["purchase_vouchers"]) == (1, 1)  # replaced, not doubled
    figures = api.get(f"/entries/{client_id}").json()[0]
    assert figures["turnover"] is not None and figures["purchases"] is not None

    bad = api.post(
        "/imports/tally-json/confirm",
        data={"client_id": client_id, "preview_token": "x", "replace_fys": "2025"},
    )
    assert bad.status_code == 422


def test_manual_entry_leaves_an_untyped_year_as_imported(api) -> None:
    client_id = make_client(api, "Entry Co")
    upload_json(api, client_id, sample_files())  # FY 2025-26 figures come from the import
    saved = api.post(
        "/entries",
        json={"client_id": client_id, "previous_fy": "2025-26", "current_fy": "2026-27",
              "current_turnover": "500"},
    )  # fmt: skip
    assert saved.status_code < 300, saved.text
    rows = {r["fy"]: r for r in api.get("/coverage").json() if r["client_id"] == client_id}
    assert rows["2025-26"]["figures"] == "imported"
    assert rows["2026-27"]["figures"] == "manual"
