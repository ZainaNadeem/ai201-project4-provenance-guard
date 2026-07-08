"""Detection pipeline — runs both signals, scores, and builds a label.

This is the pure decision core: no HTTP, no DB. Both the Flask app and the demo
script call ``classify()`` so behaviour is identical whether exercised over the
network or offline.
"""

from __future__ import annotations

from . import labels, scoring
from .llm_signal import analyze_llm
from .stylometry import analyze_stylometry


def classify(text: str, content_type: str = "text") -> dict:
    """Classify a piece of text. Returns a decision dict (no persistence)."""
    llm = analyze_llm(text)
    stylo = analyze_stylometry(text)

    decision = scoring.combine(llm, stylo)
    label = labels.build_label(
        decision["attribution"], decision["confidence"], decision["ai_score"]
    )

    return {
        "content_type": content_type,
        "attribution": decision["attribution"],
        "confidence": decision["confidence"],
        "ai_score": decision["ai_score"],
        "label": label,
        "weights": decision["weights"],
        "components": decision["components"],
        "single_signal": decision["single_signal"],
        "signals": [
            _summarize_llm(llm),
            _summarize_stylo(stylo),
        ],
    }


def _summarize_llm(llm: dict) -> dict:
    if llm.get("available"):
        return {
            "name": "llm",
            "available": True,
            "ai_probability": llm["ai_probability"],
            "reasoning": llm.get("reasoning", ""),
            "model": llm.get("model"),
        }
    return {"name": "llm", "available": False, "reason": llm.get("reason", "")}


def _summarize_stylo(stylo: dict) -> dict:
    return {
        "name": "stylometry",
        "available": True,
        "ai_probability": stylo["ai_probability"],
        "reliability": stylo["reliability"],
        "metrics": stylo["metrics"],
        "n_words": stylo["n_words"],
        "n_sentences": stylo["n_sentences"],
    }
