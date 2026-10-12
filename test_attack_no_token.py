"""Requests that omit the tenant token from the local auth benchmark."""

from track_d_attack_support import assert_attack_class_refused, benchmark_attack_cases


def test_requests_without_a_tenant_token_are_refused():
    with benchmark_attack_cases() as cases:
        assert_attack_class_refused(cases, "no-token attacks")
