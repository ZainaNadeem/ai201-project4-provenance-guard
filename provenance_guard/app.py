"""Flask API for Provenance Guard.

Endpoints:
  POST /submit            classify text -> decision + label (rate limited)
  POST /appeal            contest a classification (rate limited)
  GET  /submission/<id>   current stored state of one submission
  GET  /appeals           appeal queue for human moderators
  GET  /log               structured audit log
  GET  /analytics         detection patterns, appeal rate, avg confidence
  GET  /health            liveness + whether the LLM signal is configured
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from . import config, store
from .llm_signal import _get_client
from .pipeline import classify

MAX_TEXT_CHARS = 20_000


def create_app(db_path: str | None = None) -> Flask:
    load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or config.DB_PATH

    store.init_db(app.config["DB_PATH"])

    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[config.DEFAULT_LIMITS],
        storage_uri="memory://",
    )

    def _db():
        return app.config["DB_PATH"]

    @app.post("/submit")
    @limiter.limit(config.SUBMIT_LIMITS)
    def submit():
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Field 'text' is required and must be non-empty."}), 400
        if len(text) > MAX_TEXT_CHARS:
            return jsonify({"error": f"Text exceeds {MAX_TEXT_CHARS} characters."}), 413

        creator_id = body.get("creator_id")
        content_type = body.get("content_type", "text")

        decision = classify(text, content_type=content_type)
        saved = store.record_submission(decision, text, creator_id=creator_id, db_path=_db())

        return jsonify(
            {
                "submission_id": saved["submission_id"],
                "status": saved["status"],
                "attribution": decision["attribution"],
                "confidence": decision["confidence"],
                "ai_score": decision["ai_score"],
                "label": decision["label"],
                "signals": decision["signals"],
                "weights": decision["weights"],
                "single_signal": decision["single_signal"],
            }
        ), 201

    @app.post("/appeal")
    @limiter.limit(config.APPEAL_LIMITS)
    def appeal():
        body = request.get_json(silent=True) or {}
        submission_id = body.get("submission_id")
        reasoning = (body.get("reasoning") or "").strip()
        if not submission_id:
            return jsonify({"error": "Field 'submission_id' is required."}), 400
        if not reasoning:
            return jsonify({"error": "Field 'reasoning' is required to appeal."}), 400

        result = store.record_appeal(
            submission_id, reasoning, creator_id=body.get("creator_id"), db_path=_db()
        )
        if result is None:
            return jsonify({"error": f"No submission with id '{submission_id}'."}), 404
        return jsonify(result), 201

    @app.get("/submission/<submission_id>")
    def get_submission(submission_id):
        sub = store.get_submission(submission_id, db_path=_db())
        if sub is None:
            return jsonify({"error": "Not found."}), 404
        sub.pop("decision_json", None)
        return jsonify(sub)

    @app.get("/appeals")
    def appeals():
        return jsonify({"appeals": store.list_appeals(db_path=_db())})

    @app.get("/log")
    def log():
        limit = request.args.get("limit", default=100, type=int)
        return jsonify({"entries": store.get_audit_log(limit=limit, db_path=_db())})

    @app.get("/analytics")
    def analytics():
        return jsonify(store.analytics(db_path=_db()))

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "llm_signal_configured": _get_client() is not None})

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify(
            {"error": "Rate limit exceeded.", "detail": str(e.description)}
        ), 429

    return app


app = create_app()

if __name__ == "__main__":
    app.run(port=5000, debug=True)
