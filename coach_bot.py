from fastapi import FastAPI, Request
from google import genai
import requests
import os
import re # Add this to the very top of your file with your other imports!

app = FastAPI(title="Interactive AI Running Coach")

# --- CONTEXT MEMORY ---
latest_run_context = "No recent runs logged."

# API Keys from Render Environment
genai_client = genai.Client()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

@app.get("/")
async def root():
    return {"message": "🏃 AI Running Coach Server is LIVE!"}

# ==========================================
# 1. RECEIVE AUTOMATIC APPLE WATCH RUNS (RAW DATA VERSION)
# ==========================================
@app.post("/webhook/apple-health")
async def receive_health_data(request: Request):
    global latest_run_context
    try:
        data = await request.json()
        
        distance = data.get("distance", "0")
        duration = data.get("duration", "0")
        
        # Grab the raw list of heart rate samples
        raw_heart_rates = data.get("heart_rates", [])
        
        hr_stats = ""
        if raw_heart_rates:
            # Apple Shortcuts sends things like "145 bpm". We extract just the numbers.
            hr_values = []
            for hr in raw_heart_rates:
                match = re.search(r'\d+', str(hr))
                if match:
                    hr_values.append(int(match.group()))
            
            if hr_values:
                avg_hr = round(sum(hr_values) / len(hr_values))
                max_hr = max(hr_values)
                hr_stats = f" Average HR was {avg_hr} bpm, peaking at {max_hr} bpm."
        
        latest_run_context = f"User just completed {distance} in {duration}.{hr_stats}"
        print(f"Run logged with raw data: {latest_run_context}")
        
        return {"status": "success"}
    except Exception as e:
        print(f"Error parsing health data: {e}")
        return {"status": "error", "message": str(e)}
# ==========================================
# 2. TWO-WAY TELEGRAM CHAT
# ==========================================
@app.post("/webhook/telegram")
async def telegram_reply(request: Request):
    data = await request.json()
    
    # Telegram sends a JSON payload. We extract the chat ID and the message text.
    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        user_message = data["message"]["text"]
        
        system_prompt = f"""
        You are an expert running coach. 
        Your client is working on knee stability, glute strength, and doing 30/30 interval runs.
        Context of their latest run: {latest_run_context}
        Answer their questions concisely and supportively.
        """
        
        # 1. Ask Gemini for advice
        full_prompt = f"{system_prompt}\n\nUser asks: {user_message}"
        ai_response = genai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=full_prompt
        )
        
        # 2. Send the reply back to the user via Telegram
        payload = {
            "chat_id": chat_id,
            "text": ai_response.text
        }
        requests.post(TELEGRAM_API_URL, json=payload)

    return {"status": "success"}
