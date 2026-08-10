import os
from fastapi import FastAPI, Request
import requests

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_msg(chat_id, text):
    """Utility to send messages back to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

# 1. Receiver for your Health Data from Ubuntu/iCloud
@app.post("/webhook/apple-health")
async def receive_health_data(request: Request):
    data = await request.json()
    
    workout_type = data.get("name", "Workout")
    distance = data.get("totalDistance", "N/A")
    duration = data.get("duration", "N/A")
    
    msg = f"🏃‍♂️ *Workout Synced!*\n\nActivity: {workout_type}\nDistance: {distance}\nDuration: {duration}\n\nAsk me anything about your run!"
    
    # Send proactive update to your chat ID
    if TELEGRAM_CHAT_ID:
        send_telegram_msg(TELEGRAM_CHAT_ID, msg)
        
    return {"status": "success"}

# 2. Interactive Receiver for Telegram Chat Messages
@app.post("/webhook/telegram")
async def handle_telegram_chat(request: Request):
    data = await request.json()
    
    # Parse incoming Telegram message
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_text = message.get("text", "")
    
    if chat_id and user_text:
        # Simple test response (Replace this logic with your AI/LLM call)
        if user_text.lower() == "/start":
            reply = "Hello! I am your AI Running Coach. Send me a message or sync a workout to get started!"
        else:
            reply = f"🤖 AI Coach Received: '{user_text}'\n\n(Connect your AI logic here to answer questions based on your logged workouts!)"
            
        send_telegram_msg(chat_id, reply)
        
    return {"status": "ok"}
