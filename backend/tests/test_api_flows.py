"""Import, threshold, alert-desk and YTD flows through the API."""

from decimal import Decimal

from tests.test_api import enter_worked_example, make_client, upload


def test_import_sales_register_and_skip_duplicates(api, sample_dir) -> None:
    client_id = make_client(api, "Sharma Traders")
    path = sample_dir / "sharma_traders_sales_register.xlsx"

    preview = upload(api, path, client_id, "sales_register", endpoint="preview").json()
    assert preview["error"] is None
    assert preview["header_row"] == 5
    assert preview["mapping_source"] == "suggested"
    assert preview["totals_ignored"] == 1
    assert preview["would_import"] == preview["rows_found"] > 80

    first = upload(api, path, client_id, "sales_register").json()
    assert first["imported"] == preview["would_import"]
    assert first["duplicates_skipped"] == 0
    assert first["duplicate_file"] is False
    assert first["fys_affected"] == ["2024-25", "2025-26"]

    second = upload(api, path, client_id, "sales_register").json()
    assert second["imported"] == 0
    assert second["duplicates_skipped"] == first["imported"]
    assert second["duplicate_file"] is True
    assert second["alerts_raised"] == 0

    log = api.get("/imports/log", params={"client_id": client_id}).json()
    assert [(row["rows_imported"], row["rows_skipped"]) for row in log] == [
        (0, first["imported"]),
        (first["imported"], 0),
    ]

    figures = {f["fy"]: f for f in api.get(f"/entries/{client_id}").json()}
    # sample data: annual sales less two credit notes of 0.4% each => 99.2% of target
    assert Decimal(figures["2024-25"]["turnover"]) == Decimal("7936000.00")
    assert Decimal(figures["2025-26"]["turnover"]) == Decimal("9920000.00")


def test_saved_mapping_is_reused(api, sample_dir) -> None:
    client_id = make_client(api)
    path = sample_dir / "patel_engineering_works_sales_register.csv"
    upload(api, path, client_id, "sales_register")
    saved = api.get(f"/imports/mapping/{client_id}/sales_register").json()
    assert saved["mapping"]["total_value"] == "Gross Total"

    preview = upload(api, path, client_id, "sales_register", endpoint="preview").json()
    assert preview["mapping_source"] == "saved"
    assert preview["would_import"] == 0  # everything already imported


def test_full_sample_flow_raises_expected_alerts(api, sample_dir) -> None:
    name = "patel_engineering_works"
    client_id = make_client(api, "Patel Engineering Works")
    for suffix, report in (
        ("sales_register.csv", "sales_register"),
        ("purchase_register.csv", "purchase_register"),
    ):
        assert upload(api, sample_dir / f"{name}_{suffix}", client_id, report).status_code == 200
    for fy in ("2024-25", "2025-26"):
        result = upload(
            api,
            sample_dir / f"{name}_profit_loss_fy{fy}.xlsx",
            client_id,
            "profit_loss",
        )
        assert result.json()["fys_affected"] == [fy]

    report = api.get(f"/reports/comparison/{client_id}", params={"fy": "2025-26"}).json()
    rows = {r["key"]: r for r in report["rows"]}
    assert rows["turnover"]["status_label"] == "Significant Decrease"
    assert rows["net_profit"]["sign_change"] == "turned_to_loss"
    assert Decimal(rows["net_profit"]["current"]) == Decimal("-200000.00")

    limits = api.get(f"/reports/comparison/{client_id}", params={"fy": "2024-25"}).json()["limits"]
    crossed = [
        x for x in limits if x["status"] == "crossed" and "44AB - business (standard)" in x["name"]
    ]
    assert len(crossed) == 1
    assert crossed[0]["crossed_on"] is not None
    assert crossed[0]["crossed_voucher_no"].startswith("S-")
    assert (
        "crossed" in crossed[0]["message"]
        and crossed[0]["crossed_voucher_no"] in crossed[0]["message"]
    )

    # the crossing was recorded exactly once, with its date and voucher
    alerts = api.get("/alerts", params={"client_id": client_id, "fy": "2024-25"}).json()
    tax_audit = [a for a in alerts if "44AB - business (standard)" in a["metric_label"]]
    assert [a["new_status"] for a in tax_audit].count("crossed") == 1
    crossing = next(a for a in tax_audit if a["new_status"] == "crossed")
    assert crossing["crossed_voucher_no"] == crossed[0]["crossed_voucher_no"]
    assert crossing["crossed_on"] == crossed[0]["crossed_on"]


def test_changing_band_thresholds_rechecks_all_clients(api) -> None:
    client_id = make_client(api)
    enter_worked_example(api, client_id)  # turnover +25%: significant at the 20% default
    body = {
        "moderate_pct": "5",
        "significant_pct": "30",
        "include_gst_in_turnover": False,
    }
    assert api.put("/thresholds/settings", json=body).status_code == 200
    alerts = api.get("/alerts", params={"client_id": client_id}).json()
    turnover = [a for a in alerts if a["metric"] == "turnover"]
    assert turnover[0]["new_status"] == "moderate_increase"  # newest first
    assert turnover[0]["old_status"] == "significant_increase"

    assert api.put("/thresholds/settings", json={**body, "moderate_pct": "40"}).status_code == 422


def test_limits_can_be_added_edited_and_disabled(api) -> None:
    limits = api.get("/thresholds/limits").json()
    assert len(limits) >= 10
    seeded = [x for x in limits if x["is_default_seed"]]
    assert seeded and all("Verify current limit" in x["description"] for x in seeded)

    new_limit = {"name": "Internal cap", "metric": "sales_turnover", "amount": "500000"}
    created = api.post("/thresholds/limits", json={**new_limit, "approaching_pct": "90"})
    assert created.status_code == 201
    limit_id = created.json()["id"]

    client_id = make_client(api)
    enter_worked_example(api, client_id)
    alerts = api.get("/alerts", params={"client_id": client_id}).json()
    internal = [a for a in alerts if a["metric_label"] == "Limit: Internal cap"]
    assert internal and internal[0]["new_status"] == "crossed"

    edited = api.put(f"/thresholds/limits/{limit_id}", json={**new_limit, "is_enabled": False})
    assert edited.json()["is_enabled"] is False
    report = api.get(f"/reports/comparison/{client_id}", params={"fy": "2025-26"}).json()
    assert "Internal cap" not in [x["name"] for x in report["limits"]]

    assert api.delete(f"/thresholds/limits/{limit_id}").status_code == 204
    assert api.put("/thresholds/limits/9999", json=new_limit).status_code == 404
    assert api.post("/thresholds/limits", json={**new_limit, "amount": "-1"}).status_code == 422


def test_alert_filters_count_and_acknowledge(api) -> None:
    first, second = make_client(api, "One"), make_client(api, "Two")
    enter_worked_example(api, first)
    enter_worked_example(api, second)

    total = api.get("/alerts/count").json()["unacknowledged"]
    assert total > 0
    only_first = api.get("/alerts", params={"client_id": first}).json()
    assert {a["client_name"] for a in only_first} == {"One"}
    critical = api.get("/alerts", params={"severity": "critical"}).json()
    assert critical and all(a["severity"] == "critical" for a in critical)
    assert api.get("/alerts", params={"severity": "bogus"}).status_code == 422

    target = critical[0]
    acked = api.post(f"/alerts/{target['id']}/acknowledge", json={"acknowledged_by": "CA Mehta"})
    assert acked.json()["acknowledged"] is True
    assert acked.json()["acknowledged_by"] == "CA Mehta"
    assert api.get("/alerts/count").json()["unacknowledged"] == total - 1
    open_ids = [a["id"] for a in api.get("/alerts", params={"unacknowledged_only": True}).json()]
    assert target["id"] not in open_ids
    assert api.post("/alerts/9999/acknowledge", json={"acknowledged_by": "x"}).status_code == 404


def test_in_progress_year_compares_like_for_like_months(api) -> None:
    """Apr-Aug 2025 vs Apr-Aug 2024, not five months against a full year."""
    client_id = make_client(api)
    rows = ["Date,Particulars,Voucher Type,Voucher No.,Value,Gross Total"]
    data = [
        ("01-04-2024", 100),
        ("01-08-2024", 100),
        ("01-11-2024", 300),
        ("01-04-2025", 110),
        ("01-08-2025", 110),
    ]
    for i, (day, amount) in enumerate(data):
        rows.append(f"{day},P,Sales,S-{i},{amount},{amount}")
    response = api.post(
        "/imports/confirm",
        data={"client_id": client_id, "report_type": "sales_register"},
        files={"file": ("s.csv", "\n".join(rows).encode())},
    )
    assert response.status_code == 200

    params = {"fy": "2025-26", "as_of": "2025-08-20"}
    report = api.get(f"/reports/comparison/{client_id}", params=params).json()
    assert report["is_ytd"] is True
    assert report["period_label"] == "Apr-Aug"
    turnover = next(r for r in report["rows"] if r["key"] == "turnover")
    assert Decimal(turnover["previous"]) == Decimal(200)  # Apr-Aug 2024 only, not the full 500
    assert Decimal(turnover["current"]) == Decimal(220)
    assert Decimal(turnover["change_pct"]) == Decimal("10.00")
    assert Decimal(turnover["annualised"]) == Decimal("528.00")  # 220 * 12 / 5

    full = api.get(
        f"/reports/comparison/{client_id}",
        params={"fy": "2025-26", "as_of": "2026-06-01"},
    )
    assert full.json()["is_ytd"] is False


def test_gst_setting_switches_turnover_to_gross(api) -> None:
    client_id = make_client(api)
    rows = "Date,Particulars,Voucher Type,Voucher No.,Value,Output CGST,Gross Total\n"
    rows += "01-04-2025,P,Sales,S-1,1000,180,1180\n"
    body = {
        "moderate_pct": "5",
        "significant_pct": "20",
        "include_gst_in_turnover": True,
    }
    assert api.put("/thresholds/settings", json=body).status_code == 200
    files = {"file": ("s.csv", rows.encode())}
    data = {"client_id": client_id, "report_type": "sales_register"}
    assert api.post("/imports/confirm", data=data, files=files).status_code == 200
    figures = api.get(f"/entries/{client_id}").json()
    assert Decimal(figures[0]["turnover"]) == Decimal("1180.00")


def test_summary_and_fy_list(api, sample_dir) -> None:
    sharma = make_client(api, "Sharma Traders")
    quiet = make_client(api, "No Data Yet")
    upload(api, sample_dir / "sharma_traders_sales_register.xlsx", sharma, "sales_register")

    assert "2025-26" in api.get("/reports/fys").json()
    rows = {
        r["client_name"]: r for r in api.get("/reports/summary", params={"fy": "2025-26"}).json()
    }
    assert rows["Sharma Traders"]["status_label"] == "Significant Increase"
    assert Decimal(rows["Sharma Traders"]["change_pct"]) == Decimal("25.00")
    assert rows["Sharma Traders"]["open_alerts"] > 0
    assert rows["No Data Yet"]["status_label"] == "New / No comparison"
    assert rows["No Data Yet"]["open_alerts"] == 0 and quiet
    assert api.get("/reports/summary", params={"fy": "bad"}).status_code == 422
