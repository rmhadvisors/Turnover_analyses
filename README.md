# Turnover Analysis & Alert Tool

Compares a client's current-FY sales/purchase turnover, gross profit, and net
profit against the previous FY, checks the results against configurable
thresholds, and raises alerts the moment a value crosses a threshold. Built
for a CA practice that exports data from Tally (TallyPrime / Tally ERP 9).

## Project layout

- `backend/` — FastAPI REST API, SQLAlchemy models, calculation and alert
  logic, Tally importer, Excel/PDF report generation. See `backend/app/`.
- `frontend/` — Streamlit UI. Talks to the backend only through the REST API
  (`frontend/api_client.py`); never touches the database directly.
- `sample_data/` — dummy Tally-style exports for trying the tool without real
  client data.
- `docs/TURNOVER_TOOLS.xlsx` — original requirement sheet.

## Running the backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # adjust if needed
uvicorn app.main:app --reload
```

The API is then available at http://localhost:8000 (docs at `/docs`).

## Running the frontend

In a second terminal, with the backend already running:

```bash
cd frontend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
streamlit run app.py
```

## Running backend tests

```bash
cd backend
pytest
```

## Status

Project skeleton stage. Data model, calculation/alert logic, Tally import,
API endpoints, frontend screens, and report export are being built out in
stages — see the build plan in project notes.
