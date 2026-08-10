from fastapi import FastAPI, Form, Request
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os

app = FastAPI(title="Interactive AI Running Coach")

# --- CONTEXT MEMORY ---
# We store your latest run data here so the AI remembers it when you chat!
latest_run_context = "No recent runs logged."

# Set your OpenAI API Key (replace with your actual key)
openai.api_key = "sk-YOUR_OPENAI_API_KEY"

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
        
        # Save this to memory so the AI knows your stats when you text it later
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
    """
    Twilio hits this endpoint whenever you send a message to the bot on WhatsApp.
    'Body' contains the text you typed on your phone.
    """
    user_message = Body
    print(f"Received WhatsApp message: {user_message}")

    # Build the prompt with your training context
    system_prompt = f"""
    You are an expert running coach. 
    Your client is working on knee stability, glute strength, and doing 30/30 interval runs.
    Context of their latest run: {latest_run_context}
    Answer their questions concisely and supportively.
    """

    # Ask OpenAI for advice
    ai_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        temperature=0.7
    )
    
    coach_reply_text = ai_response.choices[0].message.content

    # Format the response for Twilio
    twiml_response = MessagingResponse()
    twiml_response.message(coach_reply_text)

    # Return XML back to Twilio so it sends the WhatsApp text
    return Response(content=str(twiml_response), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
