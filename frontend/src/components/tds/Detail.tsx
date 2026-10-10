/** The click-through TDS panel for one party and section (from the report or an alert). */

import { useState } from "react";
import {
  acknowledgeTdsAlert,
  exportTdsDetail,
  parseMoneyInput,
  saveBlob,
  saveTdsAssignment,
  saveTdsParty,
  saveTdsResolution,
  saveTdsVoucherFlag,
  toNumber,
  type Row,
} from "../../api";
import { PAYEE_TYPES, TDS_STATUS_EMOJI, num2, plainNumber, rs, safeName } from "../../format";
import { useSelection } from "../../state";
import {
  ActionButton,
  Button,
  Caption,
  Checkbox,
  DataTable,
  Expander,
  Grid,
  Metric,
  Notice,
  Section,
  Select,
  TextInput,
  autoColumns,
  toCsv,
  useAction,
} from "../../ui";

const payeeOptions: [string | null, string][] = [[null, "From the PAN"], ...Object.entries(PAYEE_TYPES)];
const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function TdsDetail({ detail, alert }: { detail: Row; alert?: Row | null }) {
  const { party, section: sec, threshold, calculation: calc } = detail;
  const why = sec.why;
  const verdict = why.applies === true ? "Applies" : why.applies === false ? "Does not apply" : "Cannot determine";
  const turnover = why.previous_turnover
    ? `${rs(why.previous_turnover)} (FY ${why.previous_fy})`
    : `not imported (FY ${why.previous_fy})`;
  const flags = [
    party.transporter_declaration && "Transporter declaration (NIL u/s 194C)",
    party.tcs_206c1h && "Seller charges TCS u/s 206C(1H)",
  ].filter(Boolean);
  const hints = [
    party.tally_transporter && "Tally marks this party as a transporter.",
    party.tcs_vouchers && `TCS was charged on ${party.tcs_vouchers} voucher(s) (${rs(party.tcs_amount)}).`,
  ].filter(Boolean);
  const estimate = detail.estimate;
  const other: Row[] = detail.other_section_vouchers ?? [];
  const pool = detail.section_pool;

  const vouchers = detail.vouchers.map((v: Row) => ({
    Date: v.date,
    "Voucher type": v.voucher_type,
    "Voucher no": v.voucher_no,
    Ledger: v.ledger,
    "Amount excl. GST": num2(v.amount),
    "Running total": num2(v.running_total),
    Liable: num2(v.liable),
    "Rate %": plainNumber(v.rate),
    "Threshold crossed": v.is_crossing ? "◀ crossed here" : "",
  }));

  return (
    <div>
      <Section title={`${TDS_STATUS_EMOJI[detail.status] ?? ""} ${party.name} · ${sec.key}`} />
      <Caption>
        {detail.client_name} · FY {detail.fy} · {detail.status_label}
      </Caption>
      {!detail.mapping_approved && (
        <Notice kind="warning">The ledger mapping of this client is not approved yet (Settings → TDS): figures are provisional.</Notice>
      )}

      <Grid cols={2}>
        <div>
          <b>a) Party</b>
          <ul className="plain">
            <li>Name: <b>{party.name}</b></li>
            <li>PAN: <b>{party.pan || "not available"}</b> ({party.pan_source})</li>
            <li>Payee type: <b>{party.payee_type_label}</b> ({party.payee_source})</li>
            <li>GSTIN: {party.gstin || "—"}</li>
            <li>Special flags: {flags.join(", ") || "none"}</li>
          </ul>
          {hints.map((hint) => (
            <Caption key={String(hint)}>Hint: {hint}</Caption>
          ))}
          {party.pan_warning && (
            <Notice kind="error">
              PAN check: {party.pan_warning} {party.pan_warning_consequence}
            </Notice>
          )}
        </div>
        <div>
          <b>b) Section</b>
          <ul className="plain">
            <li><b>{sec.key}</b> – {sec.nature}</li>
            <li>Who must deduct: {sec.who_must_deduct}</li>
            <li>Payee: {sec.payee}</li>
            <li>Client: {why.constitution || "unknown"} ({why.constitution_source})</li>
            <li>Previous-year turnover used: {turnover}</li>
            <li><b>{verdict}:</b> {why.reason}</li>
          </ul>
          {why.needs_confirmation && <Notice kind="warning">Audit liability last year is not confirmed – set it on Settings → TDS.</Notice>}
          {sec.mapped_section && (
            <Notice>Mapped to {sec.mapped_section}, tested u/s 194-IB because the client is an Individual/HUF not liable to audit.</Notice>
          )}
          {sec.provisional && (
            <Notice kind="warning">Provisional: the rent ledger still needs your choice of 194I(a) or 194I(b) (Settings → TDS).</Notice>
          )}
        </div>
      </Grid>

      {detail.unidentified && <Unidentified detail={detail} />}

      <b>c) Threshold test</b>
      <Grid cols={5}>
        <Metric label="Threshold" value={String(threshold.text).replace("Rs ", "₹")} />
        <Metric label="FY aggregate" value={rs(threshold.aggregate)} />
        <Metric label="Largest single payment" value={rs(threshold.largest_single)} />
        <Metric label="Crossed by" value={capitalize(threshold.crossing_test || "not crossed")} />
        <Metric label="Crossed on (voucher)" value={threshold.crossed_on ? `${threshold.crossed_on} · #${threshold.crossed_voucher_no}` : "—"} />
      </Grid>
      {threshold.excess_over_threshold !== null && threshold.excess_over_threshold !== undefined && (
        <Caption>
          194Q: excess over ₹50 lakh = <b>{rs(threshold.excess_over_threshold)}</b> (TDS only on this).
        </Caption>
      )}
      {threshold.approaching && <Caption>Approaching: the aggregate is at or above {threshold.approaching_pct}% of the threshold.</Caption>}
      {threshold.months && Object.keys(threshold.months).length > 0 && (
        <Caption>
          194-IB monthly rent: {Object.entries(threshold.months).map(([k, v]) => `${k}: ${rs(v)}`).join(" · ")}. TDS falls due
          once, in {threshold.deduct_on}.
        </Caption>
      )}

      <b>d) Calculation</b>
      <DataTable
        rows={calc.steps.map((s: Row) => ({ Step: s.step, Value: toNumber(s.value) !== null ? rs(s.value) : s.value, Why: s.note }))}
        columns={[{ key: "Step", label: "Step" }, { key: "Value", label: "Value", align: "right" }, { key: "Why", label: "Why", wide: true }]}
      />
      {calc.likely_cause && <Notice kind="warning">Likely cause: {calc.likely_cause}</Notice>}
      {estimate && (
        <>
          <p>
            <b>Estimated amounts as of {estimate.as_of}</b> · money at stake <b>{rs(estimate.money_at_stake)}</b>
          </p>
          <Notice kind="warning">{estimate.label}</Notice>
          <DataTable
            rows={estimate.lines.map((l: Row) => ({ Item: l.item, Amount: l.amount ? rs(l.amount) : "", Working: l.working }))}
            columns={[{ key: "Item", label: "Item" }, { key: "Amount", label: "Amount", align: "right" }, { key: "Working", label: "Working", wide: true }]}
          />
          {estimate.interest_working?.length > 0 && (
            <Expander title="Interest working - voucher by voucher (grouped by the date the TDS became deductible)">
              {(() => {
                const rows = estimate.interest_working.map((w: Row) => ({
                  "Deductible on": w.deductible_on,
                  Vouchers: w.vouchers,
                  "Unpaid TDS": rs(w.tds),
                  "Deposit due": w.deposit_due,
                  [`Months to ${estimate.as_of}`]: w.months,
                  "Interest 1%": rs(w.interest_1),
                  "Interest 1.5% (alt.)": rs(w.interest_15),
                }));
                return <DataTable rows={rows} columns={autoColumns(rows)} />;
              })()}
            </Expander>
          )}
          <Caption>
            Money at stake = {estimate.money_at_stake_working}. The 1.5% interest is an alternative. {estimate.fee_note}
          </Caption>
        </>
      )}
      {detail.year_end_note && <Notice>{detail.year_end_note}</Notice>}
      {other.length > 0 && <OtherSectionVouchers detail={detail} other={other} />}
      {pool && (
        <Caption>
          Section {sec.key}: {rs(pool.amount)} of TDS was booked in {pool.entries} entry(ies) with no party -{" "}
          {pool.attributed_to ? `attributed to ${pool.attributed_to} (the only party over the threshold)` : "not tied to any party"}.
          Attributed {rs(pool.attributed)} · shared pro rata, not counted {rs(pool.allocated)} · unallocated {rs(pool.unallocated)}.
          Paid to the government per Tally: {rs(pool.deposited)}.
        </Caption>
      )}

      <b>e) Vouchers making up the aggregate</b>
      {vouchers.length > 0 && (
        <>
          <DataTable
            rows={vouchers}
            maxHeight={420}
            rowStyle={(r) => (r["Threshold crossed"] ? { background: "#5b2b2b" } : undefined)}
            columns={Object.keys(vouchers[0]).map((key) => ({
              key,
              label: key,
              align: ["Amount excl. GST", "Running total", "Liable", "Rate %"].includes(key) ? "right" : undefined,
            }))}
          />
          <Button onClick={() => saveBlob(toCsv(vouchers), `TDS_vouchers_${safeName(party.name)}_${sec.key}.csv`)}>Vouchers (CSV)</Button>
        </>
      )}
      {detail.excluded_vouchers.length > 0 && (
        <Caption>{detail.excluded_vouchers.length} voucher(s) left out of 194Q (seller charged TCS u/s 206C(1H)).</Caption>
      )}
      {detail.deductions.length > 0 && (
        <Expander title={`TDS deducted in this party's vouchers (${detail.deductions.length})`}>
          <DataTable rows={detail.deductions} columns={autoColumns(detail.deductions)} />
        </Expander>
      )}

      <b>f) Compliance and consequences</b>
      <Notice kind="warning">{detail.compliance.disclaimer}</Notice>
      <DataTable
        rows={detail.compliance.items.map((item: Row) => ({
          Item: item.item,
          Detail: item.detail,
          Figure: item.figure && toNumber(item.figure) !== null ? rs(item.figure) : item.figure || "",
        }))}
        columns={[{ key: "Item", label: "Item" }, { key: "Detail", label: "Detail", wide: true }, { key: "Figure", label: "Figure", align: "right" }]}
      />

      <b>g) Actions</b>
      <Actions detail={detail} alert={alert ?? null} />
    </div>
  );
}

function Unidentified({ detail }: { detail: Row }) {
  const unidentified = detail.unidentified;
  const alternatives: Row[] = detail.alternatives ?? [];
  const rows = alternatives.map((a) => ({
    "If it is": `u/s ${a.section}`,
    Test: a.test,
    "Crossed?": a.crossed ? "Yes" : "No",
    Rate: `${plainNumber(a.rate)}%`,
    TDS: rs(a.tds),
  }));
  return (
    <>
      <Notice kind="error">{unidentified.message}</Notice>
      {rows.length > 0 && <DataTable rows={rows} columns={autoColumns(rows)} />}
      <Caption>Disallowance u/s 40(a)(ia) if no TDS: {unidentified.disallowance_text}</Caption>
      <AssignForm detail={detail} ledgers={unidentified.ledgers} />
    </>
  );
}

/** Assign the payee (e.g. the landlord) of party-less payments. */
function AssignForm({ detail, ledgers }: { detail: Row; ledgers: string[] }) {
  const [ledger, setLedger] = useState<string>(ledgers[0] ?? "");
  const [name, setName] = useState("");
  const [pan, setPan] = useState("");
  const [payee, setPayee] = useState<string | null>(null);
  return (
    <div className="card">
      <b>Assign the payee of these payments</b>
      <Grid cols="2fr 2fr 1fr 1fr">
        <Select label="Ledger" value={ledger} options={ledgers.map((l): [string, string] => [l, l])} onChange={setLedger} />
        <TextInput label="Payee name (e.g. the landlord)" value={name} onChange={setName} />
        <TextInput label="PAN (optional)" value={pan} onChange={setPan} />
        <Select label="Payee type" value={payee} options={payeeOptions} onChange={setPayee} />
      </Grid>
      <Caption>Every payment of this ledger with no party ledger in the year goes to this payee.</Caption>
      <ActionButton
        primary
        message="Payee assigned; re-checked."
        action={() =>
          saveTdsAssignment(detail.client_id, { fy: detail.fy, ledger, payee_name: name, pan: pan.trim() || null, payee_type: payee })
        }
      >
        Assign payee
      </ActionButton>
    </div>
  );
}

function OtherSectionVouchers({ detail, other }: { detail: Row; other: Row[] }) {
  const sec = detail.section;
  const [chosen, setChosen] = useState<string>(other[0].voucher_key);
  const rows = other.map((o) => ({
    section: o.section,
    date: o.date,
    voucher_type: o.voucher_type,
    voucher_no: o.voucher_no,
    ledger: o.ledger,
    amount: o.amount,
  }));
  return (
    <Expander title={`This party's vouchers under other sections (${other.length}) - move one here if it was booked to the wrong ledger`}>
      <DataTable rows={rows} columns={autoColumns(rows)} />
      <Select
        label="Voucher"
        value={chosen}
        options={other.map((o): [string, string] => [
          o.voucher_key,
          `${o.date} · ${o.voucher_type} ${o.voucher_no} · ${o.ledger} · ${rs(o.amount)} (${o.section})`,
        ])}
        onChange={setChosen}
      />
      <div className="row">
        <ActionButton
          message="Moved; re-checked."
          action={() => saveTdsVoucherFlag(detail.client_id, chosen, true, "Moved by the CA", `section:${sec.key}`)}
        >
          Count this voucher under {sec.key}
        </ActionButton>
      </div>
    </Expander>
  );
}

function Actions({ detail, alert }: { detail: Row; alert: Row | null }) {
  const { reviewer: rawReviewer } = useSelection();
  const reviewer = rawReviewer.trim();
  const { party, section: sec, calculation: calc, client_id: clientId, fy } = detail;
  const stem = `TDS_${sec.key}_${safeName(party.name)}_FY${fy}`;
  const [ackNote, setAckNote] = useState("");
  const [outAmount, setOutAmount] = useState("");
  const [outNote, setOutNote] = useState("");

  return (
    <>
      <Grid cols={3}>
        <div className="stack">
          {alert === null ? (
            <Caption>Open this from an alert to acknowledge it.</Caption>
          ) : alert.acknowledged ? (
            <Notice kind="success">
              Acknowledged by {alert.acknowledged_by}
              {alert.note ? `: ${alert.note}` : ""}
            </Notice>
          ) : (
            <>
              <TextInput label="Note" value={ackNote} onChange={setAckNote} placeholder="What was done / agreed" />
              <ActionButton
                primary
                disabled={!reviewer}
                title={reviewer ? undefined : "Enter a reviewer name in the sidebar"}
                message="Acknowledged."
                action={() => acknowledgeTdsAlert(alert.id, reviewer, ackNote || null)}
              >
                Mark as acknowledged
              </ActionButton>
            </>
          )}
        </div>
        <div className="stack">
          {calc.deducted_outside !== null && calc.deducted_outside !== undefined ? (
            <>
              <Notice>
                Marked as deducted outside Tally: {rs(calc.deducted_outside)}
                {calc.outside_note ? ` – ${calc.outside_note}` : ""}
              </Notice>
              <ActionButton
                message="Mark removed."
                action={() => saveTdsResolution(clientId, { fy, party_key: party.key, section_key: sec.key, remove: true })}
              >
                Remove the mark
              </ActionButton>
            </>
          ) : (
            <>
              <TextInput label="Amount deducted outside Tally (blank = the full shortfall)" value={outAmount} onChange={setOutAmount} />
              <TextInput label="Note (challan / voucher reference)" value={outNote} onChange={setOutNote} />
              <ActionButton
                message="Marked."
                action={() =>
                  saveTdsResolution(clientId, {
                    fy,
                    party_key: party.key,
                    section_key: sec.key,
                    amount: parseMoneyInput(outAmount),
                    note: outNote || null,
                    marked_by: reviewer || null,
                  })
                }
              >
                Mark as 'TDS deducted outside Tally'
              </ActionButton>
            </>
          )}
        </div>
        <div className="stack">
          <ActionButton action={() => exportTdsDetail(clientId, fy, party.key, sec.key, "xlsx", `${stem}.xlsx`)}>
            Export working (Excel)
          </ActionButton>
          <ActionButton action={() => exportTdsDetail(clientId, fy, party.key, sec.key, "pdf", `${stem}.pdf`)}>
            Export working (PDF)
          </ActionButton>
        </div>
      </Grid>
      {party.identified && <PartyFacts detail={detail} />}
      {party.identified && sec.key === "194Q" && detail.vouchers.length > 0 && <Tcs194Q detail={detail} />}
    </>
  );
}

function PartyFacts({ detail }: { detail: Row }) {
  const { party, client_id: clientId } = detail;
  const [pan, setPan] = useState(party.pan_source === "entered by user" ? party.pan ?? "" : "");
  const [payee, setPayee] = useState<string | null>(party.payee_source === "set by user" ? party.payee_type : null);
  const [transporter, setTransporter] = useState<boolean>(party.transporter_declaration);
  const [tcs, setTcs] = useState<boolean>(party.tcs_206c1h);
  const [note, setNote] = useState<string>(party.note ?? "");
  return (
    <Expander title="Party facts and special cases (PAN, payee type, declarations)">
      <div className="stack">
        <TextInput label="PAN (overrides GSTIN / Tally)" value={pan} onChange={setPan} />
        <Select label="Payee type override" value={payee} options={payeeOptions} onChange={setPayee} />
        <Checkbox
          label="Transporter (≤ 10 goods carriages) has given a declaration with PAN – NIL TDS u/s 194C"
          checked={transporter}
          onChange={setTransporter}
        />
        <Checkbox label="Seller charges TCS u/s 206C(1H) on all sales to the client – no 194Q" checked={tcs} onChange={setTcs} />
        <TextInput label="Note" value={note} onChange={setNote} />
        <div>
          <ActionButton
            primary
            message="Saved and re-checked."
            action={() =>
              saveTdsParty(clientId, {
                party_key: party.key,
                party_name: party.name,
                pan: pan.trim() || null,
                payee_type: payee,
                transporter_declaration: transporter,
                tcs_206c1h: tcs,
                note: note || null,
              })
            }
          >
            Save party facts
          </ActionButton>
        </div>
      </div>
    </Expander>
  );
}

function Tcs194Q({ detail }: { detail: Row }) {
  const clientId = detail.client_id;
  const [chosen, setChosen] = useState<number[]>([]);
  const { run, error } = useAction();
  const rows = detail.vouchers.map((v: Row) => ({
    Date: v.date,
    Voucher: `${v.voucher_type} ${v.voucher_no}`,
    Amount: rs(v.amount),
  }));
  return (
    <Expander title="Leave single vouchers out of 194Q (seller charged TCS u/s 206C(1H))">
      <DataTable rows={rows} columns={autoColumns(rows)} selection="multi" selected={chosen} onSelect={setChosen} maxHeight={300} />
      <ActionButton
        disabled={!chosen.length}
        message="Re-checked."
        action={async () => {
          for (const i of chosen) await saveTdsVoucherFlag(clientId, detail.vouchers[i].voucher_key, true);
          setChosen([]);
        }}
      >
        Leave out
      </ActionButton>
      {detail.excluded_vouchers.map((item: Row) => (
        <div className="row" key={item.voucher_key}>
          <Caption>
            Left out: {item.date} · {item.voucher_type} {item.voucher_no} · {rs(item.amount)}
          </Caption>
          <Button onClick={() => run(() => saveTdsVoucherFlag(clientId, item.voucher_key, false), "Re-checked.")}>Include</Button>
        </div>
      ))}
      {error && <Notice kind="error">{error}</Notice>}
    </Expander>
  );
}
