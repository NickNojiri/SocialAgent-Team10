"""The rest of queue5, finished by a cutoff so SpotBot can be hosted on this PC afterwards.

Waits for the tuning run queue5 left going (its PID is the first argument), then runs the
remaining jobs one at a time until the cutoff (second argument, HH:MM, default 17:00). A job
starts only if its usual run time fits before the cutoff; one still running at the cutoff is
stopped. The fixed comparison goes first: it is the result we still need.
"""
from datetime import datetime, timedelta
import os
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import job_queue as Q  # noqa: E402

# Copied from queue5: importing it would start its job list (it has no __main__ guard).
V5 = ["--clean", "--parser-pick", "--recipe-cue", "--dev-plus"]


def free_gb():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) / 1024 / 1024


# (name, args, usual minutes) — minutes from today's queue.log
JOBS = [
    ("compare_fixed", [Q.S + "compare_models.py", "--stage", "all"], 25),
    ("M2_v5_tune_lr3e5", [Q.S + "ml_reranker.py", *V5, "--lr", "3e-5", "--tag", "v5_tune_lr3e5"], 42),
    ("M2_v5_tune_lr1e5_e5", [Q.S + "ml_reranker.py", *V5, "--lr", "1e-5", "--epochs", "5", "--tag", "v5_tune_lr1e5_e5"], 65),
    ("summary_final", [Q.S + "overnight_summary.py"], 0),
]
h, m = map(int, (sys.argv[2] if len(sys.argv) > 2 else "17:00").split(":"))
CUTOFF = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


def run_until_cutoff(name, args):
    Q.log(f"START {name}: {' '.join(args)}")
    t0 = time.time()
    with (Q.QDIR / f"{name}.log").open("w", encoding="utf-8") as out:
        p = subprocess.Popen([Q.PY, *args], cwd=Q.REPO, stdout=out, stderr=subprocess.STDOUT)
        while p.poll() is None and datetime.now() < CUTOFF:
            time.sleep(15)
        if p.poll() is None:
            subprocess.call(["taskkill", "/PID", str(p.pid), "/T", "/F"], stdout=subprocess.DEVNULL)
            p.wait()
            Q.log(f"STOPPED {name} at the {CUTOFF:%H:%M} cutoff")
    tail = [l for l in (Q.QDIR / f"{name}.log").read_text(encoding="utf-8", errors="ignore").split("\n") if l.strip()]
    Q.log(f"END {name}: exit {p.returncode} after {(time.time() - t0) / 60:.0f} min | " + " / ".join(t.strip() for t in tail[-4:]))


pid = int(sys.argv[1])
while subprocess.call(["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid}"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
    time.sleep(60)
tail = [l for l in (Q.QDIR / "M2_v5_tune_e5.log").read_text(encoding="utf-8", errors="ignore").split("\n") if l.strip()]
Q.log("END M2_v5_tune_e5 (queue5 stopped, run finished on its own) | " + " / ".join(t.strip() for t in tail[-3:]))
Q.log("queue6 start")
for name, args, minutes in JOBS:
    if datetime.now() + timedelta(minutes=minutes) > CUTOFF and minutes:
        Q.log(f"SKIP {name}: about {minutes} min would run past the {CUTOFF:%H:%M} cutoff")
        continue
    for _ in range(30):
        if free_gb() >= 8:
            break
        time.sleep(60)
    else:
        Q.log(f"SKIP {name}: under 8 GB free memory for 30 min")
        continue
    run_until_cutoff(name, args)
    if minutes:
        time.sleep(120)
Q.log("queue6 done")
