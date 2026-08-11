import json
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
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

# Model String
MODEL_NAME = "gemini-3.5-flash-lite"

scheduler = BackgroundScheduler(timezone="America/Los_Angeles")


# --- HELPER FUNCTIONS ---


def km_to_miles(km_val):
    if km_val is None:
        return None
    return round(float(km_val) * 0.621371, 2)


def pace_km_to_miles(dist_km, dur_min):
    if not dist_km or not dur_min or dist_km == 0:
        return None
    dist_mi = dist_km * 0.621371
    pace_dec = dur_min / dist_mi
    p_min = int(pace_dec)
    p_sec = int((pace_dec - p_min) * 60)
    return f"{p_min}:{p_sec:02d} /mi"


def send_telegram_msg(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    res = requests.post(
        url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    )
    if res.status_code != 200:
        requests.post(url, json={"chat_id": chat_id, "text": text})


# --- DATABASE FUNCTIONS ---


def save_workout_to_db(payload: dict):
    if not db:
        return
    val = payload.get("value", {})
    uuid = payload.get("uuid") or f"run_{payload.get('start')}"
    record = {
        "uuid": uuid,
        "local_date": payload.get("localDate"),
        "distance_km": val.get("distance_km"),
        "duration_min": val.get("duration_min"),
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
            formatted_runs.append(
                {
                    "date": r.get("local_date"),
                    "distance_miles": km_to_miles(d_km),
                    "duration_min": d_min,
                    "avg_pace_per_mile": pace_km_to_miles(d_km, d_min)
                    or r.get("avg_pace"),
                    "avg_hr_bpm": r.get("avg_hr"),
                    "max_hr_bpm": r.get("max_hr"),
                    "avg_cadence_spm": r.get("avg_cadence"),
                    "active_calories": r.get("active_calories"),
                }
            )
        return formatted_runs
    except Exception as e:
        print(f"Supabase workout fetch error: {e}")
        return []


def get_athlete_profile(chat_id: str):
    if not db:
        return "Unit Preference: Imperial (miles, min/mi)"
    try:
        res = (
            db.table("athlete_profile")
            .select("*")
            .eq("chat_id", str(chat_id))
            .execute()
        )
        data = res.data
        if data:
            p = data[0]
            return f"""
ATHLETE DYNAMIC PROFILE & GOALS:
- Primary Goal: {p.get('primary_goal', 'General Fitness')}
- Target Date: {p.get('target_date', 'N/A')}
- Target Time/Pace: {p.get('target_time', 'N/A')}
- Weekly Schedule Constraints: {p.get('schedule_constraints', 'Flexible')}
- Unit Preference: {p.get('unit_preference', 'Imperial (miles, min/mi)')}
"""
    except Exception as e:
        print(f"Error fetching profile: {e}")
    return "Unit Preference: Imperial (miles, min/mi)"


def get_weekly_training_plan(chat_id: str):
    if not db:
        return []
    try:
        res = (
            db.table("training_plans")
            .select("*")
            .eq("chat_id", str(chat_id))
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"Error fetching training plan: {e}")
        return []


def save_chat_turn(chat_id: str, sender: str, text: str):
    if not db:
        return
    try:
        db.table("chat_history").insert(
            {"chat_id": str(chat_id), "sender": sender, "message": text}
        ).execute()
    except Exception as e:
        print(f"Chat save error: {e}")


def get_recent_chat_history(chat_id: str, limit=25):
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
        return "\n".join([f"{t['sender'].upper()}: {t['message']}" for t in turns])
    except Exception as e:
        print(f"Chat history fetch error: {e}")
        return ""


# --- SCHEDULER JOBS ---


def send_daily_reminder():
    target_chat = TELEGRAM_CHAT_ID or "8682930690"
    training_plan = get_weekly_training_plan(target_chat)
    today_day = datetime.now().strftime("%a")  # e.g., 'Mon', 'Tue'

    # Find today's plan item
    today_plan = next((item for item in training_plan if item.get("day_of_week") == today_day), None)

    # Optional: Skip sending if today is marked as a 'Rest' day
    if today_plan and today_plan.get("workout_type") == "Rest":
        print(f"Skipping daily reminder: Today ({today_day}) is a rest day.")
        return

    # Otherwise, generate and send the reminder as usual...

    prompt = f"""
    You are an AI Running Coach sending a short, motivating morning reminder (2-3 sentences) to your athlete on Telegram.

    {athlete_profile}

    Weekly Training Plan:
    ```json
    {json.dumps(training_plan, indent=2)}
    ```

    Recent Completed Workouts:
    ```json
    {json.dumps(past_runs, indent=2)}
    ```

    Today is {today_day}. Remind them of today's target workout according to the plan. Keep it energetic!
    """
    try:
        response = ai.models.generate_content(model=MODEL_NAME, contents=prompt)
        send_telegram_msg(target_chat, response.text)
    except Exception as e:
        print(f"Error sending daily reminder: {e}")


def send_weekly_recap():
    target_chat = TELEGRAM_CHAT_ID or "8682930690"
    if not db:
        return
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    athlete_profile = get_athlete_profile(target_chat)
    training_plan = get_weekly_training_plan(target_chat)

    try:
        res = (
            db.table("workouts")
            .select("*")
            .gte("local_date", seven_days_ago)
            .execute()
        )
        weekly_runs = res.data or []
    except Exception as e:
        print(f"Error fetching weekly runs: {e}")
        return

    prompt = f"""
    You are an AI Running Coach generating a Weekly Training Recap for your athlete on Telegram.

    {athlete_profile}

    Planned Schedule:
    ```json
    {json.dumps(training_plan, indent=2)}
    ```

    Completed Workouts Past 7 Days:
    ```json
    {json.dumps(weekly_runs, indent=2)}
    ```

    Format using Markdown:
    1. 📊 **Weekly Totals:** Planned vs. Actual Distance (miles).
    2. 🏃 **Pace & HR Analysis:** Evaluation of effort and heart rate.
    3. 🎯 **Progress Toward Goal:** Progress toward target race.
    4. 💡 **Focus for Next Week:** Key action items for upcoming week.
    """
    try:
        response = ai.models.generate_content(model=MODEL_NAME, contents=prompt)
        send_telegram_msg(target_chat, response.text)
    except Exception as e:
        print(f"Error generating weekly recap: {e}")


scheduler.add_job(send_daily_reminder, "cron", hour=7, minute=30)
scheduler.add_job(send_weekly_recap, "cron", day_of_week="sun", hour=19, minute=0)


@app.on_event("startup")
def start_scheduler():
    scheduler.start()


# --- WEBHOOK ENDPOINTS ---


@app.post("/webhook/apple-health")
@app.post("/webhook/apple-health/")
async def receive_health_data(request: Request):
    payload = await request.json()
    save_workout_to_db(payload)

    val = payload.get("value", {})
    dist_km = val.get("distance_km")
    dur_min = val.get("duration_min")

    imperial_latest_summary = {
        "date": payload.get("localDate"),
        "distance_miles": km_to_miles(dist_km),
        "duration_min": dur_min,
        "avg_pace_per_mile": pace_km_to_miles(dist_km, dur_min),
        "avg_hr_bpm": val.get("avgHeartRate_bpm"),
        "max_hr_bpm": val.get("maxHeartRate_bpm"),
        "avg_cadence_spm": val.get("avgCadence_spm"),
        "active_calories": val.get("activeEnergy_kcal"),
    }

    target_chat = TELEGRAM_CHAT_ID or "8682930690"
    athlete_profile = get_athlete_profile(target_chat)
    training_plan = get_weekly_training_plan(target_chat)
    past_runs = get_recent_workouts(limit=5)

    prompt = f"""
    You are an expert AI Running Coach.

    {athlete_profile}

    Weekly Planned Schedule:
    ```json
    {json.dumps(training_plan, indent=2)}
    ```

    Latest Logged Workout (Imperial):
    ```json
    {json.dumps(imperial_latest_summary, indent=2)}
    ```

    Recent Workout History:
    ```json
    {json.dumps(past_runs, indent=2)}
    ```

    Provide a concise workout summary for Telegram in Imperial units. Compare this effort against their planned target for today.
    """

    try:
        response = ai.models.generate_content(model=MODEL_NAME, contents=prompt)
        reply = response.text
    except Exception as e:
        reply = f"Workout saved, but error generating AI analysis: {e}"

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

        athlete_profile = get_athlete_profile(chat_id)
        training_plan = get_weekly_training_plan(chat_id)
        past_runs = get_recent_workouts(limit=5)
        chat_context = get_recent_chat_history(chat_id, limit=25)

        prompt = f"""
        You are an AI Running Coach chatting with your athlete on Telegram.

        {athlete_profile}

        Stored Weekly Training Plan:
        {json.dumps(training_plan, indent=2)}

        Recent Workout History (Imperial):
        {json.dumps(past_runs, indent=2)}

        Recent Chat History:
        {chat_context}

        Athlete's Message: "{user_text}"

        INSTRUCTIONS:
        1. Answer their message directly as a supportive coach in "reply_text".
        2. IF they mentioned changing a race goal or weekly schedule (e.g., "Saturday run is 5 miles"), extract those updates under "plan_update" or "profile_update".

        Output STRICT JSON matching this format:
        {{
          "reply_text": "Your conversational reply to the runner here...",
          "plan_update": [
            {{
              "day_of_week": "Sat",
              "target_distance_miles": 5.0,
              "workout_type": "Long Run"
            }}
          ],
          "profile_update": {{
            "primary_goal": "optional string",
            "target_time": "optional string"
          }}
        }}
        """

        try:
            # Force structured JSON mode to prevent parsing exceptions
            response = ai.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )

            parsed = json.loads(response.text)
            reply = parsed.get("reply_text", "Got it!")

            # Update Training Plan in Supabase
            plan_updates = parsed.get("plan_update", [])
            if isinstance(plan_updates, list) and len(plan_updates) > 0:
                for item in plan_updates:
                    day = item.get("day_of_week")
                    if day:
                        day_abbr = day[:3].capitalize()
                        record = {
                            "chat_id": str(chat_id),
                            "day_of_week": day_abbr,
                            "workout_type": item.get("workout_type", "Long Run"),
                            "target_distance_miles": item.get(
                                "target_distance_miles", 0
                            ),
                            "updated_at": datetime.now().isoformat(),
                        }
                        db.table("training_plans").upsert(
                            record, on_conflict="chat_id,day_of_week"
                        ).execute()
                        print(
                            f"✅ Supabase training_plans updated for {day_abbr}: {record}"
                        )

            # Update Athlete Profile in Supabase
            prof_update = parsed.get("profile_update")
            if (
                prof_update
                and isinstance(prof_update, dict)
                and any(prof_update.values())
            ):
                prof_update["chat_id"] = str(chat_id)
                prof_update["updated_at"] = datetime.now().isoformat()
                db.table("athlete_profile").upsert(
                    prof_update, on_conflict="chat_id"
                ).execute()
                print(f"✅ Supabase athlete_profile updated: {prof_update}")

        except Exception as e:
            print(f"❌ Error during AI processing: {e}")
            reply = "I've noted that! Let's keep working toward your race."

        save_chat_turn(chat_id, "coach", reply)
        send_telegram_msg(chat_id, reply)

    return {"status": "ok"}