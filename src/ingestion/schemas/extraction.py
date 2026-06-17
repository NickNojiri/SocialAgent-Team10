"""The narrow contract the local LLM is allowed to fill.

Deliberately NOT EventInspiration: the model never sees or invents record_id,
provenance, content_hash, or coordinates — those stay code-owned. Keeping this
schema flat and $ref-free (Literal instead of Enum) means model_json_schema()
inlines the category list, which is the most reliable shape for Ollama's
grammar-constrained `format` decoding.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict

# Mirrors EventCategory's values exactly; a test asserts they never drift apart.
CategoryLiteral = (
    "food_drink",
    "cafe_dessert",
    "nightlife",
    "live_music",
    "market_popup",
    "outdoors",
    "community",
    "other",
)


class LlmExtraction(BaseModel):
    """Fields the model judges from the post text. Everything optional/typed so
    a constrained-decode response always validates; cross-checks happen later."""

    model_config = ConfigDict(extra="forbid")

    venue_name: Optional[str] = None        # verbatim from input text, else null
    core_theme: Optional[str] = None        # one short phrase describing the post
    category: str = "other"                  # one of CategoryLiteral
    raw_location_text: Optional[str] = None  # location as written in the post, else null
    place_names: list[str] = []              # neighborhoods/cities/venues mentioned
    candidate_times: list[str] = []          # raw time phrases verbatim, no date math
    is_vague: bool = False                    # true when not clearly a place/event post

    @staticmethod
    def ollama_format_schema() -> dict:
        """JSON schema for Ollama's `format` param, with category constrained to
        the enum literals (model_json_schema gives `str`; we tighten it)."""
        schema = LlmExtraction.model_json_schema()
        schema.get("properties", {})["category"] = {"enum": list(CategoryLiteral)}
        return schema
