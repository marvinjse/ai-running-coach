from fastapi import FastAPI, Request
from google import genai
import requests
import os

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
# 1. RECEIVE AUTOMATIC APPLE WATCH RUNS
# ==========================================
@app.post("/webhook/apple-health")
async def receive_health_data(request: Request):
    global latest_run_context
    data = await request.json()
    
    try:
        workout = data.get("data", {}).get("workouts", [])[0]
        distance_miles = round(workout.get("distance", 0) * 0.000621371, 2)
        duration_mins = round(workout.get("duration", 0) / 60.0, 1)
        
        latest_run_context = f"User just completed {distance_miles} miles in {duration_mins} minutes."
        return {"status": "success"}
    except Exception as e:
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
            model="gemini-2.5-flash",
            contents=full_prompt
        )
        
        # 2. Send the reply back to the user via Telegram
        payload = {
            "chat_id": chat_id,
            "text": ai_response.text
        }
        requests.post(TELEGRAM_API_URL, json=payload)

    return {"status": "success"}
