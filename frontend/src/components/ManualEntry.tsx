/** Manual entry of yearly figures for the sidebar's client and financial year. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { parseMoneyInput, sameAmount, saveEntry, toNumber, useFigures, useSettings, type Row } from "../api";
import { previousFy, signedPct } from "../format";
import { ActionButton, Badge, Caption, Card, Grid, Notice, Section, Spinner, TextInput, ErrorNotice } from "../ui";

const FIELDS: [string, string][] = [
  ["turnover", "Turnover"],
  ["purchases", "Purchases"],
  ["gross_profit", "Gross profit"],
  ["net_profit", "Net profit (negative = loss)"],
];

export default function ManualEntry({ client, fy }: { client: Row; fy: string | null }) {
  const figures = useFigures(client.id);
  const settings = useSettings();
  if (!fy) return <Notice>Select the Viewing year in the sidebar.</Notice>;
  if (figures.isPending || settings.isPending) return <Spinner text="Loading saved figures…" />;
  if (figures.isError || settings.isError) return <ErrorNotice error={figures.error ?? settings.error} />;
  return <Form key={`${client.id}|${fy}`} client={client} fy={fy} figures={figures.data} settings={settings.data} />;
}

function Form({ client, fy, figures, settings }: { client: Row; fy: string; figures: Row[]; settings: Row }) {
  const priorFy = previousFy(fy);
  const saved = Object.fromEntries(figures.map((row) => [row.fy, row])) as Record<string, Row>;
  const initial = () => {
    const typed: Record<string, string> = {};
    for (const [prefix, year] of [["previous", priorFy], ["current", fy]]) {
      for (const [key] of FIELDS) {
        const old = saved[year]?.[key];
        typed[`${prefix}_${key}`] = old === null || old === undefined ? "" : String(old);
      }
    }
    return typed;
  };
  const [typed, setTyped] = useState<Record<string, string>>(initial);
  const [result, setResult] = useState<Row | null>(null);
  useEffect(() => setTyped(initial()), [figures]); // eslint-disable-line react-hooks/exhaustive-deps

  const moderate = toNumber(settings.moderate_pct) ?? 5;
  const significant = toNumber(settings.significant_pct) ?? 20;

  const save = async () => {
    const payload: Row = { client_id: client.id, previous_fy: priorFy, current_fy: fy };
    for (const [key, value] of Object.entries(typed)) {
      const [prefix, ...rest] = key.split("_");
      const field = rest.join("_");
      const year = prefix === "current" ? fy : priorFy;
      const amount = parseMoneyInput(value);
      // Unchanged pre-filled values are not re-sent, so imported figures stay "imported".
      payload[key] = sameAmount(amount, saved[year]?.[field]) ? null : amount;
    }
    setResult(await saveEntry(payload));
  };

  return (
    <>
      <Caption>
        Entering <b>FY {fy}</b> and its previous year <b>FY {priorFy}</b>. To enter another year, change the Viewing year in
        the sidebar.
      </Caption>
      {result && (
        <Notice kind="success">
          Saved. Checks completed; {result.alerts_raised} new alert(s) raised.{" "}
          {result.alerts_raised > 0 && <Link to="/alerts">View alerts</Link>} <Link to="/clients">Open the client report</Link>
        </Notice>
      )}
      <Grid cols={2}>
        {(
          [
            ["Previous FY", priorFy, "previous"],
            ["Current FY", fy, "current"],
          ] as const
        ).map(([heading, year, prefix]) => (
          <Card key={prefix}>
            <Section title={`${heading} · ${year}`} />
            <div className="stack">
              {FIELDS.map(([key, label]) => (
                <TextInput
                  key={key}
                  label={`${label} (₹)`}
                  value={typed[`${prefix}_${key}`]}
                  onChange={(v) => setTyped((t) => ({ ...t, [`${prefix}_${key}`]: v }))}
                />
              ))}
            </div>
          </Card>
        ))}
      </Grid>
      <Caption>Leave a box empty to keep the stored value. Commas are fine (1,00,000).</Caption>

      <Section title="Live year-on-year preview" />
      <Grid cols={4}>
        {FIELDS.map(([metric, label]) => {
          let previous: number | null = null;
          let current: number | null = null;
          try {
            previous = toNumber(parseMoneyInput(typed[`previous_${metric}`]));
            current = toNumber(parseMoneyInput(typed[`current_${metric}`]));
          } catch {
            previous = current = null;
          }
          const delta = previous !== null && previous !== 0 && current !== null ? ((current - previous) / Math.abs(previous)) * 100 : null;
          const magnitude = delta === null ? 0 : Math.abs(delta);
          const band = delta === null ? "Info" : magnitude > significant ? "Critical" : magnitude > moderate ? "Warning" : "Info";
          return (
            <Card key={metric}>
              <b>{label}</b>
              <p>{delta === null ? "No comparison" : signedPct(delta)}</p>
              <Badge level={band} />
            </Card>
          );
        })}
      </Grid>
      <div className="row">
        <ActionButton primary action={save}>
          ✓ Save &amp; re-check
        </ActionButton>
      </div>
    </>
  );
}
