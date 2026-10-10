"""Forged and tampered tenant-token attacks from the local auth benchmark."""

from track_d_attack_support import assert_attack_class_refused, benchmark_attack_cases


def test_forged_or_tampered_tenant_tokens_are_refused():
    with benchmark_attack_cases() as cases:
        assert_attack_class_refused(cases, "forged/tampered tenant token")
