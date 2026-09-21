"""RAIA - Radio AI Africa. The FastAPI app: routers only.

    uv run uvicorn main:app --port 8000

The API serves precomputed files. Nothing slow happens in a request handler: the pipeline
(`uv run python -m src.pipeline.morning`) writes runs/<date>/ ahead of time.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.admin.jobs import nightly_loop
from src.api import admin, bulletins, hotlines, now, sources, whatsapp
from src.settings import get_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """The agents prepare the coming day overnight (settings.nightly_build_at), so the morning plays
    from disk. The newsroom can still generate any bulletin by hand."""
    task = asyncio.create_task(nightly_loop()) if get_settings().nightly_build_enabled else None
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="RAIA - Radio AI Africa",
    description="A civic radio station produced by AI agents. The presenters are synthetic voices; "
                "every aired claim traces back to named sources.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(now.router)
app.include_router(bulletins.router)
app.include_router(sources.router)
app.include_router(hotlines.router)
app.include_router(whatsapp.router)
app.include_router(admin.router)
