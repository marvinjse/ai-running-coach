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

# Background Scheduler for Reminders & Recaps (PST)
scheduler = BackgroundScheduler(timezone="America/Los_Angeles")


# --- UNIT CONVERSION HELPERS ---


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


def check_and_update_dynamic_data(chat_id: str, user_text: str):
    """Parses user message and explicitly updates athlete_profile and training_plans in Supabase."""
    if not db or not user_text:
        return

    keywords = [
        "goal",
        "target",
        "race",
        "schedule",
        "marathon",
        "half",
        "plan",
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
        "mile",
        "run",
    ]
    if not any(kw in user_text.lower() for kw in keywords):
        return

    extraction_prompt = f"""
    You are a data extraction assistant. Analyze this runner's message to their coach:
    "{user_text}"

    If they mention changing a workout, distance, or activity for any day of the week, extract a "plan_update".
    If they mention changing their primary race goal, target date, or target time, extract a "profile_update".

    Output STRICT JSON matching this schema:
    {{
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

    Rules for day_of_week: Use 3-letter abbreviation ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun').
    If no updates are made, output: {{}}
    Return ONLY raw valid JSON without markdown fences or extra prose.
    """

    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash", contents=extraction_prompt
        )

        raw_text = response.text.strip()
        if "```" in raw_text:
            raw_text = (
                raw_text.split("```")[1]
                .replace("json", "")
                .replace("```", "")
                .strip()
            )

        data = json.loads(raw_text)
        print(f"🔍 Extracted intent data: {data}")

        # Update Training Plan
        plan_updates = data.get("plan_update", [])
        if isinstance(plan_updates, list) and len(plan_updates) > 0:
            for item in plan_updates:
                day = item.get("day_of_week")
                if day:
                    day_abbr = day[:3].capitalize()

                    # Fetch existing record to merge fields
                    existing = (
                        db.table("training_plans")
                        .select("*")
                        .eq("chat_id", str(chat_id))
                        .eq("day_of_week", day_abbr)
                        .execute()
                    )

                    record = {
                        "chat_id": str(chat_id),
                        "day_of_week": day_abbr,
                        "workout_type": item.get("workout_type")
                        or (
                            existing.data[0]["workout_type"]
                            if existing.data
                            else "Long Run"
                        ),
                        "target_distance_miles": item.get(
                            "target_distance_miles"
                        )
                        or (
                            existing.data[0]["target_distance_miles"]
                            if existing.data
                            else 0
                        ),
                        "updated_at": datetime.now().isoformat(),
                    }

                    db.table("training_plans").upsert(
                        record, on_conflict="chat_id,day_of_week"
                    ).execute()
                    print(
                        f"✅ Supabase training_plans updated successfully for {day_abbr}!"
                    )

        # Update Athlete Profile
        prof_update = data.get("profile_update")
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
            print("✅ Supabase athlete_profile updated successfully!")

    except Exception as e:
        print(f"❌ Error during dynamic update extraction: {e}")


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
        formatted = [f"{t['sender'].upper()}: {t['message']}" for t in turns]
        return "\n".join(formatted)
    except Exception as e:
        print(f"Chat history fetch error: {e}")
        return ""


# --- AUTOMATED SCHEDULER JOBS ---


def send_daily_reminder():
    target_chat = TELEGRAM_CHAT_ID or "8682930690"
    athlete_profile = get_athlete_profile(target_chat)
    training_plan = get_weekly_training_plan(target_chat)
    past_runs = get_recent_workouts(limit=3)

    today_day = datetime.now().strftime("%a")

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

    Today is {today_day}. Remind them of today's target workout according to the training plan. Keep it energetic and focused!
    """
    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash", contents=prompt
        )
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
    2. 🏃 **Pace & HR Analysis:** Evaluation of effort, heart rate control, and consistency.
    3. 🎯 **Progress Toward Goal:** Progress evaluation toward their target race.
    4. 💡 **Focus for Next Week:** Key action items for the upcoming week.
    """
    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash", contents=prompt
        )
        send_telegram_msg(target_chat, response.text)
    except Exception as e:
        print(f"Error generating weekly recap: {e}")


# Run daily at 7:30 AM PST & Sunday at 7:00 PM PST
scheduler.add_job(send_daily_reminder, "cron", hour=7, minute=30)
scheduler.add_job(
    send_weekly_recap, "cron", day_of_week="sun", hour=19, minute=0
)


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

    Provide a concise, motivating workout summary for Telegram in Imperial units. Compare this effort against their planned target for this day of the week.
    """

    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash", contents=prompt
        )
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

        # 1. Check if user wants to update goals or weekly schedule
        check_and_update_dynamic_data(chat_id, user_text)

        # 2. Pull all dynamic context
        athlete_profile = get_athlete_profile(chat_id)
        training_plan = get_weekly_training_plan(chat_id)
        past_runs = get_recent_workouts(limit=5)
        chat_context = get_recent_chat_history(chat_id, limit=25)

        prompt = f"""
        You are an AI Running Coach chatting with your athlete on Telegram.

        {athlete_profile}

        Stored Weekly Training Plan:
        ```json
        {json.dumps(training_plan, indent=2)}
        ```

        Athlete's Workout History (Imperial Units):
        ```json
        {json.dumps(past_runs, indent=2)}
        ```

        Recent Chat History:
        {chat_context}

        Athlete's Message: "{user_text}"

        Answer their question directly using their workout database, stored training plan, and chat memory.
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
