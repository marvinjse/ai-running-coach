import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import glob

RAW_DIR = Path.home() / "Desktop" / "HealthMirror" / "raw"
hr_dir = RAW_DIR / "heart_rate"

# Workout bounds in UTC
# Start: 2026-08-08 15:15:25 UTC
# End:   2026-08-08 16:13:45 UTC
w_start = datetime(2026, 8, 8, 15, 15, 25, tzinfo=timezone.utc)
w_end = datetime(2026, 8, 8, 16, 13, 45, tzinfo=timezone.utc)

# Expand search window by 3 minutes on each side
search_start = w_start - timedelta(minutes=3)
search_end = w_end + timedelta(minutes=3)

print(f"Searching HR samples between {search_start} and {search_end} UTC...\n")

matched_samples = []

for filepath in glob.glob(str(hr_dir / "*.jsonl")):
    with open(filepath, "r") as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line)
            
            # Print first sample schema for inspection
            if not matched_samples and 'value' in r:
                print("Sample record structure:", r)
                
            raw_time = r.get("startDate") or r.get("recordedAt") or r.get("start") or r.get("endDate")
            if not raw_time: continue
            
            try:
                dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                # If naive, assume UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                    
                if search_start <= dt <= search_end:
                    val = r.get("value")
                    bpm = val.get("bpm") if isinstance(val, dict) else val
                    matched_samples.append((dt, bpm))
            except Exception:
                continue

print(f"\nTotal matched HR samples: {len(matched_samples)}")
if matched_samples:
    bpms = [b for _, b in matched_samples if isinstance(b, (int, float))]
    if bpms:
        print(f"❤️ HR Stats -> Avg: {round(sum(bpms)/len(bpms), 1)} bpm | Max: {max(bpms)} bpm | Min: {min(bpms)} bpm")
