"""Shared local helpers for the per-class Track D authorization tests."""

from contextlib import contextmanager


@contextmanager
def benchmark_attack_cases():
    """Provide the existing benchmark probes against the stubbed local API."""
    from fastapi.testclient import TestClient

    from scripts.bench_admin_authz import admin, run

    with TestClient(admin.app) as client:
        forged, _authentic = run(client)
        yield forged


def assert_attack_class_refused(cases, expected_class):
    """Run every existing probe in one class and require the auth gate to refuse it."""
    from scripts.bench_admin_authz import attack_class

    selected = [case for case in cases if attack_class(case[2]) == expected_class]
    assert selected, f"no benchmark attack cases found for {expected_class}"

    for _method, _path, label, attack in selected:
        response = attack()
        assert response.status_code in (401, 403), (
            f"{expected_class} attack was accepted ({response.status_code}): {label}"
        )
