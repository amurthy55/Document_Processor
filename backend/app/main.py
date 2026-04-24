from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from platformdirs import user_data_dir

from app import db
from app.security import get_fernet
from app.settings import settings


def _appdata_root() -> Path:
    if settings.appdata_root:
        return Path(settings.appdata_root)
    return Path(user_data_dir(settings.app_name, "eSeva"))


def create_app() -> FastAPI:
    root = _appdata_root()
    keys_dir = root / "keys"
    key_path = keys_dir / "master.key"

    fernet = get_fernet(key_path)

    data_dir = root / "data"
    conn = db.connect(data_dir / "eseva.sqlite3")
    db.init_schema(conn)

    operations_root = root / "Operations"
    operations_root.mkdir(parents=True, exist_ok=True)

    from app.operations.store import OperationStore
    from app.services.config_store import ServiceConfigStore

    op_store = OperationStore(
        conn=conn,
        fernet=fernet,
        operations_root=operations_root,
        ttl_seconds=settings.operation_ttl_seconds,
    )
    configs_root = (
        Path(settings.configs_dir)
        if settings.configs_dir
        else Path(__file__).resolve().parents[2] / "configs"
    )
    cfg_store = ServiceConfigStore(configs_root)

    app = FastAPI(title=settings.app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins for desktop app
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.state.op_store = op_store
    app.state.cfg_store = cfg_store

    from app.routers.operations import router as operations_router
    from app.routers.services import router as services_router
    from app.routers.admin import router as admin_router
    from app.routers.documents import router as documents_router
    from app.routers.sync_documents import router as sync_documents_router

    app.include_router(operations_router, prefix="/api")
    app.include_router(services_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    app.include_router(sync_documents_router, prefix="/api")

    async def expiry_loop() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                op_store.expire_due_operations()
            except Exception:
                continue

    @app.on_event("startup")
    async def _startup() -> None:
        asyncio.create_task(expiry_loop())

    return app


app = create_app()
