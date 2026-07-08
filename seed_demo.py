"""Seed the audit log with sample decisions + an appeal, and verify that
confidence and labels vary meaningfully across clearly-different inputs.

Run:  python seed_demo.py
This calls the pipeline directly (no HTTP), so it works offline and is not
subject to rate limiting. It (re)creates provenance_guard.db so `GET /log`
shows real entries.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from provenance_guard import store
from provenance_guard.pipeline import classify

load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

SAMPLES = [
    {
        "creator_id": "creator_ai_demo",
        "label": "Clearly AI-flavored (uniform, generic, no voice)",
        "text": (
            "Artificial intelligence is transforming the modern world in numerous "
            "significant ways. It offers a wide range of benefits across many "
            "different industries. Businesses are able to improve their efficiency "
            "and productivity. Customers are able to receive faster and more helpful "
            "responses. In conclusion, artificial intelligence represents an important "
            "and valuable technology for the future of society and the global economy."
        ),
    },
    {
        "creator_id": "creator_human_demo",
        "label": "Clearly human (bursty, idiosyncratic, personal)",
        "text": (
            "I didn't sleep. The radiator clanked all night like somebody trapped "
            "behind the wall, and by four I'd given up. So I made tea — the good "
            "stuff my sister mailed me, wrapped in a receipt she'd scribbled on — and "
            "sat by the window. Snow. Actual snow, in April! I laughed out loud, alone, "
            "at nothing. Sometimes the city surprises you like that, right when you've "
            "decided it never will. I don't know. Maybe I was just tired."
        ),
    },
    {
        "creator_id": "creator_mixed_demo",
        "label": "Ambiguous (edited, mid-length, mixed signals)",
        "text": (
            "The project began with a simple idea, but it grew complicated fast. "
            "We wanted to help people, and honestly we underestimated the work. There "
            "were setbacks. Some weeks nothing moved at all. Still, the team kept going, "
            "iterating on the design and gathering feedback from early users along the way."
        ),
    },
    {
        "creator_id": "creator_poet_demo",
        "label": "Short repetitive poem (edge case -> should be uncertain)",
        "text": "so much depends\nupon\na red wheel\nbarrow\nglazed with rain\nwater",
    },
]


def main():
    # Fresh DB for a clean, reproducible demo.
    if os.path.exists(store.config.DB_PATH):
        os.remove(store.config.DB_PATH)
    store.init_db()

    print("=" * 78)
    print("CONFIDENCE / LABEL VARIATION CHECK")
    print("=" * 78)
    content_ids = {}
    for s in SAMPLES:
        decision = classify(s["text"])
        saved = store.record_submission(decision, s["text"], creator_id=s["creator_id"])
        content_ids[s["creator_id"]] = saved["content_id"]
        print(f"\n▶ {s['label']}")
        print(
            f"    attribution = {decision['attribution']:<14} "
            f"ai_score = {decision['ai_score']:.2f}   "
            f"confidence = {decision['confidence']:.2f}"
        )
        print(f"    label       = {decision['label']['variant']}  ->  {decision['label']['title']}")
        stylo = next(x for x in decision["signals"] if x["name"] == "stylometry")
        llm = next(x for x in decision["signals"] if x["name"] == "llm")
        print(f"    stylometry  = {stylo['ai_probability']:.2f} (reliability {stylo['reliability']:.2f})")
        print(f"    llm         = {'p=%.2f' % llm['ai_probability'] if llm['available'] else 'unavailable'}")

    # Demonstrate the appeal flow on the AI-flavored sample.
    print("\n" + "=" * 78)
    print("APPEAL FLOW")
    print("=" * 78)
    target = content_ids["creator_ai_demo"]
    appeal = store.record_appeal(
        target,
        creator_reasoning="I wrote this myself for a class assignment; the flat tone is just my formal style.",
        creator_id="creator_ai_demo",
    )
    print(f"Appealed {target} -> status now '{appeal['status']}'")

    # Summary that the label ranges actually differ.
    print("\n" + "=" * 78)
    print("SANITY: distinct confidence values were produced (not a binary flip)")
    print("=" * 78)
    log = store.get_audit_log()
    confs = sorted({round(e["confidence"], 2) for e in log if e["event_type"] == "decision"})
    variants = sorted({e["label_variant"] for e in log if e["event_type"] == "decision"})
    print(f"distinct confidence values: {confs}")
    print(f"label variants produced:    {variants}")
    print(f"\nAudit log now has {len(log)} entries (see GET /log).")


if __name__ == "__main__":
    main()
