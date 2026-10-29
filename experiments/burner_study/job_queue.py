"""Overnight queue: CPU experiments one at a time, each waiting for the data it needs.

Runs independently of the burner job (dev_fetch.py), which keeps its own pace. Every job's
output goes to data/experiments/queue/<name>.log; one summary line per job goes to queue.log.
A failed job is logged and the queue moves on.
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXP = REPO / "data" / "experiments"
QDIR = EXP / "queue"
QDIR.mkdir(parents=True, exist_ok=True)
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
S = "experiments/burner_study/"


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with (QDIR / "queue.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def count_codes(name):
    p = EXP / name
    return len({json.loads(l)["code"] for l in p.read_text(encoding="utf-8").split("\n") if l.strip()}) if p.exists() else 0


def dev_inputs_ready():
    return count_codes("dev_av.jsonl") >= 110 or "phase 1b" in _fetch_log()


def train_accounts_ready():
    return "phase 2:" in _fetch_log() and ("all done" in _fetch_log() or _phase2_progress() >= 255)


def _fetch_log():
    p = EXP / "dev_fetch2.log"
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def _phase2_progress():
    n = 0
    for line in _fetch_log().split("\n"):
        if "phase 2:" in line and "/" in line and "accounts" in line:
            try:
                n = int(line.split("phase 2:")[1].split("/")[0].strip())
            except ValueError:
                pass
    return n


def wait_for(cond, label, deadline_hhmm):
    h, m = map(int, deadline_hhmm.split(":"))
    while not cond():
        now = datetime.now()
        if (now.hour, now.minute) >= (h, m):
            log(f"{label}: not ready by {deadline_hhmm}, running anyway")
            return
        time.sleep(120)
    log(f"{label}: ready")


def run(name, args):
    log(f"START {name}: {' '.join(args)}")
    t0 = time.time()
    with (QDIR / f"{name}.log").open("w", encoding="utf-8") as out:
        rc = subprocess.call([PY, *args], cwd=REPO, stdout=out, stderr=subprocess.STDOUT)
    tail = [l for l in (QDIR / f"{name}.log").read_text(encoding="utf-8", errors="ignore").split("\n") if l.strip()][-6:]
    log(f"END {name}: exit {rc} after {(time.time() - t0) / 60:.0f} min | " + " / ".join(t.strip() for t in tail[-4:]))


def main():
    # 0. let the M2 v1 run (started by hand) finish so jobs don't share the CPU
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    while first and subprocess.call(["powershell", "-NoProfile", "-Command", f"Get-Process -Id {first}"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        time.sleep(60)
    log("queue start")
    # 1. learning curve on the v1 setup (raw inputs): does more data help?
    for frac in ("0.25", "0.5", "0.75"):
        run(f"lc_frac{frac}", [S + "ml_reranker.py", "--frac", frac, "--epochs", "3", "--tag", "v1_raw"])
    # 2-4 need the dev reels' speech / screen text / full captions
    wait_for(dev_inputs_ready, "dev inputs", "17:30")
    run("dev_breakdown_clean_inputs", [S + "dev_breakdown.py"])
    run("B3_ollama", [S + "b3_ollama.py"])
    run("M2_v2_clean_parserpick", [S + "ml_reranker.py", "--clean", "--parser-pick", "--no-accounts", "--tag", "v2_clean_pick"])
    run("M2_v2b_clean_only", [S + "ml_reranker.py", "--clean", "--no-accounts", "--tag", "v2b_clean"])
    # 5-6 need the train posters' account types
    wait_for(train_accounts_ready, "train account types", "19:45")
    run("M2_v3_clean_pick_accounts", [S + "ml_reranker.py", "--clean", "--parser-pick", "--tag", "v3_clean_pick_acct"])
    run("M2_v3_seed2", [S + "ml_reranker.py", "--clean", "--parser-pick", "--seed", "7", "--tag", "v3_seed7"])
    run("poster_role_with_accounts", [S + "poster_role.py"])
    log("queue done")


if __name__ == "__main__":
    main()
