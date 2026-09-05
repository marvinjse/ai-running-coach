import json
import os
import time
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
MODEL_NAME = "gemini-3.6-flash"

# Scheduler (Background tasks handled locally via Ubuntu cron, but kept configured for timezones)
scheduler = BackgroundScheduler(timezone="America/Los_Angeles")

# --- RESILIENT AI CALL HELPER ---

def generate_ai_response(prompt: str, is_json: bool = False):
    """Calls Gemini with automatic retries and fallback to Flash-Lite on 503 errors."""
    if not ai:
        raise Exception("Gemini client is not initialized.")

    models_to_try = [MODEL_NAME, "gemini-3.5-flash-lite"]
    config = {"response_mime_type": "application/json"} if is_json else None

    for model in models_to_try:
        for attempt in range(2):  # Try up to 2 times per model
            try:
                if config:
                    return ai.models.generate_content(
                        model=model, contents=prompt, config=config
                    )
                else:
                    return ai.models.generate_content(
                        model=model, contents=prompt
                    )
            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    print(f"⚠️ {model} hit capacity limit (Attempt {attempt + 1}). Retrying in 2s...")
                    time.sleep(2)
                else:
                    raise e

    raise Exception("All Gemini models are currently experiencing high demand.")

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
        print("⚠️ TELEGRAM_BOT_TOKEN is missing.")
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
        print("⚠️ Supabase client 'db' is not initialized.")
        return

    val = payload.get("value") or {}
    meta = payload.get("metadata") or {}

    dist_m = val.get("totalDistance_m") or val.get("totalDistance_n")
    if dist_m is not None:
        try:
            dist_km = round(float(dist_m) / 1000.0, 2)
        except (ValueError, TypeError):
            dist_km = 0.0
    else:
        try:
            dist_km = float(val.get("distance_km") or 0.0)
        except (ValueError, TypeError):
            dist_km = 0.0

    dur_s = val.get("duration_s")
    if dur_s is not None:
        try:
            dur_min = round(float(dur_s) / 60.0, 1)
        except (ValueError, TypeError):
            dur_min = 0.0
    else:
        try:
            dur_min = float(val.get("duration_min") or 0.0)
        except (ValueError, TypeError):
            dur_min = 0.0

    avg_pace = None
    if dist_km > 0 and dur_min > 0:
        pace_dec = dur_min / dist_km
        p_min = int(pace_dec)
        p_sec = int(round((pace_dec - p_min) * 60))
        if p_sec == 60:
            p_min += 1
            p_sec = 0
        avg_pace = f"{p_min}:{p_sec:02d} /km"

    def extract_float(keys):
        for k in keys:
            v = val.get(k) or payload.get(k) or meta.get(k)
            if v is not None:
                try:
                    return round(float(v), 1)
                except (ValueError, TypeError):
                    pass
        return None

    record = {
        "uuid": payload.get("uuid"),
        "local_date": payload.get("localDate"),
        "distance_km": dist_km if dist_km > 0 else None,
        "duration_min": dur_min if dur_min > 0 else None,
        "avg_pace": avg_pace,
        "avg_hr": extract_float(["avgHeartRate_bpm", "avg_hr", "HKQuantityTypeIdentifierHeartRate"]),
        "max_hr": extract_float(["maxHeartRate_bpm", "max_hr"]),
        "avg_cadence": extract_float(["avgCadence_spm", "avg_cadence", "step_cadence"]),
        "active_calories": extract_float(["totalEnergy_kcal", "activeEnergy_kcal", "active_calories"]),
        "raw_payload": payload
    }

    try:
        db.table("workouts").upsert(record).execute()
    except Exception as e:
        print(f"Supabase Upsert Error: {e}")


def get_recent_workouts(limit=15):
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


def get_plan_exercises(chat_id: str, day_of_week: str = None):
    if not db:
        return []
    try:
        query = db.table("plan_exercises").select("*").eq("chat_id", str(chat_id))
        if day_of_week:
            query = query.eq("day_of_week", day_of_week)
        res = query.execute()
        return res.data or []
    except Exception as e:
        print(f"Error fetching plan exercises: {e}")
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


def get_recent_chat_history(chat_id: str, limit=50):
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

def run_daily_reminder():
    """Triggered locally or manually via API endpoint."""
    target_chat = TELEGRAM_CHAT_ID or "8682930690"
    today_abbr = datetime.now().strftime("%a")

    todays_plan_res = db.table("training_plans").select("*").eq("chat_id", target_chat).eq("day_of_week", today_abbr).execute() if db else None
    todays_plan = todays_plan_res.data[0] if (todays_plan_res and todays_plan_res.data) else None
    todays_exercises = get_plan_exercises(target_chat, today_abbr)

    if not todays_plan and not todays_exercises:
        print(f"ℹ️ No training plan or exercise entries found for today ({today_abbr}).")
        return

    workout_type = todays_plan.get("workout_type", "Rest") if todays_plan else "Strength"
    if workout_type.lower() == "rest" and not todays_exercises:
        print(f"😴 Skipping reminder: Today ({today_abbr}) is a Rest day with no assigned strength exercises.")
        return

    target_dist = todays_plan.get("target_distance_miles", 0) if todays_plan else 0
    target_pace = todays_plan.get("target_pace_per_mile", "N/A") if todays_plan else "N/A"
    plan_notes = todays_plan.get("notes", "") if todays_plan else ""

    prompt = f"""
    You are an AI Running Coach sending a clear, highly motivating morning reminder to your athlete on Telegram.

    Today is {today_abbr}.
    
    Overall Day Overview:
    - Workout Category: {workout_type}
    - Target Running Distance: {target_dist} miles
    - Target Running Pace: {target_pace}
    - Overall Notes: {plan_notes}

    Assigned Gym/Strength Exercises:
    {json.dumps(todays_exercises, indent=2)}

    INSTRUCTIONS:
    1. Remind them of today's running targets (if distance > 0).
    2. IF there are assigned exercises, list each exercise clearly with target sets, reps, current target weight (lbs), and notes.
    3. **PROGRESSIVE OVERLOAD RECOMMENDATIONS**:
       - Provide 1-2 specific Coaching Tips / Progressive Overload recommendations for their strength session.
       - Suggest when to bump the weight (e.g., "+2.5 to +5 lbs if all sets felt smooth last session"), increase reps, or adjust tempo to build running-specific strength and injury resilience.
    4. Keep it energetic, well-formatted with bullet points, and concise. Use 1-2 relevant emojis.
    5. Output raw Markdown text only.
    """

    try:
        response = generate_ai_response(prompt, is_json=False)
        message = response.text
        print(f"🚀 Sending automated daily reminder for {today_abbr} ({workout_type})...")
        send_telegram_msg(target_chat, message)
        print("✅ Daily reminder sent successfully.")
    except Exception as e:
        print(f"❌ Error generating or sending daily reminder: {e}")


@app.on_event("startup")
def start_scheduler():
    scheduler.start()
    print("⏰ Background scheduler booted.")


# --- ENDPOINTS ---

@app.get("/")
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "AI Running Coach API"}


@app.get("/trigger-reminder")
def trigger_reminder_now():
    print("🧪 Manually triggering daily workout reminder...")
    run_daily_reminder()
    return {"status": "success", "message": "Daily reminder executed. Check Telegram!"}


@app.post("/webhook/apple-health")
@app.post("/webhook/apple-health/")
async def receive_health_data(request: Request):
    payload = await request.json()
    save_workout_to_db(payload)

    if request.query_params.get("silent") == "true":
        return {"status": "success", "mode": "silent_backfill"}

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
        "active_calories": val.get("activeEnergy_kcal")
    }

    target_chat = TELEGRAM_CHAT_ID or "8682930690"
    athlete_profile = get_athlete_profile(target_chat)
    training_plan = get_weekly_training_plan(target_chat)
    past_runs = get_recent_workouts(limit=15)
    recent_chat = get_recent_chat_history(target_chat, limit=50)

    prompt = f"""
    You are an expert, highly analytical AI Running Coach reviewing a newly uploaded workout.

    Athlete Profile & Goals:
    {athlete_profile}

    Weekly Planned Schedule:
    ```json
    {json.dumps(training_plan, indent=2)}
    ```

    JUST COMPLETED WORKOUT (Imperial):
    ```json
    {json.dumps(imperial_latest_summary, indent=2)}
    ```

    RECENT WORKOUT HISTORY (Last 15 Runs):
    ```json
    {json.dumps(past_runs, indent=2)}
    ```

    RECENT CHAT CONTEXT WITH ATHLETE:
    {recent_chat}

    INSTRUCTIONS & COACHING ANALYSIS:
    1. **Workout Breakdown:** Briefly summarize distance, pace, and HR/effort for today's run.
    2. **Historical Context & Progress:**
       - Compare today's pace and HR against their recent average over the past 15 runs. Is HR lower at a similar pace? Are they trending faster/slower?
       - Check if today's run aligns with their target pace and distance for today's day of the week in the training plan.
    3. **Actionable Recommendations:**
       - Provide 2 specific, actionable takeaways for their next workout or rest period based on their rolling volume, recent fatigue/pain mentioned in chat, or upcoming target runs.
    4. Keep it engaging, clear, and well-structured using Markdown bullets and emojis.
    """

    try:
        response = generate_ai_response(prompt, is_json=False)
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
        all_exercises = get_plan_exercises(chat_id)
        past_runs = get_recent_workouts(limit=15)
        chat_context = get_recent_chat_history(chat_id, limit=50)

        prompt = f"""
        You are an AI Running Coach chatting with your athlete on Telegram.

        Athlete Profile:
        {athlete_profile}

        Stored Weekly Running Plan:
        {json.dumps(training_plan, indent=2)}

        Stored Detailed Strength/Gym Exercises:
        {json.dumps(all_exercises, indent=2)}

        Recent Workout History (Imperial):
        {json.dumps(past_runs, indent=2)}

        Recent Chat History:
        {chat_context}

        Athlete's Message: "{user_text}"

        INSTRUCTIONS:
        1. Answer their message directly as a supportive coach in "reply_text".
        2. IF they ask about their strength exercises for any day, accurately list every assigned exercise, target set, rep, weight, and note.
        3. IF they mention changing running distance, target pace, or workout type for any day, extract it under "plan_update".
        4. IF they mention adding, modifying, or completing a strength/gym exercise, extract it under "plan_exercise_update".

        CRITICAL: Always extract numeric weights into "target_weight_lbs".

        Output STRICT JSON matching this format:
        {{
          "reply_text": "Your conversational reply to the runner here...",
          "plan_update": [
            {{
              "day_of_week": "Sat",
              "target_distance_miles": 8.0,
              "target_pace_per_mile": "8:00",
              "workout_type": "Long Run"
            }}
          ],
          "plan_exercise_update": [
            {{
              "day_of_week": "Tue",
              "exercise_name": "Goblet Squats",
              "target_sets": 3,
              "target_reps": 10,
              "target_weight_lbs": 40.0,
              "notes": "optional string or notes"
            }}
          ],
          "profile_update": {{}}
        }}
        """

        try:
            response = generate_ai_response(prompt, is_json=True)
            parsed = json.loads(response.text)
            reply = parsed.get("reply_text", "Got it!")

            plan_updates = parsed.get("plan_update", [])
            if isinstance(plan_updates, list) and len(plan_updates) > 0:
                for item in plan_updates:
                    day = item.get("day_of_week")
                    if day:
                        day_abbr = day[:3].capitalize()
                        record = {
                            "chat_id": str(chat_id),
                            "day_of_week": day_abbr,
                            "workout_type": item.get("workout_type", "Running"),
                            "target_distance_miles": item.get("target_distance_miles", 0),
                            "target_pace_per_mile": item.get("target_pace_per_mile") or item.get("target_pace") or "N/A",
                            "updated_at": datetime.now().isoformat(),
                        }
                        db.table("training_plans").upsert(
                            record, on_conflict="chat_id,day_of_week"
                        ).execute()

            exercise_updates = parsed.get("plan_exercise_update", [])
            if isinstance(exercise_updates, list) and len(exercise_updates) > 0:
                for item in exercise_updates:
                    day = item.get("day_of_week")
                    ex_name = item.get("exercise_name")
                    if day and ex_name:
                        day_abbr = day[:3].capitalize()
                        weight_val = (
                            item.get("target_weight_lbs") 
                            or item.get("weight_lbs") 
                            or item.get("weight") 
                            or 0
                        )

                        ex_record = {
                            "chat_id": str(chat_id),
                            "day_of_week": day_abbr,
                            "exercise_name": ex_name,
                            "target_sets": item.get("target_sets", 3),
                            "target_reps": item.get("target_reps", 10),
                            "target_weight_lbs": float(weight_val),
                            "notes": item.get("notes", ""),
                        }

                        db.table("plan_exercises").upsert(
                            ex_record, 
                            on_conflict="chat_id,day_of_week,exercise_name"
                        ).execute()

            prof_update = parsed.get("profile_update")
            if prof_update and isinstance(prof_update, dict) and any(prof_update.values()):
                prof_update["chat_id"] = str(chat_id)
                prof_update["updated_at"] = datetime.now().isoformat()
                db.table("athlete_profile").upsert(
                    prof_update, on_conflict="chat_id"
                ).execute()

        except Exception as e:
            print(f"❌ Error during AI processing: {e}")
            reply = "I've noted that! Let's keep working toward your goals."

        save_chat_turn(chat_id, "coach", reply)
        send_telegram_msg(chat_id, reply)

    return {"status": "ok"}
