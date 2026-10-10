/** Settings -> TDS editors: rate & threshold master, who must deduct, ledger mapping. */

import { useEffect, useMemo, useState } from "react";
import {
  approveTdsMapping,
  createTdsSection,
  deleteTdsSection,
  normDecimal,
  refreshTdsMapping,
  saveTdsPayer,
  saveTdsSettings,
  updateTdsMapping,
  updateTdsSection,
  useTdsMapping,
  useTdsPayer,
  useTdsSections,
  useTdsSettings,
  type Row,
} from "../../api";
import { MAPPABLE_SECTIONS, ROLE_LABELS, SECTIONS, fyLabel, num2, plainNumber, rs } from "../../format";
import { useSelection } from "../../state";
import {
  ActionButton,
  Caption,
  DataTable,
  Expander,
  Grid,
  Loaded,
  Notice,
  Radio,
  Segmented,
  Select,
  TextInput,
  autoColumns,
  useToast,
} from "../../ui";

/** Equal as amounts when both are numbers ('30000' == '30000.00'), else as text. */
function same(a: unknown, b: unknown): boolean {
  const x = normDecimal(a);
  const y = normDecimal(b);
  if (x !== null && y !== null) return x === y;
  return String(a ?? "") === String(b ?? "");
}

function numberText(value: string): string {
  const n = normDecimal(value.replace(/,/g, ""));
  if (n === null) throw new Error(`'${value}' is not a number`);
  return n;
}

// ------------------------------------------------------------ rate master

export function RateMaster() {
  const sections = useTdsSections();
  const settings = useTdsSettings();
  return (
    <Loaded query={sections}>
      {(data) => (
        <>
          <Notice>{data.note}</Notice>
          <Loaded query={settings}>{(s) => <TdsSettingsForm settings={s} />}</Loaded>
          <MasterTable rows={data.sections} />
          <AddRateRow rows={data.sections} />
        </>
      )}
    </Loaded>
  );
}

function TdsSettingsForm({ settings }: { settings: Row }) {
  const start = Number(String(settings.analysis_fy).slice(0, 4));
  const years = [-2, -1, 0, 1, 2].map((d) => fyLabel(start + d));
  const [fy, setFy] = useState<string>(settings.analysis_fy);
  const [pct, setPct] = useState(plainNumber(settings.approaching_pct));
  const [materiality, setMateriality] = useState(plainNumber(settings.unidentified_min));
  return (
    <div className="card">
      <Grid cols={2}>
        <Select
          label="TDS analysis year"
          help="TDS is analysed and alerted for this year only; the year before is used only to decide who must deduct (194Q turnover test, 44AB audit test). Move it forward each year."
          value={fy}
          options={years.map((y): [string, string] => [y, y])}
          onChange={setFy}
        />
        <TextInput label="Raise 'approaching threshold' alerts at (% of the threshold)" type="number" value={pct} onChange={setPct} />
      </Grid>
      <TextInput
        label="Payments with no party ledger: alert above (₹)"
        help="Below this total they are listed in the report but not alerted. Default ₹30,000, the lowest TDS threshold."
        type="number"
        value={materiality}
        onChange={setMateriality}
      />
      <div className="row">
        <ActionButton
          message="Saved."
          action={() => {
            const p = Number(pct);
            if (!(p >= 1 && p <= 100)) throw new Error("The approaching % must be between 1 and 100.");
            if (!(Number(materiality) >= 0)) throw new Error("The amount must be zero or more.");
            return saveTdsSettings(numberText(pct), fy, numberText(materiality));
          }}
        >
          Save and re-check
        </ActionButton>
      </div>
    </div>
  );
}

type MasterEdit = Record<string, string> & { delete?: any };

const MASTER_FIELDS: [string, string][] = [
  ["nature", "Nature of payment"],
  ["single_threshold", "Single payment threshold (0 = n/a)"],
  ["aggregate_threshold", "Annual / aggregate threshold"],
  ["rate_individual", "Rate Ind/HUF %"],
  ["rate_other", "Rate other %"],
  ["rate_no_pan", "Rate no PAN %"],
  ["base", "Base"],
  ["effective_from", "Effective from"],
  ["remarks", "Remarks"],
];

function MasterTable({ rows }: { rows: Row[] }) {
  const toast = useToast();
  const initial = useMemo(
    () =>
      Object.fromEntries(
        rows.map((r) => [
          r.id,
          {
            nature: r.nature,
            single_threshold: plainNumber(r.single_threshold),
            aggregate_threshold: plainNumber(r.aggregate_threshold),
            rate_individual: plainNumber(r.rate_individual),
            rate_other: plainNumber(r.rate_other),
            rate_no_pan: plainNumber(r.rate_no_pan),
            base: r.base,
            effective_from: r.effective_from,
            remarks: r.remarks ?? "",
            delete: "",
          } as MasterEdit,
        ]),
      ),
    [rows],
  );
  const [edits, setEdits] = useState<typeof initial>(initial);
  useEffect(() => setEdits(initial), [initial]);
  const set = (id: number, field: string, value: string) => setEdits((e: typeof initial) => ({ ...e, [id]: { ...e[id], [field]: value } }));

  const save = async () => {
    const originals = Object.fromEntries(rows.map((r) => [r.id, r]));
    for (const row of rows) {
      const edit = edits[row.id];
      if (edit.delete) {
        await deleteTdsSection(row.id);
        continue;
      }
      const body: Row = {
        key: row.key,
        nature: edit.nature.trim(),
        single_threshold: numberText(edit.single_threshold),
        aggregate_threshold: numberText(edit.aggregate_threshold),
        rate_individual: numberText(edit.rate_individual),
        rate_other: numberText(edit.rate_other),
        rate_no_pan: numberText(edit.rate_no_pan),
        base: edit.base,
        effective_from: edit.effective_from,
        remarks: edit.remarks.trim() || null,
      };
      const old = originals[row.id];
      if (Object.keys(body).some((k) => !same(body[k], old[k]))) await updateTdsSection(row.id, body);
    }
    toast("Rate master saved; every client was re-checked.");
  };

  const input = (row: Row, field: string) => {
    const value = edits[row.id]?.[field] ?? "";
    if (field === "base") {
      return (
        <select value={value} onChange={(e) => set(row.id, field, e.target.value)}>
          <option value="full">full</option>
          <option value="excess">excess</option>
        </select>
      );
    }
    return (
      <input
        type={field === "effective_from" ? "date" : "text"}
        value={value}
        style={field === "remarks" || field === "nature" ? { minWidth: 220 } : undefined}
        onChange={(e) => set(row.id, field, e.target.value)}
      />
    );
  };

  return (
    <>
      <Caption>
        Each row applies from its effective-from date: a later row for the same section changes the rate from that date, and
        earlier years keep theirs. 'Excess' = TDS only on the amount above the threshold (194Q).
      </Caption>
      <DataTable
        rows={rows}
        columns={[
          { key: "key", label: "Key" },
          ...MASTER_FIELDS.map(([field, label]) => ({ key: field, label, render: (r: Row) => input(r, field) })),
          {
            key: "delete",
            label: <span title="Tick to delete the row">Delete</span>,
            render: (r: Row) => (
              <input type="checkbox" checked={!!edits[r.id]?.delete} onChange={(e) => set(r.id, "delete", e.target.checked ? "1" : "")} />
            ),
          },
        ]}
      />
      <ActionButton primary action={save}>
        💾 Save rate master
      </ActionButton>
    </>
  );
}

function AddRateRow({ rows }: { rows: Row[] }) {
  const [key, setKey] = useState(SECTIONS[0]);
  const base = rows.filter((r) => r.key === key).sort((a, b) => String(b.effective_from).localeCompare(a.effective_from))[0];
  const [effective, setEffective] = useState(new Date().toISOString().slice(0, 10));
  const [values, setValues] = useState<Record<string, string>>({});
  const [remarks, setRemarks] = useState("");
  useEffect(() => {
    setValues({
      single_threshold: base ? String(base.single_threshold) : "0",
      aggregate_threshold: base ? String(base.aggregate_threshold) : "0",
      rate_individual: base ? String(base.rate_individual) : "",
      rate_other: base ? String(base.rate_other) : "",
      rate_no_pan: base ? String(base.rate_no_pan) : "20",
    });
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const field = (name: string, label: string) => (
    <TextInput label={label} value={values[name] ?? ""} onChange={(v) => setValues((s) => ({ ...s, [name]: v }))} />
  );
  return (
    <Expander title="Add a rate change (a new row from a later date)">
      <div className="stack">
        <Select label="Section" value={key} options={SECTIONS.map((s): [string, string] => [s, s])} onChange={setKey} />
        <TextInput label="Effective from" type="date" value={effective} onChange={setEffective} />
        <Grid cols={5}>
          {field("single_threshold", "Single payment threshold")}
          {field("aggregate_threshold", "Aggregate threshold")}
          {field("rate_individual", "Rate Ind/HUF %")}
          {field("rate_other", "Rate other %")}
          {field("rate_no_pan", "Rate no PAN %")}
        </Grid>
        <TextInput label="Remarks (source of the change)" value={remarks} onChange={setRemarks} />
        <div>
          <ActionButton
            message="Row added."
            action={() =>
              createTdsSection({
                key,
                nature: base ? base.nature : key,
                ...values,
                base: base ? base.base : "full",
                effective_from: effective,
                remarks: remarks || null,
              })
            }
          >
            Add row
          </ActionButton>
        </div>
      </div>
    </Expander>
  );
}

// ------------------------------------------------------------ who must deduct

/** Constitution and audit-liability overrides for a client and FY. */
export function PayerEditor({ client, fy }: { client: Row; fy: string }) {
  return (
    <Loaded query={useTdsPayer(client.id, fy)}>{(payer) => <PayerForm key={`${client.id}|${fy}`} client={client} fy={fy} payer={payer} />}</Loaded>
  );
}

function PayerForm({ client, fy, payer }: { client: Row; fy: string; payer: Row }) {
  const [constitution, setConstitution] = useState<string | null>(payer.constitution_override);
  const [audit, setAudit] = useState<boolean | null>(payer.audit_override);
  const turnover = payer.previous_turnover ? rs(payer.previous_turnover) : "not imported";
  return (
    <>
      <p>
        <b>
          {client.name} – FY {fy}.
        </b>{" "}
        Constitution: <b>{payer.constitution || "unknown"}</b> ({payer.constitution_source}). FY {payer.previous_fy} turnover:{" "}
        <b>{turnover}</b>.
      </p>
      <ul className="plain">
        <li>194Q: {payer.s194q.reason}</li>
        <li>194C / H / J / I: {payer.others.reason}</li>
      </ul>
      <div className="card stack">
        <Select
          label="Constitution"
          value={constitution}
          options={[[null, "From the PAN (GSTIN)"], ...payer.constitutions.map((c: string): [string, string] => [c, c])]}
          onChange={setConstitution}
        />
        <Radio
          horizontal
          label={`Liable to tax audit u/s 44AB in FY ${payer.previous_fy}? (decides 194C/H/J/I for an Individual/HUF)`}
          value={audit}
          options={[
            [null, "Not confirmed (use the turnover test)"],
            [true, "Yes"],
            [false, "No"],
          ]}
          onChange={setAudit}
        />
        <div>
          <ActionButton primary message="Saved." action={() => saveTdsPayer(client.id, fy, constitution, audit)}>
            Save and re-check
          </ActionButton>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------ ledger mapping

type MapEdit = { role: string; section: string | null; confirm: boolean };
const hasPostings = (r: Row) => Object.values(r.amounts as Record<string, string>).some((v) => Number(v) !== 0);

/** The proposed ledger -> section mapping, editable, with approval. */
export function MappingEditor({ client }: { client: Row }) {
  return <Loaded query={useTdsMapping(client.id)}>{(mapping) => <Mapping client={client} mapping={mapping} />}</Loaded>;
}

function Mapping({ client, mapping }: { client: Row; mapping: Row }) {
  const { reviewer: rawReviewer } = useSelection();
  const reviewer = rawReviewer.trim();
  const toast = useToast();
  const rows: Row[] = mapping.rows;
  const [show, setShow] = useState<"With postings" | "Unmapped" | "All">("With postings");
  const initial = useMemo(
    () => Object.fromEntries(rows.filter((r) => r.match_type === "ledger").map((r) => [r.name, { role: r.role, section: r.section_key, confirm: false } as MapEdit])),
    [rows],
  );
  const [edits, setEdits] = useState<typeof initial>(initial);
  useEffect(() => setEdits(initial), [initial]);

  if (!rows.length) return <Notice>No ledgers yet: import this client's Tally JSON export (Master + Transactions).</Notice>;

  const fys = [...new Set(rows.flatMap((r) => Object.keys(r.amounts)))].sort().reverse();
  let ledgers = rows.filter((r) => r.match_type === "ledger");
  if (show === "With postings") ledgers = ledgers.filter(hasPostings);
  else if (show === "Unmapped") ledgers = ledgers.filter((r) => r.role === "unmapped");
  const unmappedActive = rows.filter((r) => r.match_type === "ledger" && r.role === "unmapped" && hasPostings(r));
  const set = (name: string, patch: Partial<MapEdit>) => setEdits((e: typeof initial) => ({ ...e, [name]: { ...e[name], ...patch } }));

  const save = async () => {
    const changes: Row[] = [];
    for (const old of ledgers) {
      const edit = edits[old.name];
      const confirmed = !!(old.choice_pending && edit.confirm);
      if (confirmed && !["194I(a)", "194I(b)"].includes(edit.section ?? "") && ["base", "tds"].includes(edit.role)) {
        throw new Error(`${old.name}: choose 194I(a) or 194I(b) before confirming.`);
      }
      if (edit.role !== old.role || edit.section !== old.section_key || confirmed) {
        changes.push({ match_type: "ledger", name: old.name, role: edit.role, section_key: edit.section });
      }
    }
    if (!changes.length) {
      toast("Nothing changed.");
      return;
    }
    await updateTdsMapping(client.id, changes);
    toast(`Saved ${changes.length} change(s); re-checked.`);
  };

  return (
    <>
      {mapping.approved_at ? (
        <Notice kind="success">
          Mapping approved by {mapping.approved_by} on {String(mapping.approved_at).slice(0, 10)}. TDS alerts are on.
        </Notice>
      ) : (
        <Notice kind="warning">Proposed mapping – review it, correct it, then approve. TDS alerts start only after approval.</Notice>
      )}
      <Segmented label="Show" options={["With postings", "Unmapped", "All"]} value={show} onChange={setShow} />
      {unmappedActive.length > 0 && (
        <Notice kind="error">Unmapped ledgers with payments: {unmappedActive.map((r) => r.name).join(", ")}</Notice>
      )}
      {ledgers.map((r) => (
        <div key={`hint-${r.name}`}>
          {r.hint && (
            <Notice kind="warning">
              <b>{r.name}</b> – {r.hint}
            </Notice>
          )}
          {r.rent_comparison && (
            (() => {
              const cmp = r.rent_comparison.map((c: Row) => ({
                "If it is": `${c.section} at ${plainNumber(c.rate)}%`,
                [`Rent (FY ${c.fy})`]: rs(c.rent),
                "TDS due": rs(c.tds_due),
                "TDS on rent in the books": rs(c.deducted),
                "Books minus due": rs(c.difference),
              }));
              return <DataTable rows={cmp} columns={autoColumns(cmp)} />;
            })()
          )}
        </div>
      ))}
      <DataTable
        rows={ledgers}
        columns={[
          { key: "name", label: "Ledger" },
          { key: "kind", label: "Kind", render: (r) => r.kind || "" },
          { key: "groups", label: "Group", render: (r) => [...r.groups.slice(0, 3)].reverse().join(" > ") },
          ...fys.map((fy) => ({
            key: `fy-${fy}`,
            label: <span title="Net debit posted in the year">FY {fy}</span>,
            align: "right" as const,
            render: (r: Row) => num2(r.amounts[fy] ?? "0"),
          })),
          {
            key: "confirm",
            label: (
              <span title="Rent ledgers only: set Section to 194I(a) (plant, machinery, equipment - 2%) or 194I(b) (land, building, furniture - 10%) and tick to confirm. Needed before approval.">
                Rent: confirm section
              </span>
            ),
            render: (r) =>
              r.choice_pending ? (
                <input type="checkbox" checked={!!edits[r.name]?.confirm} onChange={(e) => set(r.name, { confirm: e.target.checked })} />
              ) : null,
          },
          {
            key: "role",
            label: <span title={Object.entries(ROLE_LABELS).map(([k, v]) => `${k} = ${v}`).join("; ")}>Role</span>,
            render: (r) => (
              <select value={edits[r.name]?.role ?? r.role} onChange={(e) => set(r.name, { role: e.target.value })}>
                {Object.keys(ROLE_LABELS).map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </select>
            ),
          },
          {
            key: "section",
            label: "Section",
            render: (r) => (
              <select value={edits[r.name]?.section ?? ""} onChange={(e) => set(r.name, { section: e.target.value || null })}>
                <option value="">—</option>
                {MAPPABLE_SECTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            ),
          },
          { key: "reason", label: "Why", wide: true, render: (r) => r.reason || "" },
          { key: "source", label: "By" },
        ]}
      />
      <Grid cols={3}>
        <ActionButton primary action={save}>
          💾 Save mapping changes
        </ActionButton>
        {mapping.approved_at ? (
          <ActionButton message="Approval withdrawn." action={() => approveTdsMapping(client.id, reviewer || "user", false)}>
            Withdraw approval (stop TDS alerts)
          </ActionButton>
        ) : (
          <ActionButton
            disabled={!reviewer}
            title={reviewer ? undefined : "Enter a reviewer name in the sidebar"}
            message="Approved: TDS alerts are on for this client."
            action={() => approveTdsMapping(client.id, reviewer)}
          >
            ✔ Approve mapping
          </ActionButton>
        )}
        <ActionButton
          title="Re-applies the rules to ledgers nobody has edited or approved"
          message="Proposals refreshed."
          action={() => refreshTdsMapping(client.id)}
        >
          Re-run proposals
        </ActionButton>
      </Grid>
      <GroupRules client={client} groups={rows.filter((r) => r.match_type === "group")} />
    </>
  );
}

function GroupRules({ client, groups }: { client: Row; groups: Row[] }) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("base");
  const [section, setSection] = useState<string | null>(null);
  return (
    <Expander title={`Group rules (${groups.length}) – map every ledger under a Tally group`}>
      {groups.map((rule) => (
        <Grid key={rule.id} cols="3fr 2fr 1fr">
          <span>{rule.name}</span>
          <span>
            {rule.role} {rule.section_key ?? ""}
          </span>
          <ActionButton
            message="Deleted."
            action={() => updateTdsMapping(client.id, [{ match_type: "group", name: rule.name, role: rule.role, delete: true }])}
          >
            Delete
          </ActionButton>
        </Grid>
      ))}
      <div className="card">
        <Grid cols={3}>
          <TextInput label="Tally group name" value={name} onChange={setName} />
          <Select
            label="Role"
            value={role}
            options={Object.entries(ROLE_LABELS).slice(0, 3).map(([k, v]): [string, string] => [k, v])}
            onChange={setRole}
          />
          <Select label="Section" value={section} options={[[null, "—"], ...SECTIONS.map((s): [string, string] => [s, s])]} onChange={setSection} />
        </Grid>
        <Caption>A ledger's own rule always beats a group rule.</Caption>
        <ActionButton
          disabled={!name.trim()}
          message="Group rule added."
          action={async () => {
            await updateTdsMapping(client.id, [{ match_type: "group", name: name.trim(), role, section_key: section }]);
            setName("");
          }}
        >
          Add group rule
        </ActionButton>
      </div>
    </Expander>
  );
}

