/** Alerts: filters, the alert table, acknowledgement and the TDS detail panel. */

import { useEffect, useState } from "react";
import { acknowledgeAlerts, toNumber, useAlerts, useTdsAlertDetail, type Row } from "../api";
import TdsDetail from "../components/tds/Detail";
import { SECTIONS, SEVERITY_EMOJI, TDS_STATUS_LABELS, formatInr, rs, timestamp, titleCase } from "../format";
import { useSelection } from "../state";
import { ActionButton, Caption, DataTable, Grid, Loaded, Notice, PageHeader, Section, Segmented, Select, Toggle } from "../ui";

const SEVERITY_COLOURS: Record<string, string> = { critical: "#ffc2c2", high: "#ffcfa8", warning: "#ffdfaa", info: "#c4dcff" };
type Kind = "All" | "Turnover" | "TDS";
type Level = "All" | "Critical" | "High" | "Warning" | "Info";

function row(alert: Row, unit: string): Row {
  const isTds = alert.kind === "tds";
  const isLimit = String(alert.metric).startsWith("limit:");
  const threshold: string = alert.threshold_description || "";
  const value = toNumber(alert.value);
  let status: string;
  let shownValue: string;
  let limit: string;
  let crossedBy = "";
  if (isTds) {
    status = TDS_STATUS_LABELS[alert.new_status] ?? alert.new_status;
    shownValue = rs(alert.aggregate);
    limit = rs(alert.threshold);
    const at = threshold.lastIndexOf("; crossed by ");
    if (at >= 0) crossedBy = threshold.slice(at + "; crossed by ".length).replace(/\)+$/, "");
  } else {
    const amount = threshold.includes(" limit = ") ? threshold.split(" limit = ").pop()! : null;
    status = ({ crossed: "Crossed limit", approaching: "Approaching limit" } as Record<string, string>)[alert.new_status] ?? titleCase(alert.new_status);
    shownValue = isLimit ? formatInr(value, unit) : value !== null ? `${value.toFixed(2)}%` : "—";
    limit = isLimit && amount ? formatInr(amount, unit) : threshold;
  }
  const crossed = [alert.crossed_on, alert.crossed_voucher_no && `voucher ${alert.crossed_voucher_no}`, crossedBy && `by ${crossedBy}`];
  return {
    Severity: `${SEVERITY_EMOJI[alert.severity] ?? ""} ${titleCase(alert.severity)}`,
    Client: alert.client_name,
    Alert: isTds ? alert.party : String(alert.metric_label).replace(/^Limit: /, ""),
    FY: alert.fy,
    Type: isTds ? `TDS ${alert.section}` : "Turnover",
    PAN: isTds ? alert.party_pan || "not available" : "",
    Status: status,
    "Value / aggregate": shownValue,
    "Limit / threshold": limit,
    "TDS computed": isTds ? rs(alert.tds_computed) : "",
    "TDS deducted": isTds ? rs(alert.tds_deducted) : "",
    Shortfall: isTds ? rs(alert.shortfall) : "",
    "Money at stake": isTds ? rs(alert.money_at_stake) : "",
    Crossed: crossed.filter(Boolean).join(" · "),
    "Triggered at": timestamp(alert.triggered_at),
    "Acknowledged by": alert.acknowledged_by || "",
  };
}

const NUMERIC = new Set(["Value / aggregate", "Limit / threshold", "TDS computed", "TDS deducted", "Shortfall", "Money at stake"]);
const WIDE = new Set(["Alert", "Limit / threshold", "Crossed"]);

export default function AlertsPage() {
  const { clientId, fy, unit, reviewer: rawReviewer } = useSelection();
  const reviewer = rawReviewer.trim();
  const [kindLabel, setKindLabel] = useState<Kind>("All");
  const [level, setLevel] = useState<Level>("All");
  const [section, setSection] = useState<string | null>(null);
  const [openOnly, setOpenOnly] = useState(true);
  const [showOk, setShowOk] = useState(false);
  const [ranked, setRanked] = useState(true);
  const [selected, setSelected] = useState<number[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const kind = ({ All: null, Turnover: "turnover", TDS: "tds" } as const)[kindLabel];
  const query = useAlerts({
    client_id: clientId,
    fy,
    unacknowledged_only: openOnly,
    kind,
    section: kind !== "turnover" ? section : null,
    sort: ranked ? "at_stake" : "recent",
  });
  useEffect(() => setSelected([]), [clientId, fy, kindLabel, level, section, openOnly, showOk, ranked]);

  return (
    <>
      <PageHeader
        title="Alerts"
        subtitle="Review threshold and TDS events, open a TDS alert for the full working, and acknowledge what you have handled."
      />
      <Grid cols="2fr 3fr 2fr">
        <Segmented label="Type" options={["All", "Turnover", "TDS"] as Kind[]} value={kindLabel} onChange={setKindLabel} />
        <Segmented label="Severity" options={["All", "Critical", "High", "Warning", "Info"] as Level[]} value={level} onChange={setLevel} />
        <Select
          label="TDS section"
          value={section}
          disabled={kindLabel === "Turnover"}
          options={[[null, "All sections"], ...SECTIONS.map((s): [string, string] => [s, s])]}
          onChange={setSection}
        />
      </Grid>
      <Grid cols={3}>
        <Toggle label="Unacknowledged only" checked={openOnly} onChange={setOpenOnly} />
        <Toggle label="Show 🟢 'TDS correctly deducted' (informational)" checked={showOk} onChange={setShowOk} />
        <Toggle
          label="Rank by money at stake"
          checked={ranked}
          onChange={setRanked}
          title="TDS shortfall + 30% s.40(a)(ia) disallowance + estimated interest at 1% a month, largest first. Turnover alerts follow, newest first."
        />
      </Grid>
      <Loaded query={query} text="Loading alerts…">
        {(all) => {
          let alerts = all;
          if (level !== "All") alerts = alerts.filter((a) => a.severity === level.toLowerCase());
          if (!showOk) alerts = alerts.filter((a) => !(a.kind === "tds" && a.severity === "info"));
          const picked = selected.filter((i) => i < alerts.length).map((i) => alerts[i]);
          const tdsPicked = picked.filter((a) => a.kind === "tds");
          return (
            <>
              <Section title={`${alerts.length} alert(s)`} />
              {!alerts.length ? (
                <Notice>No alerts match the selected client, year and filters.</Notice>
              ) : (
                <>
                  {(() => {
                    const rows = alerts.map((a) => row(a, unit));
                    // only the columns some row fills, so the table fits without sideways scrolling
                    const keys = Object.keys(rows[0]).filter((key) => rows.some((r) => r[key] && r[key] !== "—"));
                    return (
                      <DataTable
                        wrap
                        rows={rows}
                        selection="multi"
                        selected={selected}
                        onSelect={(s) => {
                          setSelected(s);
                          setMessage(null);
                        }}
                        columns={keys.map((key) => ({
                          key,
                          label: key,
                          align: NUMERIC.has(key) ? ("right" as const) : undefined,
                          wide: WIDE.has(key),
                          style:
                            key === "Severity"
                              ? (_r: Row) => ({ color: SEVERITY_COLOURS[alerts[rows.indexOf(_r)]?.severity], fontWeight: 700 })
                              : undefined,
                        }))}
                      />
                    );
                  })()}
                  <Caption>{picked.length} row(s) selected. Select one TDS alert to open its full working below.</Caption>
                  <div className="row">
                    <ActionButton
                      primary
                      disabled={!picked.length || !reviewer}
                      action={async () => {
                        await acknowledgeAlerts(picked.map((a) => a.id), reviewer);
                        setMessage(`Acknowledged ${picked.length} alert(s) as ${reviewer}.`);
                        setSelected([]);
                      }}
                    >
                      Acknowledge selected
                    </ActionButton>
                    <Caption>
                      {reviewer
                        ? "Reviewer name is saved in the sidebar and applied to every selected alert."
                        : "Enter a reviewer name in the sidebar to enable this action."}
                    </Caption>
                  </div>
                  {message && <Notice kind="success">{message}</Notice>}
                  {tdsPicked.length === 1 && (
                    <>
                      <hr />
                      <AlertDetail alertId={tdsPicked[0].id} />
                    </>
                  )}
                </>
              )}
            </>
          );
        }}
      </Loaded>
    </>
  );
}

function AlertDetail({ alertId }: { alertId: number }) {
  return (
    <Loaded query={useTdsAlertDetail(alertId)} text="Opening the TDS working…">
      {(detail) => <TdsDetail key={alertId} detail={detail} alert={detail.alert} />}
    </Loaded>
  );
}
