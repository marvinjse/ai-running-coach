import json
import os
from fastapi import FastAPI, Request
from google import genai
import requests
from supabase import Client, create_client

app = FastAPI()

# Credentials from Render Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

# Initialize API Clients
ai = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
db: Client = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if (SUPABASE_URL and SUPABASE_KEY)
    else None
)


def km_to_miles(km_val):
    """Helper to convert kilometers to miles."""
    if km_val is None:
        return None
    return round(float(km_val) * 0.621371, 2)


def pace_km_to_miles(dist_km, dur_min):
    """Calculates pace in min/mile given distance in km and duration in minutes."""
    if not dist_km or not dur_min or dist_km == 0:
        return None
    dist_mi = dist_km * 0.621371
    pace_dec = dur_min / dist_mi
    p_min = int(pace_dec)
    p_sec = int((pace_dec - p_min) * 60)
    return f"{p_min}:{p_sec:02d} /mi"


def send_telegram_msg(chat_id: str, text: str):
    """Sends Telegram message with markdown fallback."""
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    res = requests.post(
        url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    )
    if res.status_code != 200:
        requests.post(url, json={"chat_id": chat_id, "text": text})


def save_workout_to_db(payload: dict):
    """Inserts or updates workout in Supabase PostgreSQL database."""
    if not db:
        return

    val = payload.get("value", {})
    uuid = payload.get("uuid") or f"run_{payload.get('start')}"

    # Extract raw metric units
    dist_km = val.get("distance_km")
    dur_min = val.get("duration_min")

    # Convert to Imperial
    dist_mi = km_to_miles(dist_km)
    pace_mi = pace_km_to_miles(dist_km, dur_min)

    record = {
        "uuid": uuid,
        "local_date": payload.get("localDate"),
        "distance_km": dist_km,
        "duration_min": dur_min,
        "avg_pace": val.get("avgPace"),
        "avg_hr": val.get("avgHeartRate_bpm"),
        "max_hr": val.get("maxHeartRate_bpm"),
        "avg_cadence": val.get("avgCadence_spm"),
        "active_calories": val.get("activeEnergy_kcal"),
        "raw_payload": payload,
    }

    try:
        db.table("workouts").upsert(record, on_conflict="uuid").execute()
    except Exception as e:
        print(f"Supabase workout save error: {e}")


def get_recent_workouts(limit=5):
    """Fetches the last N workouts from Supabase and formats in Imperial units."""
    if not db:
        return []
    try:
        res = (
            db.table("workouts")
            .select("*")
            .order("local_date", desc=True)
            .limit(limit)
            .execute()
        )
        data = res.data or []

        formatted_runs = []
        for r in data:
            d_km = float(r.get("distance_km") or 0)
            d_min = float(r.get("duration_min") or 0)
            d_mi = km_to_miles(d_km)
            p_mi = pace_km_to_miles(d_km, d_min)

            formatted_runs.append(
                {
                    "date": r.get("local_date"),
                    "distance_miles": d_mi,
                    "duration_min": d_min,
                    "avg_pace_per_mile": p_mi or r.get("avg_pace"),
                    "avg_hr_bpm": r.get("avg_hr"),
                    "max_hr_bpm": r.get("max_hr"),
                    "avg_cadence_spm": r.get("avg_cadence"),
                    "active_calories": r.get("active_calories"),
                }
            )
        return formatted_runs
    except Exception as e:
        print(f"Supabase fetch error: {e}")
        return []


def save_chat_turn(chat_id: str, sender: str, text: str):
    """Stores a message turn in conversation memory."""
    if not db:
        return
    try:
        db.table("chat_history").insert(
            {"chat_id": str(chat_id), "sender": sender, "message": text}
        ).execute()
    except Exception as e:
        print(f"Chat save error: {e}")


def get_recent_chat_history(chat_id: str, limit=6):
    """Fetches recent conversation exchanges for context."""
    if not db:
        return ""
    try:
        res = (
            db.table("chat_history")
            .select("sender, message")
            .eq("chat_id", str(chat_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        turns = res.data or []
        turns.reverse()
        formatted = [f"{t['sender'].upper()}: {t['message']}" for t in turns]
        return "\n".join(formatted)
    except Exception as e:
        print(f"Chat history fetch error: {e}")
        return ""


@app.post("/webhook/apple-health")
@app.post("/webhook/apple-health/")
async def receive_health_data(request: Request):
    payload = await request.json()

    # 1. Save permanently to PostgreSQL
    save_workout_to_db(payload)

    # 2. Extract and convert latest run metrics to Imperial
    val = payload.get("value", {})
    dist_km = val.get("distance_km")
    dur_min = val.get("duration_min")
    dist_mi = km_to_miles(dist_km)
    pace_mi = pace_km_to_miles(dist_km, dur_min)

    imperial_latest_summary = {
        "date": payload.get("localDate"),
        "distance_miles": dist_mi,
        "duration_min": dur_min,
        "avg_pace_per_mile": pace_mi,
        "avg_hr_bpm": val.get("avgHeartRate_bpm"),
        "max_hr_bpm": val.get("maxHeartRate_bpm"),
        "avg_cadence_spm": val.get("avgCadence_spm"),
        "active_calories": val.get("activeEnergy_kcal"),
        "vo2_max": val.get("vo2Max_mL_kg_min"),
    }

    # 3. Fetch past runs for trend comparison
    past_runs = get_recent_workouts(limit=5)

    prompt = f"""
    You are an expert AI Running Coach communicating in IMPERIAL UNITS ONLY (miles, min/mile, bpm, SPM).

    IMPORTANT UNIT INSTRUCTION:
    - ALWAYS format distance in MILES (mi).
    - ALWAYS format pace in MINUTES PER MILE (e.g., 8:15 /mi). Never use km or min/km.

    Latest Workout Metrics (Imperial):
    ```json
    {json.dumps(imperial_latest_summary, indent=2)}
    ```

    Recent Workout History (Imperial):
    ```json
    {json.dumps(past_runs, indent=2)}
    ```

    Provide a concise, motivating workout summary for Telegram. Highlight pace in min/mile and distance in miles. Compare performance against recent trends.
    """

    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash", contents=prompt
        )
        reply = response.text
    except Exception as e:
        reply = f"Workout saved, but error generating AI analysis: {e}"

    target_chat = TELEGRAM_CHAT_ID or "8682930690"
    save_chat_turn(target_chat, "coach", reply)
    send_telegram_msg(target_chat, reply)
    return {"status": "success"}


@app.post("/webhook/telegram")
@app.post("/webhook/telegram/")
async def handle_telegram_chat(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    user_text = message.get("text", "")

    if chat_id and user_text:
        save_chat_turn(chat_id, "user", user_text)

        past_runs = get_recent_workouts(limit=5)
        chat_context = get_recent_chat_history(chat_id, limit=6)

        prompt = f"""
        You are an AI Running Coach chatting with your athlete on Telegram.

        IMPORTANT UNIT INSTRUCTION:
        - Use IMPERIAL UNITS ONLY for all responses: Miles (mi), Minutes per Mile (min/mi), SPM, and BPM.
        - NEVER reference kilometers or min/km.

        Athlete's Workout History Database (Imperial Units):
        ```json
        {json.dumps(past_runs, indent=2)}
        ```

        Recent Chat History:
        {chat_context}

        Athlete's New Message: "{user_text}"

        Answer their question directly using their workout database and conversation memory. If they ask about baselines, heart rate, or trends, evaluate the metrics in miles and min/mile. Keep replies concise and conversational.
        """

        try:
            response = ai.models.generate_content(
                model="gemini-3.6-flash", contents=prompt
            )
            reply = response.text
        except Exception as e:
            reply = "I had trouble accessing your workout history. Please try again in a moment."

        save_chat_turn(chat_id, "coach", reply)
        send_telegram_msg(chat_id, reply)

    return {"status": "ok"}
