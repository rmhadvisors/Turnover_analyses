/** 'Other formats': Sales / Purchase Register and P&L exports from Tally as Excel or CSV. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { confirmImport, previewImport, type Row } from "../api";
import { formatInr } from "../format";
import { useSelection } from "../state";
import {
  Button,
  Caption,
  Checkbox,
  DataTable,
  Expander,
  Grid,
  Metric,
  Notice,
  Radio,
  Select,
  Spinner,
  TextInput,
  autoColumns,
  useAction,
} from "../ui";

const REPORT_TYPES: [string, string][] = [
  ["sales_register", "Sales Register"],
  ["purchase_register", "Purchase Register"],
  ["profit_loss", "Profit & Loss / Trial Balance summary"],
];
const FIELD_LABELS: [string, string][] = [
  ["date", "Date *"],
  ["voucher_no", "Voucher No."],
  ["party", "Party / Particulars"],
  ["voucher_type", "Voucher Type"],
  ["taxable_value", "Value (excl. GST)"],
  ["total_value", "Gross Total (incl. GST)"],
];

/** The mapping the preview used, in the shape the editor edits (only headers that exist). */
function mappingFrom(preview: Row): Row {
  const chosen: Row = {};
  for (const [field] of FIELD_LABELS) {
    const wanted = preview.mapping[field];
    if (typeof wanted === "string" && preview.headers.includes(wanted)) chosen[field] = wanted;
  }
  const tax = (preview.mapping.tax_value ?? []).filter((h: string) => preview.headers.includes(h));
  if (tax.length) chosen.tax_value = tax;
  return chosen;
}

export default function RegisterImport({ client }: { client: Row }) {
  const { unit } = useSelection();
  const [reportType, setReportType] = useState("sales_register");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Row | null>(null);
  const [mapping, setMapping] = useState<Row | null>(null);
  const [fyOverride, setFyOverride] = useState("");
  const [saveMapping, setSaveMapping] = useState(true);
  const [result, setResult] = useState<Row | null>(null);
  const loading = useAction();
  const importing = useAction();

  const load = (chosen: File | null, type: string, map?: Row | null) => {
    setResult(null);
    if (!chosen) {
      setPreview(null);
      return;
    }
    loading.run(async () => {
      const p = await previewImport(client.id, type, chosen, map);
      setPreview(p);
      if (type !== "profit_loss" && map === undefined) setMapping(mappingFrom(p));
    });
  };

  useEffect(() => {
    setMapping(null);
    load(file, reportType);
  }, [file, reportType, client.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const changeMapping = (next: Row) => {
    setMapping(next);
    load(file, reportType, next);
  };

  const isPl = reportType === "profit_loss";
  const canImport = isPl || !!(preview?.would_import || preview?.duplicates);

  return (
    <>
      <Radio horizontal label="Report type" value={reportType} options={REPORT_TYPES} onChange={setReportType} />
      <label className="field">
        <span className="field-label">Tally export (Excel or CSV)</span>
        <input type="file" accept=".xlsx,.xls,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <Caption>Header rows above the table and Grand Total rows are handled.</Caption>
      {loading.busy && <Spinner text="Validating file and preparing preview…" />}
      {loading.error && <Notice kind="error">{loading.error}</Notice>}
      {file && preview && !loading.busy && (
        <>
          {isPl ? (
            <>
              <b>Figures found</b>
              <Grid cols={2}>
                <Metric label="Gross Profit" value={formatInr(preview.profit_loss.gross_profit, unit)} />
                <Metric label="Net Profit" value={formatInr(preview.profit_loss.net_profit, unit)} />
              </Grid>
              {preview.profit_loss.fy ? (
                <Notice kind="success">Financial year detected from the file's period: FY {preview.profit_loss.fy}</Notice>
              ) : (
                <TextInput label="Financial year (could not be detected)" placeholder="2025-26" value={fyOverride} onChange={setFyOverride} />
              )}
            </>
          ) : (
            mapping && (
              <>
                <MappingEditor preview={preview} mapping={mapping} onChange={changeMapping} />
                {preview.error ? (
                  <Notice kind="error">{preview.error}</Notice>
                ) : (
                  <>
                    <b>Preview</b>
                    <Grid cols={4}>
                      <Metric label="Vouchers found" value={preview.rows_found} />
                      <Metric label="Will import" value={preview.would_import} />
                      <Metric label="Duplicates (skipped)" value={preview.duplicates} />
                      <Metric label="Total rows ignored" value={preview.totals_ignored} />
                    </Grid>
                    {preview.period && <Caption>Period in file: {preview.period}</Caption>}
                    {preview.duplicates > 0 && !preview.would_import && (
                      <Notice kind="warning">Every voucher in this file has already been imported - nothing new to add.</Notice>
                    )}
                    {preview.invalid_count > 0 && (
                      <Expander title={`⚠️ ${preview.invalid_count} row(s) could not be read`}>
                        <ul className="plain">
                          {preview.invalid.map((m: string, i: number) => (
                            <li key={i}>{m}</li>
                          ))}
                        </ul>
                      </Expander>
                    )}
                    {preview.parsed_sample?.length > 0 &&
                      (() => {
                        const rename: Record<string, string> = { voucher_no: "Voucher", taxable_value: "Taxable", tax_value: "Tax", total_value: "Total" };
                        const sample = preview.parsed_sample.map((r: Row) =>
                          Object.fromEntries(Object.entries(r).map(([k, v]) => [rename[k] ?? k, v])),
                        );
                        return <DataTable rows={sample} columns={autoColumns(sample)} />;
                      })()}
                    <Expander title="First rows of the file, as read">
                      <DataTable rows={preview.raw_rows} columns={autoColumns(preview.raw_rows)} />
                    </Expander>
                  </>
                )}
                <Checkbox label="Remember this column mapping for this client" checked={saveMapping} onChange={setSaveMapping} />
              </>
            )
          )}
          {!(preview.error && !isPl) && (
            <div className="row">
              <Button
                primary
                disabled={!canImport || importing.busy}
                onClick={() =>
                  importing.run(async () =>
                    setResult(await confirmImport(client.id, reportType, file, isPl ? null : mapping, isPl ? fyOverride || null : null, isPl ? true : saveMapping)),
                  )
                }
              >
                {importing.busy ? "Importing…" : "Confirm import"}
              </Button>
            </div>
          )}
          {importing.error && <Notice kind="error">{importing.error}</Notice>}
          {result && (
            <>
              <Notice kind="success">
                Imported <b>{result.imported}</b> row(s) from {result.file_name}. Skipped {result.duplicates_skipped} duplicate(s)
                {result.invalid_skipped ? ` and ${result.invalid_skipped} unreadable row(s)` : ""}. {result.alerts_raised} new
                alert(s) raised.
              </Notice>
              {result.duplicate_file && <Notice kind="warning">This exact file had been imported before.</Notice>}
              {result.fys_affected.length > 0 && (
                <Caption>Financial years updated: {result.fys_affected.map((fy: string) => `FY ${fy}`).join(", ")}</Caption>
              )}
              <Link to="/clients">Open the client report →</Link>
            </>
          )}
        </>
      )}
    </>
  );
}

/** Let the user confirm/adjust the column mapping. */
function MappingEditor({ preview, mapping, onChange }: { preview: Row; mapping: Row; onChange: (m: Row) => void }) {
  const source = ({ saved: "saved for this client", suggested: "auto-detected" } as Record<string, string>)[preview.mapping_source] ?? preview.mapping_source;
  const headers: string[] = preview.headers;
  const set = (field: string, value: unknown) => {
    const next = { ...mapping };
    if (value === null || (Array.isArray(value) && !value.length)) delete next[field];
    else next[field] = value;
    onChange(next);
  };
  const tax: string[] = mapping.tax_value ?? [];
  return (
    <>
      <p>
        <b>Column mapping</b> ({source}; header found on row {preview.header_row})
      </p>
      <Grid cols={3}>
        {FIELD_LABELS.map(([field, label]) => (
          <Select
            key={field}
            label={label}
            value={mapping[field] ?? null}
            options={[[null, "(not in file)"], ...headers.map((h): [string, string] => [h, h])]}
            onChange={(v) => set(field, v)}
          />
        ))}
      </Grid>
      <span className="field-label">GST / tax columns (summed)</span>
      <div className="row">
        {headers.map((h) => (
          <Checkbox key={h} label={h} checked={tax.includes(h)} onChange={(on) => set("tax_value", on ? [...tax, h] : tax.filter((t) => t !== h))} />
        ))}
      </div>
    </>
  );
}
