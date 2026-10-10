/** Settings: change bands, statutory turnover limits and the TDS editors. */

import { useEffect, useMemo, useState } from "react";
import {
  createLimit,
  deleteLimit,
  normDecimal,
  parseMoneyInput,
  saveImportSettings,
  saveSettings,
  updateLimit,
  useLimits,
  useSettings,
  useTdsDataQuality,
  useWorkspace,
  type Row,
} from "../api";
import { MappingEditor, PayerEditor, RateMaster } from "../components/tds/SettingsEditors";
import { formatInr, plainNumber } from "../format";
import { useSelection } from "../state";
import {
  ActionButton,
  Button,
  Caption,
  Checkbox,
  DataTable,
  Expander,
  Grid,
  Loaded,
  Modal,
  Notice,
  PageHeader,
  Section,
  Select,
  Tabs,
  TextInput,
  autoColumns,
} from "../ui";

const METRICS: Record<string, string> = {
  sales_turnover: "Sales turnover",
  purchase_turnover: "Purchase turnover",
  aggregate_turnover: "Aggregate turnover",
  purchase_per_seller: "Purchases from one seller",
};
// per-seller purchases were only for 194Q, which the TDS module now checks per party
const metricOptions = (Object.entries(METRICS) as [string, string][]).filter(([m]) => m !== "purchase_per_seller");
const fmtG = (n: number) => String(Number(n.toPrecision(6)));

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" subtitle="Change bands, statutory turnover limits and the TDS rate master and ledger mapping." />
      <Tabs
        tabs={[
          ["Change bands", () => <Bands />],
          ["Statutory limits", () => <Limits />],
          ["TDS", () => <Tds />],
        ]}
      />
    </>
  );
}

function Bands() {
  return (
    <Loaded query={useSettings()} text="Loading threshold settings…">
      {(settings) => (
        <>
          <BandsForm settings={settings} />
          <ImportChecks settings={settings} />
        </>
      )}
    </Loaded>
  );
}

function BandsForm({ settings }: { settings: Row }) {
  const [moderate, setModerate] = useState(plainNumber(settings.moderate_pct));
  const [significant, setSignificant] = useState(plainNumber(settings.significant_pct));
  const [includeGst, setIncludeGst] = useState<boolean>(settings.include_gst_in_turnover);
  const [saved, setSaved] = useState<string | null>(null);
  const m = Number(moderate);
  const s = Number(significant);
  return (
    <>
      <Section title="Year-on-year change bands" />
      <Caption>
        Applied to turnover, purchases, gross profit and net profit. Changes must move beyond a band edge to enter the next band.
      </Caption>
      <div className="card stack">
        <Grid cols={2}>
          <TextInput label="Moderate limit (%)" type="number" value={moderate} onChange={setModerate} />
          <TextInput label="Significant limit (%)" type="number" value={significant} onChange={setSignificant} />
        </Grid>
        <Checkbox label="Include GST in turnover and purchases" checked={includeGst} onChange={setIncludeGst} />
        <Caption>
          Critical: beyond ±{fmtG(s)}% · Warning: {fmtG(m)}% to {fmtG(s)}% · Info: within ±{fmtG(m)}%
        </Caption>
        <div>
          <ActionButton
            primary
            action={async () => {
              setSaved(null);
              if (!(m >= 0.1 && m <= 1000 && s >= 0.1 && s <= 1000)) throw new Error("Limits must be between 0.1 and 1000.");
              if (m >= s) throw new Error("The moderate limit must be below the significant limit.");
              await saveSettings(fmtG(m), fmtG(s), includeGst);
              setSaved(
                includeGst !== settings.include_gst_in_turnover
                  ? "Saved. All clients were re-checked. The GST setting applies to vouchers imported from now on."
                  : "Saved. All clients were re-checked.",
              );
            }}
          >
            Save bands and re-check all clients
          </ActionButton>
        </div>
        {saved && <Notice kind="success">{saved}</Notice>}
      </div>
    </>
  );
}

function ImportChecks({ settings }: { settings: Row }) {
  const [pct, setPct] = useState(plainNumber(settings.max_unmatched_ledger_pct));
  return (
    <>
      <Section title="Tally import checks" />
      <Caption>
        A Master and Transactions pair from different Tally companies is always refused. Even from the same company, an import
        is refused when more than this share of the vouchers' value is on ledgers missing from the Master file: the Master is
        older than the Transactions and must be exported again.
      </Caption>
      <div className="card stack">
        <TextInput label="Refuse the import above (% of voucher value on ledgers missing from the Master)" type="number" value={pct} onChange={setPct} />
        <div>
          <ActionButton
            primary
            message="Import check saved."
            action={async () => {
              const n = Number(pct);
              if (!(n >= 0 && n <= 100)) throw new Error("Enter a percentage between 0 and 100.");
              await saveImportSettings(fmtG(n));
            }}
          >
            Save import check
          </ActionButton>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------ statutory limits

type LimitEdit = {
  select: boolean;
  name: string;
  metric: string;
  amount: string;
  approaching: string;
  fy_scope: string;
  enabled: boolean;
  description: string;
};

function verifiedStore(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem("ta.verifiedLimits") ?? "{}");
  } catch {
    return {};
  }
}

function Limits() {
  return (
    <>
      <Notice>
        Turnover limits only (GST registration, tax audit, presumptive taxation, e-invoicing, LLP audit). TDS section thresholds
        are per-party limits and are checked in the TDS tab. Pre-filled limits are editable starting points: verify each one
        against current law before relying on it.
      </Notice>
      <Loaded query={useLimits()}>{(limits) => <LimitsTable limits={limits} />}</Loaded>
      <AddLimit />
    </>
  );
}

/** One editable table: edit cells and save, or tick rows and delete them. */
function LimitsTable({ limits }: { limits: Row[] }) {
  const initial = useMemo(
    () =>
      Object.fromEntries(
        limits.map((l) => [
          l.id,
          {
            select: false,
            name: l.name,
            metric: l.metric,
            // Full rupees, whatever the display unit: this cell is parsed back on save.
            amount: formatInr(l.amount, "full"),
            approaching: plainNumber(l.approaching_pct),
            fy_scope: l.fy_scope ?? "",
            enabled: l.is_enabled,
            description: l.description ?? "",
          } as LimitEdit,
        ]),
      ),
    [limits],
  );
  const [edits, setEdits] = useState<typeof initial>(initial);
  const [verified, setVerified] = useState(verifiedStore);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => setEdits(initial), [initial]);
  useEffect(() => {
    try {
      localStorage.setItem("ta.verifiedLimits", JSON.stringify(verified));
    } catch {
      // UI-only marker; losing it is harmless
    }
  }, [verified]);

  if (!limits.length) return <Notice>No statutory limits are configured.</Notice>;
  const set = (id: number, patch: Partial<LimitEdit>) => setEdits((e: typeof initial) => ({ ...e, [id]: { ...e[id], ...patch } }));
  const selected = limits.filter((l) => edits[l.id]?.select);

  const save = async () => {
    for (const old of limits) {
      const e = edits[old.id];
      const amount = parseMoneyInput(e.amount);
      if (amount === null) throw new Error(`${e.name}: enter an amount.`);
      if (!e.name.trim()) throw new Error("Every limit needs a name.");
      const approaching = normDecimal(e.approaching);
      if (approaching === null || Number(approaching) < 0.1 || Number(approaching) > 100) {
        throw new Error(`${e.name}: approaching % must be between 0.1 and 100.`);
      }
      const body = {
        name: e.name.trim(),
        metric: e.metric,
        amount: normDecimal(amount),
        approaching_pct: approaching,
        fy_scope: e.fy_scope.trim() || null,
        description: e.description.trim() || null,
        is_enabled: e.enabled,
      };
      const changed =
        body.name !== old.name ||
        body.metric !== old.metric ||
        body.amount !== normDecimal(old.amount) ||
        body.approaching_pct !== normDecimal(old.approaching_pct) ||
        body.fy_scope !== old.fy_scope ||
        body.description !== old.description ||
        body.is_enabled !== old.is_enabled;
      if (changed) await updateLimit(old.id, body);
    }
  };

  const text = (l: Row, field: keyof LimitEdit, width?: number) => (
    <input value={String(edits[l.id]?.[field] ?? "")} style={width ? { minWidth: width } : undefined} onChange={(ev) => set(l.id, { [field]: ev.target.value })} />
  );
  const tick = (l: Row, field: "select" | "enabled") => (
    <input type="checkbox" checked={!!edits[l.id]?.[field]} onChange={(ev) => set(l.id, { [field]: ev.target.checked })} />
  );

  return (
    <>
      <DataTable
        rows={limits}
        columns={[
          { key: "select", label: <span title="Tick rows to delete them">Select</span>, render: (l) => tick(l, "select") },
          { key: "name", label: "Name", render: (l) => text(l, "name", 200) },
          {
            key: "metric",
            label: "Metric",
            render: (l) => (
              <select value={edits[l.id]?.metric} onChange={(ev) => set(l.id, { metric: ev.target.value })}>
                {metricOptions.map(([v, t]) => (
                  <option key={v} value={v}>
                    {t}
                  </option>
                ))}
              </select>
            ),
          },
          { key: "amount", label: <span title="Enter rupees; Indian comma grouping is accepted.">Amount</span>, render: (l) => text(l, "amount", 130) },
          { key: "approaching", label: "Approaching %", render: (l) => text(l, "approaching") },
          { key: "fy_scope", label: "FY scope", render: (l) => text(l, "fy_scope") },
          { key: "enabled", label: "Enabled", render: (l) => tick(l, "enabled") },
          {
            key: "verified",
            label: <span title="Reviewer marker kept in this browser only; no database field is available.">Verified</span>,
            render: (l) => (
              <input
                type="checkbox"
                checked={!!verified[String(l.id)]}
                onChange={(ev) => setVerified((v) => ({ ...v, [String(l.id)]: ev.target.checked }))}
              />
            ),
          },
          {
            key: "applies_to",
            label: (
              <span title="Which clients this limit is checked for, from each client's profile (Clients -> Profile). A profile field left Unknown keeps a limit on, except where the rule says the field must be set in the profile (LLP audit, 44ADA).">
                Applies to
              </span>
            ),
            render: (l) => l.applies_to || "Every client",
          },
          { key: "description", label: "Why it matters", render: (l) => text(l, "description", 280) },
        ]}
      />
      <div className="row">
        <ActionButton primary message="Limits saved." action={save}>
          💾 Save changes
        </ActionButton>
        <Button disabled={!selected.length} onClick={() => setConfirmDelete(true)}>
          🗑 Delete selected ({selected.length})
        </Button>
      </div>
      {confirmDelete && (
        <Modal title="Delete statutory limits" onClose={() => setConfirmDelete(false)}>
          <Notice kind="warning">These limits will be deleted permanently:</Notice>
          <ul className="plain">
            {selected.map((l) => (
              <li key={l.id}>{l.name}</li>
            ))}
          </ul>
          <div className="row">
            <ActionButton
              primary
              action={async () => {
                for (const l of selected) await deleteLimit(l.id);
                setConfirmDelete(false);
              }}
            >
              Delete
            </ActionButton>
            <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
          </div>
        </Modal>
      )}
    </>
  );
}

function AddLimit() {
  const blank = { name: "", metric: "sales_turnover", amount: "", approaching: "80", fyScope: "", description: "", enabled: true };
  const [form, setForm] = useState(blank);
  const set = (patch: Partial<typeof blank>) => setForm((f) => ({ ...f, ...patch }));
  return (
    <Expander title="Add statutory limit">
      <div className="stack">
        <TextInput label="Name" value={form.name} onChange={(name) => set({ name })} />
        <Select label="Metric" value={form.metric} options={metricOptions} onChange={(metric) => set({ metric })} />
        <TextInput label="Amount (₹)" value={form.amount} onChange={(amount) => set({ amount })} />
        <TextInput label="Approaching at (% of limit)" type="number" value={form.approaching} onChange={(approaching) => set({ approaching })} />
        <TextInput label="Only for FY (blank = every year)" placeholder="2025-26" value={form.fyScope} onChange={(fyScope) => set({ fyScope })} />
        <label className="field">
          <span className="field-label">Why it matters</span>
          <textarea value={form.description} onChange={(e) => set({ description: e.target.value })} />
        </label>
        <Checkbox label="Enabled" checked={form.enabled} onChange={(enabled) => set({ enabled })} />
        <div>
          <ActionButton
            primary
            message="Limit added."
            action={async () => {
              const amount = parseMoneyInput(form.amount);
              if (amount === null) throw new Error("Enter an amount.");
              const approaching = Number(form.approaching);
              if (!(approaching >= 1 && approaching <= 100)) throw new Error("Approaching % must be between 1 and 100.");
              await createLimit({
                name: form.name.trim(),
                metric: form.metric,
                amount,
                approaching_pct: String(approaching),
                fy_scope: form.fyScope.trim() || null,
                description: form.description || null,
                is_enabled: form.enabled,
              });
              setForm(blank);
            }}
          >
            Add limit
          </ActionButton>
        </div>
      </div>
    </Expander>
  );
}

// ------------------------------------------------------------ TDS

function Tds() {
  return (
    <>
      <Tabs
        tabs={[
          ["Rate & threshold master", () => <RateMaster />],
          ["Client: who deducts & ledger mapping", () => <TdsClient />],
        ]}
      />
      <DataQuality />
    </>
  );
}

function TdsClient() {
  const { clientId, fy } = useSelection();
  const clients: Row[] = useWorkspace().data?.clients ?? [];
  const [picked, setPicked] = useState<number | null>(null);
  const client = clients.find((c) => c.id === (clientId ?? picked));
  return (
    <>
      {clientId === null && (
        <Select
          label="Client"
          value={picked}
          placeholder="Choose a client (or pick one in the sidebar)"
          options={clients.map((c): [number, string] => [c.id, c.name])}
          onChange={setPicked}
        />
      )}
      {!client ? (
        <Notice>Choose a client to review who must deduct and its ledger mapping.</Notice>
      ) : (
        <>
          <Section title="Who must deduct" />
          {fy && <PayerEditor client={client} fy={fy} />}
          <Section title="Ledger → TDS section mapping" />
          <MappingEditor key={client.id} client={client} />
        </>
      )}
    </>
  );
}

function DataQuality() {
  return (
    <>
      <Section title="Data quality: PAN vs party name (all clients)" />
      <Loaded query={useTdsDataQuality()}>
        {(problems) => {
          if (!problems.length) return <Caption>No party has a PAN that contradicts its name.</Caption>;
          const rows = problems.map((p) => ({
            Level: p.level === "contradiction" ? "⛔ Contradiction" : "⚠ Check",
            Client: p.client_name,
            Party: p.party,
            PAN: p.pan,
            "PAN from": p.pan_source,
            Problem: p.problem,
          }));
          return (
            <>
              <Caption>{problems[0].consequence}</Caption>
              <DataTable rows={rows} columns={autoColumns(rows)} />
            </>
          );
        }}
      </Loaded>
    </>
  );
}
