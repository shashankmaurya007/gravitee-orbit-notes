# gravitee-orbit-notes

AI-generated weekly CRM notes from Orbit Kanban boards, automatically pushed to HubSpot.

Live at: https://gravitee-lms.info/playground/csms/go-live-analysis

> **Note:** An Orbit Notes widget is embedded in the Customer 360 / Account Operations app
> (`https://gravitee-lms.info/playground/csms/customer-360`). It references this application
> directly — no code is duplicated.

---

## What it does

1. Pulls Kanban board activity from Orbit for all CSM-managed accounts (Production + Onboarding boards)
2. Runs a 4-step AI pipeline (Gemini 2.0 Flash via Gravitee proxy) to summarise board activity per company
3. Stores summaries in a local SQLite database (idempotent — skips if the week already exists)
4. Pushes formatted HTML notes to HubSpot as company-level CRM notes

---

## Stack

| Layer    | Technology                              |
|----------|-----------------------------------------|
| Backend  | Python · FastAPI · aiosqlite/SQLAlchemy |
| Frontend | Alpine.js · Tailwind CSS (CDN)          |
| AI       | Gemini 2.0 Flash via Gravitee proxy     |
| Database | SQLite (local) → Postgres (EC2)         |

---

## Project structure

```
gravitee-orbit-notes/
├── backend/
│   ├── ai_client.py          # Gemini API calls via Gravitee proxy
│   ├── bigquery_client.py    # BigQuery integration
│   ├── config.py             # Environment config
│   ├── database.py           # SQLite async ORM
│   ├── hubspot_client.py     # HubSpot API (push notes)
│   ├── models.py             # SQLAlchemy models
│   ├── orbit_client.py       # Orbit Kanban API
│   ├── scheduler.py          # Weekly cron sync
│   ├── summarizer.py         # 4-step AI pipeline
│   └── routers/
│       ├── admin.py          # Admin endpoints
│       ├── notes.py          # Note read/preview endpoints
│       └── sync.py           # Sync trigger endpoints
├── frontend/
│   └── index.html            # Card UI (expand/collapse, Preview/Push/Delete)
├── data/                     # Reserved for exports
├── run.py                    # App entrypoint
├── pyproject.toml
└── .env.example              # Copy to .env and fill in keys
```

---

## Setup

```bash
# 1. Copy and fill in environment variables
cp .env.example .env

# 2. Install dependencies (uses uv)
uv sync

# 3. Run locally
uv run python run.py --no-reload
# Open http://localhost:8000
```

A full sync across ~90 companies takes 15–20 minutes (rate-limited to 1 req / 3 s).

---

## Environment variables (.env)

| Variable            | Description                        |
|---------------------|------------------------------------|
| `ORBIT_API_TOKEN`   | Orbit Bearer token                 |
| `GEMINI_API_KEY`    | Gravitee proxy API key for Gemini  |
| `HUBSPOT_API_TOKEN` | HubSpot private app token          |

---

## Deployment

See `Deploy to EC2.command` for the EC2 deployment script.
Swap SQLite for Postgres when moving to production.

---

## Change history

| Date       | Change                                                   |
|------------|----------------------------------------------------------|
| 2026-05-10 | Initial commit — migrated from `gravitee-csm-team` repo |
