"""
main.py — Dende Telegram Bot for Ethiopian Dental Clinics
Flask + Supabase + Telegram Webhook
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
from supabase import create_client

import telegram_api as tg

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OWNER_CHAT_ID = "5174408636"
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "https://dende.onrender.com")
DEFAULT_BILL_AMOUNT = 500.0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Supabase client
# ---------------------------------------------------------------------------

def get_supabase():
    url = os.environ.get("SUPABASE_URL", "https://wjcmvfdlnqvtckssswht.supabase.co")
    key = os.environ.get("SUPABASE_KEY", "")
    return create_client(url, key)

# ---------------------------------------------------------------------------
# In-memory registration session store
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}

STEP_AWAIT_LANGUAGE   = "await_language"
STEP_AWAIT_FIRST_NAME = "await_first_name"
STEP_AWAIT_LAST_NAME  = "await_last_name"
STEP_AWAIT_PHONE      = "await_phone"
STEP_AWAIT_CLINIC     = "await_clinic"
STEP_REGISTERED       = "registered"


def get_session(chat_id: str) -> dict:
    return _sessions.get(chat_id, {})


def set_session(chat_id: str, step: str, data: dict | None = None) -> None:
    _sessions[chat_id] = {"step": step, "data": data or {}}


def clear_session(chat_id: str) -> None:
    _sessions.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Diagnostic endpoint
# ---------------------------------------------------------------------------

@app.route("/test-db", methods=["GET"])
def test_db():
    try:
        client = get_supabase()
        res = client.table("clinics").select("*").execute()
        return jsonify({
            "ok": True,
            "clinic_count": len(res.data),
            "first_clinic": res.data[0] if res.data else None
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Cron: Send 24-hour reminders
# ---------------------------------------------------------------------------

@app.route("/cron/send-reminders", methods=["GET", "POST"])
def cron_send_reminders():
    try:
        client = get_supabase()
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(hours=24)
        
        res = client.table("appointments").select("*").eq("reminder_sent", False).eq("status", "PENDING").execute()
        
        sent = 0
        for appt in res.data or []:
            try:
                appt_time_str = appt["appointment_time"]
                if "T" in appt_time_str:
                    appt_time = datetime.fromisoformat(appt_time_str.replace("Z", "+00:00"))
                else:
                    appt_time = datetime.fromisoformat(appt_time_str)
                if appt_time.tzinfo is not None:
                    appt_time = appt_time.replace(tzinfo=None)
                
                if now.replace(tzinfo=None) <= appt_time <= tomorrow.replace(tzinfo=None):
                    chat_id = appt.get("phone", "")
                    if not chat_id:
                        continue
                    
                    patient_name = appt.get("patient_name", "ታካሚ")
                    clinic_name = "Test Clinic"
                    local_time = appt_time.strftime("%H:%M")
                    
                    msg = f'ሰላም {patient_name}! ነገ በ{local_time} በ{clinic_name} ቀጠሮ አለዎት። እንደምትመጡ ተስፋ እናደርጋለን!'
                    
                    keyboard = {
                        "inline_keyboard": [
                            [
                                {"text": "✅ አዎ / Confirm", "callback_data": f"confirm_{appt['id']}"},
                                {"text": "❌ አይ / Cancel", "callback_data": f"cancel_{appt['id']}"}
                            ]
                        ]
                    }
                    
                    tg.send_message(chat_id, msg, reply_markup=keyboard)
                    
                    client.table("appointments").update({"reminder_sent": True}).eq("id", appt["id"]).execute()
                    sent += 1
            except Exception as inner_e:
                logger.error(f"Error processing appointment {appt.get('id')}: {inner_e}")
                continue
        
        return jsonify({"ok": True, "sent": sent})
    except Exception as e:
        logger.error(f"cron_send_reminders error: {e}")
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Webhook — Telegram update handler
# ---------------------------------------------------------------------------

@app.route("/webhook/telegram", methods=["POST"])
def webhook_telegram():
    try:
        update = request.get_json(force=True, silent=True)
        if not update:
            return jsonify({"ok": True}), 200

        if "callback_query" in update:
            _handle_callback_query(update["callback_query"])
            return jsonify({"ok": True}), 200

        message = update.get("message")
        if not message:
            return jsonify({"ok": True}), 200

        chat_id = str(message["chat"]["id"])
        text    = (message.get("text") or "").strip()
        user    = message.get("from", {})

        if not text:
            return jsonify({"ok": True}), 200

        if text.startswith("/start"):
            _handle_start(chat_id, user)
        elif text.startswith("/help"):
            tg.send_message(chat_id, tg.msg_help())
        else:
            _handle_text(chat_id, text, user)

    except Exception as e:
        logger.error(f"webhook_telegram unhandled error: {e}", exc_info=True)

    return jsonify({"ok": True}), 200


# ---------------------------------------------------------------------------
# Telegram message routing helpers
# ---------------------------------------------------------------------------

def _handle_start(chat_id: str, user: dict) -> None:
    """Begin registration flow with language selection."""
    try:
        try:
            client = get_supabase()
            res = client.table("patients").select("*").eq("chat_id", chat_id).limit(1).execute()
            if res.data:
                tg.send_message(chat_id, tg.msg_already_registered(res.data[0].get("first_name", "")))
                return
        except Exception as db_err:
            logger.warning(f"DB lookup failed, continuing without it: {db_err}")

        set_session(chat_id, STEP_AWAIT_LANGUAGE, {"first_name": user.get("first_name", "")})
        keyboard = {
            "inline_keyboard": [
                [{"text": "🇪🇹 አማርኛ", "callback_data": "lang_am"}],
                [{"text": "🇬🇧 English", "callback_data": "lang_en"}],
                [{"text": "🌍 Afaan Oromoo", "callback_data": "lang_or"}]
            ]
        }
        tg.send_message(chat_id, "ቋንቋ ይምረጡ | Choose language | Afaan filadhu:", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"_handle_start error: {e}")
        tg.send_message(chat_id, "Welcome to Dende! Please choose your language.")


def _handle_text(chat_id: str, text: str, user: dict) -> None:
    """State-machine for multi-step registration."""
    try:
        session = get_session(chat_id)
        step    = session.get("step", "")
        data    = session.get("data", {})
        lang    = data.get("language", "am")

        if step == STEP_AWAIT_FIRST_NAME:
            data["first_name"] = text
            set_session(chat_id, STEP_AWAIT_LAST_NAME, data)
            prompts = {
                'am': 'ስም ቤተሰብዎን ያስገቡ።',
                'en': 'Please enter your last name.',
                'or': 'Maqaa abbaa kee galchi.'
            }
            tg.send_message(chat_id, prompts.get(lang, prompts['am']))

        elif step == STEP_AWAIT_LAST_NAME:
            data["last_name"] = text
            set_session(chat_id, STEP_AWAIT_PHONE, data)
            prompts = {
                'am': 'ስልክ ቁጥርዎን ያስገቡ (ወይም ዝለል ይጫኑ)',
                'en': 'Please enter your phone number (or press skip).',
                'or': 'Lakkoofsa bilbilaa kee galchi (ykn skip jedhi).'
            }
            tg.send_message(chat_id, prompts.get(lang, prompts['am']),
                reply_markup={"keyboard": [[{"text": "ዝለል / Skip"}]], "resize_keyboard": True, "one_time_keyboard": True})

        elif step == STEP_AWAIT_PHONE:
            data["phone"] = None if text in ("ዝለል", "Skip", "skip", "ዝለል / Skip") else text
            clinics = []
            try:
                client = get_supabase()
                res = client.table("clinics").select("*").execute()
                clinics = res.data or []
            except Exception:
                pass
            
            if not clinics:
                clinics = [{"id": "TEST001", "name": "Test Clinic"}]
            
            data["clinics"] = clinics
            set_session(chat_id, STEP_AWAIT_CLINIC, data)
            buttons = [[{"text": f"{i+1}. {c['name']}"}] for i, c in enumerate(clinics)]
            prompts = {
                'am': 'እባክዎ ክሊኒክ ይምረጡ፦',
                'en': 'Please select your clinic:',
                'or': 'Kiliinika kee filadhu:'
            }
            tg.send_message(chat_id, prompts.get(lang, prompts['am']),
                reply_markup={"keyboard": buttons, "resize_keyboard": True, "one_time_keyboard": True})

        elif step == STEP_AWAIT_CLINIC:
            clinics = data.get("clinics", [])
            try:
                idx = int(text.split(".")[0]) - 1
                if idx < 0 or idx >= len(clinics):
                    raise ValueError
                chosen_clinic = clinics[idx]
            except (ValueError, TypeError, IndexError):
                tg.send_message(chat_id, "⚠️ ትክክለኛ ቁጥር ያስገቡ።")
                return

            try:
                client = get_supabase()
                client.table("patients").insert({
                    "chat_id": chat_id,
                    "name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
                    "first_name": data.get("first_name", ""),
                    "last_name": data.get("last_name", ""),
                    "phone": data.get("phone") or chat_id,
                    "clinic_id": chosen_clinic["id"],
                    "language": lang,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }).execute()
            except Exception as db_err:
                logger.warning(f"Could not save patient to DB, but continuing: {db_err}")

            set_session(chat_id, STEP_REGISTERED, {"patient_id": chat_id})
            messages = {
                'am': f'✅ ምዝገባዎ ተጠናቋል!\n\nእንኳን ደህና መጡ {data.get("first_name", "")}!\n🏥 {chosen_clinic.get("name", "")}',
                'en': f'✅ Registration complete!\n\nWelcome {data.get("first_name", "")}!\n🏥 {chosen_clinic.get("name", "")}',
                'or': f'✅ Galmaheen kee xumurame!\n\nBaga nagaan dhufte {data.get("first_name", "")}!\n🏥 {chosen_clinic.get("name", "")}'
            }
            tg.send_message(chat_id, messages.get(lang, messages['am']))

        else:
            tg.send_message(chat_id, "Send /start to begin.")

    except Exception as e:
        logger.error(f"_handle_text error: {e}")
        tg.send_message(chat_id, "Something went wrong. Please try /start again.")


def _handle_callback_query(callback_query: dict) -> None:
    """Handle inline button presses."""
    try:
        cq_id   = callback_query["id"]
        data    = callback_query.get("data", "")
        chat_id = str(callback_query["from"]["id"])

        tg.answer_callback_query(cq_id)

        # --- Confirm appointment ---
        if data.startswith("confirm_"):
            appt_id = data.replace("confirm_", "")
            try:
                client = get_supabase()
                client.table("appointments").update({"status": "CONFIRMED", "confirmed": True}).eq("id", appt_id).execute()
                tg.send_message(chat_id, "✅ ቀጠሮዎ ተረጋግጧል! እናመሰግናለን!")
            except Exception as e:
                logger.error(f"confirm error: {e}")
            return

        # --- Cancel appointment ---
        if data.startswith("cancel_"):
            appt_id = data.replace("cancel_", "")
            try:
                client = get_supabase()
                client.table("appointments").update({"status": "CANCELLED"}).eq("id", appt_id).execute()
                tg.send_message(chat_id, "❌ ቀጠሮዎ ተሰርዟል። ሌላ ቀጠሮ ለማስያዝ ክሊኒኩን ያነጋግሩ።")
            except Exception as e:
                logger.error(f"cancel error: {e}")
            return

        # --- Language selection ---
        if data.startswith("lang_"):
            language_code = data.replace("lang_", "")
            session = get_session(chat_id)
            session_data = session.get("data", {})
            first_name = session_data.get("first_name", "ታካሚ")
            
            set_session(chat_id, STEP_AWAIT_FIRST_NAME, {"language": language_code, "first_name": first_name})
            
            welcome_messages = {
                'am': f'ተመዝግበዋል! እንኳን ደህና መጡ {first_name}! 🦷\n\nስምዎን ያስገቡ።\n\n— ዴንዴ ፕላቲነም',
                'en': f'Registered! Welcome {first_name}! 🦷\n\nPlease enter your first name.\n\n— Dende Platinum',
                'or': f"Galmaa'e! Baga nagaan dhufte {first_name}! 🦷\n\nMaqaa kee galchi.\n\n— Dende Platinum"
            }
            tg.send_message(chat_id, welcome_messages.get(language_code, welcome_messages['am']))

    except Exception as e:
        logger.error(f"_handle_callback_query error: {e}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "dende-bot-v2",
    }), 200


# ---------------------------------------------------------------------------
# Webhook setup
# ---------------------------------------------------------------------------

@app.route("/setup-webhook", methods=["GET"])
def setup_webhook():
    try:
        webhook_url = f"{WEBHOOK_BASE_URL}/webhook/telegram"
        success = tg.set_webhook(webhook_url)
        return jsonify({"ok": success, "webhook_url": webhook_url}), 200
    except Exception as e:
        logger.error(f"setup_webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
