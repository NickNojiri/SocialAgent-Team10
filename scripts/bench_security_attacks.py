"""Run Track D's local attacks and print outcomes by Feature #12 class.

    python scripts/bench_security_attacks.py

The tenant-auth benchmark uses local FastAPI test clients and stubs. The focused
pytest run checks protected-route coverage plus the fake caption/SSRF probes.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


class AttackClassResults:
    """Count Track D test probes without changing their assertions."""

    def __init__(self):
        self.classes = {
            "injected caption": {"attacks": 0, "refused": 0, "got_through": 0},
            "internal-address/SSRF": {"attacks": 0, "refused": 0, "got_through": 0},
        }

    def pytest_runtest_logreport(self, report):
        if report.when != "call":
            return

        if "test_attack_injected_caption.py::" in report.nodeid:
            category = "injected caption"
        elif "test_attack_internal_address_ssrf.py::" in report.nodeid:
            category = "internal-address/SSRF"
        else:
            return
        counts = self.classes[category]
        counts["attacks"] += 1
        counts["refused" if report.passed else "got_through"] += 1


def main() -> int:
    benchmark = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "bench_admin_authz.py")],
        cwd=REPO,
        capture_output=True,
        encoding="utf-8",
    )
    print(benchmark.stdout, end="")
    if benchmark.stderr:
        print(benchmark.stderr, file=sys.stderr, end="")

    match = re.search(
        r"=> (\d+) attacks, (\d+) refused, (\d+) got through",
        benchmark.stdout,
    )
    if not match:
        print("Could not read the tenant-auth benchmark summary.", file=sys.stderr)
        return 1
    auth_attacks, auth_refused, auth_got_through = map(int, match.groups())

    results = AttackClassResults()
    pytest_result = pytest.main(
        [
            "-q",
            "--tb=no",
            str(REPO / "test_admin_authz.py"),
            str(REPO / "test_attack_forged_tampered_token.py"),
            str(REPO / "test_attack_server_token_cross_tenant.py"),
            str(REPO / "test_attack_faked_votes.py"),
            str(REPO / "test_attack_injected_caption.py"),
            str(REPO / "test_attack_internal_address_ssrf.py"),
            str(REPO / "test_attack_no_token.py"),
        ],
        plugins=[results],
    )

    print("\nFeature #12 attack-class results from focused security tests:")
    test_attacks = test_refused = test_got_through = 0
    for category, counts in results.classes.items():
        print(
            f"  {category}: {counts['attacks']} attacks, "
            f"{counts['refused']} refused, {counts['got_through']} got through"
        )
        test_attacks += counts["attacks"]
        test_refused += counts["refused"]
        test_got_through += counts["got_through"]

    total_attacks = auth_attacks + test_attacks
    total_refused = auth_refused + test_refused
    total_got_through = auth_got_through + test_got_through
    print(
        f"\n=> {total_attacks} attacks, {total_refused} refused, "
        f"{total_got_through} got through"
    )

    if benchmark.returncode or pytest_result != pytest.ExitCode.OK:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
