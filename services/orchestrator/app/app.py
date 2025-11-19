from __future__ import annotations

import logging
import os
from typing import Any, Dict

from flask import Flask, flash, redirect, render_template, request, url_for

from . import database
from .database import db
from .models import MediaAsset, MediaStatus
from .scanner import scan
from .worker import spawn_worker

LOGGER = logging.getLogger(__name__)


def create_app(config: Dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///media_assets.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MEDIA_ROOT=os.environ.get("MEDIA_ROOT", "/watch"),
        OUTPUT_ROOT=os.environ.get("OUTPUT_ROOT", "/output"),
        DELETE_ORIGINAL=os.environ.get("DELETE_ORIGINAL", "false").lower() == "true",
        WORKER_POLL_INTERVAL=float(os.environ.get("WORKER_POLL_INTERVAL", "2.0")),
        ITEMS_PER_PAGE=int(os.environ.get("ITEMS_PER_PAGE", "25")),
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret"),
        ENABLE_WORKER=os.environ.get("ENABLE_WORKER", "true").lower() == "true",
    )
    if config:
        app.config.update(config)

    database.init_app(app)

    with app.app_context():
        db.create_all()
        _sanity_check_jobs()
        scan()

    if app.config.get("ENABLE_WORKER", True):
        spawn_worker(app)

    _register_routes(app)
    return app


def _register_routes(app: Flask) -> None:
    @app.route("/")
    def index() -> Any:
        return redirect(url_for("library"))

    @app.route("/library")
    def library() -> Any:
        page = request.args.get("page", 1, type=int)
        pagination = MediaAsset.query.order_by(MediaAsset.id.desc()).paginate(
            page=page, per_page=app.config["ITEMS_PER_PAGE"]
        )
        return render_template(
            "library.html",
            pagination=pagination,
            status_colors=_status_colors(),
            MediaStatus=MediaStatus,
        )

    @app.post("/reprocess/<int:asset_id>")
    def reprocess(asset_id: int) -> Any:
        asset = MediaAsset.query.get_or_404(asset_id)
        if asset.status_enum in {MediaStatus.Archived, MediaStatus.Processing}:
            flash("Cannot reprocess archived or in-flight assets", "error")
            return redirect(url_for("library"))

        asset.status = MediaStatus.Queued.value
        asset.retry_count = 0
        asset.error_log = None
        db.session.commit()
        flash(f"Asset {asset_id} queued for reprocessing", "success")
        return redirect(url_for("library"))

    @app.post("/scan")
    def manual_scan() -> Any:
        count = scan()
        flash(f"Scan complete. {count} files evaluated.", "success")
        return redirect(url_for("library"))


def _sanity_check_jobs() -> None:
    LOGGER.info("Resetting any Processing jobs back to Queued")
    MediaAsset.query.filter_by(status=MediaStatus.Processing.value).update(
        {"status": MediaStatus.Queued.value}
    )
    db.session.commit()


def _status_colors() -> Dict[str, str]:
    return {
        MediaStatus.Completed.value: "success",
        MediaStatus.Archived.value: "secondary",
        MediaStatus.Failed.value: "danger",
        MediaStatus.Processing.value: "warning",
        MediaStatus.Queued.value: "info",
        MediaStatus.New.value: "primary",
    }


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
