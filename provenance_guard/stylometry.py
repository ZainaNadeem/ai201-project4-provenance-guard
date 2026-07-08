"""Signal 2 — stylometric heuristics (pure Python, no external libraries).

Measures *structural* statistical properties of prose that tend to differ
between human and AI writing. AI text trends toward statistical uniformity;
human text is burstier and more varied. Each sub-metric produces a
``human_likeness`` in [0, 1]; they are combined (weighted) into an
``ai_probability`` = 1 - weighted_mean(human_likeness).

This is intentionally a small internal ensemble — see config.STYLO_WEIGHTS.
"""

from __future__ import annotations

import math
import re
from statistics import mean, pstdev

from . import config

_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s+|$)")
_WORD = re.compile(r"[A-Za-z']+")
_PUNCT_MARKS = set(",;:—–-()[]\"'…?!.")
_CONTRACTION = re.compile(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", re.IGNORECASE)
_FIRST_PERSON = re.compile(r"\b(i|i'm|i'd|i've|i'll|me|my|mine|myself)\b", re.IGNORECASE)
_HEDGES = re.compile(
    r"\b(maybe|perhaps|i think|i guess|sort of|kind of|somehow|probably|"
    r"honestly|actually|really|just|pretty much|i suppose)\b",
    re.IGNORECASE,
)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _map_range(x: float, lo: float, hi: float) -> float:
    """Linearly map x from [lo, hi] to [0, 1], clamped."""
    if hi == lo:
        return 0.5
    return _clamp((x - lo) / (hi - lo))


def _split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    return parts


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def analyze_stylometry(text: str) -> dict:
    """Return the stylometric signal.

    Output shape:
        {
          "name": "stylometry",
          "available": True,
          "ai_probability": float in [0,1],
          "reliability": float in [0,1],   # low for short texts
          "metrics": { <sub-metric>: {"value":..., "human_likeness":...}, ... },
        }
    """
    words = _tokens(text)
    n_words = len(words)
    sentences = _split_sentences(text)

    # Reliability: short texts don't give stable statistics.
    reliability = _clamp(n_words / config.MIN_RELIABLE_WORDS)

    metrics: dict[str, dict] = {}

    # 1) Sentence-length burstiness (coefficient of variation).
    #    Humans vary a lot (CV ~0.5-0.9); AI is uniform (CV ~0.2-0.4).
    sent_lengths = [len(_tokens(s)) for s in sentences]
    if len(sent_lengths) >= 2 and mean(sent_lengths) > 0:
        cv = pstdev(sent_lengths) / mean(sent_lengths)
    else:
        cv = 0.0
    burst_human = _map_range(cv, 0.25, 0.75)  # higher CV -> more human
    if len(sent_lengths) < 2:
        burst_human = 0.5  # not enough sentences to tell
    metrics["burstiness"] = {"value": round(cv, 3), "human_likeness": round(burst_human, 3)}

    # 2) Lexical diversity — root type-token ratio (Guiraud's R), length-robust.
    if n_words > 0:
        rttr = len(set(words)) / math.sqrt(n_words)
    else:
        rttr = 0.0
    # Typical RTTR lands ~ 4-9 for real prose; higher = richer vocabulary.
    lex_human = _map_range(rttr, 4.0, 8.0)
    metrics["lexical_diversity"] = {"value": round(rttr, 3), "human_likeness": round(lex_human, 3)}

    # 3) Punctuation variety — count of DISTINCT punctuation marks used.
    #    Human prose mixes dashes/semicolons/parentheticals; AI is monotonous.
    distinct_punct = len({c for c in text if c in _PUNCT_MARKS})
    punct_human = _map_range(distinct_punct, 2, 7)
    metrics["punctuation_variety"] = {
        "value": distinct_punct,
        "human_likeness": round(punct_human, 3),
    }

    # 4) Human-voice markers — contractions, first person, hedges (rate per word).
    if n_words > 0:
        marker_hits = (
            len(_CONTRACTION.findall(text))
            + len(_FIRST_PERSON.findall(text))
            + len(_HEDGES.findall(text))
        )
        marker_rate = marker_hits / n_words
    else:
        marker_rate = 0.0
    voice_human = _map_range(marker_rate, 0.0, 0.06)
    metrics["human_voice"] = {
        "value": round(marker_rate, 4),
        "human_likeness": round(voice_human, 3),
    }

    # Weighted combine -> ai_probability.
    total_w = sum(config.STYLO_WEIGHTS.values())
    human_score = (
        sum(config.STYLO_WEIGHTS[k] * metrics[k]["human_likeness"] for k in config.STYLO_WEIGHTS)
        / total_w
    )
    ai_probability = _clamp(1.0 - human_score)

    return {
        "name": "stylometry",
        "available": True,
        "ai_probability": round(ai_probability, 4),
        "reliability": round(reliability, 3),
        "metrics": metrics,
        "n_words": n_words,
        "n_sentences": len(sentences),
    }
