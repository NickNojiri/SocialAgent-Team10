"""After queue3 (answer-key check) finishes: big vs small vs medium comparison.

One job at a time, never two (the PC shut off under heavy load on 2026-10-23), with a free-memory
check and a two-minute cool-down between jobs. The CPU trains; the GPU only runs Ollama, and
never while a training job is running.
"""
import datetime as dt
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import job_queue as Q  # noqa: E402

V5 = ["--clean", "--parser-pick", "--recipe-cue", "--dev-plus"]
JOBS = [   # (name, args, expected minutes)
    ("M2_v5_small_s14", [Q.S + "ml_reranker.py", *V5, "--seed", "14", "--tag", "v5_small_s14"], 45),
    ("M2_v5_mdeberta_s13", [Q.S + "ml_reranker.py", *V5, "--model", "microsoft/mdeberta-v3-base", "--seed", "13", "--tag", "v5_mdeberta_s13"], 180),
    ("M2_v5_mdeberta_s14", [Q.S + "ml_reranker.py", *V5, "--model", "microsoft/mdeberta-v3-base", "--seed", "14", "--tag", "v5_mdeberta_s14"], 180),
    ("compare_models", [Q.S + "compare_models.py", "--stage", "all"], 90),
]
# Nick uses the PC after this time: start nothing that would still be running then
# (pass "--no-deadline" to run the whole list, e.g. overnight).
DEADLINE = None if "--no-deadline" in sys.argv else dt.datetime.now().replace(hour=19, minute=30, second=0)


def free_gb():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) / 1024 / 1024


pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
while pid and subprocess.call(["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
    time.sleep(60)
Q.log("queue4 start")
for name, args, minutes in JOBS:
    if DEADLINE and dt.datetime.now() + dt.timedelta(minutes=minutes) > DEADLINE:
        Q.log(f"STOP before {name}: it would run past {DEADLINE:%H:%M} (PC in use); rerun queue4.py --no-deadline later")
        break
    tag = args[args.index("--tag") + 1] if "--tag" in args else None
    if tag and (Q.REPO / "data" / "models" / f"reranker_{tag}_frac1.0").exists():
        Q.log(f"SKIP {name}: model already trained")
        continue
    for _ in range(30):                     # wait up to 30 min for 8 GB free
        if free_gb() >= 8:
            break
        time.sleep(60)
    else:
        Q.log(f"SKIP {name}: under 8 GB free memory for 30 min")
        continue
    Q.run(name, args)
    time.sleep(120)
Q.log("queue4 done")
