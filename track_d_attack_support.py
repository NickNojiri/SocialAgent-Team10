"""Shared local helpers for the per-class Track D authorization tests."""

import importlib
import os
import sys
from contextlib import contextmanager

# scripts/bench_admin_authz.py stubs these at import time; restore them afterwards so
# later tests in the same pytest run see the real admin module.
_ADMIN_STUBBED = ("_sink_for", "_purge_catalog", "_jobs", "_guild_settings",
                  "_feedback", "_geocode_city", "_time_to_card")
_BENCH = "scripts.bench_admin_authz"
_KEY_ENV = "SPOTBOT_SIGNING_KEY"


@contextmanager
def benchmark_attack_cases():
    """Provide the existing benchmark probes against the stubbed local API."""
    from fastapi.testclient import TestClient

    import src.ingestion.serving.admin as admin
    import src.ingestion.serving.app as serving_app

    saved_admin = {name: getattr(admin, name) for name in _ADMIN_STUBBED}
    saved_service = serving_app.get_service
    saved_key = os.environ.get(_KEY_ENV)
    try:
        # (Re)loading applies the stubs fresh each time, even after a restore.
        bench = (importlib.reload(sys.modules[_BENCH]) if _BENCH in sys.modules
                 else importlib.import_module(_BENCH))
        with TestClient(bench.admin.app) as client:
            forged, _authentic = bench.run(client)
            yield forged
    finally:
        for name, value in saved_admin.items():
            setattr(admin, name, value)
        serving_app.get_service = saved_service
        if saved_key is None:
            os.environ.pop(_KEY_ENV, None)
        else:
            os.environ[_KEY_ENV] = saved_key


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
