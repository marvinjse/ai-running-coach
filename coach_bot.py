import os
import requests
from fastapi import FastAPI, Request

app = FastAPI()

# Clean environment variables (strips any accidental spaces or hidden line breaks)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def send_telegram_msg(chat_id, text):
    """Sends a message back to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN environment variable is missing on Render!")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    res = requests.post(url, json=payload)
    print(f"Outgoing Telegram HTTP Status: {res.status_code}")
    print(f"Telegram API Response: {res.text}")

# 1. Web Browser Homepage (so visiting your Render URL in browser doesn't 404)
@app.get("/")
def home():
    return {"status": "online", "message": "AI Running Coach API is active"}

# 2. Webhook for Apple Health / HealthMirror data
@app.post("/webhook/apple-health")
async def receive_health_data(request: Request):
    data = await request.json()
    
    workout_type = data.get("name", "Workout")
    distance = data.get("totalDistance", "N/A")
    duration = data.get("duration", "N/A")
    
    msg = f"🏃‍♂️ *Workout Synced!*\n\nActivity: {workout_type}\nDistance: {distance}\nDuration: {duration}"
    
    if TELEGRAM_CHAT_ID:
        send_telegram_msg(TELEGRAM_CHAT_ID, msg)
        
    return {"status": "success"}

# 3. Webhook for Telegram Chat Messages
@app.post("/webhook/telegram")
async def handle_telegram_chat(request: Request):
    data = await request.json()
    
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_text = message.get("text", "")
    
    if chat_id and user_text:
        reply = f"🤖 AI Coach Received: '{user_text}'"
        send_telegram_msg(chat_id, reply)
        
    return {"status": "ok"}
