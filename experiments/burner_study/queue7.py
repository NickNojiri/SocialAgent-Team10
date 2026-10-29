"""2026-10-28: confirm the recommended model (M2 v5, 5 epochs; 74.2% on 271 dev reels with
seed 13) with two more seeds, before anyone reads the test split.

Same safety rules as queue5: one job at a time, 8 GB free memory before each job, two-minute
cool-down. It also waits while a game is running (Nick's rule: no heavy jobs while gaming)
and while SpotBot is running, since CPU training never overlaps the GPU's Ollama jobs.
"""
import re
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import job_queue as Q  # noqa: E402

V5 = ["--clean", "--parser-pick", "--recipe-cue", "--dev-plus"]      # as in queue5 (importing it would run it)
BUSY = r"VALORANT-Win64-Shipping|League of Legends\.exe|uvicorn|app[/\\]bot\.py|ollama runner"


def free_gb():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) / 1024 / 1024


def busy():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process | ForEach-Object { $_.Name + ' ' + $_.CommandLine }"],
                         capture_output=True, text=True)
    return bool(re.search(BUSY, out.stdout, re.IGNORECASE))


JOBS = [
    ("M2_v5_tune_e5_s14", [Q.S + "ml_reranker.py", *V5, "--epochs", "5", "--seed", "14", "--tag", "v5_tune_e5_s14"]),
    ("M2_v5_tune_e5_s15", [Q.S + "ml_reranker.py", *V5, "--epochs", "5", "--seed", "15", "--tag", "v5_tune_e5_s15"]),
]

Q.log("queue7 start (confirm v5_tune_e5 with seeds 14, 15)")
for name, args in JOBS:
    while busy():
        time.sleep(120)
    for _ in range(30):
        if free_gb() >= 8:
            break
        time.sleep(60)
    else:
        Q.log(f"SKIP {name}: under 8 GB free memory for 30 min")
        continue
    Q.run(name, args)
    time.sleep(120)
Q.log("queue7 done")
