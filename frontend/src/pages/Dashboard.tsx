/** Dashboard: KPIs plus the all-clients turnover comparison. */

import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { exportSummary, saveBlob, toNumber, useDashboard, useTdsAlertCounts, useTdsAnalysisFy, useWorkspace, type Row } from "../api";
import { BAND_COLOURS, formatInr } from "../format";
import { useSelection } from "../state";
import { ActionButton, Button, Caption, DataTable, Grid, KpiCard, Loaded, Notice, PageHeader, Section, Toggle, toCsv } from "../ui";

const hasData = (row: Row) => row.current_turnover !== null || row.previous_turnover !== null;
const sum = (rows: Row[], key: string) => rows.reduce((total, r) => total + (toNumber(r[key]) ?? 0), 0);

export default function DashboardPage() {
  const { fy, clientId, setClientId, unit } = useSelection();
  const workspace = useWorkspace();
  const dashboard = useDashboard(fy ?? "");
  const tdsCounts = useTdsAlertCounts(fy ?? "");
  const tdsFy = useTdsAnalysisFy();

  return (
    <>
      <PageHeader title="Dashboard" subtitle="Turnover movement, statutory limits and open alerts across clients." />
      {!fy ? (
        <Notice>Select a financial year in the sidebar.</Notice>
      ) : (
        <Loaded query={dashboard} text="Loading dashboard…">
          {(data) => {
            let clients: Row[] = workspace.data?.clients ?? [];
            let rows: Row[] = data.rows;
            let counts: Row[] = data.open_alerts;
            const name = clients.find((c) => c.id === clientId)?.name ?? "this client";
            if (clientId !== null) {
              clients = clients.filter((c) => c.id === clientId);
              rows = rows.filter((r) => r.client_id === clientId);
              counts = counts.filter((c) => c.client_id === clientId);
            }
            const tds = tdsCounts.data ?? { critical: 0, high: 0, warning: 0 };
            const tdsRows = rows.filter((r) => r.tds_parties_crossed !== null && r.tds_parties_crossed !== undefined);
            return (
              <>
                {clientId !== null && (
                  <>
                    <Notice>
                      Showing only <b>{name}</b> (selected in the sidebar).
                    </Notice>
                    <Button onClick={() => setClientId(null)}>👥 Show all clients</Button>
                  </>
                )}
                <Grid cols={5}>
                  <KpiCard label="Clients" value={clients.length} />
                  <KpiCard label="Critical alerts" value={sum(counts, "critical")} delta={`Open, FY ${fy}`} />
                  <KpiCard label="Warning alerts" value={sum(counts, "warning")} delta={`Open, FY ${fy}`} />
                  <KpiCard label="Info alerts" value={sum(counts, "info")} delta={`Open, FY ${fy}`} />
                  <KpiCard label="Clients over a limit" value={rows.filter((r) => Number(r.limits_crossed) > 0).length} delta={`FY ${fy}`} />
                </Grid>
                {tdsFy && fy !== tdsFy && (
                  <Caption>TDS analysis is available for FY {tdsFy} only - choose it in the sidebar to see TDS figures.</Caption>
                )}
                <Grid cols={4}>
                  <KpiCard
                    label="TDS alerts open"
                    value={tds.critical + tds.high + tds.warning}
                    delta={`🔴 ${tds.critical} not deducted · 🟠 ${tds.high} short · 🟡 ${tds.warning} near`}
                  />
                  <KpiCard label="Parties over a TDS threshold" value={sum(tdsRows, "tds_parties_crossed")} delta={`FY ${fy}`} />
                  <KpiCard label="TDS payable" value={formatInr(sum(tdsRows, "tds_payable"), unit)} />
                  <KpiCard label="TDS not deducted" value={formatInr(sum(tdsRows, "tds_not_deducted"), unit)} delta="Shortfall across clients" />
                </Grid>
                <ClientTable rows={rows} fy={fy} />
              </>
            );
          }}
        </Loaded>
      )}
      <Section title="Quick actions" />
      <Grid cols={3}>
        <Link to="/data">⇪ Import Tally data or enter figures</Link>
        <Link to="/clients">📊 Client reports</Link>
        <Link to="/alerts">🔔 Review alerts</Link>
      </Grid>
    </>
  );
}

function ClientTable({ rows: allRows, fy }: { rows: Row[]; fy: string }) {
  const { unit, clientId } = useSelection();
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  if (!allRows.length) {
    return (
      <>
        <Section title="Client turnover" />
        <Notice>No clients yet. Add one on the Clients page.</Notice>
      </>
    );
  }
  const rows = onlyFlagged
    ? allRows.filter((r) => !["normal", null].includes(r.band) || r.limits_crossed || r.open_alerts || toNumber(r.tds_not_deducted))
    : allRows;

  const table = rows.map((r) => ({
    Client: r.client_name,
    [`FY ${fy} turnover`]: r.current_turnover !== null ? formatInr(r.current_turnover, unit) + (r.is_ytd ? " (YTD)" : "") : "No data",
    "Previous year": r.previous_turnover !== null ? formatInr(r.previous_turnover, unit) : "No data",
    "Change %": toNumber(r.change_pct) === null ? "" : `${toNumber(r.change_pct)!.toFixed(2)}%`,
    Band: hasData(r) ? r.status_label : "No data",
    "Limits crossed": r.limits_crossed,
    Approaching: r.limits_approaching,
    "Open alerts": r.open_alerts,
    "TDS: parties crossed": r.tds_parties_crossed ?? "",
    "TDS not deducted": r.tds_not_deducted !== null && r.tds_not_deducted !== undefined ? formatInr(r.tds_not_deducted, unit) : "No TDS data",
  }));
  const missing = rows.filter((r) => !hasData(r)).map((r) => r.client_name);

  const chartRows = rows.filter((r) => toNumber(r.current_turnover) !== null);
  const peak = Math.max(0, ...chartRows.map((r) => Math.abs(toNumber(r.current_turnover)!)));
  const [divisor, suffix] =
    unit === "full" ? [1, "₹"] : unit === "crores" || (unit === "auto" && peak >= 1e7) ? [1e7, "₹ crores"] : [1e5, "₹ lakhs"];
  const chart = chartRows.map((r) => ({ client: r.client_name, turnover: toNumber(r.current_turnover)! / divisor }));

  return (
    <>
      <Section title="Client turnover" />
      <Toggle label="Only clients that need attention" checked={onlyFlagged} onChange={setOnlyFlagged} />
      {!rows.length ? (
        <Notice>No clients need attention for this year.</Notice>
      ) : (
        <>
          <DataTable
            rows={table}
            columns={Object.keys(table[0]).map((key) => ({
              key,
              label:
                key === "Open alerts" ? <span title="Unacknowledged critical, high and warning alerts">{key}</span> :
                key === "TDS: parties crossed" ? <span title="Parties over a TDS threshold this year">{key}</span> : key,
              align: ["Change %", "Limits crossed", "Approaching", "Open alerts", "TDS: parties crossed"].includes(key) ? "right" : undefined,
              style: key === "Band" ? (row) => (BAND_COLOURS[row.Band] ? { color: BAND_COLOURS[row.Band], fontWeight: 600 } : undefined) : undefined,
            }))}
          />
          {missing.length > 0 && (
            <Caption>
              No data for FY {fy} or the year before: {missing.join(", ")}. Import Tally data or enter figures on the Data page.
            </Caption>
          )}
          {chart.length > 0 && (
            <>
              <Section title="Turnover by client" />
              <div style={{ width: "100%", height: 340 }}>
                <ResponsiveContainer>
                  <BarChart data={chart} margin={{ top: 20, right: 10, bottom: 20, left: 20 }}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="client" stroke="#c3c2b7" tick={{ fontSize: 12 }} />
                    <YAxis stroke="#c3c2b7" tick={{ fontSize: 12 }} label={{ value: `Turnover (${suffix})`, angle: -90, position: "insideLeft", fill: "#c3c2b7", dx: -10 }} />
                    <Tooltip
                      cursor={{ fill: "#ffffff10" }}
                      contentStyle={{ background: "var(--bg-2)", border: "1px solid var(--border)" }}
                      formatter={(v: number) => [`${suffix} ${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`, "Turnover"]}
                    />
                    <Bar dataKey="turnover" fill="#a78bfa" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
          <Section title="Export" />
          <div className="row">
            <Button onClick={() => saveBlob(toCsv(table), `Turnover_Summary_FY${fy}.csv`)}>Download CSV</Button>
            {clientId === null && (
              <ActionButton action={() => exportSummary(fy, unit, `Turnover_Summary_FY${fy}.xlsx`)}>Download Excel (.xlsx)</ActionButton>
            )}
          </div>
        </>
      )}
    </>
  );
}
