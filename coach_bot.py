import os
import requests
from fastapi import FastAPI, Request
from google import genai

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Initialize Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def generate_ai_response(user_message: str) -> str:
    if not ai_client:
        return "AI Client not configured. Please set GEMINI_API_KEY on Render."
    
    prompt = f"""
    You are an expert, encouraging AI Running Coach.
    Answer the athlete's question concisely and accurately.
    
    Athlete: {user_message}
    """
    try:
        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"LLM Error: {e}")
        return "Sorry, I had trouble processing your coaching query!"

def send_telegram_msg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

@app.post("/webhook/telegram")
async def handle_telegram_chat(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_text = message.get("text", "")
    
    if chat_id and user_text:
        # Get response from the AI model
        ai_reply = generate_ai_response(user_text)
        send_telegram_msg(chat_id, ai_reply)
        
    return {"status": "ok"}
