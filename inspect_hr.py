import json
from pathlib import Path

hr_file = Path.home() / "Desktop" / "HealthMirror" / "raw" / "heart_rate" / "2026-08.jsonl"

if not hr_file.exists():
    print("❌ 2026-08.jsonl not found in heart_rate folder.")
else:
    with open(hr_file, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
        print(f"✅ Total heart rate records in 2026-08.jsonl: {len(lines)}\n")
        print("--- First 3 Sample Records ---")
        for line in lines[:3]:
            print(json.loads(line))
