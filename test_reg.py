import requests
from requests.auth import HTTPBasicAuth
import os 
from dotenv import load_dotenv
import base64

load_dotenv()

# ====== CONFIG ======
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET")
MPESA_AUTH_URL = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"


# Use sandbox or production
BASE_URL = "https://api.safaricom.co.ke"  # for production

SHORT_CODE = os.getenv("MPESA_SHORT_CODE")  # your paybill/till number

CONFIRMATION_URL = "https://api.ryfty.net/pesagrid-api/api/v1/ingest/a2d70695-f572-470c-8a48-256b4ee79317/c2b/callback"

# ====== STEP 1: GET ACCESS TOKEN ======
def get_mpesa_auth_token():
    auth = base64.b64encode(f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode("utf-8")).decode("utf-8")
    headers = {"Authorization": f"Basic {auth}"}
    # print(auth)
    try:
        response = requests.get(MPESA_AUTH_URL, headers=headers)
        if response.status_code == 200:
            return response.json().get("access_token")
        print(f"Failed to get auth token, status code: {response.status_code}, response: {response.text}")
        return None
    except Exception as e:
        print(f"Exception while fetching auth token: {e}")
        return None

# ====== STEP 2: REGISTER URL ======
def register_urls():
    access_token = get_mpesa_auth_token()
    print(access_token)
    
    url = f"https://api.safaricom.co.ke/mpesa/c2b/v2/registerurl"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    print(SHORT_CODE)
    
    payload = {
        "ShortCode": SHORT_CODE,
        "ResponseType": "Completed",  # or "Cancelled"
        "ConfirmationURL": CONFIRMATION_URL,
        "ValidationURL": "https://api.ryfty.net/pesagrid-api/api/v1/ingest/a2d70695-f572-470c-8a48-256b4ee79317/c2b/validate"
    }
    
    response = requests.post(url, json=payload, headers=headers)
    
    print("Status Code:", response.status_code)
    print("Response:", response.json())


# ====== RUN ======
if __name__ == "__main__":
    register_urls()