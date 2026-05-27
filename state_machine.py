"""
State Machine for WhatsApp Bot Conversations
Manages user sessions and conversation flow states.
"""
import uuid
from datetime import datetime


# ── State Constants ─────────────────────────────────────────────────────
LANG_SELECT = "LANG_SELECT"
COLLECT_USER_NAME = "COLLECT_USER_NAME"
COLLECT_USER_STATE = "COLLECT_USER_STATE"
COLLECT_USER_VILLAGE = "COLLECT_USER_VILLAGE"
COLLECT_USER_CONTACT = "COLLECT_USER_CONTACT"
COLLECT_USER_EMAIL = "COLLECT_USER_EMAIL"
CATEGORY_MENU = "CATEGORY_MENU"
MAIN_CATEGORY_MENU = "MAIN_CATEGORY_MENU"
PRODUCT_MENU = "PRODUCT_MENU"
SERVICE_MENU = "SERVICE_MENU"
MORE_OPTIONS_MENU = "MORE_OPTIONS_MENU"
AWAITING_AI_INPUT = "AWAITING_AI_INPUT"
FAQ_LIST = "FAQ_LIST"
FAQ_ANSWER = "FAQ_ANSWER"
COLLECT_DESCRIPTION = "COLLECT_DESCRIPTION"
COLLECT_OPPOSITE_NAME = "COLLECT_OPPOSITE_NAME"
COLLECT_OPPOSITE_ADDRESS = "COLLECT_OPPOSITE_ADDRESS"
COLLECT_OPPOSITE_PHONE = "COLLECT_OPPOSITE_PHONE"
COLLECT_OPPOSITE_EMAIL = "COLLECT_OPPOSITE_EMAIL"
COLLECT_MONETARY = "COLLECT_MONETARY"
COLLECT_DOCS = "COLLECT_DOCS"
SUMMARY = "SUMMARY"
EDIT_FIELD = "EDIT_FIELD"
CONFIRMED = "CONFIRMED"


class UserSession:
    """Tracks all data for one user's complaint filing journey."""

    def __init__(self):
        self.state = LANG_SELECT
        self.lang = None  # e.g. "lang_english"
        self.category = None
        self.selected_question = None

        # Personal details
        self.user_name = None
        self.user_state = None
        self.user_village = None
        self.user_contact = None
        self.user_email = None

        # Optional location fields (kept for legacy/compatibility with email + sheets exports).
        # These are no longer collected via the chatbot, so they remain None and appear empty
        # in the Excel sheet/email — but their presence prevents AttributeError downstream.
        self.user_district = None
        self.user_taluka = None

        # Complaint details
        self.complaint_description = None
        self.opposite_party_name = None
        self.opposite_party_address = None
        self.opposite_party_phone = None
        self.opposite_party_email = None
        self.monetary_amount = None

        # Documents (list of dicts: {"media_id": ..., "mime_type": ..., "filename": ...})
        self.documents = []

        # Metadata
        self.ticket_id = None
        self.created_at = datetime.now().isoformat()

        # Edit tracking
        self.edit_field = None  # which field is being edited

    def generate_ticket_id(self) -> str:
        """Generate a unique ticket ID."""
        short_id = uuid.uuid4().hex[:8].upper()
        self.ticket_id = f"CERC-{short_id}"
        return self.ticket_id

    def get_summary_text(self) -> str:
        """Return a formatted summary of all collected complaint data."""
        lang_map = {
            "lang_english": "English", "lang_hindi": "Hindi",
            "lang_gujarati": "Gujarati", "lang_marathi": "Marathi"
        }
        docs_str = f"{len(self.documents)} document(s) attached" if self.documents else "None"

        return (
            f"📋 *Complaint Summary*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Your Details:*\n"
            f"   Name: {self.user_name}\n"
            f"   State: {self.user_state}\n"
            f"   City/Village/Town: {self.user_village}\n"
            f"   Contact: {self.user_contact}\n"
            f"   Email: {self.user_email}\n\n"
            f"🗂 *Category:* {self.category}\n"
            f"🌐 *Language:* {lang_map.get(self.lang, self.lang)}\n\n"
            f"📝 *Description:*\n{self.complaint_description}\n\n"
            f"🏢 *Opposite Party:*\n"
            f"   Name: {self.opposite_party_name}\n"
            f"   Address: {self.opposite_party_address}\n"
            f"   Phone: {self.opposite_party_phone or 'N/A'}\n"
            f"   Email: {self.opposite_party_email or 'N/A'}\n\n"
            f"💰 *Monetary Amount:* {self.monetary_amount or 'N/A'}\n"
            f"📎 *Documents:* {docs_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Please review and confirm."
        )

    def get_editable_fields(self) -> list:
        """Return list of fields that can be edited."""
        return [
            ("edit_user_name", "Your Name"),
            ("edit_user_state", "Your State"),
            ("edit_user_village", "Your City/Village/Town"),
            ("edit_user_contact", "Your Contact Info"),
            ("edit_user_email", "Your Email"),
            ("edit_desc", "Complaint Description"),
            ("edit_opp_name", "Opposite Party Name"),
            ("edit_opp_addr", "Opposite Party Address"),
            ("edit_opp_phone", "Opposite Party Phone"),
            ("edit_opp_email", "Opposite Party Email"),
            ("edit_monetary", "Monetary Amount"),
        ]


# ── Session Store ───────────────────────────────────────────────────────
# In-memory store. Resets on server restart.
_sessions: dict[str, UserSession] = {}


def get_session(phone: str) -> UserSession:
    """Get or create a session for a phone number."""
    if phone not in _sessions:
        _sessions[phone] = UserSession()
    return _sessions[phone]


def reset_session(phone: str):
    """Reset a user's session (start fresh)."""
    _sessions[phone] = UserSession()
    return _sessions[phone]
