"""Run explicitly: python -m app.domain.documents.scan_worker. No network vendor API."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models as identity_models  # noqa: F401
from app.domain.documents.scan_jobs import run_once
from app.domain.documents.storage import QuarantineStorage


async def main():
    settings = get_settings()
    if not settings.document_scanner_socket or not settings.document_storage_root:
        raise SystemExit("Local scanner and private storage must be explicitly configured.")
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise SystemExit("The scan worker requires PostgreSQL for concurrent admission.")
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = QuarantineStorage(settings.document_storage_root)
    try:
        while True:
            worked = await run_once(factory, storage, settings.document_scanner_socket)
            await asyncio.sleep(1 if worked else 5)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
