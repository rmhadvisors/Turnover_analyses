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

You need Python 3.11+ for the backend. Run the backend and the frontend in two terminals; each has
its own `requirements.txt` and virtual environment.

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

Use **Python 3.12** (pinned in `frontend/.python-version`), not 3.13/3.14 - pandas' compiled
wheels for very new Python releases are the most likely to be blocked by Windows Application
Control / Smart App Control (see Troubleshooting below), and are the least tested by pandas
itself.

```bash
cd frontend
py -3.12 -m venv .venv          # Windows; use `python3.12 -m venv .venv` on macOS/Linux
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env            # set BACKEND_URL if the API is not on 127.0.0.1:8000
streamlit run app.py
```

**Troubleshooting: `AttributeError: partially initialized module 'pandas' ...`**

If the Clients or Dashboard page fails with a pandas import error that mentions a "circular
import", the real cause is almost always that a pandas `.pyd` file failed to load - Windows raises
this exact misleading message whenever pandas' C extension partially fails to initialise for any
reason. Check, in order:

1. **Windows Application Control / Smart App Control blocked the file.** Look in *Windows
   Security -> App & browser control* (and *Protection history*) for a block naming one of
   pandas' `_libs` `.pyd` files (e.g. `timedeltas`, `ops_dispatch`). Smart App Control has no
   per-file allow-list, so the only fix is turning it off (device-wide) or - on a managed machine
   - asking your IT admin to allow it through a WDAC policy.
2. **Wrong Python version.** Confirm the venv is on Python 3.12 (`python --version` inside the
   activated venv) - see above.
3. **A local file shadowing pandas.** Make sure there is no `pandas.py`, `numpy.py` or `pandas/`
   folder anywhere in `frontend/` outside `.venv`.

If pandas still won't import after that, the page shows a friendly error instead of a raw
traceback; the full traceback is still printed to the terminal running `streamlit run`.

**Tests and lint** (backend)

```bash
cd backend
pytest
ruff check app tests
black --check app tests
```

## Try it with the sample data

1. Start both services, open **Clients** and add "Sharma Traders" (it becomes the sidebar client).
2. **Data** -> *Tally import* -> open *Other formats* -> *Sales Register* -> upload
   `sample_data/sharma_traders_sales_register.xlsx` -> check the preview -> **Confirm import**.
   Repeat with the *Purchase Register* and the two *Profit & Loss* files.
3. **Clients** (with the client selected) shows FY 2025-26 vs 2024-25 (turnover +25%, net profit
   -28.57%); **Dashboard** compares all clients; **Alerts** lists what fired.
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

**On the Data page** (*Tally import* tab), with the client chosen in the sidebar, select the
Master file and ONE financial year's Transactions file together (Ctrl+click both) - from the same
company; the order does not matter. The year is detected from the voucher dates and shown before
you confirm; if that year was imported before you choose to add only new vouchers, replace the
year's imported data, or skip it. Repeat for each year. Files up to 1 GB are read as a stream,
with a progress bar. The tool then:

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

The sidebar is the one place to choose the client and financial year; every page uses them.

- **Clients** -> pick a client (sidebar, or a row in the directory) -> *Report*. The table shows Turnover, Purchases, Gross Profit and
  Net Profit for that year and the previous one, with the difference, the change %, and a
  coloured status (🟢 Normal, 🟡 Moderate, 🔴 Significant). Below it, any absolute-limit alert
  (e.g. "Sales turnover crossed Tax audit u/s 44AB... on 14-Jan-2026 (voucher S-0090)") names the
  date and voucher on which the limit was crossed. The chart underneath compares month-by-month
  sales and purchases for the two years side by side.
- **Dashboard**: KPIs and one row per client for the sidebar's FY (clients without data show
  "No data"), with a filter for clients that need attention, a chart and CSV / Excel export.
- **Data coverage** (Clients -> *Data coverage*, or the directory grid): which years each client
  has Tally imports, manual figures or nothing yet.
- **Alerts**: every status change (e.g. Normal → Significant Increase, or Approaching → Crossed)
  is logged here with the old and new status, filterable by client, financial year and severity.
  An alert stays "open" until you tick Acknowledge; the sidebar badge ("N unacknowledged (all
  clients)") counts open critical and warning alerts across all clients and years. Importing the same data again does not create repeat alerts - only an actual change
  in status raises a new one.

## Gross / net profit and client profiles

- **GP / NP from Tally**: a Tally JSON import also works out Gross Profit (trading-account ledgers
  plus closing minus opening stock) and Net Profit (all other revenue ledgers), exactly as Tally's
  P&L groups them, for one finished financial year. Figures typed manually or imported from a P&L
  file are never replaced. Net profit is held back when ledgers missing from the Master exceed 5%
  of it; the import review shows why.
- **Client profile** (Clients -> client -> *Profile*): GSTIN, state, registration and entity type
  fill in from an import; nature of work, supplies, presumptive scheme and the 5% cash condition
  are chosen by you. Each statutory limit has an "Applies to" rule (see Settings); limits that do
  not apply are not checked and their open alerts are acknowledged as "System - not applicable to
  client profile". A field left Unknown never switches a limit off.
- **TDS u/s 194Q purchases** is checked per seller (cash purchases excluded), only for buyers whose
  previous-year turnover exceeded Rs 10 crore.

## Changing thresholds

Open **Settings**. Statutory limits are one editable table: edit cells and **Save changes**, or
tick *Select* on rows and **Delete selected** (you confirm before anything is deleted).

- **Percentage bands**: the Moderate (default 5%) and Significant (default 20%) limits. A value
  must go *beyond* a limit to enter the next band (exactly +20% is still Moderate).
- **Absolute limits**: GST registration, tax audit (44AB), presumptive taxation (44AD / 44ADA),
  e-invoicing and TDS 194Q are pre-filled as **editable starting points - verify each against
  current law**. Add, edit, or disable any limit; "Approaching" triggers at a configurable
  share (default 80%) of the limit.

Saving a change re-checks every client. Alerts are recorded only when a status *changes*, so
re-importing does not repeat them.

## Reports and exports

The client **Report** (Clients page) shows the comparison table, absolute-limit alerts, a month-wise sales vs
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

## TDS applicability and threshold alerts (194C, 194H, 194J, 194Q, 194I)

Spec: `docs/TDS_Applicability_Threshold_194C_194H_194J_194Q_194I.xlsx`. Party-wise, from the
Tally JSON export: every ledger line of every posted voucher is kept (table `tds_entries`,
separate from the turnover vouchers, so turnover figures never change).

1. **Rate master** (Settings → TDS): thresholds and rates per section with an effective-from date.
   The sheet's values apply from 01.04.2025; the earlier limits are kept for FY 2024-25 and before.
   The rules that are law (the "exceeds" test, 194C's two tests, 194Q's excess-only base, no PAN →
   the higher of the normal and s.206AA rate) are code in `services/tds_engine.py`.
2. **Who must deduct** (Settings → TDS → client): 194Q if last year's turnover exceeded ₹10 Cr;
   194C/H/J/I always for a non-individual, and for an Individual/HUF only if liable to audit u/s
   44AB last year. The constitution comes from the 4th character of the PAN inside the client's
   GSTIN; both it and the audit answer can be overridden.
3. **Ledger mapping** (Settings → TDS → client): every expense, purchase and TDS/TCS ledger gets a
   proposed role and section. **Review it and press Approve**: TDS alerts for a client start only
   after approval. Ledgers with postings that are not mapped are listed, never ignored.
4. **Analysis**: payments are grouped per party (by the Tally ledger guid) and section; PAN comes
   from an entered PAN, else the party's GSTIN, else Tally's PAN field. TDS actually deducted is
   read from the TDS ledgers; TDS booked in a voucher with no party (monthly lump-sum journals) is
   allocated to the section's parties that are short, and labelled as such.
5. **Alerts**: 🔴 not deducted, 🟠 short deducted, 🟡 approaching (default 80%), 🟢 correctly
   deducted (hidden by default). Select a TDS alert on the Alerts page for the full working:
   party, why the section applies, threshold test, calculation, vouchers, compliance and
   consequences, and the actions (acknowledge, deducted outside Tally, party flags, export).
6. **Reports**: Clients → TDS tab (TDS Applicability, Excel / PDF); a TDS block on the client
   report and TDS columns on the Dashboard / Summary.

Further rules:

- **TDS analysis year** (Settings → TDS, default 2025-26): TDS is analysed and alerted for this
  one FY only, from that year's vouchers (1 April - 31 March). The year before is used only to
  decide who must deduct. Move it forward each year; alerts of other years are closed.
- **Rent**: every rent ledger needs an explicit 194I(a) (machinery, 2%) or 194I(b) (building, 10%)
  choice (tick *Rent: confirm section*) before the mapping can be approved. An Individual/HUF
  not liable to audit is tested u/s **194-IB** instead (rent > ₹50,000 a month, 2%, once a year).
- **Payments with no party ledger** over a threshold raise a 🟠 "cannot determine - payee
  unidentified" alert; assign the payee (e.g. the landlord) from the alert's detail panel.
- **TDS booked without a party** (lump-sum journals) is counted for a party only when it is the
  one party over that section's threshold; otherwise it stays "unallocated" and never turns a
  red status green. Over-deduction is reported 🔵 with its likely cause; the rounding tolerance
  is at most ₹100.
- **Data quality**: parties whose PAN contradicts their name (Pvt Ltd with a firm's PAN, etc.)
  are listed on Settings → TDS - a wrong PAN in Form 26Q means 20% u/s 206AA.

Clients imported before this feature: re-import their Tally JSON files (duplicates are skipped,
turnover is unchanged), or capture only the TDS data with
`python -m app.tools.backfill_tds "CLIENT NAME" Master.json Transactions.json` from `backend/`.

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
