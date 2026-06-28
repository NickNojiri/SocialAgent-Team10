"""Verify the group-planning brain: a multi-person chat transcript -> synthesized
request + a shortlist, against the live Chroma store + Ollama.

    python scripts/test_plan.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ingestion.config import IngestionSettings
from src.ingestion.sinks.chroma_sink import ChromaSink
from src.ingestion.serving.recommender import RecommendationService

TRANSCRIPTS = {
    "boba in Irvine, cheap, tonight": (
        "ava: where should we eat tonight?\n"
        "bo: boba tbh, been craving it\n"
        "cy: somewhere in irvine, nothing too pricey"
    ),
    "late-night tacos this weekend": (
        "ava: anyone down to hang this weekend?\n"
        "bo: im starving lol, tacos?\n"
        "cy: yeah a late night taco run sounds good"
    ),
}


def main():
    settings = IngestionSettings(chroma_path="data")
    svc = RecommendationService(ChromaSink(settings), settings)
    for label, transcript in TRANSCRIPTS.items():
        print("=" * 72)
        print("CHAT:", label)
        result = svc.plan(f"chan-{label}", transcript)
        print("WHAT I HEARD (request):", result.request)
        print("QUERY:", result.query)
        if not result.recommendations:
            print("SHORTLIST: (no relevant match)")
        for r in result.recommendations:
            print(f"  - {r.venue_name}  [{r.category}]  dist={r.distance:.3f}  :: {r.core_theme[:60]}")


if __name__ == "__main__":
    main()
