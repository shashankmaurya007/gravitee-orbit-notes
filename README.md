# Orbit Notes

Fetches weekly Orbit Kanban board activity, summarises it with Gemini AI, and pushes structured notes to HubSpot CRM companies.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) package manager

## Setup

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
cd orbit-notes
uv sync
```

The `.env` file is already populated. If you need to reset it, copy from `.env.example` and fill in your keys.

## Run

```bash
python run.py
```

Open **http://localhost:8000** in your browser.

## Usage

1. **Generate Notes** — Select the week offset (0 = last week, 1 = two weeks ago) and click **Generate Notes**. The sync runs in the background; the table auto-refreshes every 5 seconds until notes appear.

2. **Review** — Click **Preview** on any row to read and edit the note body before pushing.

3. **Push to HubSpot** — Push individual notes via the row action, or select multiple rows and use **Push Selected**.

4. **Delete** — Remove drafts or failed notes you don't want to keep.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sync` | Trigger sync (background, non-blocking) |
| `POST` | `/api/sync/run` | Trigger sync (blocking, waits for result) |
| `GET` | `/api/notes` | List all notes (`?week_start=YYYY-MM-DD` to filter) |
| `GET` | `/api/notes/{id}` | Get a single note |
| `PATCH` | `/api/notes/{id}/body` | Edit note body |
| `POST` | `/api/notes/push` | Push notes to HubSpot (`{"note_ids": [...]}`) |
| `DELETE` | `/api/notes/{id}` | Delete a note |

Interactive docs: **http://localhost:8000/docs**

## Project Structure

```
orbit-notes/
├── backend/
│   ├── config.py          # Pydantic settings (reads .env)
│   ├── database.py        # Async SQLAlchemy + SQLite
│   ├── models.py          # WeeklyNote ORM model + Pydantic schemas
│   ├── orbit_client.py    # Orbit REST API client
│   ├── ai_client.py       # Gemini via Gravitee proxy (OpenAI-compatible)
│   ├── summarizer.py      # 4-step AI summarisation pipeline
│   ├── hubspot_client.py  # HubSpot CRM v3 note creation
│   ├── main.py            # FastAPI app + static file serving
│   └── routers/
│       ├── notes.py       # CRUD + push endpoints
│       └── sync.py        # Sync trigger endpoints
├── frontend/
│   └── index.html         # Alpine.js + Tailwind CSS SPA
├── data/                  # SQLite database stored here
├── run.py                 # Entry point
├── pyproject.toml
└── .env                   # API keys (not committed)
```

## EC2 Deployment

When moving to EC2, swap SQLite for Postgres by updating `DATABASE_URL` in `.env`:

```
DATABASE_URL=postgresql+asyncpg://user:password@localhost/orbitnotes
```

Add `asyncpg` to dependencies:

```bash
uv add asyncpg
```

Run behind `nginx` + `systemd` or use `gunicorn -k uvicorn.workers.UvicornWorker`.

## Notes

- **Idempotency**: Re-running sync for the same week skips companies that already have a draft note.
- **Board type detection**: Currently uses keyword matching on the `phase_type` field (`"onboard"` → onboarding, `"prod"` → production). Confirm with the Orbit data schema and update `_board_phase()` in `orbit_client.py` if needed.
- **HubSpot association**: Notes are associated to companies using association type ID `190`.
