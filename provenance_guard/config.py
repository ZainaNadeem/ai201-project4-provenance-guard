"""Single source of truth for tunable thresholds, weights, and limits.

Every "magic number" in the pipeline lives here so the behaviour described in
planning.md and the README can be traced to one place.
"""

# --- Signal combination weights -------------------------------------------
# The LLM is the stronger holistic judge, so it carries more weight when both
# signals are available. When the LLM is unavailable, stylometry takes the full
# weight and SINGLE_SIGNAL_PENALTY is applied to confidence.
WEIGHT_LLM = 0.60
WEIGHT_STYLO = 0.40
SINGLE_SIGNAL_PENALTY = 0.85

# --- Direction thresholds on ai_score = P(AI) ------------------------------
AI_THRESHOLD = 0.65      # ai_score >= this -> leans AI
HUMAN_THRESHOLD = 0.35   # ai_score <= this -> leans human
# (between the two -> "uncertain" regardless of confidence)

# --- Label-variant confidence bars (asymmetric on purpose) -----------------
# Calling a human's work AI is the worst outcome on a writing platform, so the
# AI label demands MORE confidence than the human label.
HIGH_CONF_AI = 0.75
HIGH_CONF_HUMAN = 0.65

# --- Confidence-shaping ----------------------------------------------------
# Below this word count the text is treated as increasingly unreliable to judge.
MIN_RELIABLE_WORDS = 40
# How much signal agreement matters in the confidence formula (the rest is a
# floor so a strong single direction still counts even under mild disagreement).
AGREEMENT_FLOOR = 0.60   # confidence *= (AGREEMENT_FLOOR + (1-floor)*agreement)

# --- Stylometry sub-metric weights (the internal "ensemble") ---------------
STYLO_WEIGHTS = {
    "burstiness": 0.40,       # sentence-length variation
    "lexical_diversity": 0.20,
    "punctuation_variety": 0.20,
    "human_voice": 0.20,      # contractions / first person / hedges
}

# --- LLM signal ------------------------------------------------------------
GROQ_MODEL = "llama-3.3-70b-versatile"
LLM_TIMEOUT_SECONDS = 20

# --- Rate limits (documented reasoning in the README) ----------------------
SUBMIT_LIMITS = "10 per minute;100 per day"
APPEAL_LIMITS = "5 per minute;20 per day"
DEFAULT_LIMITS = "200 per day"

# --- Storage ---------------------------------------------------------------
DB_PATH = "provenance_guard.db"
