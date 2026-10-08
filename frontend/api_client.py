"""Every call from the frontend to the backend REST API lives in this module.

Pages never build URLs or touch the database; they call these functions. Money
comes back from the API as strings and is converted to Decimal here, never float.

Speed: one shared HTTP connection pool is reused for every call, and read calls are
cached (st.cache_data, keyed by their arguments such as client and FY). Every write
function clears the caches it can affect, so pages never show stale data after a
save, import, acknowledgement or threshold change made through the app.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from typing import IO, Any

import httpx
import streamlit as st

from config import (
    BACKEND_URL,
    CACHE_TTL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    UPLOAD_TIMEOUT_SECONDS,
)

# Thread-safe; shared by every Streamlit session. Creating a client per call cost
# ~200 ms each time (it rebuilds its TLS context even for plain http).
_http = httpx.Client(
    base_url=BACKEND_URL,
    timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=5.0),
)
_UPLOAD_TIMEOUT = httpx.Timeout(UPLOAD_TIMEOUT_SECONDS, connect=5.0)


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


def _send(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = _http.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(
            f"Cannot reach the backend at {BACKEND_URL}. Is it running? ({exc.__class__.__name__})"
        ) from exc
    if response.status_code >= 400:
        raise ApiError(_detail(response))
    return response


def _request(method: str, path: str, **kwargs) -> Any:
    response = _send(method, path, **kwargs)
    return response.json() if response.content else None


# ------------------------------------------------------------------ caching

# Cache groups: a write clears every cached read in the groups it touches.
CLIENTS, FIGURES, ALERTS, THRESHOLDS, IMPORTS = "clients", "figures", "alerts", "thresholds", "imports"
TDS = "tds"  # rate master, ledger mapping and the party-wise TDS analysis
_GROUPS: dict[str, list] = {}


def _cached(*groups: str):
    def wrap(function):
        cached = st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)(function)
        for group in groups:
            _GROUPS.setdefault(group, []).append(cached)
        return cached

    return wrap


def _invalidate(*groups: str) -> None:
    for group in groups:
        for cached in _GROUPS.get(group, []):
            cached.clear()


def clear_all_caches() -> None:
    _invalidate(*_GROUPS)


# ------------------------------------------------------------------ helpers


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


# ---------------------------------------------------------------- workspace


@_cached(CLIENTS, FIGURES, ALERTS)
def workspace() -> dict:
    """Sidebar data in one call: clients, FYs with data (newest first), open alert count."""
    return _request("GET", "/workspace")


@_cached(CLIENTS, FIGURES, IMPORTS)
def coverage() -> list[dict]:
    """Per client and FY: sales/purchase vouchers stored and where yearly figures came from."""
    return _request("GET", "/coverage")


# ------------------------------------------------------------------ clients


@_cached(CLIENTS)
def list_clients() -> list[dict]:
    return _request("GET", "/clients")


def create_client(name: str) -> dict:
    result = _request("POST", "/clients", json={"name": name})
    _invalidate(CLIENTS)
    return result


def rename_client(client_id: int, name: str) -> dict:
    result = _request("PUT", f"/clients/{client_id}", json={"name": name})
    _invalidate(CLIENTS)
    return result


def delete_client(client_id: int) -> None:
    _request("DELETE", f"/clients/{client_id}")
    clear_all_caches()


@_cached(CLIENTS)
def get_profile(client_id: int) -> dict:
    """Client profile: GSTIN facts and the choices that decide which limits apply."""
    return _request("GET", f"/clients/{client_id}/profile")


def save_profile(client_id: int, body: dict) -> dict:
    """Save the profile; the backend re-checks the client, closing alerts that no longer apply."""
    result = _request("PUT", f"/clients/{client_id}/profile", json=body)
    _invalidate(CLIENTS, ALERTS, FIGURES)
    return result


# ------------------------------------------------------------ manual entries


@_cached(FIGURES)
def list_figures(client_id: int) -> list[dict]:
    return _request("GET", f"/entries/{client_id}")


def save_entry(payload: dict) -> dict:
    """payload: client_id, previous_fy, current_fy and previous_/current_ amounts (str)."""
    body = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in payload.items()}
    result = _request("POST", "/entries", json=body)
    _invalidate(FIGURES, ALERTS)
    return result


# ------------------------------------------------------------------ imports


def _upload(path: str, client_id: int, report_type: str, file, extra: dict) -> dict:
    data = {"client_id": str(client_id), "report_type": report_type}
    data.update({k: v for k, v in extra.items() if v not in (None, "")})
    name, content = file
    return _request(
        "POST", path, data=data, files={"file": (name, content)}, timeout=_UPLOAD_TIMEOUT
    )


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
    result = _upload("/imports/confirm", client_id, report_type, file, extra)
    _invalidate(FIGURES, ALERTS, IMPORTS)
    return result


@_cached(IMPORTS, CLIENTS)
def import_log(client_id: int | None = None) -> list[dict]:
    params = {"client_id": client_id} if client_id else None
    return _request("GET", "/imports/log", params=params)


# --------------------------------------------------------------- thresholds


@_cached(THRESHOLDS)
def get_settings() -> dict:
    return _request("GET", "/thresholds/settings")


def save_settings(moderate_pct, significant_pct, include_gst: bool) -> dict:
    body = {
        "moderate_pct": str(moderate_pct),
        "significant_pct": str(significant_pct),
        "include_gst_in_turnover": include_gst,
    }
    result = _request("PUT", "/thresholds/settings", json=body)
    _invalidate(THRESHOLDS, ALERTS)
    return result


@_cached(THRESHOLDS)
def list_limits() -> list[dict]:
    return _request("GET", "/thresholds/limits")


def create_limit(body: dict) -> dict:
    result = _request("POST", "/thresholds/limits", json=body)
    _invalidate(THRESHOLDS, ALERTS)
    return result


def update_limit(limit_id: int, body: dict) -> dict:
    result = _request("PUT", f"/thresholds/limits/{limit_id}", json=body)
    _invalidate(THRESHOLDS, ALERTS)
    return result


def delete_limit(limit_id: int) -> None:
    _request("DELETE", f"/thresholds/limits/{limit_id}")
    _invalidate(THRESHOLDS, ALERTS)


# ------------------------------------------------------------------ reports
# Reports combine figures, alerts, thresholds and client names, so they are in every group.


@_cached(FIGURES)
def financial_years() -> list[str]:
    return _request("GET", "/reports/fys")


@_cached(CLIENTS, FIGURES, ALERTS, THRESHOLDS)
def comparison(client_id: int, fy: str) -> dict:
    return _request("GET", f"/reports/comparison/{client_id}", params={"fy": fy})


@_cached(CLIENTS, FIGURES, ALERTS, THRESHOLDS)
def summary(fy: str) -> list[dict]:
    return _request("GET", "/reports/summary", params={"fy": fy})


@_cached(CLIENTS, FIGURES, ALERTS, THRESHOLDS)
def dashboard(fy: str) -> dict:
    """All-clients summary rows plus open alert counts by client and severity."""
    return _request("GET", "/reports/dashboard", params={"fy": fy})


@_cached(CLIENTS, FIGURES, ALERTS, THRESHOLDS)
def client_report(client_id: int, fy: str) -> dict:
    """{comparison, monthly, alerts} for one client and FY."""
    return _request("GET", f"/reports/client/{client_id}", params={"fy": fy})


# ------------------------------------------------------------------- alerts


@_cached(CLIENTS, ALERTS)
def list_alerts(
    client_id: int | None = None,
    fy: str | None = None,
    severity: str | None = None,
    unacknowledged_only: bool = False,
    kind: str | None = None,
    section: str | None = None,
    sort: str = "recent",
) -> list[dict]:
    """`kind`: 'tds' or 'turnover' (None = both); `section`: a TDS section key;
    `sort`: 'recent' or 'at_stake' (TDS money at stake, largest first)."""
    params = {
        "client_id": client_id,
        "fy": fy,
        "severity": severity,
        "unacknowledged_only": "true" if unacknowledged_only else None,
        "kind": kind,
        "section": section,
        "sort": sort,
    }
    return _request("GET", "/alerts", params={k: v for k, v in params.items() if v is not None})


@_cached(ALERTS)
def unacknowledged_count() -> int:
    return _request("GET", "/alerts/count")["unacknowledged"]


def acknowledge_alerts(alert_ids: list[int], by: str) -> None:
    """Acknowledge several alerts, then clear the alert caches once."""
    try:
        for alert_id in alert_ids:
            _request("POST", f"/alerts/{alert_id}/acknowledge", json={"acknowledged_by": by})
    finally:
        _invalidate(ALERTS)


def acknowledge_alert(alert_id: int, by: str) -> None:
    acknowledge_alerts([alert_id], by)


def recheck_all() -> int:
    result = _request("POST", "/alerts/recheck")["alerts_raised"]
    _invalidate(ALERTS, TDS)
    return result


# ------------------------------------------------------------ chart + exports


@_cached(CLIENTS, FIGURES, THRESHOLDS)
def monthly(client_id: int, fy: str) -> dict:
    return _request("GET", f"/reports/monthly/{client_id}", params={"fy": fy})


def _download(path: str, params: dict) -> bytes:
    return _send("GET", path, params=params).content


def export_report(client_id: int, fy: str, fmt: str, unit: str) -> bytes:
    """The client comparison report as .xlsx or .pdf bytes."""
    return _download(f"/reports/export/{client_id}", {"fy": fy, "format": fmt, "unit": unit})


def export_summary(fy: str, unit: str) -> bytes:
    return _download("/reports/summary/export", {"fy": fy, "unit": unit})


# ------------------------------------------------------- Tally JSON export

ProgressFn = Callable[[str, float], None]  # (stage, fraction 0..1)


class _CountingFile:
    """Wraps an upload so the bytes sent can be shown as progress."""

    def __init__(self, file: IO[bytes], counter: list[int]):
        self._file = file
        self._counter = counter

    def read(self, size: int = -1) -> bytes:
        chunk = self._file.read(size)
        self._counter[0] += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()


def _file_size(file: IO[bytes]) -> int:
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    return size


def preview_tally_json(
    client_id: int,
    files: list[tuple[str, IO[bytes]]],
    on_progress: ProgressFn | None = None,
) -> dict:
    """Dry run for a Tally JSON export (Master + Transactions files sent together).

    Files are streamed, not copied into memory. `on_progress(stage, fraction)` is
    called about three times a second: stage "uploading", then "reading"/"parsing" as
    reported by the backend. The result's `preview_token` confirms without re-uploading.
    """
    token = uuid.uuid4().hex
    sent = [0]
    total = sum(_file_size(file) for _, file in files) or 1
    parts = [("files", (name, _CountingFile(file, sent))) for name, file in files]
    data = {"client_id": str(client_id), "progress_token": token}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _request,
            "POST",
            "/imports/tally-json/preview",
            data=data,
            files=parts,
            timeout=_UPLOAD_TIMEOUT,
        )
        while not future.done():
            time.sleep(0.3)
            if on_progress is None:
                continue
            if sent[0] < total:
                on_progress("uploading", sent[0] / total)
                continue
            try:
                state = _request("GET", f"/imports/tally-json/progress/{token}")
            except ApiError:
                continue  # not started yet, or just finished
            on_progress(state["stage"], state["fraction"])
        return future.result()


def confirm_tally_json(
    client_id: int,
    preview_token: str,
    replace_fys: list[str] | None = None,
    skip_fys: list[str] | None = None,
) -> dict:
    """Import what an earlier preview found (the backend kept the parsed result).
    `replace_fys` delete those years' earlier imported vouchers first; `skip_fys` are left alone."""
    data = {
        "client_id": str(client_id),
        "preview_token": preview_token,
        "replace_fys": ",".join(replace_fys or []),
        "skip_fys": ",".join(skip_fys or []),
    }
    result = _request("POST", "/imports/tally-json/confirm", data=data, timeout=_UPLOAD_TIMEOUT)
    _invalidate(FIGURES, ALERTS, IMPORTS, TDS)
    return result


# ---------------------------------------------------------------------- TDS
# Reads also sit in FIGURES: who must deduct depends on last year's turnover.


@_cached(TDS)
def tds_sections() -> dict:
    """{note, sections}: the rate & threshold master with effective-from dates."""
    return _request("GET", "/tds/sections")


def _tds_write(method: str, path: str, **kwargs) -> Any:
    try:
        return _request(method, path, **kwargs)
    finally:
        _invalidate(TDS, ALERTS)


def create_tds_section(body: dict) -> dict:
    return _tds_write("POST", "/tds/sections", json=body)


def update_tds_section(section_id: int, body: dict) -> dict:
    return _tds_write("PUT", f"/tds/sections/{section_id}", json=body)


def delete_tds_section(section_id: int) -> None:
    _tds_write("DELETE", f"/tds/sections/{section_id}")


@_cached(TDS, FIGURES)
def tds_settings() -> dict:
    return _request("GET", "/tds/settings")


def save_tds_settings(approaching_pct: Decimal | float, analysis_fy: str, unidentified_min) -> dict:
    """Approaching %, the TDS analysis year (the only year TDS is analysed / alerted) and the
    materiality cut-off for payments with no party ledger."""
    body = {"approaching_pct": str(approaching_pct), "analysis_fy": analysis_fy,
            "unidentified_min": str(unidentified_min)}  # fmt: skip
    result = _tds_write("PUT", "/tds/settings", json=body)
    _invalidate(FIGURES)
    return result


def tds_analysis_fy() -> str:
    try:
        return tds_settings()["analysis_fy"]
    except ApiError:
        return ""


def save_tds_assignment(client_id: int, body: dict) -> dict:
    """Assign a payee to a ledger's party-less payments (or remove it with remove=True)."""
    return _tds_write("PUT", f"/tds/clients/{client_id}/assignment", json=body)


@_cached(TDS, FIGURES, IMPORTS)
def tds_completeness(client_id: int, fy: str) -> dict:
    """Every expense and purchase ledger with postings, and what happened to it."""
    return _request("GET", f"/tds/clients/{client_id}/completeness", params={"fy": fy})


@_cached(TDS, IMPORTS, CLIENTS)
def tds_data_quality(client_id: int | None = None) -> list[dict]:
    """Parties whose PAN contradicts their name (one client, or every client)."""
    path = f"/tds/clients/{client_id}/data-quality" if client_id else "/tds/data-quality"
    return _request("GET", path)


@_cached(TDS, FIGURES, IMPORTS)
def tds_mapping(client_id: int) -> dict:
    """Every ledger rule of the client with its role, section and amounts per FY."""
    return _request("GET", f"/tds/clients/{client_id}/mapping")


def update_tds_mapping(client_id: int, changes: list[dict]) -> dict:
    return _tds_write("PUT", f"/tds/clients/{client_id}/mapping", json={"changes": changes})


def approve_tds_mapping(client_id: int, by: str, approved: bool = True) -> dict:
    body = {"approved_by": by, "approved": approved}
    return _tds_write("POST", f"/tds/clients/{client_id}/mapping/approve", json=body)


def refresh_tds_mapping(client_id: int) -> dict:
    return _tds_write("POST", f"/tds/clients/{client_id}/mapping/refresh")


@_cached(TDS, FIGURES, IMPORTS)
def tds_payer(client_id: int, fy: str) -> dict:
    return _request("GET", f"/tds/clients/{client_id}/payer", params={"fy": fy})


def save_tds_payer(client_id: int, fy: str, constitution: str | None, audit_liable: bool | None) -> dict:
    body = {"fy": fy, "constitution": constitution, "audit_liable": audit_liable}
    return _tds_write("PUT", f"/tds/clients/{client_id}/payer", json=body)


@_cached(TDS, FIGURES, IMPORTS, CLIENTS)
def tds_report(client_id: int, fy: str) -> dict:
    return _request("GET", f"/tds/clients/{client_id}/report", params={"fy": fy})


def export_tds_report(client_id: int, fy: str, fmt: str) -> bytes:
    return _download(f"/tds/clients/{client_id}/report/export", {"fy": fy, "format": fmt})


@_cached(TDS, FIGURES, IMPORTS, ALERTS)
def tds_detail(client_id: int, fy: str, party_key: str, section: str) -> dict:
    params = {"fy": fy, "party_key": party_key, "section": section}
    return _request("GET", f"/tds/clients/{client_id}/detail", params=params)


@_cached(TDS, FIGURES, IMPORTS, ALERTS)
def tds_alert_detail(alert_id: int) -> dict:
    return _request("GET", f"/tds/alerts/{alert_id}/detail")


def export_tds_detail(client_id: int, fy: str, party_key: str, section: str, fmt: str) -> bytes:
    params = {"fy": fy, "party_key": party_key, "section": section, "format": fmt}
    return _download(f"/tds/clients/{client_id}/detail/export", params)


def save_tds_party(client_id: int, body: dict) -> dict:
    return _tds_write("PUT", f"/tds/clients/{client_id}/party", json=body)


def save_tds_resolution(client_id: int, body: dict) -> dict:
    payload = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in body.items()}
    return _tds_write("PUT", f"/tds/clients/{client_id}/resolution", json=payload)


def save_tds_voucher_flag(
    client_id: int, voucher_key: str, on: bool, note: str | None = None, flag: str = "tcs_206c1h"
) -> dict:
    """flag 'tcs_206c1h' (leave out of 194Q) or 'section:<key>' (count it under another section)."""
    body = {"voucher_key": voucher_key, "on": on, "note": note, "flag": flag}
    return _tds_write("PUT", f"/tds/clients/{client_id}/voucher-flag", json=body)


def acknowledge_tds_alert(alert_id: int, by: str, note: str | None) -> dict:
    return _tds_write("POST", f"/tds/alerts/{alert_id}/acknowledge", json={"acknowledged_by": by, "note": note})


@_cached(ALERTS, TDS)
def tds_alert_counts(fy: str | None = None) -> dict:
    """Unacknowledged TDS alerts by severity."""
    return _request("GET", "/tds/alert-counts", params={"fy": fy} if fy else None)
