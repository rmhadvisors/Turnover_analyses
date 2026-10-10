/**
 * The one-page "Add client" / "Add data" panel: client details, then one block per year with
 * separate Master and Transactions drop boxes, the detected year and a review of the files,
 * and one button that creates the client and imports every year. Also the import log.
 */

import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  confirmTallyJson,
  createClient,
  previewTallyJson,
  saveProfile,
  toNumber,
  useImportLog,
  useProfile,
  type Row,
} from "../api";
import { formatInr, timestamp } from "../format";
import { useSelection } from "../state";
import {
  Button,
  Caption,
  Card,
  Checkbox,
  DataTable,
  Expander,
  Grid,
  Loaded,
  Metric,
  Notice,
  Progress,
  Radio,
  Section,
  Select,
  TextInput,
  autoColumns,
} from "../ui";

const STAGE_LABELS: Record<string, string> = {
  uploading: "Uploading files",
  reading: "Reading files",
  parsing: "Reading vouchers",
  done: "Checking against existing data",
};
const ENTITY_TYPES: [string | null, string][] = [
  [null, "Detect from the files"],
  ["individual", "Individual / proprietorship"],
  ["huf", "HUF"],
  ["firm", "Partnership firm"],
  ["llp", "LLP"],
  ["company", "Company"],
  ["other", "Other (AOP, trust …)"],
];
const PROFILE_KEYS = ["gstin", "pan", "gst_registered", "special_category", "entity_type", "nature", "supplies", "presumptive", "cash_within_5pct"];
type Choice = "add" | "replace" | "skip";
const CHOICE_LABELS: [Choice, string][] = [
  ["add", "Add only vouchers not imported yet (keeps existing data)"],
  ["replace", "Replace: delete this year's imported vouchers, then import these files"],
  ["skip", "Skip this year (import nothing for it)"],
];

type Year = {
  key: number;
  master: File | null;
  transactions: File[];
  preview: Row | null;
  progress: [string, number] | null;
  error: string | null;
  fy: string | null; // the year to import (detected, or chosen when the files hold several)
  choice: Choice; // when the year is already imported for this client
  replaceOk: boolean;
  result: Row | null;
};

let nextKey = 1;
const newYear = (): Year => ({
  key: nextKey++,
  master: null,
  transactions: [],
  preview: null,
  progress: null,
  error: null,
  fy: null,
  choice: "add",
  replaceOk: false,
  result: null,
});

// ------------------------------------------------------------------ file sorting

/** 'master' / 'transactions' from the record types at the start of a Tally JSON file. */
async function kindOf(file: File): Promise<"master" | "transactions" | "unknown"> {
  try {
    const bytes = new Uint8Array(await file.slice(0, 65536).arrayBuffer());
    const utf16 = (bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes.length > 1 && bytes[1] === 0 && bytes[0] !== 0);
    const encoding = bytes[0] === 0xfe && bytes[1] === 0xff ? "utf-16be" : utf16 ? "utf-16le" : "utf-8";
    const text = new TextDecoder(encoding).decode(bytes);
    const types = [...text.matchAll(/"type"\s*:\s*"([^"]+)"/g)].map((m) => m[1]);
    if (types.includes("Voucher")) return "transactions";
    if (types.some((t) => ["Ledger", "Group", "Currency", "Stock Item", "Stock Group", "Unit", "Cost Centre"].includes(t)))
      return "master";
  } catch {
    // unreadable here: leave it where it was dropped; the backend reports what it is
  }
  return "unknown";
}

const sizeText = (bytes: number) => (bytes >= 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`);
const dmy = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  return `${String(d).padStart(2, "0")}-${new Date(y, m - 1, 1).toLocaleString("en-GB", { month: "short" })}-${y}`;
};

function DropZone({
  label,
  hint,
  files,
  multiple,
  disabled,
  onFiles,
  onRemove,
}: {
  label: string;
  hint: string;
  files: File[];
  multiple?: boolean;
  disabled?: boolean;
  onFiles: (files: File[]) => void;
  onRemove: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (!disabled) onFiles(Array.from(e.dataTransfer.files));
  };
  return (
    <div
      className={`dropzone ${over ? "over" : ""} ${files.length ? "filled" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <div className="dropzone-label">{label}</div>
      {files.map((f) => (
        <div key={f.name + f.size} className="dropzone-file">
          <span>
            📄 {f.name} <span className="caption">({sizeText(f.size)})</span>
          </span>
          <button className="icon-btn small" aria-label={`Remove ${f.name}`} disabled={disabled} onClick={() => onRemove(f)}>
            ×
          </button>
        </div>
      ))}
      {(multiple || !files.length) && (
        <div className="dropzone-pick">
          <span className="caption">{files.length ? "Drop another file here, or" : "Drop the file here, or"}</span>
          <Button disabled={disabled} onClick={() => input.current?.click()}>
            Choose file…
          </Button>
        </div>
      )}
      <Caption>{hint}</Caption>
      <input
        ref={input}
        type="file"
        accept=".json,application/json"
        multiple={multiple}
        hidden
        onChange={(e) => {
          onFiles(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
    </div>
  );
}

// ------------------------------------------------------------------ the panel

/** `client` null: a new client (created on save); else add years to that client. */
export function AddDataPanel({ client, onClose, onDone }: { client: Row | null; onClose?: () => void; onDone?: (clientId: number) => void }) {
  return client ? (
    <Loaded query={useProfile(client.id)} text="Loading client details…">
      {(profile) => <Panel client={client} profile={profile} onClose={onClose} onDone={onDone} />}
    </Loaded>
  ) : (
    <Panel client={null} profile={null} onClose={onClose} onDone={onDone} />
  );
}

function Panel({ client, profile, onClose, onDone }: { client: Row | null; profile: Row | null; onClose?: () => void; onDone?: (clientId: number) => void }) {
  const { unit } = useSelection();
  const [name, setName] = useState<string>(client?.name ?? "");
  const [pan, setPan] = useState<string>(profile?.pan ?? "");
  const [gstin, setGstin] = useState<string>(profile?.gstin ?? "");
  const [entity, setEntity] = useState<string | null>(profile?.entity_type ?? null);
  const [years, setYears] = useState<Year[]>(() => [newYear()]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<number | null>(null);
  const [createdId, setCreatedId] = useState<number | null>(null); // a new client created by a failed save

  const update = (key: number, patch: Partial<Year> | ((y: Year) => Partial<Year>)) =>
    setYears((list) => list.map((y) => (y.key === key ? { ...y, ...(typeof patch === "function" ? patch(y) : patch) } : y)));

  const typedGstin = gstin.trim().toUpperCase();
  const typedPan = pan.trim().toUpperCase() || (typedGstin.length === 15 ? typedGstin.slice(2, 12) : "");
  const detailProblems: string[] = [];
  if (typedGstin && !/^\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]{3}$/.test(typedGstin)) detailProblems.push("GSTIN must be 15 characters, e.g. 27ABCDE1234F1Z5.");
  if (pan.trim() && !/^[A-Z]{5}\d{4}[A-Z]$/.test(pan.trim().toUpperCase())) detailProblems.push("PAN must be 10 characters, e.g. ABCDE1234F.");
  if (typedGstin.length === 15 && pan.trim() && typedGstin.slice(2, 12) !== pan.trim().toUpperCase())
    detailProblems.push(`The PAN inside GSTIN ${typedGstin} is ${typedGstin.slice(2, 12)}, not ${pan.trim().toUpperCase()}.`);

  /** Blockers for one year: the backend's, plus files vs the GSTIN / PAN typed above. */
  const blockersOf = (y: Year): string[] => {
    const p = y.preview;
    if (!p) return [];
    const list: string[] = [...(p.blockers ?? [])];
    const fileGstin: string | null = p.company?.gstin ?? null;
    if (fileGstin && typedGstin.length === 15 && fileGstin !== typedGstin && !list.some((b) => b.includes(typedGstin)))
      list.push(`The Transactions file is for GSTIN ${fileGstin}, but the client details say ${typedGstin}.`);
    else if (fileGstin && typedPan.length === 10 && fileGstin.slice(2, 12) !== typedPan && !list.some((b) => b.includes(typedPan)))
      list.push(`The Transactions file is for PAN ${fileGstin.slice(2, 12)}, but the client details say ${typedPan}.`);
    if (!p.detected_fy) list.push("No dated vouchers were found, so the financial year cannot be detected.");
    return list;
  };

  const loaded = years.filter((y) => y.master || y.transactions.length);
  const fyCounts = loaded.reduce<Record<string, number>>((acc, y) => (y.fy ? { ...acc, [y.fy]: (acc[y.fy] ?? 0) + 1 } : acc), {});
  const problems: string[] = [];
  if (!client && !name.trim()) problems.push("Enter the client name.");
  problems.push(...detailProblems);
  if (!loaded.length) problems.push("Add the Master and Transactions files for at least one year.");
  loaded.forEach((y, i) => {
    const label = y.fy ? `FY ${y.fy}` : `Year ${i + 1}`;
    if (!y.master || !y.transactions.length) problems.push(`${label}: add both the Master and the Transactions file.`);
    else if (y.progress) problems.push(`${label}: still checking the files.`);
    else if (!y.preview) problems.push(`${label}: ${y.error ? "the files could not be read" : "not checked yet"}.`);
    else if (blockersOf(y).length) problems.push(`${label}: cannot be imported (see its review).`);
    else if (y.fy && fyCounts[y.fy] > 1) problems.push(`${label} is added twice; remove one of them.`);
    else if (y.preview.fys_already_imported?.includes(y.fy) && y.choice === "replace" && !y.replaceOk)
      problems.push(`${label}: tick the box to confirm replacing the imported vouchers.`);
  });
  const done = savedId !== null;

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    let clientId: number | null = client?.id ?? createdId;
    try {
      if (clientId === null) {
        clientId = (await createClient(name.trim())).id as number;
        setCreatedId(clientId);
      }
      const changed =
        (typedGstin || null) !== (profile?.gstin ?? null) || (pan.trim().toUpperCase() || null) !== (profile?.pan ?? null) || entity !== (profile?.entity_type ?? null);
      if (changed) {
        const base = Object.fromEntries(PROFILE_KEYS.map((k) => [k, profile?.[k] ?? null]));
        await saveProfile(clientId, { ...base, gstin: typedGstin || null, pan: pan.trim().toUpperCase() || null, entity_type: entity });
      }
      for (const y of loaded) {
        if (y.result) continue; // imported in an earlier attempt
        const p = y.preview!;
        const fys: string[] = p.by_fy.map((r: Row) => r.fy);
        const others = fys.filter((fy) => fy !== y.fy);
        const already = (p.fys_already_imported ?? []).includes(y.fy);
        const skip = already && y.choice === "skip" ? [...others, y.fy!] : others;
        const replace = already && y.choice === "replace" ? [y.fy!] : [];
        try {
          const result = await confirmTallyJson(clientId, p.preview_token, replace, skip);
          update(y.key, { result });
        } catch (exc) {
          throw new Error(`FY ${y.fy}: ${(exc as Error).message}`);
        }
      }
      setSavedId(clientId);
      onDone?.(clientId);
    } catch (exc) {
      const created = !client && clientId !== null ? ` The client ${name.trim()} was created; fix this and save again to finish.` : "";
      setSaveError(`${(exc as Error).message}${created}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card panel">
      <div className="row">
        <h3>{client ? `Add data · ${client.name}` : "Add client"}</h3>
        <span className="spacer" />
        {onClose && (
          <button className="icon-btn" aria-label="Close" onClick={onClose} disabled={saving}>
            ×
          </button>
        )}
      </div>

      <Section title="1 · Client details" />
      <Grid cols="2fr 1fr 1.4fr 1.4fr">
        {client ? (
          <Metric label="Client" value={client.name} />
        ) : (
          <TextInput label="Client name" placeholder="e.g. Sharma Traders" value={name} onChange={(v) => !createdId && setName(v)} />
        )}
        <TextInput label="PAN (optional)" placeholder="from the files" value={pan} maxLength={10} onChange={setPan} />
        <TextInput label="GSTIN (optional)" placeholder="from the files" value={gstin} maxLength={15} onChange={setGstin} />
        <Select label="Constitution (optional)" value={entity} options={ENTITY_TYPES} onChange={setEntity} />
      </Grid>
      <Caption>Leave PAN, GSTIN and constitution blank to take them from the Tally files. When filled, the files are checked against them.</Caption>
      {detailProblems.map((p) => (
        <Notice key={p} kind="error">
          {p}
        </Notice>
      ))}

      {years.map((y, i) => (
        <YearBlock
          key={y.key}
          index={i}
          year={y}
          clientId={client?.id ?? null}
          expected={{ gstin: typedGstin.length === 15 ? typedGstin : null, pan: typedPan.length === 10 ? typedPan : null }}
          blockers={blockersOf(y)}
          duplicate={!!y.fy && fyCounts[y.fy] > 1}
          unit={unit}
          locked={saving || done}
          update={(patch) => update(y.key, patch)}
          onRemove={years.length > 1 ? () => setYears((list) => list.filter((x) => x.key !== y.key)) : undefined}
        />
      ))}
      {!done && (
        <div className="row">
          <Button disabled={saving} onClick={() => setYears((list) => [...list, newYear()])}>
            + Add another year
          </Button>
        </div>
      )}

      <hr />
      {done ? (
        <Notice kind="success">
          {client ? "Imported" : `Client ${name.trim()} saved and imported`}:{" "}
          {loaded.map((y) => `FY ${y.fy} (${y.result?.imported ?? 0} entries)`).join(", ")}.{" "}
          {loaded.reduce((n, y) => n + (y.result?.alerts_raised ?? 0), 0)} new alert(s) raised.
        </Notice>
      ) : (
        <>
          {problems.length > 0 && (
            <ul className="plain caption">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {saveError && <Notice kind="error">{saveError}</Notice>}
          <Button primary disabled={saving || problems.length > 0} onClick={save}>
            {saving ? "Saving and importing…" : client ? "Import" : "Save client and import"}
          </Button>
        </>
      )}
      {done && onClose && <Button onClick={onClose}>Close</Button>}
    </div>
  );
}

// ------------------------------------------------------------------ one year

function YearBlock({
  index,
  year,
  clientId,
  expected,
  blockers,
  duplicate,
  unit,
  locked,
  update,
  onRemove,
}: {
  index: number;
  year: Year;
  clientId: number | null;
  expected: { gstin: string | null; pan: string | null };
  blockers: string[];
  duplicate: boolean;
  unit: string;
  locked: boolean;
  update: (patch: Partial<Year> | ((y: Year) => Partial<Year>)) => void;
  onRemove?: () => void;
}) {
  const p = year.preview;
  const busy = !!year.progress;
  const run = useRef(0);

  // check the files as soon as both kinds are present (and again whenever they change)
  useEffect(() => {
    if (!year.master || !year.transactions.length) return;
    const attempt = ++run.current;
    update({ preview: null, error: null, result: null, progress: ["uploading", 0] });
    previewTallyJson(clientId, [year.master, ...year.transactions], (stage, fraction) => {
      if (attempt === run.current) update({ progress: [stage, fraction] });
    }, expected)
      .then((preview) => {
        if (attempt === run.current) update({ preview, fy: preview.detected_fy ?? null, progress: null });
      })
      .catch((exc: Error) => {
        if (attempt === run.current) update({ error: exc.message, progress: null });
      });
  }, [year.master, year.transactions]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Files dropped on either box are sorted by what they contain. */
  const addFiles = async (dropped: File[], target: "master" | "transactions") => {
    const kinds = await Promise.all(dropped.map(kindOf));
    const masters = dropped.filter((_, i) => kinds[i] === "master" || (kinds[i] === "unknown" && target === "master"));
    const txs = dropped.filter((_, i) => kinds[i] === "transactions" || (kinds[i] === "unknown" && target === "transactions"));
    update((y) => ({
      master: masters[0] ?? y.master,
      transactions: txs.length
        ? [...y.transactions, ...txs.filter((f) => !y.transactions.some((t) => t.name === f.name && t.size === f.size))]
        : y.transactions,
      error: masters.length > 1 ? `Only one Master file per year: kept ${masters[0].name}.` : null,
    }));
  };

  const row = p?.by_fy.find((r: Row) => r.fy === year.fy);
  const months = p?.months.find((m: Row) => m.fy === year.fy);
  const fyOptions: [string, string][] = (p?.by_fy ?? []).map((r: Row) => [r.fy, `FY ${r.fy}`]);
  const already = p && year.fy && (p.fys_already_imported ?? []).includes(year.fy);
  const unmatched: Row[] = p?.unmatched_ledgers ?? [];

  return (
    <div className="year-block">
      <div className="row">
        <Section title={`${index + 2} · Year ${index + 1}${year.fy ? ` · FY ${year.fy}` : ""}`} />
        <span className="spacer" />
        {onRemove && (
          <Button disabled={locked} onClick={onRemove}>
            Remove this year
          </Button>
        )}
      </div>
      <Grid cols={2}>
        <DropZone
          label="Master file (.json)"
          hint="Ledgers and groups, exported from the same company as the Transactions."
          files={year.master ? [year.master] : []}
          disabled={locked || busy}
          onFiles={(f) => addFiles(f, "master")}
          onRemove={() => update({ master: null, preview: null, fy: null })}
        />
        <DropZone
          label="Transactions file (.json)"
          hint="One file for the year, or several if Tally split the export (e.g. by quarter): they are merged."
          files={year.transactions}
          multiple
          disabled={locked || busy}
          onFiles={(f) => addFiles(f, "transactions")}
          onRemove={(file) => update((y) => ({ transactions: y.transactions.filter((t) => t !== file), preview: null, fy: null }))}
        />
      </Grid>
      {year.progress && (
        <Progress value={year.progress[1]} text={`${STAGE_LABELS[year.progress[0]] ?? year.progress[0]}… ${Math.round(year.progress[1] * 100)}%`} />
      )}
      {year.error && <Notice kind="error">{year.error}</Notice>}

      {p && (
        <>
          {p.detected_fy && (
            <p className="detected">
              Detected: <b>FY {p.detected_fy}</b> ({dmy(p.first_date)} to {dmy(p.last_date)}, {Number(p.vouchers_read).toLocaleString("en-IN")} vouchers)
            </p>
          )}
          {fyOptions.length > 1 && (
            <>
              <Notice kind="warning">
                These files hold vouchers of {fyOptions.length} financial years ({p.period}). Only the year below is imported;
                the others are left out.
              </Notice>
              <Select label="Year to import" value={year.fy} options={fyOptions} disabled={locked} onChange={(fy) => update({ fy })} />
            </>
          )}

          <Card>
            <b>Review</b>
            <Grid cols={4}>
              <Metric label="Company in the files" value={p.company?.client_name ?? "Not loaded here yet"} />
              <Metric label="GSTIN" value={p.company?.gstin ?? "not in the file"} />
              <Metric label="Year" value={year.fy ? `FY ${year.fy}` : "—"} />
              <Metric label="Vouchers" value={Number(p.vouchers_read).toLocaleString("en-IN")} />
              <Metric label="Date range" value={`${dmy(p.first_date)} – ${dmy(p.last_date)}`} />
              <Metric label="Sales (excl. GST)" value={row ? `${formatInr(row.sales, unit)} · ${row.sales_count} entries` : "—"} />
              <Metric label="Purchases (excl. GST)" value={row ? `${formatInr(row.purchases, unit)} · ${row.purchase_count} entries` : "—"} />
              <Metric label="Files" value={`1 Master + ${year.transactions.length} Transactions`} />
            </Grid>
            {months && (
              <Caption>
                Months with vouchers: {months.covered.length} of {months.covered.length + months.missing.length}
                {months.covered.length > 0 && ` (${months.covered[0]} to ${months.covered[months.covered.length - 1]})`}
              </Caption>
            )}
            {(months?.missing.length ?? 0) > 0 && <Notice kind="warning">No vouchers at all in: {months.missing.join(", ")}. Is a file missing?</Notice>}

            {blockers.map((b) => (
              <Notice key={b} kind="error">
                ⛔ {b}
              </Notice>
            ))}
            {duplicate && <Notice kind="error">FY {year.fy} is added twice in this panel; remove one of them.</Notice>}

            {p.unmatched_ledger_count > 0 && (
              <>
                <b>
                  {p.unmatched_ledger_count} ledger(s) used in the vouchers are not in the Master file ({p.unmatched_lines.toLocaleString("en-IN")}{" "}
                  lines, {formatInr(p.unmatched_amount, "full")}, {toNumber(p.unmatched_share_pct)?.toFixed(2)}% of the vouchers' value; imports
                  are refused above {toNumber(p.unmatched_limit_pct)}%)
                </b>
                <DataTable
                  maxHeight={260}
                  rows={unmatched.map((l) => ({ Ledger: l.name, Lines: l.lines, Amount: formatInr(l.amount, "full") }))}
                  columns={[
                    { key: "Ledger", label: "Ledger missing from the Master" },
                    { key: "Lines", label: "Lines", align: "right" },
                    { key: "Amount", label: "Amount", align: "right" },
                  ]}
                />
                {p.unmatched_ledger_count > unmatched.length && <Caption>Largest {unmatched.length} shown.</Caption>}
              </>
            )}
            {p.warnings
              .filter((w: string) => !w.includes("are not in the Master file"))
              .map((w: string, i: number) => (
                <Notice key={i} kind="warning">
                  {w}
                </Notice>
              ))}

            {already && (
              <>
                <Notice kind="warning">
                  FY {year.fy} is already imported for this client ({p.existing_vouchers?.[year.fy!] ?? 0} vouchers stored).
                </Notice>
                <Radio label={`What should happen to FY ${year.fy}?`} value={year.choice} options={CHOICE_LABELS} onChange={(choice) => update({ choice })} />
                {year.choice === "replace" && (
                  <Checkbox
                    label={`I understand ${p.existing_vouchers?.[year.fy!] ?? 0} imported vouchers for FY ${year.fy} will be deleted first.`}
                    checked={year.replaceOk}
                    onChange={(replaceOk) => update({ replaceOk })}
                  />
                )}
              </>
            )}
            <Expander title="Details: files, profit and sample entries">
              {(() => {
                const fileRows = p.files.map((f: Row) => ({
                  File: f.file_name,
                  "Recognised as": f.kind === "master" ? "Master (ledgers and groups)" : "Transactions (vouchers)",
                  "Records read": f.records,
                  "Complete?": f.truncated ? "⚠️ cut off" : "✅ yes",
                }));
                return <DataTable rows={fileRows} columns={autoColumns(fileRows)} />;
              })()}
              {p.profits?.length > 0 &&
                (() => {
                  const money = (v: unknown) => (toNumber(v) !== null ? formatInr(v, unit) : "—");
                  const rows = p.profits.map((x: Row) => ({
                    "Financial year": `FY ${x.fy}`,
                    "Gross profit": money(x.gross_profit),
                    "Net profit": money(x.net_profit),
                    Status: x.will_store ? "Will be saved" : "Not saved",
                    Notes: x.notes.join(" "),
                  }));
                  return <DataTable rows={rows} columns={autoColumns(rows)} />;
                })()}
              <Caption>
                New entries to import: {p.would_import} · already stored (skipped): {p.duplicates}
              </Caption>
            </Expander>
          </Card>
          {year.result && (
            <Notice kind="success">
              FY {year.fy}: imported {year.result.imported} entries ({year.result.duplicates_skipped} already stored).
            </Notice>
          )}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ import log

export function ImportLog({ clientId }: { clientId: number }) {
  return (
    <>
      <b>Import log</b>
      <Loaded query={useImportLog(clientId)}>
        {(log) => {
          if (!log.length) return <Caption>No imports yet for this client.</Caption>;
          const rows = log.map((r) => ({
            "Imported at": timestamp(r.imported_at),
            Files: r.file_name,
            Register: r.report_type,
            Period: r.period,
            Imported: r.rows_imported,
            Skipped: r.rows_skipped,
          }));
          return <DataTable rows={rows} columns={autoColumns(rows)} />;
        }}
      </Loaded>
    </>
  );
}
