"""
Email Service for Complaint Notifications
Uses Resend HTTP API (no SMTP ports needed — works on Render).
Also handles WhatsApp media download.
"""
import os
import base64
import requests as http_requests  # alias to avoid conflict with flask


# ── Config from environment ─────────────────────────────────────────────
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

RESEND_URL = "https://api.resend.com/emails"


def download_whatsapp_media(media_id: str) -> tuple:
    """
    Download media from WhatsApp Cloud API.

    Flow:
    1. GET /v22.0/{media_id} → returns {"url": "https://..."}
    2. GET that URL with Bearer token → returns the file bytes

    Returns: (file_bytes, content_type) or (None, None) on failure
    """
    try:
        # Step 1: Get the media URL
        meta_url = f"https://graph.facebook.com/v22.0/{media_id}"
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

        resp = http_requests.get(meta_url, headers=headers)
        resp.raise_for_status()
        media_url = resp.json().get("url")

        if not media_url:
            print(f"⚠️ No URL returned for media_id: {media_id}")
            return None, None

        # Step 2: Download the actual file
        file_resp = http_requests.get(media_url, headers=headers)
        file_resp.raise_for_status()

        content_type = file_resp.headers.get("Content-Type", "application/octet-stream")
        print(f"✅ Downloaded media {media_id} ({content_type}, {len(file_resp.content)} bytes)")
        return file_resp.content, content_type

    except Exception as e:
        print(f"❌ Error downloading media {media_id}: {e}")
        return None, None


def send_complaint_email(session) -> bool:
    """
    Send complaint email via Resend HTTP API.
    Uses simple POST request — no SMTP ports needed.

    Args:
        session: UserSession object with all complaint data

    Returns: True if sent successfully, False otherwise
    """
    if not RESEND_API_KEY or not ADMIN_EMAIL:
        print("⚠️ Resend API key or admin email not configured. Skipping email.")
        return False

    try:
        # Build HTML body
        lang_map = {
            "lang_english": "English", "lang_hindi": "Hindi",
            "lang_gujarati": "Gujarati", "lang_marathi": "Marathi"
        }

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #2c3e50;">📋 New Complaint Filed</h2>
            <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                <tr style="background: #f8f9fa;">
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Ticket ID</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.ticket_id}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Category</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.category}</td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Language</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{lang_map.get(session.lang, session.lang)}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Filed At</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.created_at}</td>
                </tr>
            </table>

            <h3 style="color: #2c3e50; margin-top: 20px;">👤 User Details</h3>
            <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                <tr style="background: #f8f9fa;">
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Name</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.user_name}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">District</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.user_district}</td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Taluka</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.user_taluka}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Village/City</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.user_village}</td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Contact</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.user_contact}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Email</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.user_email}</td>
                </tr>
            </table>

            <h3 style="color: #2c3e50; margin-top: 20px;">📝 Complaint Description</h3>
            <p style="background: #f8f9fa; padding: 15px; border-radius: 5px;">{session.complaint_description}</p>

            <h3 style="color: #2c3e50;">🏢 Opposite Party Details</h3>
            <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                <tr style="background: #f8f9fa;">
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Name</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.opposite_party_name}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Address</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.opposite_party_address}</td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Phone</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.opposite_party_phone or 'N/A'}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Email</td>
                    <td style="padding: 10px; border: 1px solid #dee2e6;">{session.opposite_party_email or 'N/A'}</td>
                </tr>
            </table>

            <h3 style="color: #2c3e50;">💰 Claim Amount</h3>
            <p>{session.monetary_amount or 'None specified'}</p>

            <h3 style="color: #2c3e50;">📎 Documents</h3>
            <p>{len(session.documents)} document(s) attached to this email.</p>

            <hr style="margin-top: 30px;">
            <p style="color: #7f8c8d; font-size: 12px;">
                This complaint was filed via the NGO WhatsApp Bot.
            </p>
        </body>
        </html>
        """

        # Build attachments list
        attachments = []
        for i, doc in enumerate(session.documents):
            media_id = doc.get("media_id")
            if not media_id:
                continue

            file_bytes, content_type = download_whatsapp_media(media_id)
            if file_bytes:
                ext_map = {
                    "image/jpeg": "jpg", "image/png": "png",
                    "application/pdf": "pdf", "image/webp": "webp",
                }
                ext = ext_map.get(content_type, "bin")
                filename = doc.get("filename", f"attachment_{i+1}.{ext}")

                attachments.append({
                    "filename": filename,
                    "content": base64.b64encode(file_bytes).decode("utf-8"),
                })

        # Send via Resend API
        payload = {
            "from": "NGO Bot <onboarding@resend.dev>",
            "to": [ADMIN_EMAIL],
            "subject": f"🚨 New Complaint: {session.ticket_id} — {session.category}",
            "html": html_body,
        }
        if attachments:
            payload["attachments"] = attachments

        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        }

        resp = http_requests.post(RESEND_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()

        print(f"✅ Complaint email sent via Resend for ticket {session.ticket_id}")
        print(f"   Resend response: {resp.json()}")
        return True

    except Exception as e:
        print(f"❌ Error sending email via Resend: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response details: {e.response.text}")
        return False
