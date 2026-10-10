/** Display formatting (rupees, percentages, financial years) and shared status labels. */

import { toNumber } from "./api";

export const DISPLAY_MODES: Record<string, string> = { Auto: "auto", Lakhs: "lakhs", Crores: "crores", Full: "full" };
const LAKH = 100_000;
const CRORE = 10_000_000;

function indianGrouping(digits: string): string {
  if (digits.length <= 3) return digits;
  let grouped = digits.slice(-3);
  let head = digits.slice(0, -3);
  while (head) {
    grouped = `${head.slice(-2)},${grouped}`;
    head = head.slice(0, -2);
  }
  return grouped;
}

/** Any rupee amount in Indian grouping ("full") or the selected scaled unit. */
export function formatInr(value: unknown, mode = "auto"): string {
  const amount = toNumber(value);
  if (amount === null) return "—";
  mode = DISPLAY_MODES[mode] ?? mode;
  if (mode === "full") {
    const [whole, paise] = Math.abs(amount).toFixed(2).split(".");
    const text = `₹${indianGrouping(whole)}${paise !== "00" ? `.${paise}` : ""}`;
    return amount < 0 ? `-${text}` : text;
  }
  const [divisor, suffix] =
    mode === "crores" || (mode === "auto" && Math.abs(amount) >= CRORE) ? [CRORE, "Cr"] : [LAKH, "L"];
  const scaled = Math.abs(amount / divisor).toFixed(2);
  const sign = amount < 0 && Number(scaled) !== 0 ? "-" : "";
  return `${sign}₹${scaled} ${suffix}`;
}

/** Full rupees with Indian grouping (TDS working is always shown in full). */
export const rs = (value: unknown) => formatInr(value, "full");

export function formatPct(value: unknown): string {
  const n = toNumber(value);
  return n === null ? "—" : `${n.toFixed(2)}%`;
}

export const signedPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

/** Plain number with thousands separators and 2 decimals (TDS tables). */
export function num2(value: unknown): string {
  const n = toNumber(value);
  return n === null ? "" : n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Decimal text without trailing zeros: "2.000" -> "2". */
export function plainNumber(value: unknown): string {
  const n = toNumber(value);
  return n === null ? "" : String(n);
}

export const ARROWS: Record<string, string> = { up: " ↑", down: " ↓", flat: "", none: "" };

export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());

export const timestamp = (value: unknown) => String(value ?? "").replace("T", " ").slice(0, 16);

export const safeName = (text: string, max = 60) => text.replace(/[^A-Za-z0-9]/g, "_").slice(0, max);

// ------------------------------------------------------------ financial years

export const fyLabel = (startYear: number) => `${startYear}-${String(startYear + 1).slice(2)}`;

export function currentFy(today = new Date()): string {
  return fyLabel(today.getMonth() >= 3 ? today.getFullYear() : today.getFullYear() - 1);
}

export const previousFy = (fy: string) => fyLabel(Number(fy.slice(0, 4)) - 1);

/** Sidebar years, newest first: every FY with data plus the next FY and the current and
 * five previous FYs, so figures can be entered for a year with no data. */
export function fyChoices(dataFys: string[]): string[] {
  const start = Number(currentFy().slice(0, 4));
  const years = new Set(dataFys);
  for (let y = start - 5; y <= start + 1; y++) years.add(fyLabel(y));
  return [...years].sort().reverse();
}

// ------------------------------------------------------------ statuses

export const SEVERITY_EMOJI: Record<string, string> = { critical: "🔴", high: "🟠", warning: "🟡", info: "🟢" };

export const BAND_COLOURS: Record<string, string> = {
  "Significant Increase": "#ff9da8",
  "Significant Decrease": "#ff9da8",
  "Moderate Increase": "#ffdfaa",
  "Moderate Decrease": "#ffdfaa",
  Normal: "#a7b3c2",
  "No data": "#7f8b99",
};

export const SECTIONS = ["194C", "194H", "194J(a)", "194J(b)", "194J(ba)", "194Q", "194I(a)", "194I(b)", "194-IB"];
export const MAPPABLE_SECTIONS = SECTIONS.slice(0, -1); // 194-IB is applied automatically, never mapped

export const TDS_STATUS_EMOJI: Record<string, string> = {
  tds_not_deducted: "🔴",
  tds_short_deducted: "🟠",
  tds_approaching: "🟡",
  tds_ok: "🟢",
  tds_excess: "🔵",
  tds_nil: "🟢",
  tds_below: "⚪",
  tds_not_applicable: "⚪",
  tds_unidentified: "🟠",
};

export const TDS_STATUS_LABELS: Record<string, string> = {
  tds_not_deducted: "Threshold crossed & TDS NOT deducted",
  tds_short_deducted: "Threshold crossed & TDS short deducted",
  tds_approaching: "Approaching threshold",
  tds_ok: "Threshold crossed & TDS correctly deducted",
  tds_excess: "Threshold crossed & TDS EXCESS deducted",
  tds_nil: "Threshold crossed - nil TDS",
  tds_below: "Below threshold",
  tds_unidentified: "Cannot determine - payee unidentified",
  tds_not_applicable: "Section does not apply to this client",
};

export const ROLE_LABELS: Record<string, string> = {
  base: "Payments (TDS base)",
  tds: "TDS deducted",
  excluded: "Excluded - not TDS",
  unmapped: "Unmapped",
};

export const PAYEE_TYPES: Record<string, string> = { individual_huf: "Individual/HUF", other: "Other (firm, company, ...)" };
