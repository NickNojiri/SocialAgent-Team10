"""Use a valid server-X token while requesting server-Y data."""

from track_d_attack_support import assert_attack_class_refused, benchmark_attack_cases


def test_server_x_token_is_refused_for_server_y():
    with benchmark_attack_cases() as cases:
        assert_attack_class_refused(cases, "server-X-token-on-server-Y")
