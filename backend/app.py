"""FastAPI application factory for NovelGenerator v5."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import ledger as ledger_router
from .api import novels as novels_router
from .api import system as system_router
from .api import writing as writing_router
from .config import FRONTEND_DIR, AppConfig
from .core.database import NarrativeLedgerDB
from .core.llm import AsyncLLMGateway, LLMConfigStore
from .core.writing_engine import WritingEngine


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    cfg = AppConfig.from_env(data_dir=data_dir)
    db = NarrativeLedgerDB(cfg.db_path)
    llm_store = LLMConfigStore(cfg.llm_config_path)
    llm_gateway = AsyncLLMGateway(llm_store)
    engine = WritingEngine(db, llm_gateway)

    app = FastAPI(
        title="墨局 · NovelGenerator v5",
        version="5.0.0",
        description="基于统一叙事状态账本与四阶段写作引擎的本地优先长篇小说创作工坊",
    )
    app.state.config = cfg
    app.state.db = db
    app.state.llm_store = llm_store
    app.state.llm = llm_gateway
    app.state.engine = engine

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8787", "http://localhost:8787"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(novels_router.router)
    app.include_router(writing_router.router)
    app.include_router(ledger_router.router)
    app.include_router(system_router.router)

    if FRONTEND_DIR.is_dir():
        app.mount("/styles", StaticFiles(directory=str(FRONTEND_DIR / "styles")), name="styles")
        app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")

        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(str(FRONTEND_DIR / "index.html"))

    return app


app = create_app()
