"""Persistence — submissions, appeals, and a structured audit log (SQLite).

The audit log is the canonical record: every attribution decision and every
appeal writes exactly one row, with the full signal breakdown stored as JSON so
each entry is self-contained and replayable.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from . import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    conn = get_conn(db_path)
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id           TEXT PRIMARY KEY,
                creator_id   TEXT,
                content_type TEXT,
                text         TEXT NOT NULL,
                attribution  TEXT NOT NULL,
                confidence   REAL NOT NULL,
                ai_score     REAL NOT NULL,
                label_variant TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                status       TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS appeals (
                id            TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL,
                creator_id    TEXT,
                reasoning     TEXT NOT NULL,
                status        TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                FOREIGN KEY (submission_id) REFERENCES submissions(id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id            TEXT PRIMARY KEY,
                event_type    TEXT NOT NULL,      -- 'decision' | 'appeal'
                submission_id TEXT NOT NULL,
                creator_id    TEXT,
                detail_json   TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
            """
        )
    conn.close()


def _signal_scores(decision: dict) -> tuple:
    """Pull the individual (llm_score, stylometry_score) out of a decision.

    llm_score is None when the LLM signal was unavailable (offline fallback).
    """
    llm_score = None
    stylo_score = None
    for sig in decision.get("signals", []):
        if sig["name"] == "llm" and sig.get("available"):
            llm_score = sig["ai_probability"]
        elif sig["name"] == "stylometry":
            stylo_score = sig["ai_probability"]
    return llm_score, stylo_score


def _audit(conn, event_type: str, submission_id: str, creator_id, detail: dict) -> str:
    entry_id = _new_id("log")
    conn.execute(
        "INSERT INTO audit_log (id, event_type, submission_id, creator_id, detail_json, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (entry_id, event_type, submission_id, creator_id, json.dumps(detail), _now()),
    )
    return entry_id


def record_submission(decision: dict, text: str, creator_id=None, db_path=None) -> dict:
    """Persist a submission + its decision, and write a 'decision' audit row."""
    conn = get_conn(db_path)
    content_id = str(uuid.uuid4())  # public identifier used by /submit, /appeal, /log
    created = _now()
    llm_score, stylo_score = _signal_scores(decision)
    with conn:
        conn.execute(
            "INSERT INTO submissions (id, creator_id, content_type, text, attribution,"
            " confidence, ai_score, label_variant, decision_json, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                content_id,
                creator_id,
                decision["content_type"],
                text,
                decision["attribution"],
                decision["confidence"],
                decision["ai_score"],
                decision["label"]["variant"],
                json.dumps(decision),
                "classified",
                created,
            ),
        )
        _audit(
            conn,
            "decision",
            content_id,
            creator_id,
            {
                "attribution": decision["attribution"],
                "confidence": decision["confidence"],
                "ai_score": decision["ai_score"],
                "llm_score": llm_score,               # None when the LLM signal was unavailable
                "stylometry_score": stylo_score,
                "label_variant": decision["label"]["variant"],
                "weights": decision["weights"],
                "signals": decision["signals"],
                "single_signal": decision["single_signal"],
                "status": "classified",
            },
        )
    conn.close()
    return {"content_id": content_id, "status": "classified", "created_at": created}


def get_submission(submission_id: str, db_path=None) -> dict | None:
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["decision"] = json.loads(d.pop("decision_json"))
    return d


def record_appeal(content_id: str, creator_reasoning: str, creator_id=None, db_path=None) -> dict | None:
    """Set submission status to 'under_review', log the appeal. None if unknown."""
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM submissions WHERE id = ?", (content_id,)).fetchone()
    if row is None:
        conn.close()
        return None
    original = json.loads(row["decision_json"])
    appeal_id = _new_id("app")
    created = _now()
    with conn:
        conn.execute("UPDATE submissions SET status = ? WHERE id = ?", ("under_review", content_id))
        conn.execute(
            "INSERT INTO appeals (id, submission_id, creator_id, reasoning, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (appeal_id, content_id, creator_id, creator_reasoning, "under_review", created),
        )
        _audit(
            conn,
            "appeal",
            content_id,
            creator_id,
            {
                "appeal_id": appeal_id,
                "appeal_reasoning": creator_reasoning,
                "status": "under_review",
                "original_decision": {
                    "attribution": original["attribution"],
                    "confidence": original["confidence"],
                    "ai_score": original["ai_score"],
                    "label_variant": original["label"]["variant"],
                    "signals": original["signals"],
                },
            },
        )
    conn.close()
    return {
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "under_review",
        "created_at": created,
        "appeal_reasoning": creator_reasoning,
        "original_decision": original,
    }


def list_appeals(db_path=None) -> list[dict]:
    """Appeal queue for human moderators: reasoning paired with the original call."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT a.*, s.text AS submission_text, s.decision_json"
        " FROM appeals a JOIN submissions s ON a.submission_id = s.id"
        " ORDER BY a.created_at DESC"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["original_decision"] = json.loads(d.pop("decision_json"))
        out.append(d)
    return out


def get_audit_log(limit: int = 100, db_path=None) -> list[dict]:
    """Return flat, structured audit entries.

    Each entry leads with content_id / creator_id / timestamp / event_type, then
    spreads the event detail (attribution, confidence, llm_score,
    stylometry_score, status, ... or the appeal fields) to the top level.
    """
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY created_at ASC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        detail = json.loads(r["detail_json"])
        entry = {
            "log_id": r["id"],
            "content_id": r["submission_id"],
            "creator_id": r["creator_id"],
            "timestamp": r["created_at"],
            "event_type": r["event_type"],
        }
        entry.update(detail)
        out.append(entry)
    return out


def analytics(db_path=None) -> dict:
    """Detection patterns, appeal rate, and average confidence (extra metric)."""
    conn = get_conn(db_path)
    subs = conn.execute("SELECT attribution, label_variant, confidence FROM submissions").fetchall()
    n_appeals = conn.execute("SELECT COUNT(*) AS c FROM appeals").fetchone()["c"]
    conn.close()

    n = len(subs)
    by_attribution: dict[str, int] = {}
    by_variant: dict[str, int] = {}
    conf_sum = 0.0
    for r in subs:
        by_attribution[r["attribution"]] = by_attribution.get(r["attribution"], 0) + 1
        by_variant[r["label_variant"]] = by_variant.get(r["label_variant"], 0) + 1
        conf_sum += r["confidence"]

    return {
        "total_submissions": n,
        "total_appeals": n_appeals,
        "appeal_rate": round(n_appeals / n, 4) if n else 0.0,
        "attribution_distribution": by_attribution,
        "label_variant_distribution": by_variant,
        "average_confidence": round(conf_sum / n, 4) if n else 0.0,
    }
