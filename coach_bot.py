import os
import requests
from fastapi import FastAPI, Request

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def send_telegram_msg(chat_id, text):
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing!")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    res = requests.post(url, json=payload)
    print(f"Telegram API Status: {res.status_code}")

@app.get("/")
def home():
    return {"status": "online"}

# Route handlers with trailing slash support
@app.post("/webhook/apple-health")
@app.post("/webhook/apple-health/")
async def receive_health_data(request: Request):
    data = await request.json()
    print("Received raw payload:", data)
    
    val = data.get("value", {})
    workout_type = val.get("workoutType", data.get("name", "Workout")).replace("_", " ").title()
    
    duration_s = val.get("duration_s", 0)
    duration_min = round(duration_s / 60, 1) if duration_s else "N/A"
    calories = round(val.get("totalEnergy_kcal", 0), 1)
    date = data.get("localDate", "N/A")
    
    # Clean non-breaking spaces from source name
    device = str(data.get("source", "Apple Watch")).replace('\xa0', ' ')
    
    msg = (
        f"🏋️‍♂️ New Activity Logged!\n\n"
        f"• Type: {workout_type}\n"
        f"• Date: {date}\n"
        f"• Duration: {duration_min} mins\n"
        f"• Calories: {calories} kcal\n"
        f"• Source: {device}\n\n"
        f"Great effort on your session!"
    )
    
    if TELEGRAM_CHAT_ID:
        send_telegram_msg(TELEGRAM_CHAT_ID, msg)
        
    return {"status": "success"}
        
    return {"status": "success"}

@app.post("/webhook/telegram")
@app.post("/webhook/telegram/")
async def handle_telegram_chat(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_text = message.get("text", "")
    
    if chat_id and user_text:
        reply = f"🤖 AI Coach Received: '{user_text}'"
        send_telegram_msg(chat_id, reply)
        
    return {"status": "ok"}
