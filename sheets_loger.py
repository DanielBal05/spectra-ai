import os
import datetime as dt
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def _client():
    creds_path = os.getenv("GOOGLE_CREDS_JSON", "credentials.json")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)

def append_log(event, user="", intent="", transcript="", answer="", source="fastapi", extra=""):
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    tab = os.getenv("GOOGLE_SHEET_TAB", "Logs").strip()

    if not sheet_id:
        raise RuntimeError("Falta GOOGLE_SHEET_ID en .env")

    ws = _client().open_by_key(sheet_id).worksheet(tab)
    ts = dt.datetime.now().isoformat(timespec="seconds")

    ws.append_row(
        [ts, event, user, intent, transcript, answer, source, extra],
        value_input_option="USER_ENTERED"
    )