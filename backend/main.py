from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routers import admin, notes, sync
from .scheduler import apply_push_schedule, apply_schedule, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.start()
    apply_schedule()           # load config and arm the sync job
    apply_push_schedule()      # load config and arm the push job
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Orbit Notes",
    description="Weekly Orbit → HubSpot note generator",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(notes.router)
app.include_router(sync.router)
app.include_router(admin.router)

# Serve the single-page frontend
_frontend = Path(__file__).parent.parent / "frontend"
if _frontend.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_ui():
        return FileResponse(str(_frontend / "index.html"))
