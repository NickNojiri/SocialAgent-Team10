"""The 2026-10-24 overnight run: big vs small vs medium comparison, then tests 1-3 and 5-7.

Same safety rules as queue4: one job at a time (CPU training never overlaps the GPU's Ollama
jobs), 8 GB free memory before each job, two-minute cool-down between jobs. A job that fails is
logged and the queue moves on. Cheap analyses run first so their results exist early.
"""
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import job_queue as Q  # noqa: E402


def free_gb():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) / 1024 / 1024


V5 = ["--clean", "--parser-pick", "--recipe-cue", "--dev-plus"]
CMP = [Q.S + "compare_models.py", "--stage", "all"]
MEDIUM = ["--model", "BAAI/bge-reranker-base"]
JOBS = [
    ("compare_models", CMP),                 # small models + both LLMs (GPU) + LLM judge: big vs small vs both
    ("error_analysis", [Q.S + "error_analysis.py", "ft_small_s13", "ft_small_s14", "llm_qwen2.5_7b", "llm_llama3.1_8b"]),
    ("name_cleanup", [Q.S + "name_cleanup.py"]),
    ("calibrate", [Q.S + "calibrate.py", "small_s13", "13"]),
    ("post_type", [Q.S + "post_type.py"]),
    ("summary_early", [Q.S + "overnight_summary.py"]),
    ("llm_hints", [Q.S + "llm_hints.py"]),                                                     # GPU
    ("M2_v6_llmhint_s13", [Q.S + "ml_reranker.py", *V5, "--llm-hint", "--seed", "13", "--tag", "v6_llmhint_s13"]),
    ("M2_v6_llmhint_s14", [Q.S + "ml_reranker.py", *V5, "--llm-hint", "--seed", "14", "--tag", "v6_llmhint_s14"]),
    ("M2_v5_small_s15", [Q.S + "ml_reranker.py", *V5, "--seed", "15", "--tag", "v5_small_s15"]),
    ("compare_refresh", CMP),                                  # scores the new models, builds the seed ensembles
    ("error_analysis_2", [Q.S + "error_analysis.py", "ft_small_s13", "ens_small", "ft_llmhint_s13", "ens_llmhint"]),
    ("name_cleanup_2", [Q.S + "name_cleanup.py"]),
    ("summary_mid", [Q.S + "overnight_summary.py"]),
    ("M2_v5_medium_s13", [Q.S + "ml_reranker.py", *V5, *MEDIUM, "--seed", "13", "--tag", "v5_medium_s13"]),
    ("M2_v5_medium_s14", [Q.S + "ml_reranker.py", *V5, *MEDIUM, "--seed", "14", "--tag", "v5_medium_s14"]),
    ("compare_medium", CMP),
    ("summary_medium", [Q.S + "overnight_summary.py"]),
    ("M2_v5_tune_e5", [Q.S + "ml_reranker.py", *V5, "--epochs", "5", "--tag", "v5_tune_e5"]),
    ("M2_v5_tune_lr3e5", [Q.S + "ml_reranker.py", *V5, "--lr", "3e-5", "--tag", "v5_tune_lr3e5"]),
    ("M2_v5_tune_lr1e5_e5", [Q.S + "ml_reranker.py", *V5, "--lr", "1e-5", "--epochs", "5", "--tag", "v5_tune_lr1e5_e5"]),
    ("summary_final", [Q.S + "overnight_summary.py"]),
]

pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
while pid and subprocess.call(["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
    time.sleep(60)
Q.log("queue5 start")
for name, args in JOBS:
    for _ in range(30):
        if free_gb() >= 8:
            break
        time.sleep(60)
    else:
        Q.log(f"SKIP {name}: under 8 GB free memory for 30 min")
        continue
    Q.run(name, args)
    time.sleep(120)
Q.log("queue5 done")
