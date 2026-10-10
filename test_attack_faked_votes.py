"""Vote identity spoofing attacks from the local auth benchmark."""

from track_d_attack_support import assert_attack_class_refused, benchmark_attack_cases


def test_faked_votes_for_another_user_are_refused():
    with benchmark_attack_cases() as cases:
        assert_attack_class_refused(cases, "faked votes")
