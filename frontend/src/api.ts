/**
 * Every call from the frontend to the backend REST API lives in this module.
 *
 * Pages never build URLs; they call these functions. Money comes back from the API as
 * strings and is sent back as strings, so amounts are never rounded through floats.
 *
 * Reads are cached by React Query under one or more cache groups. Every write function
 * clears the groups it can affect, so pages never show stale data after a save, import,
 * acknowledgement or threshold change made through the app.
 */

import { QueryClient, useQuery } from "@tanstack/react-query";

const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8010").replace(/\/+$/, "");
const CACHE_TTL_MS = 5 * 60 * 1000;

export class ApiError extends Error {}

export type Row = Record<string, any>; // API payloads are untyped JSON, as in the Streamlit app

// ------------------------------------------------------------------ caching

export type Group = "clients" | "figures" | "alerts" | "thresholds" | "imports" | "tds";
const ALL_GROUPS: Group[] = ["clients", "figures", "alerts", "thresholds", "imports", "tds"];

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: CACHE_TTL_MS, retry: false, refetchOnWindowFocus: false } },
});

/** A cached read. `groups` decide which writes clear it. */
export function useApi<T = any>(groups: Group[], key: unknown[], fn: () => Promise<T>, enabled = true) {
  return useQuery<T, ApiError>({ queryKey: [{ groups }, ...key], queryFn: fn, enabled });
}

export function invalidate(...groups: Group[]): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => {
      const head = query.queryKey[0] as { groups?: Group[] } | undefined;
      return !!head?.groups?.some((g) => groups.includes(g));
    },
  });
}

export const clearAllCaches = () => invalidate(...ALL_GROUPS);

// ------------------------------------------------------------------ transport

type Params = Record<string, string | number | boolean | null | undefined>;

function url(path: string, params?: Params): string {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== null && v !== undefined && v !== "") query.set(k, String(v));
  }
  const qs = query.toString();
  return `${BACKEND_URL}${path}${qs ? `?${qs}` : ""}`;
}

function detailOf(status: number, text: string): string {
  try {
    const detail = JSON.parse(text)?.detail ?? text;
    if (Array.isArray(detail)) {
      // FastAPI validation errors
      return detail.map((e: Row) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`).join("; ");
    }
    return String(detail);
  } catch {
    return text || `HTTP ${status}`;
  }
}

async function send(method: string, path: string, opts: { params?: Params; json?: unknown; form?: FormData } = {}) {
  let response: Response;
  try {
    response = await fetch(url(path, opts.params), {
      method,
      headers: opts.json !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.form,
    });
  } catch (exc) {
    throw new ApiError(`Cannot reach the backend at ${BACKEND_URL}. Is it running? (${(exc as Error).name})`);
  }
  if (response.status >= 400) throw new ApiError(detailOf(response.status, await response.text()));
  return response;
}

async function request<T = any>(method: string, path: string, opts: Parameters<typeof send>[2] = {}): Promise<T> {
  const response = await send(method, path, opts);
  const text = await response.text();
  return (text ? JSON.parse(text) : null) as T;
}

function form(data: Record<string, string | null | undefined>): FormData {
  const body = new FormData();
  for (const [k, v] of Object.entries(data)) if (v !== null && v !== undefined && v !== "") body.append(k, v);
  return body;
}

/** Download a backend file (an export) and hand it to the browser. */
export async function download(path: string, params: Params, fileName: string): Promise<void> {
  const blob = await (await send("GET", path, { params })).blob();
  saveBlob(blob, fileName);
}

export function saveBlob(blob: Blob, fileName: string): void {
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

// ------------------------------------------------------------------ helpers

/** A JS number for display and arithmetic shown on screen; null when blank or invalid. */
export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Parse what a user typed ('1,00,000', '-2,50,000.50', blank). Throws on rubbish.
 * Returns a decimal string (never a float) so the backend gets the exact amount. */
export function parseMoneyInput(text: string): string | null {
  const cleaned = text.trim().replace(/[,₹\s]/g, "");
  if (!cleaned) return null;
  if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(cleaned)) throw new ApiError(`'${text}' is not a valid amount`);
  return cleaned;
}

/** Normalised decimal text ('30000.00' -> '30000', '-0' -> '0'), for comparing amounts. */
export function normDecimal(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  const text = String(value).trim().replace(/^\+/, "");
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(text)) return null;
  const negative = text.startsWith("-");
  let [whole, frac = ""] = text.replace("-", "").split(".");
  whole = whole.replace(/^0+(?=\d)/, "") || "0";
  frac = frac.replace(/0+$/, "");
  const body = frac ? `${whole}.${frac}` : whole;
  return negative && body !== "0" ? `-${body}` : body;
}

export const sameAmount = (a: unknown, b: unknown) => normDecimal(a) === normDecimal(b);

// ------------------------------------------------------------------ health + workspace

export const health = async () => {
  try {
    return (await request("GET", "/health")).status === "ok";
  } catch {
    return false;
  }
};

/** Sidebar data in one call: clients, FYs with data (newest first), open alert count. */
export const useWorkspace = () => useApi(["clients", "figures", "alerts"], ["workspace"], () => request("GET", "/workspace"));

/** Per client and FY: sales/purchase vouchers stored and where yearly figures came from. */
export const useCoverage = () => useApi<Row[]>(["clients", "figures", "imports"], ["coverage"], () => request("GET", "/coverage"));

// ------------------------------------------------------------------ clients

export async function createClient(name: string): Promise<Row> {
  const result = await request("POST", "/clients", { json: { name } });
  await invalidate("clients");
  return result;
}

export async function renameClient(clientId: number, name: string): Promise<Row> {
  const result = await request("PUT", `/clients/${clientId}`, { json: { name } });
  await invalidate("clients");
  return result;
}

export async function deleteClient(clientId: number): Promise<void> {
  await request("DELETE", `/clients/${clientId}`);
  await clearAllCaches();
}

/** Client profile: GSTIN facts and the choices that decide which limits apply. */
export const useProfile = (clientId: number) =>
  useApi(["clients"], ["profile", clientId], () => request("GET", `/clients/${clientId}/profile`));

/** Save the profile; the backend re-checks the client, closing alerts that no longer apply. */
export async function saveProfile(clientId: number, body: Row): Promise<Row> {
  const result = await request("PUT", `/clients/${clientId}/profile`, { json: body });
  await invalidate("clients", "alerts", "figures");
  return result;
}

// ------------------------------------------------------------------ manual entries

export const useFigures = (clientId: number) =>
  useApi<Row[]>(["figures"], ["figures", clientId], () => request("GET", `/entries/${clientId}`));

/** payload: client_id, previous_fy, current_fy and previous_/current_ amounts (strings). */
export async function saveEntry(payload: Row): Promise<Row> {
  const result = await request("POST", "/entries", { json: payload });
  await invalidate("figures", "alerts");
  return result;
}

// ------------------------------------------------------------------ register imports

function registerForm(clientId: number, reportType: string, file: File, extra: Record<string, string | null>) {
  const body = form({ client_id: String(clientId), report_type: reportType, ...extra });
  body.append("file", file, file.name);
  return body;
}

export const previewImport = (clientId: number, reportType: string, file: File, mapping?: Row | null) =>
  request("POST", "/imports/preview", {
    form: registerForm(clientId, reportType, file, { mapping: mapping ? JSON.stringify(mapping) : null }),
  });

export async function confirmImport(
  clientId: number,
  reportType: string,
  file: File,
  mapping: Row | null,
  fy: string | null,
  saveMapping: boolean,
): Promise<Row> {
  const result = await request("POST", "/imports/confirm", {
    form: registerForm(clientId, reportType, file, {
      mapping: mapping ? JSON.stringify(mapping) : null,
      fy,
      save_mapping: saveMapping ? "true" : "false",
    }),
  });
  await invalidate("figures", "alerts", "imports");
  return result;
}

export const useImportLog = (clientId: number) =>
  useApi<Row[]>(["imports", "clients"], ["import-log", clientId], () =>
    request("GET", "/imports/log", { params: { client_id: clientId } }),
  );

// ------------------------------------------------------------------ thresholds

export const useSettings = () => useApi(["thresholds"], ["settings"], () => request("GET", "/thresholds/settings"));

export async function saveSettings(moderatePct: string, significantPct: string, includeGst: boolean): Promise<Row> {
  const result = await request("PUT", "/thresholds/settings", {
    json: { moderate_pct: moderatePct, significant_pct: significantPct, include_gst_in_turnover: includeGst },
  });
  await invalidate("thresholds", "alerts");
  return result;
}

/** Refuse a Tally import when more than this % of its voucher value is on ledgers missing from the Master. */
export async function saveImportSettings(maxUnmatchedLedgerPct: string): Promise<Row> {
  const result = await request("PUT", "/thresholds/import-settings", { json: { max_unmatched_ledger_pct: maxUnmatchedLedgerPct } });
  await invalidate("thresholds");
  return result;
}

export const useLimits = () => useApi<Row[]>(["thresholds"], ["limits"], () => request("GET", "/thresholds/limits"));

export async function createLimit(body: Row): Promise<Row> {
  const result = await request("POST", "/thresholds/limits", { json: body });
  await invalidate("thresholds", "alerts");
  return result;
}

export async function updateLimit(limitId: number, body: Row): Promise<Row> {
  const result = await request("PUT", `/thresholds/limits/${limitId}`, { json: body });
  await invalidate("thresholds", "alerts");
  return result;
}

export async function deleteLimit(limitId: number): Promise<void> {
  await request("DELETE", `/thresholds/limits/${limitId}`);
  await invalidate("thresholds", "alerts");
}

// ------------------------------------------------------------------ reports

const REPORT_GROUPS: Group[] = ["clients", "figures", "alerts", "thresholds"];

/** All-clients summary rows plus open alert counts by client and severity. */
export const useDashboard = (fy: string) =>
  useApi(REPORT_GROUPS, ["dashboard", fy], () => request("GET", "/reports/dashboard", { params: { fy } }), !!fy);

/** {comparison, monthly, alerts, tds} for one client and FY. */
export const useClientReport = (clientId: number, fy: string) =>
  useApi(
    REPORT_GROUPS,
    ["client-report", clientId, fy],
    () => request("GET", `/reports/client/${clientId}`, { params: { fy } }),
    !!fy,
  );

export const exportReport = (clientId: number, fy: string, fmt: string, unit: string, fileName: string) =>
  download(`/reports/export/${clientId}`, { fy, format: fmt, unit }, fileName);

export const exportSummary = (fy: string, unit: string, fileName: string) =>
  download("/reports/summary/export", { fy, unit }, fileName);

// ------------------------------------------------------------------ alerts

export interface AlertFilters {
  client_id?: number | null;
  fy?: string | null;
  severity?: string | null;
  unacknowledged_only?: boolean;
  kind?: string | null;
  section?: string | null;
  sort?: string;
}

export const useAlerts = (f: AlertFilters) =>
  useApi<Row[]>(["clients", "alerts"], ["alerts", f], () =>
    request("GET", "/alerts", {
      params: { ...f, unacknowledged_only: f.unacknowledged_only ? "true" : null, sort: f.sort ?? "recent" },
    }),
  );

/** Acknowledge several alerts, then clear the alert caches once. */
export async function acknowledgeAlerts(alertIds: number[], by: string): Promise<void> {
  try {
    for (const id of alertIds) await request("POST", `/alerts/${id}/acknowledge`, { json: { acknowledged_by: by } });
  } finally {
    await invalidate("alerts");
  }
}

// ------------------------------------------------------------------ Tally JSON export

export type ProgressFn = (stage: string, fraction: number) => void;

/** Dry run for a Tally JSON export (Master + Transactions files sent together).
 * `onProgress(stage, fraction)`: "uploading" while bytes are sent, then "reading"/"parsing"
 * as reported by the backend. The result's `preview_token` confirms without re-uploading;
 * `blockers` lists why the files must not be imported. `clientId` null: a client not created
 * yet, checked against the GSTIN / PAN typed for it instead of a saved profile. */
export function previewTallyJson(
  clientId: number | null,
  files: File[],
  onProgress?: ProgressFn,
  expected: { gstin?: string | null; pan?: string | null } = {},
): Promise<Row> {
  const token = crypto.randomUUID().replace(/-/g, "");
  const body = form({
    client_id: clientId === null ? null : String(clientId),
    progress_token: token,
    expected_gstin: expected.gstin,
    expected_pan: expected.pan,
  });
  for (const file of files) body.append("files", file, file.name);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let uploaded = false;
    let poller: ReturnType<typeof setInterval> | undefined;
    const stop = () => poller && clearInterval(poller);

    xhr.open("POST", url("/imports/tally-json/preview"));
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.("uploading", e.loaded / e.total);
    xhr.upload.onload = () => {
      uploaded = true;
      poller = setInterval(async () => {
        if (!uploaded) return;
        try {
          const state = await request("GET", `/imports/tally-json/progress/${token}`);
          onProgress?.(state.stage, state.fraction);
        } catch {
          // not started yet, or just finished
        }
      }, 300);
    };
    xhr.onload = () => {
      stop();
      if (xhr.status >= 400) reject(new ApiError(detailOf(xhr.status, xhr.responseText)));
      else resolve(JSON.parse(xhr.responseText));
    };
    xhr.onerror = () => {
      stop();
      reject(new ApiError(`Cannot reach the backend at ${BACKEND_URL}. Is it running?`));
    };
    xhr.send(body);
  });
}

/** Import what an earlier preview found (the backend kept the parsed result).
 * `replaceFys` delete those years' earlier imported vouchers first; `skipFys` are left alone. */
export async function confirmTallyJson(clientId: number, previewToken: string, replaceFys: string[], skipFys: string[]) {
  const result = await request("POST", "/imports/tally-json/confirm", {
    form: form({
      client_id: String(clientId),
      preview_token: previewToken,
      replace_fys: replaceFys.join(","),
      skip_fys: skipFys.join(","),
    }),
  });
  await invalidate("figures", "alerts", "imports", "tds");
  return result;
}

// ------------------------------------------------------------------ TDS
// Reads also sit in "figures": who must deduct depends on last year's turnover.

/** {note, sections}: the rate & threshold master with effective-from dates. */
export const useTdsSections = () => useApi(["tds"], ["tds-sections"], () => request("GET", "/tds/sections"));

async function tdsWrite(method: string, path: string, json?: unknown): Promise<any> {
  try {
    return await request(method, path, { json });
  } finally {
    await invalidate("tds", "alerts");
  }
}

export const createTdsSection = (body: Row) => tdsWrite("POST", "/tds/sections", body);
export const updateTdsSection = (id: number, body: Row) => tdsWrite("PUT", `/tds/sections/${id}`, body);
export const deleteTdsSection = (id: number) => tdsWrite("DELETE", `/tds/sections/${id}`);

export const useTdsSettings = () => useApi(["tds", "figures"], ["tds-settings"], () => request("GET", "/tds/settings"));

/** The TDS analysis year (the only year TDS is analysed / alerted); "" while unknown. */
export function useTdsAnalysisFy(): string {
  return useTdsSettings().data?.analysis_fy ?? "";
}

export async function saveTdsSettings(approachingPct: string, analysisFy: string, unidentifiedMin: string) {
  const result = await tdsWrite("PUT", "/tds/settings", {
    approaching_pct: approachingPct,
    analysis_fy: analysisFy,
    unidentified_min: unidentifiedMin,
  });
  await invalidate("figures");
  return result;
}

/** Assign a payee to a ledger's party-less payments. */
export const saveTdsAssignment = (clientId: number, body: Row) =>
  tdsWrite("PUT", `/tds/clients/${clientId}/assignment`, body);

/** Every expense and purchase ledger with postings, and what happened to it. */
export const useTdsCompleteness = (clientId: number, fy: string) =>
  useApi(["tds", "figures", "imports"], ["tds-completeness", clientId, fy], () =>
    request("GET", `/tds/clients/${clientId}/completeness`, { params: { fy } }),
  );

/** Parties whose PAN contradicts their name (every client). */
export const useTdsDataQuality = () =>
  useApi<Row[]>(["tds", "imports", "clients"], ["tds-data-quality"], () => request("GET", "/tds/data-quality"));

/** Every ledger rule of the client with its role, section and amounts per FY. */
export const useTdsMapping = (clientId: number) =>
  useApi(["tds", "figures", "imports"], ["tds-mapping", clientId], () =>
    request("GET", `/tds/clients/${clientId}/mapping`),
  );

export const updateTdsMapping = (clientId: number, changes: Row[]) =>
  tdsWrite("PUT", `/tds/clients/${clientId}/mapping`, { changes });

export const approveTdsMapping = (clientId: number, by: string, approved = true) =>
  tdsWrite("POST", `/tds/clients/${clientId}/mapping/approve`, { approved_by: by, approved });

export const refreshTdsMapping = (clientId: number) => tdsWrite("POST", `/tds/clients/${clientId}/mapping/refresh`);

export const useTdsPayer = (clientId: number, fy: string) =>
  useApi(["tds", "figures", "imports"], ["tds-payer", clientId, fy], () =>
    request("GET", `/tds/clients/${clientId}/payer`, { params: { fy } }),
  );

export const saveTdsPayer = (clientId: number, fy: string, constitution: string | null, auditLiable: boolean | null) =>
  tdsWrite("PUT", `/tds/clients/${clientId}/payer`, { fy, constitution, audit_liable: auditLiable });

export const useTdsReport = (clientId: number, fy: string, enabled = true) =>
  useApi(
    ["tds", "figures", "imports", "clients"],
    ["tds-report", clientId, fy],
    () => request("GET", `/tds/clients/${clientId}/report`, { params: { fy } }),
    enabled,
  );

export const exportTdsReport = (clientId: number, fy: string, fmt: string, fileName: string) =>
  download(`/tds/clients/${clientId}/report/export`, { fy, format: fmt }, fileName);

export const useTdsDetail = (clientId: number, fy: string, partyKey: string, section: string) =>
  useApi(["tds", "figures", "imports", "alerts"], ["tds-detail", clientId, fy, partyKey, section], () =>
    request("GET", `/tds/clients/${clientId}/detail`, { params: { fy, party_key: partyKey, section } }),
  );

export const useTdsAlertDetail = (alertId: number) =>
  useApi(["tds", "figures", "imports", "alerts"], ["tds-alert-detail", alertId], () =>
    request("GET", `/tds/alerts/${alertId}/detail`),
  );

export const exportTdsDetail = (
  clientId: number,
  fy: string,
  partyKey: string,
  section: string,
  fmt: string,
  fileName: string,
) => download(`/tds/clients/${clientId}/detail/export`, { fy, party_key: partyKey, section, format: fmt }, fileName);

export const saveTdsParty = (clientId: number, body: Row) => tdsWrite("PUT", `/tds/clients/${clientId}/party`, body);

export const saveTdsResolution = (clientId: number, body: Row) =>
  tdsWrite("PUT", `/tds/clients/${clientId}/resolution`, body);

/** flag 'tcs_206c1h' (leave out of 194Q) or 'section:<key>' (count it under another section). */
export const saveTdsVoucherFlag = (
  clientId: number,
  voucherKey: string,
  on: boolean,
  note: string | null = null,
  flag = "tcs_206c1h",
) => tdsWrite("PUT", `/tds/clients/${clientId}/voucher-flag`, { voucher_key: voucherKey, on, note, flag });

export const acknowledgeTdsAlert = (alertId: number, by: string, note: string | null) =>
  tdsWrite("POST", `/tds/alerts/${alertId}/acknowledge`, { acknowledged_by: by, note });

/** Unacknowledged TDS alerts by severity. */
export const useTdsAlertCounts = (fy: string) =>
  useApi(["alerts", "tds"], ["tds-alert-counts", fy], () => request("GET", "/tds/alert-counts", { params: { fy } }), !!fy);
