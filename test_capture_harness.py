"""Offline tests for scripts/capture_harness.py (never contact Instagram)."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.capture_harness import (
    FAILURE_CLASSES,
    CaptureHarnessPipeline,
    HarnessRow,
    build_artifact,
    build_harness_pipeline,
    failure_class,
    harness_settings,
    load_eligible_urls,
    render_summary,
    result_outcome,
    run_capture,
    select_urls,
    summarize,
    validate_report_path,
    write_artifact,
)
from src.ingestion.schemas.results import FetchStatus, IngestionResult
from src.ingestion.schemas.snapshot import PageSnapshot

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def _failed(
    status: FetchStatus,
    reason: str | None = None,
) -> IngestionResult:
    return IngestionResult(
        url="https://www.instagram.com/reel/FAIL/",
        fetch_status=status,
        rejection_reason=reason,
    )


def _successful(url: str = "https://www.instagram.com/reel/OK/") -> IngestionResult:
    # The classifier only needs to know that a validated record is present.
    return IngestionResult.model_construct(
        url=url, fetch_status=FetchStatus.OK, record=object()
    )


def test_corpus_reader_uses_url_only_and_preserves_u2028(tmp_path):
    source = tmp_path / "labels.jsonl"
    rows = [
        {
            "url": "https://instagram.com/reel/ABC/?igsh=tracking",
            "input": {"caption": "first line\u2028still the same JSON row"},
            "gold": {"in_catalog": False},
        },
        {"url": "https://www.instagram.com/reels/XYZ/", "gold": {"venue": "ignored"}},
        {"url": "https://www.instagram.com/reel/ABC/", "needs_recapture": True},
        {"url": "https://www.instagram.com/p/POST/"},
        {"url": "https://example.com/reel/NOPE/"},
    ]
    source.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_eligible_urls(source) == [
        "https://www.instagram.com/reel/ABC/",
        "https://www.instagram.com/reel/XYZ/",
    ]


def test_corpus_reader_reports_the_bad_line(tmp_path):
    source = tmp_path / "labels.jsonl"
    source.write_text('{"url":"https://instagram.com/reel/A/"}\n{bad}\n',
                      encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match=r"labels\.jsonl:2: invalid JSON"):
        load_eligible_urls(source)


def test_seeded_selection_is_repeatable_and_unique():
    urls = [f"https://www.instagram.com/reel/{n}/" for n in range(20)]

    first = select_urls(urls, sample_size=10, seed=20261017)
    second = select_urls(list(reversed(urls)), sample_size=10, seed=20261017)

    assert first == second
    assert len(first) == len(set(first)) == 10


def test_selection_refuses_a_sample_larger_than_the_pool():
    with pytest.raises(ValueError, match="only 1 eligible reels"):
        select_urls(["https://www.instagram.com/reel/A/"], sample_size=2, seed=1)


@pytest.mark.parametrize(
    "result, video_available, expected",
    [
        (_failed(FetchStatus.LOGIN_WALL), None, "login_wall"),
        (_failed(FetchStatus.CONSENT_WALL), None, "login_wall"),
        (_failed(FetchStatus.TIMEOUT), None, "timeout"),
        (_failed(FetchStatus.ERROR, "capture exceeded the 180s budget"), None, "timeout"),
        (
            _failed(FetchStatus.OK, "post is not clearly about a place, event, or food"),
            False,
            "not_a_place",
        ),
        (
            _failed(FetchStatus.OK, "no human-readable text was extracted from the page"),
            False,
            "no_video",
        ),
        (
            _failed(FetchStatus.OK, "no human-readable text was extracted from the page"),
            True,
            "other",
        ),
        (_failed(FetchStatus.OK, "venue_name: Field required"), True, "no_venue"),
        (_failed(FetchStatus.OK, "category: Field required"), True, "other"),
        (_failed(FetchStatus.NOT_FOUND), None, "other"),
        (_failed(FetchStatus.ERROR, "unexpected error: RuntimeError"), None, "other"),
    ],
)
def test_failure_classifier_uses_real_result_fields(result, video_available, expected):
    assert failure_class(result, video_available=video_available) == expected


def test_success_has_no_failure_class():
    result = _successful()
    assert failure_class(result, video_available=False) is None
    assert result_outcome(result) == "success"


def test_outcomes_distinguish_fetch_content_and_processing_failures():
    assert result_outcome(_failed(FetchStatus.LOGIN_WALL)) == "fetch_failed"
    assert result_outcome(_failed(FetchStatus.OK, "venue_name: Field required")) == (
        "content_rejected"
    )
    assert result_outcome(_failed(FetchStatus.ERROR, "unexpected error: KeyError")) == (
        "processing_failed"
    )


def test_pipeline_builder_disables_every_optional_service_and_sink(tmp_path):
    settings = harness_settings().model_copy(update={"raw_dir": tmp_path / "raw"})
    pipeline = build_harness_pipeline(
        settings, on_result=lambda _result: None, on_stage=lambda _url, _stage: None
    )

    assert isinstance(pipeline, CaptureHarnessPipeline)
    assert pipeline.extractor is None
    assert pipeline.transcriber is None
    assert pipeline.summarizer is None
    assert pipeline.geo_enricher is None
    assert pipeline.temporal_resolver is None
    assert pipeline.jsonl_sink is None
    assert pipeline.chroma_sink is None
    assert pipeline.authed_source is None
    assert pipeline.ocr_reader is None
    assert settings.llm_enabled is False
    assert settings.transcribe_enabled is False
    assert settings.ocr_enabled is False
    assert settings.geocode_enabled is False
    assert settings.temporal_enabled is False
    assert settings.chroma_enabled is False
    assert settings.ig_username == ""
    assert settings.ig_password == ""

    snapshot = PageSnapshot(
        url="https://www.instagram.com/reel/A/",
        status=FetchStatus.LOGIN_WALL,
        fetched_at=NOW,
        html="<html>sensitive page</html>",
    )
    assert pipeline._save_raw(snapshot) is None
    assert not (tmp_path / "raw").exists()


def test_batch_keeps_one_row_per_url_when_the_middle_url_fails():
    urls = [
        "https://www.instagram.com/reel/A/",
        "https://www.instagram.com/reel/B/",
        "https://www.instagram.com/reel/C/",
    ]
    results = [
        _successful(urls[0]),
        IngestionResult(url=urls[1], fetch_status=FetchStatus.TIMEOUT),
        IngestionResult(
            url=urls[2],
            fetch_status=FetchStatus.OK,
            rejection_reason="venue_name: Field required",
        ),
    ]

    class FakePipeline:
        def __init__(self, on_result, on_stage):
            self.on_result = on_result
            self.on_stage = on_stage
            self.video_available = {urls[0]: True, urls[2]: True}

        async def run(self, received):
            assert received == urls
            for url, result in zip(received, results):
                self.on_stage(url, "fetching")
                self.on_result(result)
                self.on_stage(url, "done")
            return SimpleNamespace(results=results)

    def factory(_settings, *, on_result, on_stage):
        return FakePipeline(on_result, on_stage)

    times = iter([0.0, 1.0, 2.0, 4.5, 5.0, 8.0])
    rows = asyncio.run(
        run_capture(urls, pipeline_factory=factory, clock=lambda: next(times))
    )

    assert [row.url for row in rows] == urls
    assert [row.outcome for row in rows] == [
        "success",
        "fetch_failed",
        "content_rejected",
    ]
    assert [row.elapsed_seconds for row in rows] == [1.0, 2.5, 3.0]
    assert [row.failure_class for row in rows] == [None, "timeout", "no_venue"]


def test_summary_has_all_failure_classes_even_when_zero():
    rows = [
        HarnessRow("u1", "success", 1.0, None, "ok", True),
        HarnessRow("u2", "fetch_failed", 2.0, "timeout", "timeout", None),
    ]

    summary = summarize(rows)

    assert summary["total_tested"] == 2
    assert summary["successful"] == 1
    assert summary["pass_rate"] == 0.5
    assert list(summary["failure_breakdown"]) == list(FAILURE_CLASSES)
    assert summary["failure_breakdown"]["timeout"] == 1
    assert "Pass rate    : 50.0%" in render_summary(summary)


def test_artifact_is_utf8_json_with_selection_and_final_newline(tmp_path):
    source = tmp_path / "labels.jsonl"
    source.write_text('{"url":"https://instagram.com/reel/A/"}\n',
                      encoding="utf-8", newline="\n")
    selected = ["https://www.instagram.com/reel/A/"]
    rows = [HarnessRow(selected[0], "success", 1.25, None, "ok", True)]
    artifact = build_artifact(
        source=source,
        seed=7,
        eligible_count=1,
        selected_urls=selected,
        rows=rows,
    )
    output = tmp_path / "report.json"

    write_artifact(output, artifact)
    text = output.read_text(encoding="utf-8")
    parsed = json.loads(text)

    assert text.endswith("\n")
    assert parsed["selection"]["seed"] == 7
    assert parsed["selection"]["selected_urls"] == selected
    assert parsed["measurement"]["jsonl_sink"] is False
    assert parsed["measurement"]["chroma_sink"] is False
    assert parsed["results"][0]["elapsed_seconds"] == 1.25


def test_cli_report_path_is_restricted_to_docs_results():
    allowed = validate_report_path(Path("docs/results/capture-harness.json"))
    assert allowed.parent == Path("docs/results").resolve()

    with pytest.raises(ValueError, match="inside docs/results"):
        validate_report_path(Path("data/capture-harness.json"))
    with pytest.raises(ValueError, match="inside docs/results"):
        validate_report_path(Path("capture-harness.json"))
