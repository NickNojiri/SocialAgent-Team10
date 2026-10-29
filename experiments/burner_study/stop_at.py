"""At a given time (default 19:00), stop every experiment job still running and unload Ollama's
models, so the CPU and GPU are free for Nick. Usage: stop_at.py [HH:MM]"""
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import job_queue as Q  # noqa: E402

hh, mm = map(int, (sys.argv[1] if len(sys.argv) > 1 else "19:00").split(":"))
at = dt.datetime.now().replace(hour=hh, minute=mm, second=0)
while dt.datetime.now() < at:
    time.sleep(30)
ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'burner_study' "
      "-and $_.CommandLine -notmatch 'stop_at' } | Select-Object -ExpandProperty ProcessId")
pids = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.split()
for pid in pids:
    subprocess.call(["taskkill", "/T", "/F", "/PID", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=30) as resp:
        for m in json.loads(resp.read()).get("models", []):
            body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
            urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/generate", data=body,
                                                          headers={"Content-Type": "application/json"}), timeout=60)
except OSError:
    pass
Q.log(f"stop_at {hh:02d}:{mm:02d}: stopped {len(pids)} experiment process(es), unloaded Ollama models")
