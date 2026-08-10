import os
from fastapi import FastAPI, Request
import requests

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})

@app.post("/webhook/apple-health")
async def receive_health_data(request: Request):
    data = await request.json()
    
    # Extract metrics from HealthMirror schema
    workout_type = data.get("name", "Workout")
    duration = data.get("duration", "N/A")
    distance = data.get("totalDistance", "N/A")
    
    # Generate your AI Coach summary / feedback here
    coaching_message = f"🏃‍♂️ *Workout Received!*\n\nActivity: {workout_type}\nDistance: {distance}\nDuration: {duration}\n\nGreat job sticking to your plan!"
    
    # Push notification straight to Telegram
    send_telegram_msg(coaching_message)
    
    return {"status": "success"}
