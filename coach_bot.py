import json
import os
from fastapi import FastAPI, Request
from google import genai
import requests

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

ai = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Store full raw payload for context
latest_athlete_context = {}


def send_telegram_msg(chat_id: str, text: str):
    """Sends text to Telegram safely without triggering Markdown syntax 400 errors."""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # 1. Try sending with Markdown formatting
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    res = requests.post(url, json=payload)

    # 2. Fallback to Plain Text if Markdown parsing fails (Status 400)
    if res.status_code != 200:
        print(f"Markdown send failed ({res.status_code}). Retrying as plain text...")
        payload_plain = {"chat_id": chat_id, "text": text}
        res_plain = requests.post(url, json=payload_plain)
        print(f"Fallback Status: {res_plain.status_code}")


@app.post("/webhook/apple-health")
@app.post("/webhook/apple-health/")
async def receive_health_data(request: Request):
    global latest_athlete_context
    raw_payload = await request.json()
    latest_athlete_context = raw_payload

    full_data_str = json.dumps(raw_payload, indent=2)

    prompt = f"""
    You are an expert running coach. Here is the COMPLETE raw health export data for a workout:

    ```json
    {full_data_str}
    ```

    Analyze this workout and write a clear, concise coaching breakdown for Telegram.
    """

    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash", contents=prompt
        )
        reply = response.text
    except Exception as e:
        reply = f"Workout logged, but error generating AI analysis: {e}"

    send_telegram_msg(TELEGRAM_CHAT_ID or "8682930690", reply)
    return {"status": "success"}


@app.post("/webhook/telegram")
@app.post("/webhook/telegram/")
async def handle_telegram_chat(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_text = message.get("text", "")

    if chat_id and user_text:
        context_str = (
            json.dumps(latest_athlete_context, indent=2)
            if latest_athlete_context
            else "No active workout logged yet."
        )

        prompt = f"""
        You are an AI Running Coach. Here is your athlete's latest logged session raw data:
        ```json
        {context_str}
        ```

        Athlete's Question: "{user_text}"

        Answer their request directly using the raw workout data context above when relevant. Keep answers concise and mobile-friendly.
        """

        try:
            response = ai.models.generate_content(
                model="gemini-3.6-flash", contents=prompt
            )
            reply = response.text
        except Exception as e:
            reply = "I had trouble generating a reply. Please try again."

        send_telegram_msg(chat_id, reply)

    return {"status": "ok"}
