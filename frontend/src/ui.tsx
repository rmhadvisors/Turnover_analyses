/** Shared presentation components: headers, cards, notices, tables, tabs, dialogs, toasts. */

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "./api";

// ------------------------------------------------------------ text blocks

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      <p className="subtitle">{subtitle ?? "Turnover, financial comparisons and compliance alerts."}</p>
    </header>
  );
}

export const Section = ({ title }: { title: ReactNode }) => <h3 className="section">{title}</h3>;

export const Caption = ({ children }: { children: ReactNode }) => <p className="caption">{children}</p>;

export function KpiCard({ label, value, delta }: { label: string; value: ReactNode; delta?: string | null }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {delta && <div className="kpi-delta">{delta}</div>}
    </div>
  );
}

export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}

export const Grid = ({ cols, children, style }: { cols: number | string; children: ReactNode; style?: CSSProperties }) => (
  <div className="grid" style={{ gridTemplateColumns: typeof cols === "number" ? `repeat(${cols}, minmax(0, 1fr))` : cols, ...style }}>
    {children}
  </div>
);

export type NoticeKind = "info" | "warning" | "error" | "success";

export function Notice({ kind = "info", children }: { kind?: NoticeKind; children: ReactNode }) {
  return <div className={`notice notice-${kind}`}>{children}</div>;
}

export const ErrorNotice = ({ error }: { error: unknown }) =>
  error ? <Notice kind="error">{error instanceof Error ? error.message : String(error)}</Notice> : null;

export function Badge({ level }: { level: string }) {
  const l = (level || "info").toLowerCase();
  const cls = ["critical", "high", "warning", "info"].includes(l) ? l : "neutral";
  return <span className={`badge badge-${cls}`}>{l[0].toUpperCase() + l.slice(1)}</span>;
}

export const Spinner = ({ text = "Loading…" }: { text?: string }) => (
  <div className="spinner">
    <span className="spinner-dot" /> {text}
  </div>
);

/** Spinner while loading, the error when it failed, else the children with the data. */
export function Loaded<T>({
  query,
  text,
  children,
}: {
  query: UseQueryResult<T, ApiError>;
  text?: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <Spinner text={text} />;
  if (query.isError) return <ErrorNotice error={query.error} />;
  return <>{children(query.data)}</>;
}

export function Progress({ value, text }: { value: number; text?: string }) {
  return (
    <div className="progress-wrap">
      {text && <div className="caption">{text}</div>}
      <div className="progress">
        <div className="progress-bar" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------ inputs

export function Toggle({ label, checked, onChange, title }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; title?: string }) {
  return (
    <label className="toggle" title={title}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track" />
      <span>{label}</span>
    </label>
  );
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label?: string;
  options: T[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="field">
      {label && <span className="field-label">{label}</span>}
      <div className="segmented" role="radiogroup">
        {options.map((o) => (
          <button key={o} type="button" role="radio" aria-checked={o === value} className={o === value ? "active" : ""} onClick={() => onChange(o)}>
            {o}
          </button>
        ))}
      </div>
    </div>
  );
}

/** A labelled <select>. Options are [value, label] pairs; values may be null. */
export function Select<T>({
  label,
  value,
  options,
  onChange,
  disabled,
  help,
  placeholder,
}: {
  label?: ReactNode;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
  disabled?: boolean;
  help?: string;
  placeholder?: string;
}) {
  const index = options.findIndex(([v]) => v === value);
  return (
    <label className="field" title={help}>
      {label && <span className="field-label">{label}</span>}
      <select value={index} disabled={disabled} onChange={(e) => onChange(options[Number(e.target.value)][0])}>
        {index === -1 && <option value={-1}>{placeholder ?? "—"}</option>}
        {options.map(([, text], i) => (
          <option key={i} value={i}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

export function TextInput({
  label,
  value,
  onChange,
  placeholder,
  maxLength,
  type = "text",
  help,
}: {
  label?: ReactNode;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  maxLength?: number;
  type?: string;
  help?: string;
}) {
  return (
    <label className="field" title={help}>
      {label && <span className="field-label">{label}</span>}
      <input type={type} value={value} placeholder={placeholder} maxLength={maxLength} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function Checkbox({ label, checked, onChange }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="checkbox">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> <span>{label}</span>
    </label>
  );
}

export function Radio<T>({
  label,
  value,
  options,
  onChange,
  horizontal,
}: {
  label?: ReactNode;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
  horizontal?: boolean;
}) {
  return (
    <fieldset className={`radio ${horizontal ? "horizontal" : ""}`}>
      {label && <legend className="field-label">{label}</legend>}
      {options.map(([v, text], i) => (
        <label key={i}>
          <input type="radio" checked={v === value} onChange={() => onChange(v)} /> {text}
        </label>
      ))}
    </fieldset>
  );
}

// ------------------------------------------------------------ buttons

type ButtonProps = {
  children: ReactNode;
  onClick?: () => unknown;
  primary?: boolean;
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
};

export function Button({ children, onClick, primary, disabled, title, type = "button" }: ButtonProps) {
  return (
    <button type={type} className={`btn ${primary ? "btn-primary" : ""}`} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

/**
 * Runs async actions: tracks busy state, shows the error under the button, and on success
 * shows `message` as a toast. Returns true on success.
 */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (action: () => Promise<unknown> | unknown, message?: string): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await action();
        if (message) toast(message);
        return true;
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [toast],
  );
  return { run, busy, error, setError };
}

/** A button that runs an async action, disabling itself while it runs. */
export function ActionButton({ children, action, message, primary, disabled, title }: Omit<ButtonProps, "onClick"> & { action: () => Promise<unknown> | unknown; message?: string }) {
  const { run, busy, error } = useAction();
  return (
    <span className="action">
      <Button primary={primary} disabled={disabled || busy} title={title} onClick={() => run(action, message)}>
        {busy ? "Working…" : children}
      </Button>
      {error && <Notice kind="error">{error}</Notice>}
    </span>
  );
}

// ------------------------------------------------------------ containers

export function Tabs({ tabs }: { tabs: [string, () => ReactNode][] }) {
  const [active, setActive] = useState(0);
  return (
    <div className="tabs">
      <div className="tab-list" role="tablist">
        {tabs.map(([name], i) => (
          <button key={name} role="tab" aria-selected={i === active} className={i === active ? "active" : ""} onClick={() => setActive(i)}>
            {name}
          </button>
        ))}
      </div>
      <div className="tab-panel">{tabs[active][1]()}</div>
    </div>
  );
}

export function Expander({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details className="expander" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>{title}</summary>
      {open && <div className="expander-body">{children}</div>}
    </details>
  );
}

export const Card = ({ children }: { children: ReactNode }) => <div className="card">{children}</div>;

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ------------------------------------------------------------ table

export interface Column<R> {
  key: string;
  label: ReactNode;
  render?: (row: R) => ReactNode;
  align?: "left" | "right";
  style?: (row: R) => CSSProperties | undefined;
  wide?: boolean;
}

/** Read-only table; `selection` turns rows into checkboxes/radios like Streamlit's row selection. */
export function DataTable<R extends Record<string, any>>({
  columns,
  rows,
  selection,
  selected = [],
  onSelect,
  rowStyle,
  maxHeight = 520,
  wrap,
}: {
  columns: Column<R>[];
  rows: R[];
  selection?: "single" | "multi";
  selected?: number[];
  onSelect?: (indices: number[]) => void;
  rowStyle?: (row: R) => CSSProperties | undefined;
  maxHeight?: number;
  /** Let cell text wrap so a wide table fits the page instead of scrolling sideways. */
  wrap?: boolean;
}) {
  const toggle = (i: number) => {
    if (!onSelect) return;
    if (selection === "single") onSelect(selected[0] === i ? [] : [i]);
    else onSelect(selected.includes(i) ? selected.filter((s) => s !== i) : [...selected, i]);
  };
  const allSelected = selection === "multi" && rows.length > 0 && selected.length === rows.length;
  return (
    <div className="table-wrap" style={{ maxHeight }}>
      <table className={`table ${wrap ? "wrap" : ""}`}>
        <thead>
          <tr>
            {selection && (
              <th className="sel">
                {selection === "multi" && (
                  <input type="checkbox" aria-label="Select all" checked={allSelected} onChange={() => onSelect?.(allSelected ? [] : rows.map((_, i) => i))} />
                )}
              </th>
            )}
            {columns.map((c) => (
              <th key={c.key} className={c.align === "right" ? "num" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className={`${selection ? "selectable" : ""} ${selected.includes(i) ? "selected" : ""}`}
              style={rowStyle?.(row)}
              onClick={selection ? () => toggle(i) : undefined}
            >
              {selection && (
                <td className="sel">
                  <input type={selection === "single" ? "radio" : "checkbox"} checked={selected.includes(i)} readOnly tabIndex={-1} />
                </td>
              )}
              {columns.map((c) => (
                <td key={c.key} className={`${c.align === "right" ? "num" : ""} ${c.wide ? "wide" : ""}`} style={c.style?.(row)}>
                  {c.render ? c.render(row) : formatCell(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): ReactNode {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "✓" : "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Columns straight from the keys of the first row (for tables the API already shaped). */
export function autoColumns(rows: Record<string, any>[]): Column<Record<string, any>>[] {
  return Object.keys(rows[0] ?? {}).map((key) => ({ key, label: key }));
}

// ------------------------------------------------------------ toasts

const ToastContext = createContext<(message: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<{ id: number; text: string }[]>([]);
  const push = useCallback((text: string) => {
    const id = Date.now() + Math.random();
    setMessages((m) => [...m, { id, text }]);
    setTimeout(() => setMessages((m) => m.filter((x) => x.id !== id)), 4000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {messages.map((m) => (
          <div key={m.id} className="toast">
            {m.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

// ------------------------------------------------------------ CSV

/** Build a CSV (with a BOM so Excel reads ₹ and Indian text correctly) from table rows. */
export function toCsv(rows: Record<string, unknown>[]): Blob {
  const keys = Object.keys(rows[0] ?? {});
  const cell = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [keys.map(cell).join(","), ...rows.map((r) => keys.map((k) => cell(r[k])).join(","))].join("\r\n");
  return new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" });
}
