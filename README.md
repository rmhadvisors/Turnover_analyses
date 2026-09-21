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
screens, reports and exports. Not built (by design): e-mail / WhatsApp delivery of alerts (the
`notify()` hook in `backend/app/services/alert_engine.py` is where channels plug in) and Tally XML
import (register a reader in `tally_importer.READERS`).
