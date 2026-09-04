"""Unit tests for SpotBot ML layers (Phase 9).

Tests are fully offline — no Ollama, no ChromaDB, no network.
All external calls are replaced with fakes/mocks.

Run with:
    venv/bin/pytest test_ml_layers.py -v
"""

import json
import math
import unittest
from unittest.mock import MagicMock, patch

from src.ingestion.serving.recommender import (
    blend_query_with_taste,
    get_user_liked_embeddings,
    rerank_with_llm,
)
from src.ingestion.config import IngestionSettings


# ── helpers ─────────────────────────────────────────────────────────────────

def _unit_vec(dim: int, idx: int = 0) -> list[float]:
    """Return a unit vector with 1.0 at position idx, 0 elsewhere."""
    v = [0.0] * dim
    v[idx] = 1.0
    return v


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _fake_settings(**overrides):
    s = IngestionSettings()
    for k, v in overrides.items():
        object.__setattr__(s, k, v)
    return s


# ── ML Layer 1: blend_query_with_taste ───────────────────────────────────────

class TestBlendQueryWithTaste(unittest.TestCase):

    def test_cold_start_no_liked_embeddings(self):
        """With no liked embeddings the query is returned unchanged."""
        q = [1.0, 0.0, 0.0]
        result = blend_query_with_taste(q, [], alpha=0.25)
        self.assertEqual(result, q)

    def test_alpha_zero_returns_query(self):
        """alpha=0.0 means 100% query, taste has no effect."""
        q = [1.0, 0.0]
        liked = [[0.0, 1.0]]  # orthogonal taste
        result = blend_query_with_taste(q, liked, alpha=0.0)
        # Should be normalised version of q (already unit)
        self.assertAlmostEqual(result[0], 1.0, places=5)
        self.assertAlmostEqual(result[1], 0.0, places=5)

    def test_alpha_one_returns_taste(self):
        """alpha=1.0 means 100% taste profile."""
        q = [1.0, 0.0]
        liked = [[0.0, 1.0], [0.0, 1.0]]  # strong taste in dim 1
        result = blend_query_with_taste(q, liked, alpha=1.0)
        self.assertAlmostEqual(result[0], 0.0, places=5)
        self.assertAlmostEqual(result[1], 1.0, places=5)

    def test_blended_is_normalised(self):
        """Output vector should always have unit length (L2 norm ≈ 1.0)."""
        q = [0.6, 0.8]
        liked = [[1.0, 0.0], [0.5, 0.5]]
        result = blend_query_with_taste(q, liked, alpha=0.25)
        self.assertAlmostEqual(_norm(result), 1.0, places=5)

    def test_multiple_liked_averaged(self):
        """Taste profile is the mean of liked embeddings before blending."""
        q = [0.0, 0.0, 1.0]
        # Two liked vecs: average is [1, 0, 0]
        liked = [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
        result = blend_query_with_taste(q, liked, alpha=0.5)
        # Blend: 0.5*[0,0,1] + 0.5*[1,0,0] = [0.5, 0, 0.5]
        # Normalised: [1/√2, 0, 1/√2]
        expected = 1.0 / math.sqrt(2)
        self.assertAlmostEqual(result[0], expected, places=5)
        self.assertAlmostEqual(result[2], expected, places=5)

    def test_default_alpha_is_reasonable(self):
        """Default alpha=0.25 biases result slightly toward taste."""
        q = _unit_vec(4, 0)      # [1, 0, 0, 0]
        liked = [_unit_vec(4, 3)]  # [0, 0, 0, 1]
        result = blend_query_with_taste(q, liked)   # alpha=0.25
        # Query component (dim 0) should be larger than taste component (dim 3)
        self.assertGreater(result[0], result[3])
        # But taste component should be non-zero
        self.assertGreater(result[3], 0.0)


# ── ML Layer 1: get_user_liked_embeddings ────────────────────────────────────

class TestGetUserLikedEmbeddings(unittest.TestCase):

    def _make_sink(self, ids, metadatas, embeddings):
        """Build a fake sink whose collection.get() returns fixed data."""
        col = MagicMock()
        col.get.return_value = {
            "ids": ids,
            "metadatas": metadatas,
            "embeddings": embeddings,
        }
        sink = MagicMock()
        sink.collection = col
        return sink

    def test_empty_user_id_returns_nothing(self):
        sink = self._make_sink(["a"], [{"voters": '{"u1": "Alice"}'}], [[0.1, 0.2]])
        result = get_user_liked_embeddings(sink, "")
        self.assertEqual(result, [])

    def test_user_with_no_votes_returns_nothing(self):
        sink = self._make_sink(
            ["a", "b"],
            [{"voters": '{"u2": "Bob"}'}, {"voters": "{}"}],
            [[0.1], [0.2]],
        )
        result = get_user_liked_embeddings(sink, "u1")
        self.assertEqual(result, [])

    def test_returns_embeddings_for_liked_spots(self):
        # The function calls col.get() twice:
        #   1st: include metadatas+embeddings+ids → scan for liked_ids
        #   2nd: col.get(ids=liked_ids, include=["embeddings"]) → fetch vectors
        col = MagicMock()
        col.get.side_effect = [
            # First call: full scan
            {
                "ids": ["a", "b", "c"],
                "metadatas": [
                    {"voters": '{"u1": "Nick", "u2": "Bob"}'},
                    {"voters": '{"u2": "Bob"}'},
                    {"voters": '{"u1": "Nick"}'},
                ],
                "embeddings": [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]],
            },
            # Second call: fetch embeddings for liked_ids ["a", "c"]
            {"embeddings": [[1.0, 0.0], [0.5, 0.5]]},
        ]
        sink = MagicMock()
        sink.collection = col
        # u1 liked spots a and c
        result = get_user_liked_embeddings(sink, "u1")
        self.assertEqual(len(result), 2)

    def test_no_collection_returns_nothing(self):
        sink = MagicMock()
        del sink.collection   # attribute doesn't exist
        sink.collection = None
        result = get_user_liked_embeddings(sink, "u1")
        self.assertEqual(result, [])

    def test_malformed_voters_json_is_skipped(self):
        """Spots with broken voters JSON don't crash — they're treated as no votes."""
        col = MagicMock()
        col.get.return_value = {
            "ids": ["a"],
            "metadatas": [{"voters": "not valid json{{"}],
            "embeddings": [[0.1]],
        }
        sink = MagicMock()
        sink.collection = col
        result = get_user_liked_embeddings(sink, "u1")
        self.assertEqual(result, [])


# ── ML Layer 2: rerank_with_llm ──────────────────────────────────────────────

class TestRerankWithLlm(unittest.TestCase):

    def _candidates(self, n=4):
        return [
            {
                "metadata": {
                    "venue_name": f"Venue {i}",
                    "core_theme": f"theme {i}",
                    "category": "bar",
                },
                "distance": 0.1 * i,
            }
            for i in range(n)
        ]

    def _settings_with_response(self, ranked_list):
        settings = _fake_settings(
            ollama_url="http://localhost:11434",
            ollama_model="llama3.2",
            llm_timeout_s=30,
        )
        return settings, json.dumps(ranked_list)

    def test_single_candidate_returned_unchanged(self):
        candidates = self._candidates(1)
        settings = _fake_settings(
            ollama_url="http://x", ollama_model="m", llm_timeout_s=5
        )
        result = rerank_with_llm("tacos", candidates, settings)
        self.assertEqual(result, candidates)

    @patch("src.ingestion.serving.recommender.httpx.post")
    def test_reorders_by_llm_ranking(self, mock_post):
        """LLM returns [2, 0, 3, 1] → candidates reordered accordingly."""
        candidates = self._candidates(4)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": json.dumps([2, 0, 3, 1])}
        mock_post.return_value = mock_resp

        settings = _fake_settings(
            ollama_url="http://x", ollama_model="m", llm_timeout_s=5
        )
        result = rerank_with_llm("tacos", candidates, settings)

        self.assertEqual(result[0]["metadata"]["venue_name"], "Venue 2")
        self.assertEqual(result[1]["metadata"]["venue_name"], "Venue 0")
        self.assertEqual(result[2]["metadata"]["venue_name"], "Venue 3")
        self.assertEqual(result[3]["metadata"]["venue_name"], "Venue 1")

    @patch("src.ingestion.serving.recommender.httpx.post")
    def test_dict_response_unwrapped(self, mock_post):
        """LLM wraps in {"ranked": [...]} — should still work."""
        candidates = self._candidates(3)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": json.dumps({"ranked": [1, 2, 0]})}
        mock_post.return_value = mock_resp

        settings = _fake_settings(
            ollama_url="http://x", ollama_model="m", llm_timeout_s=5
        )
        result = rerank_with_llm("tacos", candidates, settings)
        self.assertEqual(result[0]["metadata"]["venue_name"], "Venue 1")
        self.assertEqual(result[1]["metadata"]["venue_name"], "Venue 2")
        self.assertEqual(result[2]["metadata"]["venue_name"], "Venue 0")

    @patch("src.ingestion.serving.recommender.httpx.post")
    def test_fallback_on_llm_failure(self, mock_post):
        """Any exception from httpx → original order returned."""
        mock_post.side_effect = Exception("Ollama down")
        candidates = self._candidates(3)
        settings = _fake_settings(
            ollama_url="http://x", ollama_model="m", llm_timeout_s=5
        )
        result = rerank_with_llm("tacos", candidates, settings)
        self.assertEqual(result, candidates)

    @patch("src.ingestion.serving.recommender.httpx.post")
    def test_fallback_on_bad_json(self, mock_post):
        """Unparseable LLM response → original order returned."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "not json at all"}
        mock_post.return_value = mock_resp

        settings = _fake_settings(
            ollama_url="http://x", ollama_model="m", llm_timeout_s=5
        )
        candidates = self._candidates(3)
        result = rerank_with_llm("tacos", candidates, settings)
        self.assertEqual(result, candidates)

    @patch("src.ingestion.serving.recommender.httpx.post")
    def test_unlisted_candidates_appended(self, mock_post):
        """Candidates not in the LLM's list are appended at the end."""
        candidates = self._candidates(4)
        mock_resp = MagicMock()
        # LLM only mentions indices 0 and 2
        mock_resp.json.return_value = {"response": json.dumps([0, 2])}
        mock_post.return_value = mock_resp

        settings = _fake_settings(
            ollama_url="http://x", ollama_model="m", llm_timeout_s=5
        )
        result = rerank_with_llm("tacos", candidates, settings)
        # First two are LLM picks, then unlisted in original order
        self.assertEqual(result[0]["metadata"]["venue_name"], "Venue 0")
        self.assertEqual(result[1]["metadata"]["venue_name"], "Venue 2")
        # Remaining (1, 3) appended
        self.assertIn(result[2]["metadata"]["venue_name"], ["Venue 1", "Venue 3"])
        self.assertIn(result[3]["metadata"]["venue_name"], ["Venue 1", "Venue 3"])


if __name__ == "__main__":
    unittest.main()
