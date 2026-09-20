"""End-to-end API tests: clients, manual entry, imports, thresholds and alerts."""

from decimal import Decimal


def make_client(api, name="Acme Traders") -> int:
    response = api.post("/clients", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def enter_worked_example(api, client_id: int):
    return api.post(
        "/entries",
        json={
            "client_id": client_id,
            "previous_fy": "2024-25",
            "current_fy": "2025-26",
            "previous_turnover": "8000000",
            "current_turnover": "10000000",
            "previous_gross_profit": "1600000",
            "current_gross_profit": "1800000",
            "previous_net_profit": "700000",
            "current_net_profit": "500000",
        },
    )


def upload(api, path, client_id, report_type, endpoint="confirm", **extra):
    with open(path, "rb") as fh:
        return api.post(
            f"/imports/{endpoint}",
            data={"client_id": client_id, "report_type": report_type, **extra},
            files={"file": (path.name, fh)},
        )


# ------------------------------------------------------------------ clients


def test_client_crud_and_unique_name(api) -> None:
    client_id = make_client(api)
    assert api.post("/clients", json={"name": "  Acme   Traders "}).status_code == 409
    assert api.post("/clients", json={"name": "   "}).status_code == 422
    assert api.put(f"/clients/{client_id}", json={"name": "Acme Ltd"}).json()["name"] == "Acme Ltd"
    assert [c["name"] for c in api.get("/clients").json()] == ["Acme Ltd"]
    assert api.delete(f"/clients/{client_id}").status_code == 204
    assert api.get(f"/clients/{client_id}").status_code == 404


# ------------------------------------------------------------ manual entry


def test_worked_example_through_the_api(api) -> None:
    client_id = make_client(api)
    result = enter_worked_example(api, client_id)
    assert result.status_code == 200

    report = api.get(f"/reports/comparison/{client_id}", params={"fy": "2025-26"}).json()
    rows = {r["key"]: r for r in report["rows"]}
    assert Decimal(rows["turnover"]["change_pct"]) == Decimal("25.00")
    assert Decimal(rows["turnover"]["difference"]) == Decimal("2000000.00")
    assert rows["turnover"]["status_label"] == "Significant Increase"
    assert Decimal(rows["gross_profit"]["change_pct"]) == Decimal("12.50")
    assert rows["gross_profit"]["status_label"] == "Moderate Increase"
    assert Decimal(rows["net_profit"]["change_pct"]) == Decimal("-28.57")
    assert rows["net_profit"]["direction"] == "down"
    assert rows["net_profit"]["status_label"] == "Significant Decrease"
    assert rows["purchases"]["status_label"] == "New / No comparison"


def test_manual_entry_validation(api) -> None:
    client_id = make_client(api)
    bad_pair = {
        "client_id": client_id,
        "previous_fy": "2023-24",
        "current_fy": "2025-26",
    }
    assert api.post("/entries", json=bad_pair).status_code == 422
    assert api.post("/entries", json={**bad_pair, "previous_fy": "2024/25"}).status_code == 422
    missing = {"client_id": 999, "previous_fy": "2024-25", "current_fy": "2025-26"}
    assert api.post("/entries", json=missing).status_code == 404


def test_alerts_raised_only_when_status_changes(api) -> None:
    client_id = make_client(api)
    first = enter_worked_example(api, client_id).json()
    assert first["alerts_raised"] >= 2  # turnover + net profit (+ gross profit moderate)

    again = enter_worked_example(api, client_id).json()
    assert again["alerts_raised"] == 0  # identical data: nothing new

    alerts = api.get("/alerts", params={"client_id": client_id, "fy": "2025-26"}).json()
    by_metric = {a["metric"]: a for a in alerts if not a["metric"].startswith("limit:")}
    assert by_metric["turnover"]["old_status"] == "normal"
    assert by_metric["turnover"]["new_status"] == "significant_increase"
    assert by_metric["turnover"]["severity"] == "critical"

    # turnover falls back into the Normal band -> one new "back to normal" alert
    api.post(
        "/entries",
        json={
            "client_id": client_id,
            "previous_fy": "2024-25",
            "current_fy": "2025-26",
            "current_turnover": "8100000",
        },
    )
    turnover = [
        a
        for a in api.get("/alerts", params={"client_id": client_id}).json()
        if a["metric"] == "turnover"
    ]
    assert next(a["new_status"] for a in turnover) == "normal"
    assert len(turnover) == 2


def test_manual_turnover_against_absolute_limits(api) -> None:
    client_id = make_client(api)
    enter_worked_example(api, client_id)  # turnover is exactly Rs 1 crore
    alerts = api.get("/alerts", params={"client_id": client_id, "fy": "2025-26"}).json()
    limits = {
        a["metric_label"]: a["new_status"] for a in alerts if a["metric"].startswith("limit:")
    }
    assert limits["Limit: GST registration - goods (regular states)"] == "crossed"
    # exactly at the Rs 1 crore 44AB limit is "approaching": crossing means exceeding it
    assert limits["Limit: Tax audit u/s 44AB - business (standard)"] == "approaching"
    assert "Limit: E-invoicing applicability" not in limits  # 20% of the limit: still below


def test_bad_uploads_are_rejected_cleanly(api) -> None:
    client_id = make_client(api)
    data = {"client_id": client_id, "report_type": "sales_register"}
    assert (
        api.post(
            "/imports/confirm", data=data, files={"file": ("notes.docx", b"hello")}
        ).status_code
        == 422
    )
    no_header = {"file": ("empty.csv", b"just,some\ntext,here\n")}
    assert api.post("/imports/confirm", data=data, files=no_header).status_code == 422
    bad_type = {"client_id": client_id, "report_type": "nonsense"}
    assert (
        api.post("/imports/confirm", data=bad_type, files={"file": ("x.csv", b"a,b")}).status_code
        == 422
    )
    missing = {"client_id": 999, "report_type": "sales_register"}
    assert (
        api.post("/imports/confirm", data=missing, files={"file": ("x.csv", b"a,b")}).status_code
        == 404
    )
