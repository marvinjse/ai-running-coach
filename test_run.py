import json
import requests
from pathlib import Path

# Path to HealthMirror raw workout folder
RAW_DIR = Path.home() / "iCloudDrive" / "HealthMirror" / "raw"
RENDER_URL = "https://ai-running-coach-ye45.onrender.com/webhook/apple-health"

workout_file = RAW_DIR / "workout" / "2026-08.jsonl"

records = []
with open(workout_file, "r", encoding="utf-8") as f:
    for line in f:
        if line.strip():
            records.append(json.loads(line))

# Find the run on Saturday 2026-08-08
sat_run = None
for r in records:
    val = r.get("value", {})
    if r.get("localDate") == "2026-08-08" and "run" in val.get("workoutType", "").lower():
        sat_run = r
        break

if sat_run:
    val = sat_run.get("value", {})
    dist_km = round(val.get("totalDistance_m", 0) / 1000, 2)
    dur_min = round(val.get("duration_s", 0) / 60, 1)
    
    print(f"📦 Found Saturday Run: {sat_run['localDate']} | {dist_km} km | {dur_min} mins")
    
    res = requests.post(RENDER_URL, json=sat_run)
    print(f"✅ Render HTTP Status Code: {res.status_code}")
else:
    print("❌ Saturday run not found in 2026-08.jsonl")
