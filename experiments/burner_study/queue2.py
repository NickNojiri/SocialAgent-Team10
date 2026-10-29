"""After the v4 recipe run frees the CPU: retrain on Nick's 445 confirmations (v5) and score on
the enlarged dev set (corpus dev + Nick's dev reels), then the risk-coverage table again."""
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import job_queue as Q  # noqa: E402

pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
while pid and subprocess.call(["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
    time.sleep(60)
Q.log("queue2 start")
Q.run("M2_v5_moregold", [Q.S + "ml_reranker.py", "--clean", "--parser-pick", "--recipe-cue", "--dev-plus", "--tag", "v5_moregold"])
Q.log("queue2 done")
