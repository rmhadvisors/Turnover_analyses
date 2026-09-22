# Turnover Analysis & Alert Tool

Compares a client's current-FY sales / purchase turnover, gross profit and net profit with the
previous FY, checks them against configurable thresholds, and raises an alert the moment a value
crosses one. Built for a CA practice that exports data from Tally (TallyPrime / Tally ERP 9).

- `backend/` - FastAPI REST API, SQLAlchemy models, calculation and alert logic, Tally importer.
- `frontend/` - Streamlit UI. Talks to the backend **only** through the REST API
  (`frontend/api_client.py`); it never touches the database or the calculation logic.
- `sample_data/` - dummy Tally-style exports for three fictional clients (try the whole flow).
- `docs/TURNOVER_TOOLS.xlsx` - the original requirement sheet.

## Install and run

You need Python 3.11+. Run the backend and the frontend in two terminals; each has its own
`requirements.txt` and virtual environment.

**Backend** (http://localhost:8000, interactive API docs at `/docs`)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # optional; defaults work
uvicorn app.main:app --reload
```

The SQLite database is created on first start and pre-filled with the default absolute limits.

**Frontend** (http://localhost:8501)

```bash
cd frontend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env            # set BACKEND_URL if the API is not on localhost:8000
streamlit run app.py
```

**Tests and lint** (backend)

```bash
cd backend
pytest
ruff check app tests
black --check app tests
```

## Try it with the sample data

1. Start both services, open **Clients** and add "Sharma Traders".
2. **Import Tally Data** -> pick the client -> *Sales Register* -> upload
   `sample_data/sharma_traders_sales_register.xlsx` -> check the preview -> **Confirm import**.
   Repeat with the *Purchase Register* and the two *Profit & Loss* files.
3. **Client Report** shows FY 2025-26 vs 2024-25 (turnover +25%, net profit -28.57%);
   **Summary** compares all clients; **Alerts** lists what fired.
4. Import the same file again: every voucher is reported as a skipped duplicate.

`python sample_data/generate_samples.py` regenerates the sample files identically.

## Exporting from Tally in the expected format

Export as **Excel (.xlsx / .xls)** or **CSV** (Tally XML is not supported yet).

- **Sales Register / Purchase Register**: *Gateway of Tally -> Display More Reports -> Account
  Books -> Sales Register (or Purchase Register)*, set the period, then *Export* (Alt+E) as
  Excel/CSV. Include the columns Date, Particulars, Voucher Type, Voucher No., Value and
  Gross Total (and the GST columns if you can). Credit notes appear as *Credit Note* and debit
  notes as *Debit Note* voucher types and are deducted automatically.
- **Profit & Loss / Trial Balance**: *Display More Reports -> Profit & Loss A/c*, export the
  same way. The importer looks for *Gross Profit / Gross Loss* and *Net (Nett) Profit / Loss*
  lines. The period line in the header gives the FY; otherwise you are asked to enter it.

The importer finds the real header row itself (company name / period lines above it are
ignored), skips blank and Total / Grand Total rows, and understands Indian commas
(`1,00,00,000`), `Dr` / `Cr` suffixes, bracketed negatives and `dd-mm-yyyy` / `d-MMM-yy` dates.
You confirm the column mapping once per client and report type; it is saved and reused.

### Tally JSON export (Master + Transactions)

Tally can also export **JSON** - this is the format that handles large full-year data most
reliably, and the one to use if the Excel/CSV registers are unwieldy for a busy client.

**Exporting from Tally:**

1. Open the company, press **Alt+F2** and set the period to the full financial year
   (1 April to 31 March).
2. Export the **Masters** (ledgers and groups) as JSON, and the **Transactions** (vouchers) as
   JSON, for that same period. Keep both files - the importer needs both.
3. **If a full-year export fails, is very slow, or comes out truncated** (Tally can struggle with
   a very large single export), export **quarter by quarter** instead (Apr-Jun, Jul-Sep, Oct-Dec,
   Jan-Mar) and keep all four Transactions files. The importer accepts several Transactions files
   for the same company and year at once - select the Master plus all four quarterly files
   together on the Import screen, and it merges and de-duplicates them automatically (a voucher
   that ends up in two of the files is only counted once).

**On the Import Tally Data screen**, choose *Tally JSON export*, pick the client, and select the
Master file (or files) and the Transactions file (or files) together - all from the same company
and year; the order does not matter. The tool then:

- reads the Master to learn which ledgers belong to *Sales Accounts*, *Purchase Accounts* and
  *Duties & Taxes* (matched ignoring case and stray spaces, so "Sales GST @ 18%" and
  "SALES GST @ 18%" are the same ledger), and computes turnover the way Tally does (sales and
  purchases are the group totals; credit notes and debit notes reduce them; custom voucher types
  such as "Purchase New" work because the ledger, not the voucher name, decides);
- leaves out deleted, cancelled, void and optional (unposted) vouchers;
- does **not** count freight, TCS, round-off or state VAT booked in other groups;
- tells you in plain words about anything odd: an export that was cut off part-way (every complete
  voucher before the cut is still used), a period shorter than a year, ledgers used in vouchers but
  missing from the Master (with how many lines and how much money that affects), vouchers that do
  not balance.

The export does not name the company, only its GST number: name the client yourself. Profit
figures are not in these files, so Gross / Net Profit stay empty (enter them manually, or import a
Profit & Loss file).

A cut-off 24-25 export compared against a complete 25-26 year would understate the previous year
and overstate growth, so when one year's data does not reach 31 March, the Client Report compares
only the matching part of both years instead (labelled, for example, "Comparison for 01-Apr to
05-Sep only") rather than a misleading part-year-vs-full-year figure.

## How the numbers work

- Turnover = taxable value excluding GST (switch to gross in **Settings**), less credit notes;
  purchases = purchases less debit notes. Each voucher is assigned to an Indian FY (1 Apr - 31 Mar).
- Difference = current - previous. Change % = difference / |previous| x 100, rounded to 2 places
  (all money is `Decimal`). Previous year 0 or missing shows "New / No comparison". A profit that
  becomes a loss (or the reverse) also shows a **Turned to Loss / Turned to Profit** flag.
- A year still in progress is compared **year-to-date against the same months** of the previous
  year (Apr-Aug vs Apr-Aug), labelled YTD, with an annualised projection.
- Dr / Cr suffixes are read but do not flip signs; whether a voucher adds or deducts comes from
  its voucher type.

## Reading reports and alerts

- **Client Report**: pick a client and FY. The table shows Turnover, Purchases, Gross Profit and
  Net Profit for that year and the previous one, with the difference, the change %, and a
  coloured status (🟢 Normal, 🟡 Moderate, 🔴 Significant). Below it, any absolute-limit alert
  (e.g. "Sales turnover crossed Tax audit u/s 44AB... on 14-Jan-2026 (voucher S-0090)") names the
  date and voucher on which the limit was crossed. The chart underneath compares month-by-month
  sales and purchases for the two years side by side.
- **Summary**: one row per client, sorted by the size of the turnover change, so the biggest
  movers are at the top - use this to see at a glance which clients need a closer look.
- **Alerts**: every status change (e.g. Normal → Significant Increase, or Approaching → Crossed)
  is logged here with the old and new status, filterable by client, financial year and severity.
  An alert stays "open" until you tick Acknowledge; the sidebar badge counts open alerts across
  all clients. Importing the same data again does not create repeat alerts - only an actual change
  in status raises a new one.

## Changing thresholds

Open **Settings / Thresholds**.

- **Percentage bands**: the Moderate (default 5%) and Significant (default 20%) limits. A value
  must go *beyond* a limit to enter the next band (exactly +20% is still Moderate).
- **Absolute limits**: GST registration, tax audit (44AB), presumptive taxation (44AD / 44ADA),
  e-invoicing and TDS 194Q are pre-filled as **editable starting points - verify each against
  current law**. Add, edit, or disable any limit; "Approaching" triggers at a configurable
  share (default 80%) of the limit.

Saving a change re-checks every client. Alerts are recorded only when a status *changes*, so
re-importing does not repeat them.

## Reports and exports

**Client Report** (screen) shows the comparison table, absolute-limit alerts, a month-wise sales vs
purchases chart for the current and previous FY (shared axis, with a table view), and the client's
alerts. Choose the amount unit (auto / lakhs / crores / full Indian commas) in the sidebar.

- **Excel (.xlsx)**: numbers are real numeric cells with number formats (`₹80.00 L`, `0.00%`, an
  arrow shown by the format itself), not text, so they can be sorted and summed. A second sheet
  holds the month-wise table with two native Excel charts. The Difference column keeps its sign;
  the format shows ↑ / ↓.
- **PDF**: the same table, limit alerts and both charts on a landscape A4 page.
- **Summary** (all clients) has its own Excel download, sorted by size of turnover change, with
  filters on every column.

The PDF uses the bundled DejaVu Sans font (`backend/app/assets/fonts/`, licence included) because
the standard PDF fonts have no ₹ sign.

## Status

All five stages are complete: skeleton, data model + calculations, Tally import + API, Streamlit
screens, reports and exports; the JSON importer has also been checked against real client data for
four companies across two financial years each. Not built (by design): e-mail / WhatsApp delivery
of alerts (the `notify()` hook in `backend/app/services/alert_engine.py` is where channels plug
in) and Tally XML import (register a reader in `tally_importer.READERS`).

Real client files are never committed to this repository - `sample_data/real/`, `backups/` and
`exports/` are all git-ignored. A CA using this tool with real client data should treat those
folders, and the SQLite database file, the same way: keep them off any shared or public git
remote.
