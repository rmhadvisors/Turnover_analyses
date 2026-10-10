/** The client report (comparison, limits, monthly chart, exports, alerts) for one client + FY. */

import { exportReport, toNumber, useClientReport, type Row } from "../api";
import { ARROWS, formatInr, formatPct, safeName, signedPct, timestamp, titleCase } from "../format";
import { useSelection } from "../state";
import { ActionButton, Caption, DataTable, Grid, KpiCard, Loaded, Notice, Progress, Section, autoColumns } from "../ui";
import MonthlyChart from "./MonthlyChart";
import { TdsSummaryBlock } from "./tds/Report";

export default function ClientReport({ client, fy }: { client: Row; fy: string | null }) {
  const { unit } = useSelection();
  const query = useClientReport(client.id, fy ?? "");
  if (!fy) return <Notice>Select the Viewing year in the sidebar.</Notice>;
  return (
    <Loaded query={query} text="Preparing client report…">
      {(data) => {
        const report = data.comparison;
        const stem = `Turnover_Comparison_${safeName(client.name, 999)}_FY${fy}`;
        const alertRows = data.alerts.map((a: Row) => ({
          Severity: titleCase(a.severity),
          Type: a.kind === "tds" ? `TDS ${a.section}` : "Turnover",
          Metric: a.metric_label,
          Status: titleCase(a.new_status),
          Triggered: timestamp(a.triggered_at),
          Acknowledged: a.acknowledged_by || "Open",
        }));
        return (
          <>
            <Grid cols={4}>
              {report.rows.slice(0, 4).map((row: Row) => {
                const change = toNumber(row.change_pct);
                const value = toNumber(row.current);
                return (
                  <KpiCard
                    key={row.label}
                    label={row.label}
                    value={value !== null ? formatInr(value, unit) : "No data"}
                    delta={change !== null ? `${signedPct(change)} YoY` : "No comparison"}
                  />
                );
              })}
            </Grid>
            <Comparison report={report} unit={unit} />
            <Section title="Statutory limit usage" />
            {!report.limits.length ? (
              <Notice>No statutory limits are configured.</Notice>
            ) : (
              report.limits.map((limit: Row) => {
                const amount = toNumber(limit.amount) ?? 0;
                const used = toNumber(limit.cumulative) ?? 0;
                return (
                  <div key={limit.name} className="limit">
                    <b>{limit.name}</b> · {formatInr(used, unit)} of {formatInr(amount, unit)} · {titleCase(limit.status)}
                    <Progress value={amount ? used / amount : 0} />
                  </div>
                );
              })
            )}
            <TdsSummaryBlock tds={data.tds} />
            <Section title="Monthly turnover" />
            <MonthlyChart monthly={data.monthly} />
            <Section title="Export report" />
            <div className="row">
              <ActionButton action={() => exportReport(client.id, fy, "xlsx", unit, `${stem}.xlsx`)}>Excel (.xlsx)</ActionButton>
              <ActionButton action={() => exportReport(client.id, fy, "pdf", unit, `${stem}.pdf`)}>PDF</ActionButton>
            </div>
            <Caption>Exports use the amount display unit selected in the sidebar. Excel cells contain numeric values.</Caption>
            <Section title="Alerts for this client and year" />
            {!alertRows.length ? <Caption>No alerts recorded.</Caption> : <DataTable rows={alertRows} columns={autoColumns(alertRows)} />}
          </>
        );
      }}
    </Loaded>
  );
}

function Comparison({ report, unit }: { report: Row; unit: string }) {
  let heading = `Turnover comparison · FY ${report.fy}`;
  if (report.is_ytd) heading += ` · YTD ${report.period_label}`;
  else if (report.is_period_matched) heading += ` · Matched period ${report.period_label}`;
  const rows = report.rows.map((row: Row) => {
    const difference = toNumber(row.difference);
    return {
      Metric: row.label,
      [`FY ${report.previous_fy}`]: formatInr(row.previous, unit),
      [`FY ${report.fy}`]: formatInr(row.current, unit),
      Difference: difference !== null ? formatInr(Math.abs(difference), unit) + (ARROWS[row.direction] ?? "") : "—",
      "Change %": formatPct(row.change_pct),
      Status: row.status_label,
    };
  });
  const projections = report.rows
    .filter((row: Row) => row.annualised !== null && row.annualised !== undefined)
    .map((row: Row) => `${row.label} ≈ ${formatInr(row.annualised, unit)}`);
  return (
    <>
      <Section title={heading} />
      <DataTable rows={rows} columns={autoColumns(rows)} />
      {report.notes.map((note: string, i: number) => (
        <Caption key={i}>{note}</Caption>
      ))}
      {projections.length > 0 && <Caption>Annualised projection at the current run-rate: {projections.join("; ")}</Caption>}
    </>
  );
}
