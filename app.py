"""
WhatsApp Webhook Server for NGO Chatbot
Complete Complaint Filing System
Meta Cloud API Integration
"""

from flask import Flask, request, jsonify
import requests
import os
import json
import threading
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Local modules
from complaint_data import CATEGORIES, CATEGORY_QUESTIONS, get_dummy_answer, get_questions_for_category
from state_machine import (
    get_session, reset_session, UserSession,
    LANG_SELECT, CATEGORY_MENU, MAIN_CATEGORY_MENU, PRODUCT_MENU, SERVICE_MENU,
    AWAITING_AI_INPUT, FAQ_LIST, FAQ_ANSWER, MORE_OPTIONS_MENU,
    COLLECT_USER_NAME, COLLECT_USER_STATE,
    COLLECT_USER_VILLAGE, COLLECT_USER_CONTACT, COLLECT_USER_EMAIL,
    COLLECT_DESCRIPTION, COLLECT_OPPOSITE_NAME, COLLECT_OPPOSITE_ADDRESS,
    COLLECT_OPPOSITE_PHONE, COLLECT_OPPOSITE_EMAIL, COLLECT_MONETARY,
    COLLECT_DOCS, SUMMARY, EDIT_FIELD, CONFIRMED,
)
from email_service import send_complaint_email

app = Flask(__name__)

# ── Configuration ───────────────────────────────────────────────────────
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_API_URL = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"

# Groq Client
from groq import Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ── Message Deduplication ───────────────────────────────────────────────
# WhatsApp retries webhooks on 500 errors, causing duplicate messages.
# We track seen message IDs to skip duplicates.
SEEN_MESSAGES = {}
DEDUP_WINDOW = 60  # seconds — ignore duplicate IDs within this window

def is_duplicate(msg_id: str) -> bool:
    """Check if we've already processed this message ID."""
    now = time.time()
    # Clean old entries
    expired = [k for k, v in SEEN_MESSAGES.items() if now - v > DEDUP_WINDOW]
    for k in expired:
        del SEEN_MESSAGES[k]
    # Check
    if msg_id in SEEN_MESSAGES:
        return True
    SEEN_MESSAGES[msg_id] = now
    return False


# ── Shared helpers ──────────────────────────────────────────────────────
def _api_headers():
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }


def send_whatsapp_message(to: str, text: str):
    """Send a plain text message via WhatsApp API."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    try:
        response = requests.post(WHATSAPP_API_URL, headers=_api_headers(), json=payload)
        response.raise_for_status()
        print(f"✅ Message sent to {to}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending message: {e}")
        if e.response is not None:
            print(f"   Response details: {e.response.text}")
        return None


def send_image_message(to: str, image_url: str, caption: str = None):
    """Send an image via WhatsApp API."""
    image_obj = {"link": image_url}
    if caption:
        image_obj["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": image_obj
    }
    try:
        response = requests.post(WHATSAPP_API_URL, headers=_api_headers(), json=payload)
        response.raise_for_status()
        print(f"✅ Image sent to {to}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending image: {e}")
        if e.response is not None:
            print(f"   Response details: {e.response.text}")
        return None



def send_interactive_list(to: str, header: str, body: str, footer: str,
                          button: str, sections: list):
    """Generic helper to send an Interactive List Message."""
    interactive_obj = {
        "type": "list",
        "body": {"text": body},
        "action": {"button": button, "sections": sections}
    }
    if header:
        interactive_obj["header"] = {"type": "text", "text": header}
    
    if footer:
        interactive_obj["footer"] = {"text": footer}

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive_obj
    }
    try:
        response = requests.post(WHATSAPP_API_URL, headers=_api_headers(), json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending interactive list: {e}")
        if e.response is not None:
            print(f"   Response details: {e.response.text}")
        return None


def send_interactive_buttons(to: str, body: str, buttons: list):
    """Send Interactive Reply Buttons (max 3 buttons)."""
    btn_list = []
    for btn_id, title in buttons:
        btn_list.append({
            "type": "reply",
            "reply": {"id": btn_id, "title": title[:20]}
        })
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": btn_list}
        }
    }
    try:
        response = requests.post(WHATSAPP_API_URL, headers=_api_headers(), json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending buttons: {e}")
        if e.response is not None:
            print(f"   Response details: {e.response.text}")
        return None


# ── Translation Helper ──────────────────────────────────────────────────
TRANSLATION_CACHE = {}

def translate_text(text: str, target_lang_id: str) -> str:
    if target_lang_id == "lang_english" or not target_lang_id or not groq_client:
        return text
    
    cache_key = f"{target_lang_id}:{text}"
    if cache_key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[cache_key]
        
    lang_map = {
        "lang_hindi": "Hindi",
        "lang_gujarati": "Gujarati",
        "lang_marathi": "Marathi"
    }
    target_lang = lang_map.get(target_lang_id, "English")
    if target_lang == "English":
        return text
        
    try:
        completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": f"You are a professional translator. Translate the following text to {target_lang}. Preserve all formatting, emojis. ONLY output the translation, nothing else, without quotes."},
                {"role": "user", "content": text}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1
        )
        translated = completion.choices[0].message.content.strip()
        if translated.startswith('"') and translated.endswith('"'):
            translated = translated[1:-1]
        TRANSLATION_CACHE[cache_key] = translated
        return translated
    except Exception as e:
        print(f"❌ Translation error: {e}")
        return text

def safe_truncate(text: str, length: int) -> str:
    if len(text) <= length:
        return text
    return text[:length-1] + "…"

# ── Language Selection ──────────────────────────────────────────────────
LANGUAGE_CONFIRMATIONS = {
    "lang_english":  "✅ Continuing in English.",
    "lang_hindi":    "✅ हिन्दी में जारी है।",
    "lang_gujarati": "✅ ગુજરાતીમાં ચાલુ રાખી રહ્યા છીએ.",
    "lang_marathi":  "✅ मराठीत सुरू ठेवत आहे.",
}


def send_language_selection(to: str):
    """Send language chooser list."""
    logo_url = f"{request.host_url}static/cerc_logo.png"
    caption_text = "*Welcome to CERC Complaints Desk*\nWe are here to assist you with your Complaints"
    send_image_message(to, logo_url, caption=caption_text)

    # Delay to ensure the heavier image message is delivered first by WhatsApp
    time.sleep(1)

    rows = [
        {"id": "lang_english", "title": "English"},
        {"id": "lang_hindi", "title": "हिन्दी"},
        {"id": "lang_gujarati", "title": "ગુજરાતી"},
        {"id": "lang_marathi", "title": "मराठी"},
    ]
    
    body_text = "Please choose your preferred language to continue:"
    
    send_interactive_list(
        to,
        header="",
        body=body_text,
        footer="",
        button="Choose Language",
        sections=[{"title": "Languages", "rows": rows}]
    )


PRODUCT_CATEGORIES = [
    "Automobile", "Electrical Home Appliances", "Solar Panel",
    "Mobile Handset & Electronics", "E- Commerce", "Weight, Size & Description"
]
SERVICE_CATEGORIES = [
    "Airline", "Banking (Credit Card, Investments, and loans)", 
    "Insurances (Life, Health, Travel, Motor, etc.)", "Internet, Mobile & telecom services", 
    "Education", "Taxi, Bus, Railway", "Tour Packages (Tour, Vacation Club, and Hotel)",
    "Real Estate", "Post & courier", "LPG & Gas", "Electricity Company", "Advocate & Court Procedure"
]

def send_main_category_menu(to: str, session: UserSession):
    buttons = [
        ("menu_product", safe_truncate(translate_text("📦 Product", session.lang), 20)),
        ("menu_service", safe_truncate(translate_text("🛠️ Service", session.lang), 20)),
        ("menu_more", safe_truncate(translate_text("⚙️ More Options", session.lang), 20)),
    ]
    send_interactive_buttons(
        to, 
        translate_text("Select Categories:", session.lang), 
        buttons
    )

def send_more_options_menu(to: str, session: UserSession):
    buttons = [
        ("menu_choose", safe_truncate(translate_text("📂 All Categories", session.lang), 20)),
        ("cat_describe", safe_truncate(translate_text("📝 File Complaint", session.lang), 20)),
        ("menu_back_lang", safe_truncate(translate_text("🔙 Back", session.lang), 20)),
    ]
    send_interactive_buttons(
        to, 
        translate_text("More Options:", session.lang), 
        buttons
    )

def send_product_menu(to: str, session: UserSession):
    rows = []
    for cat in PRODUCT_CATEGORIES:
        clean = cat.replace(" ", "_").replace("&", "n")[:20]
        rows.append({"id": f"cat_{clean}", "title": safe_truncate(translate_text(cat, session.lang), 24)})
    rows.append({"id": "cat_describe", "title": safe_truncate(translate_text("📝 File Complaint", session.lang), 24)})
    rows.append({"id": "menu_back_main", "title": safe_truncate(translate_text("🔙 Main Menu", session.lang), 24)})
    send_interactive_list(
        to,
        header="",
        body=translate_text("Product Categories:", session.lang),
        footer="",
        button=translate_text("Select Product", session.lang),
        sections=[{"title": translate_text("Products", session.lang), "rows": rows}]
    )

def send_service_menu(to: str, session: UserSession, page: int = 0):
    page_size = 7
    start = page * page_size
    end = start + page_size
    page_cats = SERVICE_CATEGORIES[start:end]
    
    rows = []
    for cat in page_cats:
        clean = cat.replace(" ", "_").replace("&", "n")[:20]
        rows.append({"id": f"cat_{clean}", "title": safe_truncate(translate_text(cat, session.lang), 24)})
    
    opt_rows = []
    if page > 0:
        opt_rows.append({"id": f"serv_page_{page-1}", "title": safe_truncate(translate_text("⬅️ Previous", session.lang), 24)})
    if end < len(SERVICE_CATEGORIES):
        opt_rows.append({"id": f"serv_page_{page+1}", "title": safe_truncate(translate_text("➡️ Next Options", session.lang), 24)})
    
    opt_rows.append({"id": "cat_describe", "title": safe_truncate(translate_text("📝 File Complaint", session.lang), 24)})
    opt_rows.append({"id": "menu_back_main", "title": safe_truncate(translate_text("🔙 Main Menu", session.lang), 24)})
    
    sections = [{"title": translate_text("Services", session.lang), "rows": rows}]
    if opt_rows:
        sections.append({"title": translate_text("Navigation", session.lang), "rows": opt_rows})

    send_interactive_list(
        to,
        header="",
        body=translate_text("Service Categories:", session.lang),
        footer="",
        button=translate_text("Select Service", session.lang),
        sections=sections
    )

# ── Category Menu & Pagination ───────────────────────────────────────────
def send_category_menu(to: str, session: UserSession, page: int = 0):
    """
    Paginated category menu (max 10 rows per message).
    Each page contains up to 7 categories + 3 utility options.
    """
    page_size = 7
    start = page * page_size
    end = start + page_size
    page_cats = CATEGORIES[start:end]
    total_pages = (len(CATEGORIES) + page_size - 1) // page_size

    rows = []
    # 1. Add categories
    for cat in page_cats:
        clean = cat.replace(" ", "_").replace("&", "n")[:20]
        cat_t = translate_text(cat, session.lang)
        rows.append({
            "id": f"cat_{clean}",
            "title": safe_truncate(cat_t, 24)
        })

    option_rows = []
    
    # 2. Add 'Describe Issue' option - Always available
    option_rows.append({
        "id": "cat_describe", 
        "title": safe_truncate(translate_text("✍️ Describe Issue", session.lang), 24)
    })

    # 3. Add Previous and Next page navigation
    if page > 0:
        option_rows.append({
            "id": f"cat_page_{page-1}",
            "title": safe_truncate(translate_text("⬅️ Previous", session.lang), 24)
        })
    else:
        # If no previous page, add "Change Language" instead to consume a slot
        option_rows.append({
            "id": "cat_change_lang",
            "title": safe_truncate(translate_text("🌍 Language", session.lang), 24)
        })

    if end < len(CATEGORIES):
        option_rows.append({
            "id": f"cat_page_{page+1}",
            "title": safe_truncate(translate_text("➡️ Next Options", session.lang), 24)
        })
    elif page > 0 and len(option_rows) < 3:
        # If it's the last page, we can show "Change Language" to fill the empty slot
        option_rows.append({
            "id": "cat_change_lang",
            "title": safe_truncate(translate_text("🌍 Language", session.lang), 24)
        })

    header_t = safe_truncate(translate_text(f"Categories ({page+1}/{total_pages}) 📂", session.lang), 60)
    body_t = safe_truncate(translate_text("Choose a complaint category, or describe your issue for smart matching:", session.lang), 1024)
    footer_t = safe_truncate(translate_text("Select an option", session.lang), 60)
    button_t = safe_truncate(translate_text("Choose Category", session.lang), 20)

    send_interactive_list(
        to,
        header=header_t,
        body=body_t,
        footer=footer_t,
        button=button_t,
        sections=[
            {"title": safe_truncate(translate_text("Categories", session.lang), 24), "rows": rows},
            {"title": safe_truncate(translate_text("More Options", session.lang), 24), "rows": option_rows},
        ]
    )

def send_all_categories_paginated(to: str, page: int = 0):
    # This function is deprecated and routes back to send_category_menu.
    # Included just in case it's called somewhere without session, 
    # but since it's not we'll just redirect to an empty mock or just remove its usages.
    pass


# ── AI Category Prediction ─────────────────────────────────────────────
def predict_category(text: str) -> list:
    """Use Groq to predict top 3 categories."""
    if not groq_client:
        return CATEGORIES[:3]
    try:
        system_prompt = f"""You are a complaint classification assistant.
Classify the user's complaint into the following categories:
{", ".join(CATEGORIES)}

Return JSON: {{"top_categories": ["Cat1", "Cat2", "Cat3"]}}
Only use exact category names from the list above."""

        completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"},
            temperature=0.1
        )
        result = json.loads(completion.choices[0].message.content)
        return result.get("top_categories", CATEGORIES[:3])
    except Exception as e:
        print(f"❌ Error predicting category: {e}")
        return CATEGORIES[:3]


def send_predicted_categories(to: str, predictions: list, session: UserSession):
    """Show predicted categories as interactive list."""
    rows = []
    for cat in predictions:
        clean = cat.replace(" ", "_").replace("&", "n")[:20]
        cat_t = translate_text(cat, session.lang)
        rows.append({
            "id": f"cat_{clean}",
            "title": safe_truncate(cat_t, 24)
        })
    rows.append({
        "id": "cat_show_all",
        "title": safe_truncate(translate_text("📋 Back to Categories", session.lang), 24)
    })
    send_interactive_list(
        to,
        header=safe_truncate(translate_text("Category Match 🎯", session.lang), 60),
        body=safe_truncate(translate_text("Based on your description, here are the best matches:", session.lang), 1024),
        footer=safe_truncate(translate_text("Select or browse all", session.lang), 60),
        button=safe_truncate(translate_text("Select Category", session.lang), 20),
        sections=[{"title": safe_truncate(translate_text("Suggested", session.lang), 24), "rows": rows}]
    )


# ── FAQ Questions ───────────────────────────────────────────────────────
def send_faq_list(to: str, category: str, session: UserSession):
    """Send FAQ questions for a category as interactive list."""
    questions = get_questions_for_category(category)
    q_rows = []

    for i, q in enumerate(questions[:10]):   # max 10 per section
        q_trans = translate_text(q, session.lang)
        q_rows.append({
            "id": f"faq_{i}",
            "title": f"FAQ {i+1}",
            "description": safe_truncate(q_trans, 72)
        })

    opt_rows = [
        {
            "id": "faq_change_category",
            "title": safe_truncate(translate_text("🔄 Start Over", session.lang), 24),
            "description": safe_truncate(translate_text("Change language or category", session.lang), 72)
        },
        {
            "id": "faq_file_complaint",
            "title": safe_truncate(translate_text("📝 File a Complaint", session.lang), 24),
            "description": safe_truncate(translate_text("My issue is different", session.lang), 72)
        }
    ]

    cat_t = translate_text(category, session.lang)
    send_interactive_list(
        to,
        header=safe_truncate(f"{cat_t[:50]} FAQ ❓", 60),
        body=safe_truncate(translate_text("Here are common questions for this category.\nSelect one for guidance, or choose an option below:", session.lang), 1024),
        footer=safe_truncate(translate_text("Select an option", session.lang), 60),
        button=safe_truncate(translate_text("View Options", session.lang), 20),
        sections=[
            {"title": safe_truncate(translate_text("Questions", session.lang), 24), "rows": q_rows},
            {"title": safe_truncate(translate_text("Actions", session.lang), 24), "rows": opt_rows}
        ]
    )


# ── Data Collection Prompts ─────────────────────────────────────────────
COLLECTION_PROMPTS = {
    COLLECT_USER_NAME: "👤 Please enter your full name:",
    COLLECT_USER_STATE: "📍 Which State do you belong to?",
    COLLECT_USER_VILLAGE: "🏡 What is your city/village/Town name?",
    COLLECT_USER_CONTACT: "📞 Please enter your contact number:",
    COLLECT_USER_EMAIL: "📧 Please enter your email address:",
    COLLECT_DESCRIPTION: "📝 Please describe your complaint in detail:",
    COLLECT_OPPOSITE_NAME: "🏢 What is the name of the company or person you are complaining against?",
    COLLECT_OPPOSITE_ADDRESS: "📍 What is the contact address of the opposite party?",
    COLLECT_OPPOSITE_PHONE: "📞 Phone number of the opposite party?",
    COLLECT_OPPOSITE_EMAIL: "📧 Email of the opposite party?",
    COLLECT_MONETARY: "💰 Is there any monetary amount involved? (in ₹)",
    COLLECT_DOCS: "📎 Upload supporting documents (photos, bills, PDFs).\nSend them now, or type *done* when finished.",
}


def send_skip_button_prompt(to: str, prompt_text: str, skip_id: str, session: UserSession):
    """Send a prompt with a Skip button for optional fields."""
    send_interactive_buttons(to, translate_text(prompt_text, session.lang), [
        (skip_id, safe_truncate(translate_text("⏭️ Skip", session.lang), 20)),
    ])


def send_collection_prompt(to: str, state: str, session: UserSession):
    """Send the data collection prompt for the current state."""
    prompt = COLLECTION_PROMPTS.get(state, "Please provide the details:")
    # For skippable opposite party fields, show a Skip button
    if state == COLLECT_OPPOSITE_PHONE:
        send_skip_button_prompt(to, prompt, "skip_opp_phone", session)
    elif state == COLLECT_OPPOSITE_EMAIL:
        send_skip_button_prompt(to, prompt, "skip_opp_email", session)
    elif state == COLLECT_MONETARY:
        send_skip_button_prompt(to, prompt, "skip_monetary", session)
    elif state == COLLECT_DOCS:
        send_interactive_buttons(to, translate_text(prompt, session.lang), [
            ("action_done", safe_truncate(translate_text("✅ Done", session.lang), 20)),
        ])
    else:
        send_whatsapp_message(to, translate_text(prompt, session.lang))


# ── Summary & Confirmation ─────────────────────────────────────────────
def send_summary(to: str, session: UserSession):
    """Send complaint summary and confirm/edit buttons."""
    send_whatsapp_message(to, translate_text(session.get_summary_text(), session.lang))
    send_interactive_buttons(to, translate_text("Please confirm or edit your complaint:", session.lang), [
        ("action_confirm", safe_truncate(translate_text("✅ Confirm", session.lang), 20)),
        ("action_edit", safe_truncate(translate_text("✏️ Edit", session.lang), 20)),
    ])


def send_edit_field_list(to: str, session: UserSession):
    """Send list of editable fields."""
    fields = session.get_editable_fields()
    rows = []
    for fid, name in fields:
        name_t = translate_text(name, session.lang)
        rows.append({"id": fid, "title": safe_truncate(name_t, 24), "description": safe_truncate(translate_text("Edit this field", session.lang), 72)})
    send_interactive_list(
        to,
        header=safe_truncate(translate_text("Edit Field ✏️", session.lang), 60),
        body=safe_truncate(translate_text("Which field would you like to edit?", session.lang), 1024),
        footer=safe_truncate(translate_text("Select a field", session.lang), 60),
        button=safe_truncate(translate_text("Choose Field", session.lang), 20),
        sections=[{"title": safe_truncate(translate_text("Fields", session.lang), 24), "rows": rows}]
    )


# ── Category ID Resolution ─────────────────────────────────────────────
def resolve_category_from_id(selected_id: str) -> str:
    """Convert a cat_XYZ interactive ID back to a category name."""
    # Remove "cat_" prefix
    cat_key = selected_id[4:]
    # Try to match against CATEGORIES
    for cat in CATEGORIES:
        clean = cat.replace(" ", "_").replace("&", "n")[:20]
        if clean == cat_key:
            return cat
    return None


# ════════════════════════════════════════════════════════════════════════
# WEBHOOK ENDPOINTS
# ════════════════════════════════════════════════════════════════════════

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Webhook verification (Meta GET request)."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified!")
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Handle incoming WhatsApp messages — Full State Machine."""
    try:
        data = request.json
        print(f"\n📩 Incoming webhook data:")

        if data.get("object") != "whatsapp_business_account":
            return jsonify({"status": "ok"}), 200

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                for message in messages:
                    msg_id = message.get("id", "")
                    if is_duplicate(msg_id):
                        print(f"⏭️ Skipping duplicate message: {msg_id}")
                        continue

                    sender = message.get("from")
                    msg_type = message.get("type")
                    session = get_session(sender)

                    print(f"📱 From: {sender} | Type: {msg_type} | State: {session.state}")

                    # ── TEXT messages ────────────────────────────────
                    if msg_type == "text":
                        text = message.get("text", {}).get("body", "").strip()
                        handle_text(sender, text, session)

                    # ── INTERACTIVE replies (list / button) ─────────
                    elif msg_type == "interactive":
                        interactive = message.get("interactive", {})
                        # Could be list_reply or button_reply
                        list_reply = interactive.get("list_reply", {})
                        button_reply = interactive.get("button_reply", {})
                        selected_id = list_reply.get("id") or button_reply.get("id", "")
                        selected_title = list_reply.get("title") or button_reply.get("title", "")

                        print(f"   Selection: {selected_id} ({selected_title})")
                        handle_interactive(sender, selected_id, selected_title, session)

                    # ── IMAGE / DOCUMENT uploads ────────────────────
                    elif msg_type in ("image", "document"):
                        handle_media(sender, message, msg_type, session)

                    else:
                        send_whatsapp_message(sender, "Please send a text message to get started. 😊")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"❌ Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Text Handler ────────────────────────────────────────────────────────
def handle_text(sender: str, text: str, session: UserSession):
    """Route text messages based on current state."""
    state = session.state

    # NEW user or restart
    if state == LANG_SELECT:
        if text.lower() in ("hi", "hello", "hey", "start", "restart"):
            send_language_selection(sender)
        else:
            send_language_selection(sender)

    # Waiting for complaint description (AI matching)
    elif state == AWAITING_AI_INPUT:
        send_whatsapp_message(sender, translate_text("🔍 Analyzing your complaint...", session.lang))
        predictions = predict_category(text)
        # Store the text for later use as description
        session.complaint_description = text
        send_predicted_categories(sender, predictions, session)
        # Stay in AWAITING_AI_INPUT — will move to FAQ_LIST on category selection

    # Waiting for "Show All" text input (user typing a category name)
    elif state == CATEGORY_MENU:
        # User typed text while in category menu — treat as AI input
        send_whatsapp_message(sender, translate_text("🔍 Analyzing your complaint...", session.lang))
        predictions = predict_category(text)
        session.complaint_description = text
        send_predicted_categories(sender, predictions, session)

    # ── Data Collection States ──────────────────────────────────────
    elif state == COLLECT_USER_NAME:
        session.user_name = text
        session.state = COLLECT_USER_STATE
        send_collection_prompt(sender, COLLECT_USER_STATE, session)

    elif state == COLLECT_USER_STATE:
        session.user_state = text
        session.state = COLLECT_USER_VILLAGE
        send_collection_prompt(sender, COLLECT_USER_VILLAGE, session)

    elif state == COLLECT_USER_VILLAGE:
        session.user_village = text
        session.state = COLLECT_USER_CONTACT
        send_collection_prompt(sender, COLLECT_USER_CONTACT, session)

    elif state == COLLECT_USER_CONTACT:
        session.user_contact = text
        session.state = COLLECT_USER_EMAIL
        send_collection_prompt(sender, COLLECT_USER_EMAIL, session)

    elif state == COLLECT_USER_EMAIL:
        session.user_email = text
        session.state = MAIN_CATEGORY_MENU
        send_main_category_menu(sender, session)

    elif state == COLLECT_DESCRIPTION:
        session.complaint_description = text
        session.state = COLLECT_OPPOSITE_NAME
        send_collection_prompt(sender, COLLECT_OPPOSITE_NAME, session)

    elif state == COLLECT_OPPOSITE_NAME:
        session.opposite_party_name = text
        session.state = COLLECT_OPPOSITE_ADDRESS
        send_collection_prompt(sender, COLLECT_OPPOSITE_ADDRESS, session)

    elif state == COLLECT_OPPOSITE_ADDRESS:
        session.opposite_party_address = text
        session.state = COLLECT_OPPOSITE_PHONE
        send_collection_prompt(sender, COLLECT_OPPOSITE_PHONE, session)

    elif state == COLLECT_OPPOSITE_PHONE:
        session.opposite_party_phone = None if text.lower() == "skip" else text
        session.state = COLLECT_OPPOSITE_EMAIL
        send_collection_prompt(sender, COLLECT_OPPOSITE_EMAIL, session)

    elif state == COLLECT_OPPOSITE_EMAIL:
        session.opposite_party_email = None if text.lower() == "skip" else text
        session.state = COLLECT_MONETARY
        send_collection_prompt(sender, COLLECT_MONETARY, session)

    elif state == COLLECT_MONETARY:
        session.monetary_amount = None if text.lower() == "skip" else text
        session.state = COLLECT_DOCS
        send_collection_prompt(sender, COLLECT_DOCS, session)

    elif state == COLLECT_DOCS:
        if text.lower() == "done":
            session.state = SUMMARY
            send_summary(sender, session)
        else:
            send_whatsapp_message(sender, translate_text("📎 Please upload a photo/document, or type *done* to proceed.", session.lang))

    # ── Edit field ──────────────────────────────────────────────────
    elif state == EDIT_FIELD:
        field = session.edit_field
        if field == "edit_user_name":
            session.user_name = text
        elif field == "edit_user_state":
            session.user_state = text
        elif field == "edit_user_village":
            session.user_village = text
        elif field == "edit_user_contact":
            session.user_contact = text
        elif field == "edit_user_email":
            session.user_email = text
        elif field == "edit_desc":
            session.complaint_description = text
        elif field == "edit_opp_name":
            session.opposite_party_name = text
        elif field == "edit_opp_addr":
            session.opposite_party_address = text
        elif field == "edit_opp_phone":
            session.opposite_party_phone = None if text.lower() == "skip" else text
        elif field == "edit_opp_email":
            session.opposite_party_email = None if text.lower() == "skip" else text
        elif field == "edit_monetary":
            session.monetary_amount = None if text.lower() == "skip" else text

        send_whatsapp_message(sender, translate_text("✅ Updated!", session.lang))
        session.state = SUMMARY
        send_summary(sender, session)

    # FAQ answer state — user might type "file complaint"
    elif state == FAQ_ANSWER:
        session.state = COLLECT_DESCRIPTION
        send_collection_prompt(sender, COLLECT_DESCRIPTION, session)

    # Confirmed — allow restart
    elif state == CONFIRMED:
        reset_session(sender)
        send_language_selection(sender)

    else:
        send_whatsapp_message(sender, translate_text("Something went wrong. Type *hi* to restart.", session.lang))
        reset_session(sender)


# ── Interactive Handler ─────────────────────────────────────────────────
def handle_interactive(sender: str, selected_id: str, selected_title: str,
                       session: UserSession):
    """Route interactive (list/button) replies based on current state."""

    # ── Language Selection ──────────────────────────────────────────
    if selected_id.startswith("lang_"):
        session.lang = selected_id
        confirmation = LANGUAGE_CONFIRMATIONS.get(selected_id, "✅ Language set.")
        send_whatsapp_message(sender, translate_text(confirmation, session.lang))
        session.state = COLLECT_USER_NAME
        send_collection_prompt(sender, COLLECT_USER_NAME, session)
        return

    # ── State Selection ─────────────────────────────────────────────
    if selected_id.startswith("state_"):
        session.user_state = selected_title
        session.state = COLLECT_USER_VILLAGE
        send_collection_prompt(sender, COLLECT_USER_VILLAGE, session)
        return

    # ── Main Menu Selections ────────────────────────────────────────
    if selected_id == "menu_product":
        session.state = PRODUCT_MENU
        send_product_menu(sender, session)
        return

    if selected_id == "menu_service":
        session.state = SERVICE_MENU
        send_service_menu(sender, session)
        return

    if selected_id == "menu_choose":
        session.state = CATEGORY_MENU
        send_category_menu(sender, session)
        return

    if selected_id == "menu_more":
        session.state = MORE_OPTIONS_MENU
        send_more_options_menu(sender, session)
        return

    if selected_id == "menu_back_lang":
        reset_session(sender)
        send_language_selection(sender)
        return

    if selected_id == "menu_back_main":
        session.state = MAIN_CATEGORY_MENU
        send_main_category_menu(sender, session)
        return

    if selected_id.startswith("serv_page_"):
        page = int(selected_id.split("_")[-1])
        send_service_menu(sender, session, page=page)
        return

    # ── Category Selection ──────────────────────────────────────────
    if selected_id == "cat_show_all":
        session.state = CATEGORY_MENU
        send_category_menu(sender, session)
        return

    if selected_id == "cat_change_lang":
        reset_session(sender)
        send_language_selection(sender)
        return

    if selected_id.startswith("cat_page_"):
        page = int(selected_id.split("_")[-1])
        send_category_menu(sender, session, page=page)
        return

    if selected_id == "cat_describe":
        session.state = AWAITING_AI_INPUT
        send_whatsapp_message(sender, translate_text("✍️ Please describe your issue in a few words:", session.lang))
        return

    if selected_id.startswith("cat_"):
        category = resolve_category_from_id(selected_id)
        if category:
            session.category = category
            session.state = FAQ_LIST
            send_whatsapp_message(sender, translate_text(f"📂 Category: *{category}*", session.lang))
            send_faq_list(sender, category, session)
        else:
            send_whatsapp_message(sender, translate_text(f"Category selected: *{selected_title}*", session.lang))
            session.category = selected_title
            session.state = FAQ_LIST
            send_faq_list(sender, selected_title, session)
        return

    # ── FAQ Question Selection ──────────────────────────────────────
    if selected_id == "faq_change_category":
        reset_session(sender)
        send_language_selection(sender)
        return

    if selected_id == "faq_file_complaint":
        session.state = COLLECT_DESCRIPTION
        if session.complaint_description:
            # Already have description from AI input
            msg = f"📝 We have your description:\n_{session.complaint_description}_\n\nLet's collect more details."
            send_whatsapp_message(sender, translate_text(msg, session.lang))
            session.state = COLLECT_OPPOSITE_NAME
            send_collection_prompt(sender, COLLECT_OPPOSITE_NAME, session)
        else:
            send_collection_prompt(sender, COLLECT_DESCRIPTION, session)
        return

    if selected_id.startswith("faq_"):
        # Show the answer for selected question
        idx = int(selected_id.split("_")[1])
        questions = get_questions_for_category(session.category)
        if 0 <= idx < len(questions):
            question = questions[idx]
            session.selected_question = question
            answer = get_dummy_answer(session.category, question)
            send_whatsapp_message(sender, translate_text(answer, session.lang))
            session.state = FAQ_ANSWER
            send_interactive_buttons(sender, translate_text("Was this helpful?", session.lang), [
                ("ans_need_more", safe_truncate(translate_text("ℹ️ Need more info", session.lang), 20)),
                ("ans_file", safe_truncate(translate_text("📝 File Complaint", session.lang), 20)),
                ("ans_back", safe_truncate(translate_text("🔙 Back", session.lang), 20)),
            ])
        return

    # ── FAQ Answer actions ──────────────────────────────────────────
    if selected_id == "ans_need_more":
        more_info_msg = (
            "For more information, you can call us on our helpline number "
            "18002330332 (Mon-Fri, 10:30 AM to 5:30 PM) or email us at "
            "complaints@cercindia.org."
        )
        send_whatsapp_message(sender, translate_text(more_info_msg, session.lang))
        session.state = CONFIRMED
        return

    if selected_id == "ans_file":
        session.state = COLLECT_DESCRIPTION
        if session.complaint_description:
            msg = f"📝 We have your description:\n_{session.complaint_description}_\n\nLet's collect more details."
            send_whatsapp_message(sender, translate_text(msg, session.lang))
            session.state = COLLECT_OPPOSITE_NAME
            send_collection_prompt(sender, COLLECT_OPPOSITE_NAME, session)
        else:
            send_collection_prompt(sender, COLLECT_DESCRIPTION, session)
        return

    # ── Upload Document Done Action ──────────────────────────────────────────
    if selected_id == "action_done":
        session.state = SUMMARY
        send_summary(sender, session)
        return

    if selected_id == "ans_back":
        session.state = FAQ_LIST
        send_faq_list(sender, session.category, session)
        return

    # ── Skip Button Handlers ────────────────────────────────────────
    if selected_id == "skip_opp_phone":
        session.opposite_party_phone = None
        session.state = COLLECT_OPPOSITE_EMAIL
        send_collection_prompt(sender, COLLECT_OPPOSITE_EMAIL, session)
        return

    if selected_id == "skip_opp_email":
        session.opposite_party_email = None
        session.state = COLLECT_MONETARY
        send_collection_prompt(sender, COLLECT_MONETARY, session)
        return

    if selected_id == "skip_monetary":
        session.monetary_amount = None
        session.state = COLLECT_DOCS
        send_collection_prompt(sender, COLLECT_DOCS, session)
        return

    # ── Confirm / Edit ──────────────────────────────────────────────
    if selected_id == "action_confirm":
        ticket_id = session.generate_ticket_id()
        msg = (
            f"🎉 *Complaint Filed Successfully!*\n\n"
            f"🎫 Your Ticket ID: *{ticket_id}*\n\n"
            f"We will review your complaint and get back to you.\n"
            f"📞 Helpline: 18002330332 (Mon-Fri, 10:30 AM to 5:30 PM)\n"
            f"📧 Email: complaints@cercindia.org\n\n"
            f"Thank you for using CERC Support! 🙏"
        )
        send_whatsapp_message(sender, translate_text(msg, session.lang))
        session.state = CONFIRMED

        # Send email and upload to Google Sheets in background thread
        def _process_bg(sess, tid):
            try:
                ok = send_complaint_email(sess)
                print(f"📧 Email {'sent' if ok else 'FAILED'} for ticket {tid}")
                
                from google_sheets import append_complaint_to_sheet
                sheet_ok = append_complaint_to_sheet("NGO Chatbot Complaints", sess, tid)
                print(f"📊 Google Sheets {'updated' if sheet_ok else 'FAILED'} for ticket {tid}")
            except Exception as ex:
                print(f"❌ Background processing error: {ex}")
        threading.Thread(target=_process_bg, args=(session, ticket_id), daemon=True).start()
        return

    if selected_id == "action_edit":
        send_edit_field_list(sender, session)
        return

    # ── Edit Field Selection ────────────────────────────────────────
    if selected_id.startswith("edit_"):
        session.edit_field = selected_id
        session.state = EDIT_FIELD

        field_prompts = {
            "edit_user_name": "👤 Enter your new name:",
            "edit_user_state": "📍 Enter your new state:",
            "edit_user_village": "🏡 Enter your new city/village/town name:",
            "edit_user_contact": "📞 Enter your new contact number:",
            "edit_user_email": "📧 Enter your new email address:",
            "edit_desc": "📝 Enter the new complaint description:",
            "edit_opp_name": "🏢 Enter the new opposite party name:",
            "edit_opp_addr": "📍 Enter the new address:",
            "edit_opp_phone": "📞 Enter the new phone number (or 'skip'):",
            "edit_opp_email": "📧 Enter the new email (or 'skip'):",
            "edit_monetary": "💰 Enter the new amount (or 'skip'):",
        }
        prompt = field_prompts.get(selected_id, "Enter the new value:")
        send_whatsapp_message(sender, translate_text(prompt, session.lang))
        return


# ── Media Handler ───────────────────────────────────────────────────────
def handle_media(sender: str, message: dict, msg_type: str,
                 session: UserSession):
    """Handle image/document uploads during COLLECT_DOCS state."""
    if session.state != COLLECT_DOCS:
        msg = "📎 We're not collecting documents right now. Please follow the current step or type *hi* to restart."
        send_whatsapp_message(sender, translate_text(msg, session.lang))
        return

    media_info = message.get(msg_type, {})
    media_id = media_info.get("id")
    mime_type = media_info.get("mime_type", "unknown")
    filename = media_info.get("filename", f"upload.{mime_type.split('/')[-1]}")

    if media_id:
        session.documents.append({
            "media_id": media_id,
            "mime_type": mime_type,
            "filename": filename,
        })
        count = len(session.documents)
        msg_t = translate_text(f"✅ Document received! ({count} total)\nSend more, or type *done* to proceed.", session.lang)
        send_whatsapp_message(sender, msg_t)
    else:
        send_whatsapp_message(sender, translate_text("⚠️ Could not process the file. Please try again.", session.lang))


# ════════════════════════════════════════════════════════════════════════
# HEALTH CHECKS
# ════════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET", "HEAD"])
def health_check():
    return jsonify({"status": "running", "service": "NGO WhatsApp Bot"}), 200


@app.route("/health", methods=["GET", "HEAD"])
def health_check_monitor():
    if request.method == "HEAD":
        return "", 200
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    print("🚀 Starting WhatsApp Webhook Server...")
    print(f"   Phone Number ID: {PHONE_NUMBER_ID}")
    print(f"   Verify Token: {VERIFY_TOKEN}")
    print(f"   Groq API: {'✅' if groq_client else '❌'}")
    print(f"   Admin Email: {os.getenv('ADMIN_EMAIL', 'Not set')}")
    print(f"\n📌 Webhook URL: <your-url>/webhook\n")

    app.run(host="0.0.0.0", port=5000, debug=True)
