/** The TDS Applicability report (with click-through to the party detail) and the
 * Client Report's TDS summary block. */

import { useState } from "react";
import { exportTdsReport, toNumber, useTdsAnalysisFy, useTdsCompleteness, useTdsDetail, useTdsReport, type Row } from "../../api";
import { TDS_STATUS_EMOJI, num2, rs, safeName } from "../../format";
import { ActionButton, Button, Caption, DataTable, Expander, Grid, KpiCard, Loaded, Notice, Section, Toggle, autoColumns } from "../../ui";
import TdsDetail from "./Detail";

/** TDS block of the Client Report. */
export function TdsSummaryBlock({ tds }: { tds: Row | null | undefined }) {
  const target = useTdsAnalysisFy();
  if (!tds) {
    return (
      <>
        <Section title="TDS summary" />
        <Caption>{target ? `TDS analysis is available for FY ${target} only.` : "No TDS analysis."}</Caption>
      </>
    );
  }
  if (!tds.has_data) {
    return (
      <>
        <Section title="TDS summary" />
        <Caption>No ledger-level Tally data for this year: import the Tally JSON export to analyse TDS.</Caption>
      </>
    );
  }
  const extras = [
    tds.parties_excess && `🔵 ${tds.parties_excess} excess deducted (${rs(tds.excess)})`,
    tds.unidentified && `🟠 ${tds.unidentified} payee(s) unidentified`,
    toNumber(tds.unallocated) && `TDS booked without a party, unallocated: ${rs(tds.unallocated)}`,
  ].filter(Boolean);
  return (
    <>
      <Section title="TDS summary" />
      <Grid cols={4}>
        <KpiCard label="Parties over a TDS threshold" value={tds.parties_crossed} />
        <KpiCard label="TDS payable" value={rs(tds.tds_payable)} />
        <KpiCard label="TDS deducted" value={rs(tds.tds_deducted)} />
        <KpiCard
          label="TDS not deducted"
          value={rs(tds.not_deducted)}
          delta={`${tds.parties_not_deducted} not deducted · ${tds.parties_short} short`}
        />
      </Grid>
      {extras.length > 0 && <Caption>{extras.join(" · ")}</Caption>}
      {!tds.mapping_approved && <Caption>Provisional: approve the ledger mapping on Settings → TDS.</Caption>}
      {tds.unmapped_ledgers > 0 && <Caption>{tds.unmapped_ledgers} ledger(s) with postings are not mapped to a TDS section.</Caption>}
      <Caption>Party-wise detail: the TDS tab.</Caption>
    </>
  );
}

function PayerBox({ report }: { report: Row }) {
  const payer = report.payer;
  const turnover = payer.previous_turnover ? rs(payer.previous_turnover) : "not imported";
  const icon = (v: boolean | null) => (v === true ? "✅" : v === false ? "⛔" : "❔");
  return (
    <>
      <p>
        <b>Who must deduct (FY {report.fy})</b> · client: {payer.constitution || "unknown"} ({payer.constitution_source}) · FY{" "}
        {payer.previous_fy} turnover: {turnover}
      </p>
      <ul className="plain">
        <li>
          {icon(payer.s194q.applies)} <b>194Q:</b> {payer.s194q.reason}
        </li>
        <li>
          {icon(payer.others.applies)} <b>194C / 194H / 194J / 194I:</b> {payer.others.reason}
        </li>
      </ul>
      {payer.rent_under_194ib && <Caption>Rent is tested u/s 194-IB (2%, once a year) instead of 194I.</Caption>}
    </>
  );
}

const statusCell = (r: Row) => `${TDS_STATUS_EMOJI[r.status] ?? ""} ${r.status_label}`;
const MONEY = { align: "right" as const };

export function TdsReport({ client, fy }: { client: Row; fy: string | null }) {
  const target = useTdsAnalysisFy();
  const enabled = !!fy && (!target || fy === target);
  const query = useTdsReport(client.id, fy ?? "", enabled);
  const [onlyCrossed, setOnlyCrossed] = useState(true);
  const [open, setOpen] = useState<[string, string] | null>(null);

  if (!fy) return <Notice>Select the Viewing year in the sidebar.</Notice>;
  if (target && fy !== target) {
    return (
      <Notice>
        TDS analysis is available for FY {target} only. Choose FY {target} in the sidebar (the year is set on Settings → TDS).
      </Notice>
    );
  }
  return (
    <Loaded query={query} text="Working out TDS…">
      {(report) => {
        if (!report.has_data) {
          return (
            <Notice>
              No ledger-level Tally data for this year. Import the Tally JSON export (Master + Transactions) on the Data page.
            </Notice>
          );
        }
        const totals = report.totals;
        const ranked: Row[] = report.ranked ?? [];
        const fees: Row[] = report.return_fees ?? [];
        const isOpen = (partyKey: string, section: string) => open?.[0] === partyKey && open?.[1] === section;
        const stem = `TDS_Applicability_${safeName(client.name)}_FY${fy}`;
        return (
          <>
            {!report.mapping_approved && (
              <Notice kind="warning">Ledger mapping not yet approved: review it on Settings → TDS. Alerts start after approval.</Notice>
            )}
            <PayerBox report={report} />
            {report.pending_choice?.length > 0 && (
              <Notice kind="warning">
                Choose 194I(a) or 194I(b) for these rent ledgers on Settings → TDS (their figures are provisional):{" "}
                {report.pending_choice.join(", ")}
              </Notice>
            )}
            {(report.data_quality ?? []).map((item: Row, i: number) => (
              <Notice key={i} kind={item.level === "contradiction" ? "error" : "warning"}>
                PAN check – {item.party}: {item.problem}
              </Notice>
            ))}
            <Grid cols={4}>
              <KpiCard label="Parties over a threshold" value={totals.crossed} />
              <KpiCard label="TDS payable" value={rs(totals.tds)} />
              <KpiCard label="TDS deducted" value={rs(totals.deducted)} />
              <KpiCard
                label="Total shortfall"
                value={rs(totals.shortfall)}
                delta={`${totals.not_deducted} not deducted · ${totals.short} short`}
              />
            </Grid>
            <Caption>
              🔵 Excess deducted: {rs(totals.excess)} ({totals.excess_parties} party/ies) · TDS booked without a party:{" "}
              {rs(totals.booked_without_party)}, of which unallocated <b>{rs(totals.unallocated)}</b> · payments with no party
              ledger (not in the totals): {rs(totals.unidentified_payments)}
            </Caption>

            {ranked.length > 0 && (
              <>
                <Section title={`Ranked by money at stake · total ${rs(report.money_at_stake)}`} />
                <Caption>{report.estimate_label}</Caption>
                <DataTable
                  rows={ranked.map((r, i): Row => ({ ...r, "#": i + 1 }))}
                  selection="single"
                  selected={ranked.flatMap((r, i) => (isOpen(r.party_key, r.section) ? [i] : []))}
                  onSelect={(s) => setOpen(s.length ? [ranked[s[0]].party_key, ranked[s[0]].section] : null)}
                  columns={[
                    { key: "#", label: "#" },
                    { key: "status", label: "Status", render: statusCell },
                    { key: "party", label: "Party" },
                    { key: "section", label: "Section" },
                    { key: "aggregate", label: "Aggregate", ...MONEY, render: (r) => num2(r.aggregate) },
                    { key: "shortfall", label: "Shortfall", ...MONEY, render: (r) => num2(r.shortfall) },
                    { key: "excess", label: "Excess", ...MONEY, render: (r) => num2(r.excess) },
                    { key: "money_at_stake", label: "Money at stake", ...MONEY, render: (r) => num2(r.money_at_stake) },
                  ]}
                />
                <Section title="By section (Applicability Checker layout)" />
              </>
            )}
            <Toggle label="Only parties over or near a threshold" checked={onlyCrossed} onChange={setOnlyCrossed} />
            {report.sections.map((sec: Row) => {
              const rows: Row[] = onlyCrossed
                ? sec.rows.filter((r: Row) => String(r.crossed).startsWith("Yes") || ["tds_approaching", "tds_unidentified"].includes(r.status))
                : sec.rows;
              const sub = sec.subtotal;
              const pool = sec.pool;
              return (
                <Expander
                  key={sec.key}
                  defaultOpen={(toNumber(sub.shortfall) ?? 0) > 0}
                  title={`${sec.key} – ${sec.nature} · ${sub.crossed} over threshold · shortfall ${rs(sub.shortfall)}`}
                >
                  {sec.applies === false && <Caption>Does not apply to this client: {sec.reason}</Caption>}
                  {!rows.length ? (
                    <Caption>No party over or near the threshold.</Caption>
                  ) : (
                    <>
                      <DataTable
                        rows={rows}
                        selection="single"
                        selected={rows.flatMap((r, i) => (isOpen(r.party_key, sec.key) ? [i] : []))}
                        onSelect={(s) => setOpen(s.length ? [rows[s[0]].party_key, sec.key] : null)}
                        columns={[
                          { key: "status", label: "Status", render: statusCell },
                          { key: "party", label: "Party" },
                          { key: "pan", label: "PAN", render: (r) => r.pan || "not available" },
                          { key: "payee_type", label: "Payee type" },
                          { key: "single_payment", label: "Largest single", ...MONEY, render: (r) => num2(r.single_payment) },
                          { key: "aggregate", label: "Aggregate", ...MONEY, render: (r) => num2(r.aggregate) },
                          { key: "crossed", label: "Crossed?", render: (r) => (String(r.crossed).startsWith("Yes") ? "Yes" : "No") },
                          { key: "crossed_on", label: "Crossed on", render: (r) => (r.crossed_on ? `${r.crossed_on} #${r.crossed_voucher_no}` : "") },
                          { key: "base", label: "Base", ...MONEY, render: (r) => num2(r.base) },
                          { key: "rate", label: "Rate" },
                          { key: "tds", label: "TDS", ...MONEY, render: (r) => num2(r.tds) },
                          { key: "deducted", label: "Deducted", ...MONEY, render: (r) => num2(r.deducted) },
                          { key: "shortfall", label: "Shortfall", ...MONEY, render: (r) => num2(r.shortfall) },
                          { key: "excess", label: "Excess", ...MONEY, render: (r) => num2(r.excess) },
                          { key: "money_at_stake", label: "Money at stake", ...MONEY, render: (r) => num2(r.money_at_stake) },
                        ]}
                      />
                      <Caption>
                        Subtotal {sec.key}: aggregate {rs(sub.aggregate)} · TDS {rs(sub.tds)} · deducted {rs(sub.deducted)} ·
                        shortfall {rs(sub.shortfall)}
                      </Caption>
                      {pool && (
                        <Caption>
                          TDS booked without a party: {rs(pool.amount)} –{" "}
                          {pool.attributed_to ? `attributed to ${pool.attributed_to}` : "not tied to a party"}; unallocated{" "}
                          {rs(pool.unallocated)}.
                        </Caption>
                      )}
                    </>
                  )}
                </Expander>
              );
            })}
            <p>
              <b>Grand total shortfall: {rs(totals.shortfall)}</b> (TDS payable {rs(totals.tds)}, deducted {rs(totals.deducted)})
            </p>
            {fees.length > 0 && (
              <>
                <Section title={`s.234E late fee - per return (all parties) · at risk ${rs(report.return_fee_total)}`} />
                <Caption>{report.estimate_label}</Caption>
                {(() => {
                  const feeRows = fees.map((f) => ({
                    Return: f.return,
                    Due: f.due,
                    "TDS in the return": rs(f.tds),
                    "of which not deducted": rs(f.shortfall),
                    "Days late to today": f.days,
                    "Fee (₹200/day, capped at the return's TDS)": rs(f.fee),
                    "TDS booked in the period": rs(f.deducted_in_books),
                    Counted: f.likely_unfiled ? "at risk - no TDS booked, return probably not filed" : "only if not filed",
                  }));
                  return <DataTable rows={feeRows} columns={autoColumns(feeRows)} />;
                })()}
                <Caption>
                  A return filed on time has no fee. 'At risk' counts only periods where the books show no TDS deducted (the
                  return was probably never filed); if no return at all was filed the total is {rs(report.return_fee_if_none_filed)}.
                  Check TRACES.
                </Caption>
              </>
            )}
            {(report.pan_groups ?? []).map((g: Row, i: number) => (
              <Notice key={i} kind="warning">
                PAN {g.pan} ({g.section}) is behind {g.parties.length} party ledgers: {g.parties.join(", ")}. Form 26Q reports them
                as one deductee: aggregate {rs(g.aggregate)}, TDS {rs(g.tds)}, deducted {rs(g.deducted)}. If they are different
                payees, one PAN is wrong.
              </Notice>
            ))}
            <Completeness clientId={client.id} fy={fy} />
            {report.unmapped?.length > 0 && (
              <Expander title={`⚠ Unmapped ledgers with postings (${report.unmapped.length}) – not in the figures above`}>
                <DataTable rows={report.unmapped} columns={autoColumns(report.unmapped)} />
              </Expander>
            )}
            <div className="row">
              <ActionButton action={() => exportTdsReport(client.id, fy, "xlsx", `${stem}.xlsx`)}>Excel (.xlsx)</ActionButton>
              <ActionButton action={() => exportTdsReport(client.id, fy, "pdf", `${stem}.pdf`)}>PDF</ActionButton>
            </div>
            {open && (
              <>
                <hr />
                <Button onClick={() => setOpen(null)}>✕ Close detail</Button>
                <OpenDetail clientId={client.id} fy={fy} partyKey={open[0]} section={open[1]} />
              </>
            )}
          </>
        );
      }}
    </Loaded>
  );
}

function OpenDetail({ clientId, fy, partyKey, section }: { clientId: number; fy: string; partyKey: string; section: string }) {
  return (
    <Loaded query={useTdsDetail(clientId, fy, partyKey, section)} text="Opening the TDS working…">
      {(detail) => <TdsDetail key={`${partyKey}|${section}`} detail={detail} />}
    </Loaded>
  );
}

function Completeness({ clientId, fy }: { clientId: number; fy: string }) {
  return (
    <Loaded query={useTdsCompleteness(clientId, fy)}>
      {(view) => {
        const rows: Row[] = view.rows;
        const flagged = rows.filter((r) => r.flag).length;
        const totalKey = `Total FY ${view.fy}`;
        const table = rows.map((r) => ({
          Ledger: r.ledger,
          Kind: r.kind,
          Treatment: r.treatment,
          Why: r.reason,
          [totalKey]: num2(r.total),
          Parties: r.parties,
          "No-party payments": r.no_party ? "yes" : "",
          "Any party over its threshold": r.any_crossed ? "yes" : "no",
          Check: r.flag || "",
        }));
        return (
          <Expander title={`Completeness: every expense and purchase ledger (${rows.length})` + (flagged ? ` · ⚠ ${flagged} to check` : "")}>
            <DataTable
              rows={table}
              columns={autoColumns(table).map((c) => (c.key === totalKey ? { ...c, align: "right" as const } : c))}
            />
            <Caption>
              Purchase ledgers total {rs(view.total_purchase_ledgers)}
              {view.turnover_purchases ? ` (turnover purchases figure ${rs(view.turnover_purchases)})` : ""} · expense ledgers
              total {rs(view.total_expense_ledgers)}.
            </Caption>
          </Expander>
        );
      }}
    </Loaded>
  );
}
