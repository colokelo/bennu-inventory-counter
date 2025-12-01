from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv
from datetime import date
import os
import json
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path

# Load environment variables from .env (SHEET_ID, SERVICE_ACCOUNT_FILE or SERVICE_ACCOUNT_JSON)
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://bennu-inventory-counter-production.up.railway.app",  # Railway frontend
        "null",  # allows file:// origins when you open index.html from a phone/laptop
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config
SHEET_ID = os.getenv("SHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("SERVICE_ACCOUNT_JSON")   # Railway will use this
SERVICE_FILE = os.getenv("SERVICE_ACCOUNT_FILE")           # Local dev file option
TAB_NAME = "Input_Counts"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
ITEM_MASTER_RANGE = "Item_Master!B2:B"   # Official_Item_Name column
SHARED_PIN = os.getenv("SHARED_PIN")     # simple shared PIN for submit_count

# Path to index.html next to this file
BASE_DIR = Path(__file__).resolve().parent
INDEX_PATH = BASE_DIR / "index.html"

class CountPayload(BaseModel):
    counter_name: str
    store_name: str
    sub_location: str
    item_name: str
    condition: str
    qty: float


def get_sheets_service():
    """Build a Sheets API client from:
       1) SERVICE_ACCOUNT_JSON (Railway/Render)
       2) SERVICE_ACCOUNT_FILE (local)
    """
    if not SHEET_ID:
        raise RuntimeError("SHEET_ID env var is not set")

    creds = None

    # --- OPTION 1: Railway/Render — JSON stored in env var ---
    if SERVICE_ACCOUNT_JSON:
        try:
            info = json.loads(SERVICE_ACCOUNT_JSON)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load SERVICE_ACCOUNT_JSON: {e}")

    # --- OPTION 2: Local file development ---
    elif SERVICE_FILE:
        sa_path = SERVICE_FILE
        if not os.path.isabs(sa_path):
            sa_path = os.path.join(os.path.dirname(__file__), SERVICE_FILE)

        if not os.path.exists(sa_path):
            raise RuntimeError(f"Service account file not found: {sa_path}")

        creds = service_account.Credentials.from_service_account_file(
            sa_path, scopes=SCOPES
        )

    else:
        raise RuntimeError("No service account credentials configured.")

    return build("sheets", "v4", credentials=creds)


@app.get("/", response_class=HTMLResponse)
def serve_index():
    """
    Serve the Bennu Agriworks Inventory Counter UI (index.html)
    so counters can just open the Railway URL.
    """
    return INDEX_PATH.read_text(encoding="utf-8")


@app.get("/health")
def health():
    """Simple JSON health check endpoint."""
    return {"status": "running"}



@app.get("/items")
def get_items():
    """Returns item names from Item_Master for dropdown/autocomplete."""
    try:
        service = get_sheets_service()
        resp = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=ITEM_MASTER_RANGE,
        ).execute()

        values = resp.get("values", [])
        items = [row[0] for row in values if row and row[0].strip()]
        return {"items": items}

    except Exception as e:
        print("Error loading items from Item_Master:", e)
        return {"items": []}


@app.post("/submit_count")
def submit_count(
    payload: CountPayload,
    x_shared_pin: str | None = Header(default=None, alias="X-Shared-Pin"),
):
    """Append a single count row to Input_Counts."""
    # 1) Simple shared PIN check
    if SHARED_PIN and x_shared_pin != SHARED_PIN:
        raise HTTPException(status_code=401, detail="Invalid PIN")

    # 2) Sheets client
    try:
        service = get_sheets_service()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Config error: {e}")


    today = date.today().isoformat()

    values = [[
        payload.counter_name,
        payload.store_name,
        payload.sub_location,
        today,
        payload.item_name,
        payload.condition,
        payload.qty,
    ]]

    body = {"values": values}

    try:
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f"{TAB_NAME}!A:G",
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sheets append failed: {e}")

    return {"status": "ok", "received": payload.dict()}
