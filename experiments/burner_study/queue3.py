"""After queue2 (M2 v5) finishes: find answer-key mistakes with parser + M2 v5 + Ollama."""
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import job_queue as Q  # noqa: E402

pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
while pid and subprocess.call(["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
    time.sleep(60)
Q.log("queue3 start")
Q.run("keycheck_flags", [Q.S + "keycheck_flags.py"])
Q.log("queue3 done")
