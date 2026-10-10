/** 'Data coverage': which financial years have data for a client, and from where. */

import { useCoverage, type Row } from "../api";
import { currentFy, fyLabel } from "../format";
import { Caption, DataTable, Loaded, autoColumns } from "../ui";

const TICK = "✓";
const PROFIT_SOURCE: Record<string, string> = { manual: "manual", pl_import: "P&L import", tally_json: "from Tally" };

/** Years with data plus the current and two previous FYs, newest first. */
export function coverageYears(dataFys: Iterable<string>): string[] {
  const start = Number(currentFy().slice(0, 4));
  const years = new Set(dataFys);
  for (let y = start - 2; y <= start; y++) years.add(fyLabel(y));
  return [...years].sort().reverse();
}

const byClient = (rows: Row[], clientId: number) =>
  Object.fromEntries(rows.filter((r) => r.client_id === clientId).map((r) => [r.fy, r])) as Record<string, Row>;

/** Short label for one client/FY cell. */
function source(row: Row | undefined): string {
  if (!row) return "Missing";
  const parts: string[] = [];
  if (row.sales_vouchers || row.purchase_vouchers) parts.push("Tally");
  if (row.figures === "manual") parts.push("Manual");
  else if (row.figures === "imported" && !parts.length) parts.push("Imported figures");
  return parts.join(" + ") || "Missing";
}

/** FY rows × (Tally import / manual entry / missing) for one client. */
export function ClientCoverage({ clientId }: { clientId: number }) {
  return (
    <Loaded query={useCoverage()}>
      {(all) => {
        const rows = byClient(all, clientId);
        const table = coverageYears(Object.keys(rows)).map((fy) => {
          const row = rows[fy];
          const [sales, purchases] = row ? [row.sales_vouchers, row.purchase_vouchers] : [0, 0];
          return {
            "Financial year": `FY ${fy}` + (fy === currentFy() ? " (in progress)" : ""),
            "Tally import": sales || purchases ? `${TICK} ${sales} sales · ${purchases} purchases` : "",
            "Manual entry": row?.figures === "manual" ? TICK : "",
            "Profit figures": row?.has_profit ? `${TICK} ${PROFIT_SOURCE[row.profit_source] ?? ""}`.trim() : "",
            Missing: row ? "" : "✗ no data",
          };
        });
        return (
          <>
            <DataTable rows={table} columns={autoColumns(table)} />
            <Caption>
              Tally import: sales / purchase vouchers stored for the year. Manual entry: yearly figures typed on the Data
              page. Profit figures: gross or net profit is recorded.
            </Caption>
          </>
        );
      }}
    </Loaded>
  );
}

/** Clients × FY, each cell 'Tally', 'Manual', 'Tally + Manual' or 'Missing'. */
export function coverageMatrix(clients: Row[], coverage: Row[]): Row[] {
  const years = coverageYears(coverage.map((r) => r.fy));
  return clients.map((client) => {
    const mine = byClient(coverage, client.id);
    return { Client: client.name, ...Object.fromEntries(years.map((fy) => [`FY ${fy}`, source(mine[fy])])) };
  });
}
