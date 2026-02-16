import os
from twilio.rest import Client
from dotenv import load_dotenv

# Load env variables
load_dotenv()

try:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    
    if not sid or not token:
        print("ERROR: credentials not found in .env")
        exit()

    print(f"Authenticating with SID: {sid[:6]}...")
    client = Client(sid, token)

    print("\n--- FETCHING LAST 3 CALLS ---")
    calls = client.calls.list(limit=3)
    
    if not calls:
        print("No calls found in history.")
    
    for c in calls:
        print(f"\nDate: {c.date_created}")
        print(f"SID: {c.sid}")
        print(f"To: {c.to}")
        print(f"From: {c.from_}")
        print(f"Status: {c.status}")
        if c.error_code:
            print(f"ERROR CODE: {c.error_code}")
            print(f"ERROR MSG: {c.error_message}")
        else:
            print("No Error Logged.")
            
except Exception as e:
    print(f"CRITICAL ERROR: {e}")
