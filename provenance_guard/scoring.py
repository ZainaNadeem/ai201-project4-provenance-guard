"""Confidence scoring — combine signals into a direction + a calibrated certainty.

We deliberately separate:
  * ai_score  = P(AI)          -> which way the evidence leans (direction)
  * confidence                 -> how much to trust that lean (certainty)

See planning.md §3.2 for the rationale and formula.
"""

from __future__ import annotations

from . import config


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def combine(llm: dict, stylo: dict) -> dict:
    """Combine the two signal dicts into a scored decision.

    Returns:
        {
          "ai_score": float,          # P(AI)
          "confidence": float,        # certainty in the call
          "attribution": "likely_ai"|"likely_human"|"uncertain",
          "weights": {...},
          "components": {"distance":..., "agreement":..., "reliability":...},
          "single_signal": bool,
        }
    """
    p_stylo = stylo["ai_probability"]
    reliability = stylo.get("reliability", 1.0)

    if llm.get("available"):
        p_llm = llm["ai_probability"]
        w_llm, w_stylo = config.WEIGHT_LLM, config.WEIGHT_STYLO
        ai_score = (w_llm * p_llm + w_stylo * p_stylo) / (w_llm + w_stylo)
        agreement = 1.0 - abs(p_llm - p_stylo)
        single_signal = False
        penalty = 1.0
        weights = {"llm": w_llm, "stylometry": w_stylo}
    else:
        # Degrade to stylometry alone; penalise confidence for lost redundancy.
        ai_score = p_stylo
        agreement = 1.0  # no second opinion to disagree
        single_signal = True
        penalty = config.SINGLE_SIGNAL_PENALTY
        weights = {"llm": 0.0, "stylometry": 1.0}

    # Direction.
    if ai_score >= config.AI_THRESHOLD:
        attribution = "likely_ai"
    elif ai_score <= config.HUMAN_THRESHOLD:
        attribution = "likely_human"
    else:
        attribution = "uncertain"

    # Certainty.
    distance = abs(ai_score - 0.5) * 2.0                       # 0 at coin flip, 1 at extremes
    agreement_factor = config.AGREEMENT_FLOOR + (1 - config.AGREEMENT_FLOOR) * agreement
    confidence = distance * agreement_factor * reliability * penalty
    confidence = _clamp(confidence)

    return {
        "ai_score": round(ai_score, 4),
        "confidence": round(confidence, 4),
        "attribution": attribution,
        "weights": weights,
        "components": {
            "distance": round(distance, 4),
            "agreement": round(agreement, 4),
            "reliability": round(reliability, 4),
            "single_signal_penalty": penalty,
        },
        "single_signal": single_signal,
    }
