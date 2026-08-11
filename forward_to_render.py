import glob
import gzip
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

RAW_DIR = Path.home() / "Desktop" / "HealthMirror" / "raw"
RENDER_URL = "https://ai-running-coach-ye45.onrender.com/webhook/apple-health"
STATE_FILE = Path.home() / ".last_synced_run_uuid"


def read_jsonl(file_path):
    """Reads standard or gzip-compressed .jsonl files safely."""
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


def parse_iso(iso_str):
    """Parses ISO timestamps into naive UTC datetime objects."""
    if not iso_str:
        return None
    try:
        clean_str = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def fetch_time_series_amount(folder_name, start_dt, end_dt, window_padding_min=3):
    """Extracts values from a raw metric folder within a timeframe."""
    target_dir = RAW_DIR / folder_name
    samples = []

    if not target_dir.exists():
        return samples

    pad_start = start_dt - timedelta(minutes=window_padding_min) if start_dt else None
    pad_end = end_dt + timedelta(minutes=window_padding_min) if end_dt else None

    for filepath in glob.glob(str(target_dir / "*.jsonl")):
        records = read_jsonl(Path(filepath))
        for r in records:
            r_time = parse_iso(r.get("start") or r.get("startDate") or r.get("recordedAt") or r.get("end"))
            if pad_start and pad_end and r_time:
                if not (pad_start <= r_time <= pad_end):
                    continue

            val = r.get("value", {})
            amount = val.get("amount") if isinstance(val, dict) else val
            if isinstance(amount, (int, float)):
                samples.append(amount)

    return samples


def fetch_latest_vo2max(local_date):
    """Searches vo2_max or vo2max folders for the latest VO2 Max reading."""
    possible_folders = ["vo2_max", "vo2max", "vo2_max_sample"]
    vo2_samples = []

    for folder in possible_folders:
        vo2_dir = RAW_DIR / folder
        if vo2_dir.exists():
            for filepath in glob.glob(str(vo2_dir / "*.jsonl")):
                records = read_jsonl(Path(filepath))
                for r in records:
                    val = r.get("value", {})
                    amount = val.get("amount") if isinstance(val, dict) else val
                    if isinstance(amount, (int, float)):
                        vo2_samples.append(amount)

    return vo2_samples[-1] if vo2_samples else None


def forward_latest_workout():
    workout_dir = RAW_DIR / "workout"
    all_runs = []

    # 1. Read all running workouts
    for filepath in glob.glob(str(workout_dir / "*.jsonl")):
        file_records = read_jsonl(Path(filepath))
        for record in file_records:
            val = record.get("value", {})
            if "run" in val.get("workoutType", "").lower():
                all_runs.append(record)

    if not all_runs:
        print(f"❌ No running workouts found in {workout_dir}")
        return

    # 2. Grab the latest run
    all_runs.sort(key=lambda x: x.get("start", ""))
    latest_run = all_runs[-1]

    # 3. De-duplication Check (Skip if UUID matches the last forwarded run)
    run_uuid = latest_run.get("uuid")
    if STATE_FILE.exists() and STATE_FILE.read_text().strip() == run_uuid:
        print(f"ℹ️ Latest run ({latest_run.get('localDate')}) was already forwarded. Skipping post.")
        return

    val = latest_run.get("value", {})
    start_dt = parse_iso(latest_run.get("start"))
    end_dt = parse_iso(latest_run.get("end"))
    local_date = latest_run.get("localDate")

    # 4. Distance, Duration, Pace
    dist_m = val.get("totalDistance_m", 0)
    dur_s = val.get("duration_s", 0)

    if dist_m > 0 and dur_s > 0:
        dist_km = dist_m / 1000.0
        dur_min = dur_s / 60.0
        pace_dec = dur_min / dist_km
        pace_min = int(pace_dec)
        pace_sec = int((pace_dec - pace_min) * 60)

        latest_run["value"]["distance_km"] = round(dist_km, 2)
        latest_run["value"]["duration_min"] = round(dur_min, 1)
        latest_run["value"]["avgPace"] = f"{pace_min}:{pace_sec:02d} /km"

    # 5. Heart Rate
    hr_samples = fetch_time_series_amount("heart_rate", start_dt, end_dt)
    if hr_samples:
        avg_hr = round(sum(hr_samples) / len(hr_samples), 1)
        max_hr = round(max(hr_samples), 1)
        latest_run["value"]["avgHeartRate_bpm"] = avg_hr
        latest_run["value"]["maxHeartRate_bpm"] = max_hr

    # 6. Cadence & Steps
    if "avgCadence_spm" not in latest_run["value"]:
        step_samples = fetch_time_series_amount("steps", start_dt, end_dt, window_padding_min=0)
        if step_samples and dur_s > 0:
            total_steps = sum(step_samples)
            latest_run["value"]["totalSteps"] = int(total_steps)
            latest_run["value"]["avgCadence_spm"] = round(total_steps / (dur_s / 60.0), 1)

    # 7. VO2 Max (if available)
    vo2max_val = fetch_latest_vo2max(local_date)
    if vo2max_val:
        latest_run["value"]["vo2Max_mL_kg_min"] = round(vo2max_val, 1)

    # 8. Active Calories
    if "totalEnergy_kcal" in val:
        latest_run["value"]["activeEnergy_kcal"] = round(val["totalEnergy_kcal"], 1)

    dist_disp = latest_run["value"].get("distance_km", "N/A")
    pace_disp = latest_run["value"].get("avgPace", "N/A")

    print(f"📦 Forwarding New Workout: {local_date} | {dist_disp} km @ {pace_disp}")

    # 9. POST to Render and save state on success
    response = requests.post(RENDER_URL, json=latest_run)
    print(f"✅ Render HTTP Status: {response.status_code}")

    if response.status_code == 200 and run_uuid:
        STATE_FILE.write_text(run_uuid)


if __name__ == "__main__":
    forward_latest_workout()
