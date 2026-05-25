"""
telegram_api.py — Thin wrapper around the Telegram Bot API.
No time.sleep(). All calls wrapped in try/except.
"""

import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = "8578932007:AAHK-ba9EY8s1Sqa2IWV4FzVJVx5F6WsdgU"
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ---------------------------------------------------------------------------
# Core send helpers
# ---------------------------------------------------------------------------

def send_message(chat_id: str | int, text: str, parse_mode: str = "HTML",
                 reply_markup: dict | None = None) -> dict | None:
    """Send a text message. Returns Telegram response dict or None on error."""
    try:
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        resp = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"send_message failed to {chat_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"send_message unexpected error: {e}")
        return None


def send_message_with_keyboard(chat_id: str | int, text: str,
                                buttons: list[list[str]]) -> dict | None:
    """
    Send a message with a ReplyKeyboardMarkup.
    buttons: list of rows, each row is a list of button label strings.
    """
    try:
        keyboard = {"keyboard": [[{"text": b} for b in row] for row in buttons],
                    "resize_keyboard": True,
                    "one_time_keyboard": True}
        return send_message(chat_id, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"send_message_with_keyboard error: {e}")
        return None


def send_inline_keyboard(chat_id: str | int, text: str,
                         buttons: list[list[dict]]) -> dict | None:
    """
    Send a message with InlineKeyboardMarkup.
    buttons: list of rows; each button dict has 'text' and 'callback_data'.
    """
    try:
        markup = {"inline_keyboard": buttons}
        return send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"send_inline_keyboard error: {e}")
        return None


def answer_callback_query(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge an inline button press."""
    try:
        resp = requests.post(f"{BASE_URL}/answerCallbackQuery",
                             json={"callback_query_id": callback_query_id, "text": text},
                             timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"answer_callback_query error: {e}")
        return False


def set_webhook(webhook_url: str) -> bool:
    """Register the webhook URL with Telegram."""
    try:
        resp = requests.post(f"{BASE_URL}/setWebhook",
                             json={"url": webhook_url, "drop_pending_updates": True},
                             timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            logger.info(f"Webhook set to {webhook_url}")
            return True
        logger.error(f"setWebhook not ok: {data}")
        return False
    except Exception as e:
        logger.error(f"set_webhook error: {e}")
        return False


# ---------------------------------------------------------------------------
# Amharic message templates
# ---------------------------------------------------------------------------

def msg_welcome(first_name: str) -> str:
    return (
        f"👋 <b>እንኳን ደህና መጡ፣ {first_name}!</b>\n\n"
        "ወደ ዴንዴ የጥርስ ክሊኒክ ቦት እንኳን ደህና መጡ።\n"
        "እባክዎ ስምዎን ለማስመዝገብ ስምዎን ይፃፉ።"
    )


def msg_ask_last_name() -> str:
    return "📝 እባክዎ የአባት ስምዎን ያስገቡ:"


def msg_ask_phone() -> str:
    return "📞 እባክዎ የስልክ ቁጥርዎን ያስገቡ (ወይም ለማለፍ «ዝለል» ይጫኑ):"


def msg_ask_clinic(clinics: list[dict]) -> str:
    lines = ["🏥 <b>ክሊኒክ ይምረጡ:</b>\n"]
    for i, c in enumerate(clinics, 1):
        lines.append(f"{i}. {c.get('name', 'ክሊኒክ')}")
    return "\n".join(lines)


def msg_registration_complete(first_name: str, clinic_name: str) -> str:
    return (
        f"✅ <b>ምዝገባ ተጠናቋል!</b>\n\n"
        f"ስም: {first_name}\n"
        f"ክሊኒክ: {clinic_name}\n\n"
        "ቀጠሮ ለማስያዝ /appointment ይጻፉ።"
    )


def msg_appointment_reminder(patient_name: str, clinic_name: str,
                              appointment_time_local: str) -> str:
    return (
        f"⏰ <b>የቀጠሮ ማሳሰቢያ</b>\n\n"
        f"ውድ {patient_name},\n"
        f"ነገ ቀጠሮ አለዎት።\n\n"
        f"🏥 ክሊኒክ: {clinic_name}\n"
        f"🕐 ጊዜ: {appointment_time_local}\n\n"
        "ለመሰረዝ /cancel ይፃፉ። አመሰግናለሁ!"
    )


def msg_slot_available(clinic_name: str, slot_time: str) -> str:
    return (
        f"🎉 <b>ቦታ ተገኝቷል!</b>\n\n"
        f"ክሊኒክ: {clinic_name}\n"
        f"ቦታ: {slot_time}\n\n"
        "ቦታዉን ለማስያዝ ከታች ያሉትን ቁልፎች ይጫኑ:"
    )


def msg_slot_claimed() -> str:
    return "✅ ቦታዉ ተስፋፍቷል! ቀጠሮ ተያዘ። እናመሰግናለን።"


def msg_slot_already_taken() -> str:
    return "⚠️ ይቅርታ፣ ቦታዉ ቀድሞ ተወስዷል። ዝርዝሩ ውስጥ ቆይተዋል።"


def msg_weekly_report(clinic_name: str, total: int, completed: int,
                      cancelled: int, confirmed: int) -> str:
    return (
        f"📊 <b>ሳምንታዊ ሪፖርት — {clinic_name}</b>\n\n"
        f"📅 ጠቅላላ ቀጠሮዎች: {total}\n"
        f"✅ የተጠናቀቁ: {completed}\n"
        f"❌ የተሰረዙ: {cancelled}\n"
        f"🔜 የተረጋገጡ: {confirmed}\n\n"
        "ሪፖርቱ ተጠናቋል።"
    )


def msg_billing_summary(clinic_name: str, billed_count: int, total_amount: float) -> str:
    return (
        f"💰 <b>የክፍያ ሪፖርት — {clinic_name}</b>\n\n"
        f"የተሰበሰቡ ቀጠሮዎች: {billed_count}\n"
        f"ጠቅላላ ገቢ: {total_amount:,.2f} ብር\n\n"
        "ሂሳቡ ተዘጋጅቷል።"
    )


def msg_unknown() -> str:
    return (
        "🤔 <b>ትዕዛዙ አልተረዳም።</b>\n\n"
        "ለሚገኙ ትዕዛዞች /help ይጻፉ።"
    )


def msg_help() -> str:
    return (
        "📋 <b>የዴንዴ ቦት ትዕዛዞች:</b>\n\n"
        "/start — ምዝገባ\n"
        "/appointment — ቀጠሮ ማስያዝ\n"
        "/cancel — ቀጠሮ ሰረዝ\n"
        "/status — የቀጠሮ ሁኔታ\n"
        "/help — እርዳታ"
    )


def msg_already_registered(first_name: str) -> str:
    return (
        f"👤 <b>እንኳን ደህና መጡ ተመልሰው፣ {first_name}!</b>\n\n"
        "ቀጠሮ ለማስያዝ /appointment ይጻፉ።"
    )
