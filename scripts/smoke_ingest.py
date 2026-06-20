#!/usr/bin/env python3
import subprocess
import sys
import os
import re

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root_uri = root.replace("\\", "/")
    fix1 = f"file://{root_uri}/fixtures/instagram_post.html"
    fix2 = f"file://{root_uri}/fixtures/jsonld_event.html"
    
    cmd = [
        "python", "-m", "src.ingestion.cli",
        fix1, fix2,
        "--allow-file-urls",
        "--no-llm",
        "--no-geocode"
    ]
    
    print(f"Running: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8")
    try:
        print(result.stdout)
    except UnicodeEncodeError:
        print(result.stdout.encode('ascii', 'replace').decode('ascii'))
        
    if result.stderr:
        try:
            print(result.stderr, file=sys.stderr)
        except UnicodeEncodeError:
            print(result.stderr.encode('ascii', 'replace').decode('ascii'), file=sys.stderr)
        
    if result.returncode != 0:
        print("Pipeline exited with error.")
        sys.exit(result.returncode)
        
    if not re.search(r"validated\s*:\s*2", result.stdout):
        print("Error: Did not validate exactly 2 records.")
        sys.exit(1)
        
    print("Smoke test passed: 2 records validated successfully.")

if __name__ == "__main__":
    main()
