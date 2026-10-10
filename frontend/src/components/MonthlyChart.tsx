/**
 * Month-wise sales vs purchases chart for the current and previous FY.
 * Two panels share one value axis so the years are directly comparable.
 */

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toNumber, type Row } from "../api";
import { formatInr } from "../format";
import { useSelection } from "../state";
import { Caption, DataTable, Expander, Grid, autoColumns } from "../ui";

const COLOURS = { sales: "#a78bfa", purchases: "#82968f", grid: "#33332f", ink: "#c3c2b7" };
const tooltipStyle = { background: "#1b2633", border: "1px solid #354253" };

export default function MonthlyChart({ monthly }: { monthly: Row }) {
  const { unit } = useSelection();
  if (!monthly.has_data) {
    return (
      <>
        <b>Month-wise sales vs purchases</b>
        <Caption>Month-wise data comes from imported Sales / Purchase Registers - none yet.</Caption>
      </>
    );
  }
  const all = ["current", "previous"].flatMap((k) =>
    ["sales", "purchases"].flatMap((s) => monthly[k][s].map((v: unknown) => Math.abs(toNumber(v) ?? 0))),
  );
  const peak = Math.max(0, ...all);
  const [divisor, unitLabel] =
    unit === "full" ? [1, "₹"] : unit === "crores" || (unit === "auto" && peak >= 1e7) ? [1e7, "₹ crores"] : [1e5, "₹ lakhs"];
  const domain: [number, number] = [0, (peak / divisor) * 1.12 || 1];

  const panel = (key: "current" | "previous", first: boolean) => {
    const series = monthly[key];
    const data = monthly.months.map((month: string, i: number) => ({
      month,
      Sales: (toNumber(series.sales[i]) ?? 0) / divisor,
      Purchases: (toNumber(series.purchases[i]) ?? 0) / divisor,
    }));
    return (
      <div>
        <div style={{ textAlign: "center", color: COLOURS.ink }}>FY {series.fy}</div>
        <div style={{ width: "100%", height: 340 }}>
          <ResponsiveContainer>
            <BarChart data={data} margin={{ top: 10, right: 10, bottom: 10, left: first ? 20 : 0 }} barGap={2}>
              <CartesianGrid stroke={COLOURS.grid} vertical={false} />
              <XAxis dataKey="month" stroke={COLOURS.ink} tick={{ fontSize: 11 }} />
              <YAxis
                domain={domain}
                stroke={COLOURS.ink}
                tick={{ fontSize: 11 }}
                hide={!first}
                tickFormatter={(v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 1 })}
                label={first ? { value: unitLabel, angle: -90, position: "insideLeft", fill: COLOURS.ink, dx: -10 } : undefined}
              />
              <Tooltip
                cursor={{ fill: "#ffffff10" }}
                contentStyle={tooltipStyle}
                formatter={(v: number, name: string) => [
                  `${unitLabel} ${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                  name,
                ]}
              />
              {first && <Legend verticalAlign="top" align="left" wrapperStyle={{ color: COLOURS.ink, paddingBottom: 8 }} />}
              <Bar dataKey="Sales" fill={COLOURS.sales} />
              <Bar dataKey="Purchases" fill={COLOURS.purchases} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    );
  };

  const table = ["previous", "current"].flatMap((key) =>
    monthly.months.map((month: string, i: number) => ({
      FY: `FY ${monthly[key].fy}`,
      Month: month,
      Sales: formatInr(monthly[key].sales[i], unit),
      Purchases: formatInr(monthly[key].purchases[i], unit),
    })),
  );

  return (
    <>
      <b>Month-wise sales vs purchases</b>
      <Grid cols={2}>
        {panel("current", true)}
        {panel("previous", false)}
      </Grid>
      <Expander title="Table view">
        <DataTable rows={table} columns={autoColumns(table)} />
      </Expander>
    </>
  );
}
