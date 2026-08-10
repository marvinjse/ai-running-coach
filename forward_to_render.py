import gzip
import json
import requests
from pathlib import Path

# Path to your mounted HealthMirror raw folder
RAW_DIR = Path.home() / "iCloudDrive" / "HealthMirror" / "raw"
RENDER_URL = "https://ai-running-coach-ye45.onrender.com/webhook/apple-health"

def read_jsonl(file_path):
    """Reads standard or gzip-compressed .jsonl files."""
    records = []
    if not file_path.exists():
        return records
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
    except (UnicodeDecodeError, json.JSONDecodeError):
        with gzip.open(file_path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
    return records

def forward_latest_workout():
    workout_file = RAW_DIR / "workout" / "2026-08.jsonl"
    workouts = read_jsonl(workout_file)

    if not workouts:
        print("No workouts found.")
        return

    latest_workout = workouts[-1]

    # POST the workout data to your Render server
    response = requests.post(RENDER_URL, json=latest_workout)
    print(f"Sent to Render | Status Code: {response.status_code}")

if __name__ == "__main__":
    forward_latest_workout()
