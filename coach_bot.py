from fastapi import FastAPI, Form, Request
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
import os

app = FastAPI(title="Interactive AI Running Coach")

# --- CONTEXT MEMORY ---
latest_run_context = "No recent runs logged."

# Initialize the NEW Gemini Client using an environment variable
# The client automatically picks up the GEMINI_API_KEY environment variable!
client = genai.Client()

# ==========================================
# NEW: FIX FOR THE 404 ERROR
# ==========================================
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
        
        print("Run logged successfully!")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# 2. TWO-WAY WHATSAPP CHAT (TWILIO WEBHOOK)
# ==========================================
@app.post("/webhook/whatsapp")
async def whatsapp_reply(Body: str = Form(...)):
    user_message = Body
    print(f"Received WhatsApp message: {user_message}")

    system_prompt = f"""
    You are an expert running coach. 
    Your client is working on knee stability, glute strength, and doing 30/30 interval runs.
    Context of their latest run: {latest_run_context}
    Answer their questions concisely and supportively.
    """

    # Ask Gemini for advice using the new SDK format
    full_prompt = f"{system_prompt}\n\nUser asks: {user_message}"
    
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=full_prompt
    )
    
    coach_reply_text = response.text

    # Format the response for Twilio
    twiml_response = MessagingResponse()
    twiml_response.message(coach_reply_text)

    return Response(content=str(twiml_response), media_type="application/xml")
