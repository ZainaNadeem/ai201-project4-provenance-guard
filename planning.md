# Provenance Guard — Planning & Specification

> A backend attribution service that a creative-sharing platform can plug in to
> classify submitted text as human- or AI-written, score how confident that call
> is, surface a plain-language transparency label, and let creators appeal.

This document is the spec. It was written **before** implementation and is the
canonical reference for what the code does and why. Where a value here differs
from the code, the code in [provenance_guard/config.py](provenance_guard/config.py)
is the source of truth (all thresholds live in one place).

---

## 1. Architecture narrative (the path one submission takes)

A creator on the platform pastes a poem into a submission box and hits "post".
The platform forwards the raw text to Provenance Guard's `POST /submit`.

1. **API layer (`app.py`)** receives `{text, creator_id, content_type}`. The
   request first passes the **rate limiter**, which rejects it with `429` if this
   client has exceeded its per-minute / per-day budget.
2. The text is handed to the **detection pipeline (`pipeline.py`)**, which runs
   two independent signals:
   - **Signal 1 — LLM classifier (`llm_signal.py`)** asks Groq
     `llama-3.3-70b-versatile` to judge, holistically, how AI-like the text
     reads, returning a probability + short reasoning. If no API key is
     configured or the call fails, the signal reports `available: false` and the
     pipeline degrades to Signal 2 alone (and records that fact).
   - **Signal 2 — Stylometry (`stylometry.py`)** computes structural statistics
     in pure Python (sentence-length burstiness, lexical diversity, punctuation
     variety, human-voice markers) and derives an AI-likelihood from them.
3. **Confidence scoring (`scoring.py`)** combines the signal probabilities into a
   single `ai_score` (P(AI)), then derives a **confidence** value from how far
   the score is from the coin-flip midpoint, how much the two signals agree, and
   how much text there was to judge. It picks an attribution
   (`likely_ai` / `likely_human` / `uncertain`).
4. **Label generation (`labels.py`)** turns `(attribution, confidence, ai_score)`
   into one of three plain-language **transparency label** variants shown to
   readers.
5. **Storage (`store.py`)** persists the submission and writes a structured
   **audit-log** entry: the decision, both signal outputs, the weights used, the
   confidence, and the label variant.
6. The API returns `{content_id, attribution, confidence, ai_score, label,
   signals, status}` to the platform, which renders the label under the post.

**Appeal flow:** the creator disputes the call and the platform calls
`POST /appeal` with `{content_id, creator_id, creator_reasoning}`. Provenance Guard
sets the submission's status to `under_review`, writes an `appeal` entry to the
audit log linked to the original decision, and returns the appeal record. A human
moderator later reads the appeal queue (`GET /appeals`) — no automated
re-classification happens.

---

## 2. Architecture

### Submission flow

```
                    POST /submit  { text, creator_id, content_type }
                          │
                          ▼
                  ┌───────────────┐   429 if over budget
                  │ Rate limiter  │──────────────────────► client
                  └───────┬───────┘
                          │ raw text
                          ▼
                  ┌───────────────────────────────────────────┐
                  │          Detection pipeline                │
                  │                                            │
   raw text ─────►│  Signal 1: LLM (Groq)   Signal 2: Stylometry
                  │   ai_probability ∈[0,1]   ai_probability ∈[0,1]
                  │   + reasoning             + metrics + reliability
                  └───────┬───────────────────────┬───────────┘
                          │ p_llm                  │ p_stylo
                          ▼                        ▼
                  ┌───────────────────────────────────────────┐
                  │   Confidence scoring (weighted combine)    │
                  │   ai_score = w_llm·p_llm + w_stylo·p_stylo  │
                  │   confidence = f(distance, agreement,      │
                  │                   reliability)             │
                  │   attribution = likely_ai / uncertain /    │
                  │                 likely_human               │
                  └───────┬───────────────────────────────────┘
                          │ (attribution, confidence, ai_score)
                          ▼
                  ┌───────────────────┐
                  │ Label generation  │  → one of 3 variants (label text)
                  └───────┬───────────┘
                          │ decision + signals + label
                          ▼
                  ┌───────────────────┐     ┌──────────────────┐
                  │ Store submission  │────►│  Audit log (row) │
                  └───────┬───────────┘     └──────────────────┘
                          │ JSON response
                          ▼
        { content_id, attribution, confidence, ai_score,
          label:{variant,title,body}, signals:[...], status }
```

### Appeal flow

```
   POST /appeal { content_id, creator_id, creator_reasoning }
          │
          ▼
   ┌────────────────┐   404 if content_id unknown
   │  Load original │───────────────────────────► client
   │   submission   │
   └───────┬────────┘
           │ set status = "under_review"
           ▼
   ┌────────────────────┐   ┌──────────────────────────────┐
   │ Store appeal       │──►│ Audit log (row, type=appeal,  │
   │ (creator_reasoning)│   │  links original decision)     │
   └───────┬────────────┘   └──────────────────────────────┘
           │ appeal record
           ▼
   { appeal_id, content_id, status:"under_review",
     original_decision:{...}, appeal_reasoning }
                     │
                     ▼   (later, human moderator)
              GET /appeals → appeal queue for review
```

---

## 3. The five required questions

### 3.1 Detection signals

We use **two genuinely distinct signals** — one *semantic*, one *structural* —
so they fail in different ways and their agreement is itself informative.

| | Signal 1 — LLM classifier | Signal 2 — Stylometry |
|---|---|---|
| **Tool** | Groq `llama-3.3-70b-versatile` | Pure Python |
| **Measures** | Whether the *meaning and voice* read as human or AI: cliché density, generic "helpful assistant" phrasing, tonal coherence, hedging | *Structural statistics* of the prose, independent of meaning |
| **Output** | `ai_probability ∈ [0,1]` + one-sentence reasoning | `ai_probability ∈ [0,1]` + per-metric breakdown + `reliability ∈ [0,1]` |
| **Why it separates human/AI** | LLMs recognize their own default register; humans deviate from it in idiosyncratic ways | AI prose is statistically *uniform*; human prose is *bursty* and varied |
| **Blind spot** | No ground truth — reflects the model's bias; can flag careful/non-native human writing as AI; edited AI text fools it; **unavailable without an API key** | Short texts give unstable statistics; deliberately repetitive forms (minimalist poetry, lists) look "uniform" = AI-like; heavily human-edited AI text looks varied = human-like |

**Signal 2 is itself a weighted composite** of four sub-metrics (see
[stretch: ensemble](#5-stretch-features)):

1. **Sentence-length burstiness** (coefficient of variation of sentence lengths)
   — humans vary sentence length dramatically; AI trends toward a uniform
   medium length.
2. **Lexical diversity** (root type-token ratio, length-normalized) — captures
   vocabulary range without punishing longer texts.
3. **Punctuation variety** (distinct punctuation marks used) — human prose mixes
   dashes, semicolons, parentheticals; AI is more monotonous.
4. **Human-voice markers** (contractions, first person, hedges like "maybe",
   "I guess") — presence pushes toward human.

**Combination:** `ai_score = w_llm·p_llm + w_stylo·p_stylo` with
`w_llm = 0.6`, `w_stylo = 0.4` (the LLM is the stronger holistic judge). If the
LLM is unavailable, stylometry takes the full weight and a **single-signal
penalty** (`×0.85`) is applied to confidence to reflect the lost redundancy.

### 3.2 Uncertainty representation

We separate **direction** (`ai_score` = P(AI)) from **certainty**
(`confidence`). This is deliberate: a piece can lean AI *weakly* (score 0.6,
confidence 0.3) or *strongly* (score 0.9, confidence 0.85).

**What confidence means to a user:** confidence answers "how much should you
trust this call?" — **not** "how AI is it?".
- `confidence ≈ 0.5` → "we have a lean but you should treat it as a hint."
- `confidence ≈ 0.9` → "the signals strongly and jointly point one way."

**How confidence is computed** (`scoring.py`), all in [0,1]:
```
distance    = |ai_score − 0.5| · 2          # how far from a coin flip
agreement   = 1 − |p_llm − p_stylo|         # do the signals concur? (1.0 if single-signal)
reliability = min(1, words / MIN_RELIABLE_WORDS)   # enough text to judge?
confidence  = distance · (0.6 + 0.4·agreement) · reliability · single_signal_penalty
```
An `ai_score` of 0.62 with signals that disagree and a 30-word poem yields a
*low* confidence → an **uncertain** label. The same 0.62 with both signals
agreeing on a 300-word essay yields higher confidence.

**Thresholds** (in `config.py`):
- Direction: `ai_score ≥ 0.65` → lean AI; `≤ 0.35` → lean human; else uncertain.
- Label variant selection is **asymmetric** (see 3.3): calling something AI
  requires *more* confidence than calling it human.

### 3.3 Transparency label design (three variants)

Design principle — **false-positive asymmetry**: on a writing platform, wrongly
branding a human's work as AI is the worst outcome. So the "AI-generated" label
requires a higher confidence bar (`≥ 0.75`) than the "human" label (`≥ 0.65`),
and every label is hedged, names that it can be appealed, and never says
"proven". Anything that clears neither bar falls to **uncertain**.

| Variant | Shown when | Title | Body |
|---|---|---|---|
| **high-confidence AI** | `ai_score ≥ 0.65` **and** `confidence ≥ 0.75` | 🤖 Likely AI-generated | "Our checks suggest this text was **probably generated by AI** (confidence: {pct}%). This is an automated estimate, not a certainty. If you wrote this yourself, you can appeal and a human will review it." |
| **high-confidence human** | `ai_score ≤ 0.35` **and** `confidence ≥ 0.65` | ✍️ Likely human-written | "Our checks found no strong signs of AI generation (confidence: {pct}%). This is an automated estimate and not a guarantee of authorship." |
| **uncertain** | anything else | ❓ Attribution uncertain | "Our checks were **inconclusive** for this text (confidence: {pct}%). We can't reliably say whether it was written by a human or AI, so we're not labeling it either way. Short pieces and unusual styles are especially hard to judge." |

Exact strings live in [provenance_guard/labels.py](provenance_guard/labels.py)
and are reproduced verbatim in the README.

### 3.4 Appeals workflow

- **Who** can appeal: the creator of a submission (identified by `creator_id`),
  via the platform. Any classification can be appealed — most usefully a
  `likely_ai` call on genuinely human work.
- **What they provide:** the `content_id` and free-text `creator_reasoning`
  (why they believe the call is wrong).
- **What the system does on receipt:**
  1. Verifies the submission exists (`404` otherwise).
  2. Sets submission `status = "under_review"`.
  3. Writes an audit-log entry `type = "appeal"` containing the reasoning **and a
     snapshot of the original decision** (attribution, confidence, signals) so
     the record is self-contained.
  4. Returns the appeal record.
- **What a human reviewer sees:** `GET /appeals` returns the queue — each item
  pairs the creator's reasoning with the original decision and signal breakdown,
  so a moderator has everything needed to uphold or overturn. Re-classification
  is intentionally **manual**.

### 3.5 Anticipated edge cases (where the system is weak)

1. **Minimalist / repetitive poetry.** A poem built on deliberate repetition and
   plain vocabulary ("so much depends / upon / a red wheel / barrow") is
   statistically *uniform* — low burstiness, low lexical diversity — exactly the
   fingerprint stylometry reads as AI. Mitigation: short + low-reliability text
   drags confidence down, and the asymmetric AI threshold + "short pieces are
   hard to judge" language in the uncertain label keep us from falsely accusing
   the poet.
2. **Very short submissions (a haiku, a tweet-length excerpt).** Under
   ~40 words there aren't enough sentences for stable statistics; the LLM also
   has little to go on. Mitigation: `reliability = words / MIN_RELIABLE_WORDS`
   caps confidence for short texts, pushing them to the **uncertain** label
   rather than a false positive.
3. **Human-edited AI text (and its mirror, AI-polished human text).** A creator
   who lightly rewrites AI output breaks the structural uniformity while keeping
   AI phrasing; the two signals will *disagree*, which lowers the agreement term
   and, correctly, the confidence. This is a case we *should* be unsure about,
   and the scoring reflects that rather than pretending precision.

---

## 4. API surface (the contract)

| Method | Path | Accepts | Returns |
|---|---|---|---|
| `POST` | `/submit` | `{text, creator_id?, content_type?}` | decision + label + signals + `content_id` |
| `POST` | `/appeal` | `{content_id, creator_id?, creator_reasoning}` | appeal record + original decision |
| `GET` | `/submission/<content_id>` | — | current stored state of one submission |
| `GET` | `/appeals` | — | appeal queue for human moderators |
| `GET` | `/log` | `?limit=` | structured audit-log entries |
| `GET` | `/analytics` | — | detection counts, appeal rate, extra metric (stretch) |
| `GET` | `/health` | — | liveness + whether the LLM signal is configured |

---

## 5. Stretch features

- **Ensemble detection (implemented):** Signal 2 is a documented weighted vote of
  four stylometric sub-metrics; combined with the LLM that's a 5-input ensemble
  with explicit weights, all surfaced in the `signals` response and audit log.
- **Analytics dashboard (implemented):** `GET /analytics` reports the
  distribution of attributions, the appeal rate (appeals ÷ decisions), and the
  average confidence as the extra metric.
- Provenance certificate / multi-modal support: designed for but not built in
  this pass (see README "Future work").

---

## 6. AI Tool Plan

For each implementation milestone: the spec sections handed to the AI tool, what
we ask it to generate, and how we verify.

**M3 — submission endpoint + first signal**
- *Provide:* §3.1 Detection signals + §2 diagram (submission flow) + §4 contract.
- *Ask for:* the Flask app skeleton (`app.py`, `create_app`) with `POST /submit`,
  plus the **stylometry** signal function returning `ai_probability` + metrics.
- *Verify:* call `analyze_stylometry()` directly on 3 hand-picked texts (clearly
  human, clearly AI, ambiguous) and confirm the numbers move in the expected
  direction *before* wiring it into the endpoint.

**M4 — second signal + confidence scoring**
- *Provide:* §3.1 + §3.2 Uncertainty representation + §2 diagram.
- *Ask for:* the Groq **LLM signal** function (with graceful failure) and the
  `scoring.py` combine/confidence logic.
- *Verify:* run the full pipeline on the same three texts and confirm confidence
  and label vary meaningfully — a 0.5x result must not read the same as a 0.9x
  result. (Automated in `seed_demo.py`.)

**M5 — production layer**
- *Provide:* §3.3 label variants + §3.4 appeals + §2 diagram (appeal flow).
- *Ask for:* `labels.py` (three-variant selector) and the `POST /appeal`
  endpoint + audit-log wiring + rate limiting.
- *Verify:* exercise all three label variants (via crafted inputs) and confirm an
  appeal flips status to `under_review` and appends a linked audit-log row.

---

## 7. Progress log

- [x] M1 — architecture narrative, signals chosen, endpoints listed, diagram.
- [x] M2 — this planning.md (five questions, architecture, AI tool plan).
- [x] M3 — `/submit` + stylometry signal.
- [x] M4 — LLM signal + confidence scoring.
- [x] M5 — labels, appeals, rate limiting, audit log.
- [x] Stretch — ensemble weighting, analytics endpoint.
