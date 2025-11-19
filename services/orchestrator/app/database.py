from __future__ import annotations

import logging
from typing import Any

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

LOGGER = logging.getLogger(__name__)

db: SQLAlchemy = SQLAlchemy()


def init_app(app: Any) -> None:
    """Initialize the database extension and enable SQLite WAL mode."""

    db.init_app(app)

    with app.app_context():
        _configure_sqlite_wal(db.engine)


def _configure_sqlite_wal(engine: Engine) -> None:
    if engine.url.get_backend_name() != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[override]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.close()
        LOGGER.debug("SQLite WAL mode enabled for concurrent access")
