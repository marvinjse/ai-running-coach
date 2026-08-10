import os
import requests
from fastapi import FastAPI, Request
from google import genai

app = FastAPI()

# Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Initialize Gemini Client
ai = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def send_telegram_msg(chat_id: str, text: str):
    """Sends outgoing text to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    try:
        res = requests.post(url, json=payload)
        print(f"Telegram status: {res.status_code}")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


def generate_workout_coaching(workout_data: dict) -> str:
    """Uses Gemini to generate personalized coaching feedback for a completed workout."""
    if not ai:
        return "⚠️ Gemini API Key not set on Render. Cannot generate AI feedback."

    val = workout_data.get("value", {})
    workout_type = val.get("workoutType", workout_data.get("name", "Workout")).replace("_", " ").title()
    duration_s = val.get("duration_s", 0)
    duration_min = round(duration_s / 60, 1) if duration_s else "N/A"
    calories = round(val.get("totalEnergy_kcal", 0), 1)
    date = workout_data.get("localDate", "N/A")

    prompt = f"""
    You are an expert running and fitness coach. A user just completed a workout.
    
    Workout Details:
    - Activity: {workout_type}
    - Date: {date}
    - Duration: {duration_min} minutes
    - Calories Burned: {calories} kcal
    
    Write a short, engaging, and encouraging breakdown for the athlete:
    1. Acknowledge their effort enthusiastically.
    2. Give 1-2 actionable coaching insights (e.g., recovery tips, hydration, nutrition, or pacing advice based on activity type).
    3. Keep it under 150 words and use clear bullet points.
    """

    try:
        response = ai.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return f"🏋️ Workout Logged: {workout_type} ({duration_min} mins, {calories} kcal). Great job!"


def answer_user_chat(user_message: str) -> str:
    """Handles direct interactive coaching questions from Telegram."""
    if not ai:
        return "AI agent is offline. Please check GEMINI_API_KEY settings."

    prompt = f"""
    You are an expert AI Running & Fitness Coach chatting with your athlete in Telegram.
    
    Athlete's Message: "{user_message}"
    
    Provide helpful, accurate, and encouraging fitness advice. Keep your response concise (2-4 sentences max) for easy reading on mobile.
    """

    try:
        response = ai.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"Gemini Chat Error: {e}")
        return "I am having trouble processing your query right now. Please try again in a moment!"


@app.get("/")
def home():
    return {"status": "online", "agent": "Gemini AI Running Coach"}


@app.post("/webhook/apple-health")
@app.post("/webhook/apple-health/")
async def receive_health_data(request: Request):
    """Processes incoming workout data and sends AI coaching feedback to Telegram."""
    data = await request.json()
    print("Received raw payload:", data)

    # Generate coaching breakdown with Gemini
    ai_feedback = generate_workout_coaching(data)

    target_chat_id = TELEGRAM_CHAT_ID or "8682930690"
    send_telegram_msg(target_chat_id, ai_feedback)

    return {"status": "success"}


@app.post("/webhook/telegram")
@app.post("/webhook/telegram/")
async def handle_telegram_chat(request: Request):
    """Handles incoming interactive messages from the user on Telegram."""
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_text = message.get("text", "")

    if chat_id and user_text:
        ai_reply = answer_user_chat(user_text)
        send_telegram_msg(chat_id, ai_reply)

    return {"status": "ok"}
