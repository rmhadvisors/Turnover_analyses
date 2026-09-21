"""Every call from the frontend to the backend REST API lives in this module.

Pages never build URLs or touch the database; they call these functions. Money
comes back from the API as strings and is converted to Decimal here, never float.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from config import BACKEND_URL, REQUEST_TIMEOUT_SECONDS


class ApiError(Exception):
    """The backend refused a request, or could not be reached. `str()` is user-readable."""


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except (ValueError, AttributeError):
        return response.text or response.reason_phrase
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in detail)
    return str(detail)


def _request(method: str, path: str, **kwargs) -> Any:
    try:
        response = httpx.request(
            method, f"{BACKEND_URL}{path}", timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
        )
    except httpx.HTTPError as exc:
        raise ApiError(
            f"Cannot reach the backend at {BACKEND_URL}. Is it running? ({exc.__class__.__name__})"
        ) from exc
    if response.status_code >= 400:
        raise ApiError(_detail(response))
    return response.json() if response.content else None


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def parse_money_input(text: str) -> Decimal | None:
    """Parse what a user typed ('1,00,000', '-2,50,000.50', blank). Raises ValueError."""
    cleaned = text.strip().replace(",", "").replace("₹", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"'{text}' is not a valid amount") from exc


# ------------------------------------------------------------------- health


def health() -> bool:
    try:
        return _request("GET", "/health")["status"] == "ok"
    except ApiError:
        return False


# ------------------------------------------------------------------ clients


def list_clients() -> list[dict]:
    return _request("GET", "/clients")


def create_client(name: str) -> dict:
    return _request("POST", "/clients", json={"name": name})


def rename_client(client_id: int, name: str) -> dict:
    return _request("PUT", f"/clients/{client_id}", json={"name": name})


def delete_client(client_id: int) -> None:
    _request("DELETE", f"/clients/{client_id}")


# ------------------------------------------------------------ manual entries


def list_figures(client_id: int) -> list[dict]:
    return _request("GET", f"/entries/{client_id}")


def save_entry(payload: dict) -> dict:
    """payload: client_id, previous_fy, current_fy and previous_/current_ amounts (str)."""
    body = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in payload.items()}
    return _request("POST", "/entries", json=body)


# ------------------------------------------------------------------ imports


def _upload(path: str, client_id: int, report_type: str, file, extra: dict) -> dict:
    data = {"client_id": str(client_id), "report_type": report_type}
    data.update({k: v for k, v in extra.items() if v not in (None, "")})
    name, content = file
    return _request("POST", path, data=data, files={"file": (name, content)})


def preview_import(client_id: int, report_type: str, file: tuple[str, bytes], mapping=None) -> dict:
    extra = {"mapping": json.dumps(mapping) if mapping else None}
    return _upload("/imports/preview", client_id, report_type, file, extra)


def confirm_import(
    client_id: int,
    report_type: str,
    file: tuple[str, bytes],
    mapping=None,
    fy: str | None = None,
    save_mapping: bool = True,
) -> dict:
    extra = {
        "mapping": json.dumps(mapping) if mapping else None,
        "fy": fy,
        "save_mapping": "true" if save_mapping else "false",
    }
    return _upload("/imports/confirm", client_id, report_type, file, extra)


def import_log(client_id: int | None = None) -> list[dict]:
    params = {"client_id": client_id} if client_id else None
    return _request("GET", "/imports/log", params=params)


# --------------------------------------------------------------- thresholds


def get_settings() -> dict:
    return _request("GET", "/thresholds/settings")


def save_settings(moderate_pct, significant_pct, include_gst: bool) -> dict:
    body = {
        "moderate_pct": str(moderate_pct),
        "significant_pct": str(significant_pct),
        "include_gst_in_turnover": include_gst,
    }
    return _request("PUT", "/thresholds/settings", json=body)


def list_limits() -> list[dict]:
    return _request("GET", "/thresholds/limits")


def create_limit(body: dict) -> dict:
    return _request("POST", "/thresholds/limits", json=body)


def update_limit(limit_id: int, body: dict) -> dict:
    return _request("PUT", f"/thresholds/limits/{limit_id}", json=body)


def delete_limit(limit_id: int) -> None:
    _request("DELETE", f"/thresholds/limits/{limit_id}")


# ------------------------------------------------------------------ reports


def financial_years() -> list[str]:
    return _request("GET", "/reports/fys")


def comparison(client_id: int, fy: str) -> dict:
    return _request("GET", f"/reports/comparison/{client_id}", params={"fy": fy})


def summary(fy: str) -> list[dict]:
    return _request("GET", "/reports/summary", params={"fy": fy})


# ------------------------------------------------------------------- alerts


def list_alerts(
    client_id: int | None = None,
    fy: str | None = None,
    severity: str | None = None,
    unacknowledged_only: bool = False,
) -> list[dict]:
    params = {
        "client_id": client_id,
        "fy": fy,
        "severity": severity,
        "unacknowledged_only": "true" if unacknowledged_only else None,
    }
    return _request("GET", "/alerts", params={k: v for k, v in params.items() if v is not None})


def unacknowledged_count() -> int:
    return _request("GET", "/alerts/count")["unacknowledged"]


def acknowledge_alert(alert_id: int, by: str) -> dict:
    return _request("POST", f"/alerts/{alert_id}/acknowledge", json={"acknowledged_by": by})


def recheck_all() -> int:
    return _request("POST", "/alerts/recheck")["alerts_raised"]


# ------------------------------------------------------------ chart + exports


def monthly(client_id: int, fy: str) -> dict:
    return _request("GET", f"/reports/monthly/{client_id}", params={"fy": fy})


def _download(path: str, params: dict) -> bytes:
    try:
        response = httpx.get(f"{BACKEND_URL}{path}", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise ApiError(
            f"Cannot reach the backend at {BACKEND_URL}. ({exc.__class__.__name__})"
        ) from exc
    if response.status_code >= 400:
        raise ApiError(_detail(response))
    return response.content


def export_report(client_id: int, fy: str, fmt: str, unit: str) -> bytes:
    """The client comparison report as .xlsx or .pdf bytes."""
    return _download(f"/reports/export/{client_id}", {"fy": fy, "format": fmt, "unit": unit})


def export_summary(fy: str, unit: str) -> bytes:
    return _download("/reports/summary/export", {"fy": fy, "unit": unit})
