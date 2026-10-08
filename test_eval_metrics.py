import pytest

from src.ingestion.eval import (
    Tally,
    cohen_kappa,
    format_rate,
    kappa_for_rows,
    mcnemar_exact,
    paired_delta_interval,
    print_paired_comparison,
    print_kappa_report,
    wilson_interval,
)


def test_wilson_interval_and_rate_format_cover_boundaries():
    assert wilson_interval(0, 0) is None
    lower, upper = wilson_interval(0, 10)
    assert 0 <= lower < upper < 0.5
    lower, upper = wilson_interval(10, 10)
    assert 0.5 < lower < upper == 1
    assert "95% CI" in format_rate(3, 5)
    assert format_rate(0, 0) == "n/a"


def test_mcnemar_uses_paired_discordant_outcomes():
    assert mcnemar_exact([True, True], [False, False]) == (2, 0, 0.5)
    assert mcnemar_exact([True, False], [False, True]) == (1, 1, 1.0)
    assert mcnemar_exact([True, False], [True, False]) == (0, 0, 1.0)
    with pytest.raises(ValueError, match="paired"):
        mcnemar_exact([True], [True, False])


def test_stored_comparison_pairs_the_same_eligible_rows(capsys):
    rows = [
        {"url": "https://instagram.com/reel/one/", "gold": {"venue": "A", "category": "food"},
         "verdict": {"venue": "right", "category": "right"},
         "predicted": {"venue": "A", "category": "food"}},
        {"url": "https://instagram.com/reel/two/", "gold": {"venue": "B", "category": "cafe"},
         "verdict": {"venue": "right", "category": "right"},
         "predicted": {"venue": "Other", "category": "cafe"}},
    ]
    current = Tally(rows=[
        {"code": "one", "venue": False, "cat": False},
        {"code": "two", "venue": True, "cat": False},
    ])
    print_paired_comparison(rows, current)
    report = capsys.readouterr().out
    assert "venue exact" in report and "n=2" in report
    assert "category" in report and "McNemar" in report


def test_paired_delta_interval_is_deterministic_and_contains_observed_delta():
    before = [False, True, False, True]
    after = [True, True, False, True]
    interval = paired_delta_interval(before, after)
    assert interval is not None
    assert interval[0] <= 0.25 <= interval[1]
    assert interval == paired_delta_interval(before, after)


def test_cohen_kappa_and_invalid_inputs():
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "b", "b"]) == 0.5
    assert cohen_kappa(["same"], ["same"]) is None
    assert cohen_kappa([], []) is None
    with pytest.raises(ValueError, match="same length"):
        cohen_kappa(["a"], ["a", "b"])


def test_kappa_rows_require_distinct_known_labelers_and_two_gold_values():
    rows = [
        {"labeler": "reviewer-a", "gold": {"category": "a"},
         "second_label": {"labeler": "reviewer-b", "independent": True,
                          "gold": {"category": "a"}}},
        {"labeler": "reviewer-a", "gold": {"category": "b"},
         "second_label": {"labeler": "reviewer-b", "independent": True,
                          "gold": {"category": "b"}}},
        {"labeler": "reviewer-a", "gold": {"category": "a"},
         "second_label": {"labeler": "reviewer-b", "independent": True,
                          "gold": {"category": "b"}}},
        {"labeler": "reviewer-a", "gold": {"category": "b"},
         "second_label": {"labeler": "reviewer-b", "independent": True,
                          "gold": {"category": "b"}}},
        {"labeler": "unknown", "gold": {"category": "a"},
         "second_label": {"labeler": "reviewer-b", "independent": True,
                          "gold": {"category": "a"}}},
        {"labeler": "reviewer-a", "gold": {"category": "a"},
         "second_label": {"labeler": "reviewer-a", "independent": True,
                          "gold": {"category": "a"}}},
        {"labeler": "reviewer-a", "gold": {"category": "a"},
         "second_label": {"labeler": "reviewer-b", "independent": False,
                          "gold": {"category": "b"}}},
    ]
    count, value = kappa_for_rows(rows, "category")
    assert count == 4
    assert value == 0.5
    assert kappa_for_rows(rows[:2], "venue") == (0, None)


def test_kappa_report_explicitly_says_unavailable_without_second_labels(capsys):
    print_kappa_report([{"labeler": "unknown", "gold": {"category": "food"}}])
    report = capsys.readouterr().out
    assert report.count("unavailable (no independent paired labels)") == 4
    assert "kappa=" not in report