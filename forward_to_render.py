import glob
import gzip
import json
from pathlib import Path
import requests

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
    workout_dir = RAW_DIR / "workout"
    all_runs = []

    # 1. Scan ALL monthly jsonl files in the workout directory
    for filepath in glob.glob(str(workout_dir / "*.jsonl")):
        file_records = read_jsonl(Path(filepath))
        for record in file_records:
            val = record.get("value", {})
            w_type = val.get("workoutType", "").lower()

            # 2. Filter specifically for running workouts
            if "run" in w_type:
                all_runs.append(record)

    if not all_runs:
        print("No running workouts found across any files.")
        return

    # 3. Sort chronologically by start time and pick the newest
    all_runs.sort(key=lambda x: x.get("start", ""))
    latest_run = all_runs[-1]

    val = latest_run.get("value", {})
    date = latest_run.get("localDate")
    dist_km = round(val.get("totalDistance_m", 0) / 1000, 2)

    print(f"📦 Found Latest Run: {date} | {dist_km} km")

    # POST the latest running workout data to your Render server
    response = requests.post(RENDER_URL, json=latest_run)
    print(f"Sent to Render | Status Code: {response.status_code}")


if __name__ == "__main__":
    forward_latest_workout()
