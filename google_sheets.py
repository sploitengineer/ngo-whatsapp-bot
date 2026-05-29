import os
import json
import logging
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def append_complaint_to_sheet(sheet_name: str, session, ticket_id: str):
    """
    Appends a new row to the specified Google Sheet.
    Uses GOOGLE_CREDENTIALS_JSON from env, or falls back to local file.
    """
    try:
        env_creds = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if env_creds:
            logger.info("Loading Google Credentials from Environment Variable")
            creds_info = json.loads(env_creds)
            creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        else:
            logger.info("Loading Google Credentials from google_credentials.json file")
            creds = Credentials.from_service_account_file("google_credentials.json", scopes=SCOPES)

        client = gspread.authorize(creds)
        sheet = client.open(sheet_name).sheet1

        # Check if headers exist, if not, append them
        try:
            first_row = sheet.row_values(1)
        except Exception:
            first_row = []
            
        if not first_row:
            headers = [
                "Ticket ID", "Timestamp", "Category", 
                "User Name", "User Phone", "User Email", 
                "District", "Taluka", "Village/City",
                "Opposite Party Name", "Opposite Party Phone", 
                "Opposite Party Email", "Opposite Party Address",
                "Claim Amount",
                "Complaint Description",
                "Documents Count"
            ]
            sheet.append_row(headers)

        row_data = [
            ticket_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session.category or "N/A",
            session.user_name or "N/A",
            session.user_contact or "N/A",
            session.user_email or "N/A",
            session.user_district or "N/A",
            session.user_taluka or "N/A",
            session.user_village or "N/A",
            session.opposite_party_name or "N/A",
            session.opposite_party_phone or "N/A",
            session.opposite_party_email or "N/A",
            session.opposite_party_address or "N/A",
            session.monetary_amount or "N/A",
            session.complaint_description or "N/A",
            str(len(session.documents))
        ]
        
        sheet.append_row(row_data)
        logger.info(f"Successfully appended complaint {ticket_id} to Google Sheet.")
        return True
    
    except Exception as e:
        logger.error(f"Failed to append to Google Sheet: {e}", exc_info=True)
        return False
