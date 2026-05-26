"""
main.py — Dende Telegram Bot for Ethiopian Dental Clinics
Flask + Supabase + Telegram Webhook

Endpoints:
  POST /webhook/telegram        — Telegram updates
  POST /cron/send-reminders     — 24-hour appointment reminders
  POST /cron/recover-slots      — Slot recovery with waitlist
  POST /cron/weekly-report      — Weekly stats per clinic → owner
  POST /cron/run-billing        — Mark completed appointments billed
  POST /cron/log-masterdata     — Snapshot all table counts
  GET  /health                  — Liveness check
  GET  /setup-webhook           — Register webhook with Telegram
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify

import database as db
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
# Webhook — Telegram update handler
# ---------------------------------------------------------------------------

@app.route("/webhook/telegram", methods=["POST"])
def webhook_telegram():
    try:
        update = request.get_json(force=True, silent=True)
        if not update:
            return jsonify({"ok": True}), 200

        # --- Callback query (inline button press) ---
        if "callback_query" in update:
            _handle_callback_query(update["callback_query"])
            return jsonify({"ok": True}), 200

        # --- Regular message ---
        message = update.get("message")
        if not message:
            return jsonify({"ok": True}), 200

        chat_id = str(message["chat"]["id"])
        text    = (message.get("text") or "").strip()
        user    = message.get("from", {})

        if not text:
            return jsonify({"ok": True}), 200

        # Route commands
        if text.startswith("/start"):
            _handle_start(chat_id, user)
        elif text.startswith("/help"):
            tg.send_message(chat_id, tg.msg_help())
        elif text.startswith("/cancel"):
            _handle_cancel_appointment(chat_id)
        elif text.startswith("/appointment"):
            _handle_book_appointment(chat_id)
        elif text.startswith("/status"):
            _handle_status(chat_id)
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
        existing = db.get_patient_by_chat_id(chat_id)
        if existing:
            tg.send_message(chat_id, tg.msg_already_registered(existing.get("first_name", "")))
            return

        # Ask for language preference first
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
            tg.send_message(
                chat_id,
                prompts.get(lang, prompts['am']),
                reply_markup={"keyboard": [[{"text": "ዝለል / Skip"}]], "resize_keyboard": True, "one_time_keyboard": True},
            )

        elif step == STEP_AWAIT_PHONE:
            data["phone"] = None if text in ("ዝለል", "Skip", "skip", "ዝለል / Skip") else text
            clinics = db.get_all_clinics()
            if not clinics:
                tg.send_message(chat_id, "⚠️ ምንም ክሊኒክ አልተገኘም። በኋላ ይሞክሩ።")
                clear_session(chat_id)
                return
            data["clinics"] = clinics
            set_session(chat_id, STEP_AWAIT_CLINIC, data)
            buttons = [[{"text": f"{i+1}. {c['name']}"}] for i, c in enumerate(clinics)]
            prompts = {
                'am': 'እባክዎ ክሊኒክ ይምረጡ፦',
                'en': 'Please select your clinic:',
                'or': 'Kiliinika kee filadhu:'
            }
            tg.send_message(
                chat_id,
                prompts.get(lang, prompts['am']),
                reply_markup={"keyboard": buttons, "resize_keyboard": True, "one_time_keyboard": True},
            )

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

            patient = db.create_patient(
                chat_id   = chat_id,
                first_name= data.get("first_name", ""),
                last_name = data.get("last_name", ""),
                phone     = data.get("phone"),
                clinic_id = chosen_clinic["id"],
                language  = lang,
            )
            if not patient:
                tg.send_message(chat_id, "⚠️ ምዝገባ አልተሳካም። እባክዎ ደግመው ይሞክሩ።")
                clear_session(chat_id)
                return

            set_session(chat_id, STEP_REGISTERED, {"patient_id": patient["id"]})
            messages = {
                'am': f'✅ ምዝገባዎ ተጠናቋል!\n\nእንኳን ደህና መጡ {data.get("first_name", "")}!\n🏥 {chosen_clinic.get("name", "")}',
                'en': f'✅ Registration complete!\n\nWelcome {data.get("first_name", "")}!\n🏥 {chosen_clinic.get("name", "")}',
                'or': f'✅ Galmaheen kee xumurame!\n\nBaga nagaan dhufte {data.get("first_name", "")}!\n🏥 {chosen_clinic.get("name", "")}'
            }
            tg.send_message(chat_id, messages.get(lang, messages['am']))

        else:
            existing = db.get_patient_by_chat_id(chat_id)
            if not existing:
                set_session(chat_id, STEP_AWAIT_LANGUAGE, {"first_name": user.get("first_name", "")})
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "🇪🇹 አማርኛ", "callback_data": "lang_am"}],
                        [{"text": "🇬🇧 English", "callback_data": "lang_en"}],
                        [{"text": "🌍 Afaan Oromoo", "callback_data": "lang_or"}]
                    ]
                }
                tg.send_message(chat_id, "ቋንቋ ይምረጡ | Choose language | Afaan filadhu:", reply_markup=keyboard)
            else:
                tg.send_message(chat_id, tg.msg_unknown())

    except Exception as e:
        logger.error(f"_handle_text error: {e}")


def _handle_cancel_appointment(chat_id: str) -> None:
    """Cancel the next upcoming appointment for this patient."""
    try:
        patient = db.get_patient_by_chat_id(chat_id)
        if not patient:
            tg.send_message(chat_id, "⚠️ ምዝገባ አልተገኘም። /start ይፃፉ።")
            return

        now = datetime.now(timezone.utc).isoformat()
        res = (
            db.get_client()
            .table("appointments")
            .select("*")
            .eq("patient_id", patient["id"])
            .eq("status", "confirmed")
            .gte("appointment_time", now)
            .order("appointment_time", desc=False)
            .limit(1)
            .execute()
        )
        appt = res.data[0] if res.data else None
        if not appt:
            tg.send_message(chat_id, "ℹ️ ምንም ቀጠሮ አልተገኘም።")
            return

        db.get_client().table("appointments").update({
            "status": "cancelled",
            "cancelled_at": db.now_utc(),
        }).eq("id", appt["id"]).execute()

        lang = patient.get("language", "am")
        messages = {
            'am': '✅ ቀጠሮዎ ተሰርዟል። /appointment በመጻፍ አዲስ ቀጠሮ ይያዙ።',
            'en': '✅ Your appointment has been cancelled. Use /appointment to book a new one.',
            'or': '✅ Beellamni kee haqameera. /appointment fayyadamiitii beellama haaraa qabadhu.'
        }
        tg.send_message(chat_id, messages.get(lang, messages['am']))
    except Exception as e:
        logger.error(f"_handle_cancel_appointment error: {e}")


def _handle_book_appointment(chat_id: str) -> None:
    """Placeholder — real flow would collect date/time via inline keyboard."""
    try:
        patient = db.get_patient_by_chat_id(chat_id)
        if not patient:
            tg.send_message(chat_id, "⚠️ ምዝገባ አልተገኘም። /start ይፃፉ።")
            return
        lang = patient.get("language", "am")
        messages = {
            'am': '📅 ቀጠሮ ለማስያዝ ክሊኒኩን ያነጋግሩ። ቦታ ሲገኝ እናሳውቅዎታለን።',
            'en': '📅 Please contact the clinic to book an appointment. We will notify you when a slot opens.',
            'or': '📅 Beellama qabadhuuf kiliinikaa qunnamaa. Yeroo bakki argamu isin beeksifna.'
        }
        tg.send_message(chat_id, messages.get(lang, messages['am']))
    except Exception as e:
        logger.error(f"_handle_book_appointment error: {e}")


def _handle_status(chat_id: str) -> None:
    """Show next upcoming appointment."""
    try:
        patient = db.get_patient_by_chat_id(chat_id)
        if not patient:
            tg.send_message(chat_id, "⚠️ ምዝገባ አልተገኘም። /start ይፃፉ።")
            return

        now = datetime.now(timezone.utc).isoformat()
        res = (
            db.get_client()
            .table("appointments")
            .select("*, clinics(name)")
            .eq("patient_id", patient["id"])
            .eq("status", "confirmed")
            .gte("appointment_time", now)
            .order("appointment_time", desc=False)
            .limit(1)
            .execute()
        )
        appt = res.data[0] if res.data else None
        lang = patient.get("language", "am")
        if not appt:
            messages = {
                'am': 'ℹ️ ምንም ቀጠሮ አልተገኘም።',
                'en': 'ℹ️ No appointments found.',
                'or': "ℹ️ Beellamni hin argamne."
            }
            tg.send_message(chat_id, messages.get(lang, messages['am']))
            return

        clinic_name = (appt.get("clinics") or {}).get("name", "ክሊኒክ")
        appt_time   = appt.get("appointment_time", "—")
        messages = {
            'am': f'📅 <b>ቀጣዩ ቀጠሮዎ:</b>\n\n🏥 {clinic_name}\n🕐 {appt_time}',
            'en': f'📅 <b>Your next appointment:</b>\n\n🏥 {clinic_name}\n🕐 {appt_time}',
            'or': f'📅 <b>Beellamni kee itti aanu:</b>\n\n🏥 {clinic_name}\n🕐 {appt_time}'
        }
        tg.send_message(chat_id, messages.get(lang, messages['am']))
    except Exception as e:
        logger.error(f"_handle_status error: {e}")


def _handle_callback_query(callback_query: dict) -> None:
    """Handle inline button presses (language choice, waitlist slot claim)."""
    try:
        cq_id   = callback_query["id"]
        data    = callback_query.get("data", "")
        chat_id = str(callback_query["from"]["id"])

        tg.answer_callback_query(cq_id)

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
            return

        # --- Slot claim ---
        if data.startswith("claim_slot:"):
            parts = data.split(":", 3)
            if len(parts) < 4:
                return
            _, waitlist_id, appointment_id, slot_time = parts

            claimed = db.atomic_claim_waitlist_slot(waitlist_id, slot_time)
            if claimed:
                patient = db.get_patient_by_chat_id(chat_id)
                if patient:
                    db.create_appointment(
                        patient_id=patient["id"],
                        clinic_id=patient.get("clinic_id", ""),
                        appointment_time=slot_time,
                        notes="ከቁጥር ዝርዝር የተያዘ ቦታ",
                    )
                    db.log_recovery_event(
                        clinic_id=patient.get("clinic_id", ""),
                        appointment_id=appointment_id,
                        waitlist_id=waitlist_id,
                        patient_id=patient["id"],
                        slot_time=slot_time,
                    )
                tg.send_message(chat_id, tg.msg_slot_claimed())
            else:
                tg.send_message(chat_id, tg.msg_slot_already_taken())

        # --- Decline slot ---
        elif data == "decline_slot":
            tg.send_message(chat_id, "እሺ፣ ተረድቻለሁ። ሌላ ቀጠሮ ሲገኝ እናሳውቅዎታለን።")

    except Exception as e:
        logger.error(f"_handle_callback_query error: {e}")


# ---------------------------------------------------------------------------
# Cron: Send 24-hour reminders
# ---------------------------------------------------------------------------

@app.route("/cron/send-reminders", methods=["POST"])
def cron_send_reminders():
    """Sends reminder messages for appointments within the next 24 hours."""
    sent = 0
    failed = 0
    try:
        appointments = db.get_upcoming_appointments_needing_reminder()
        for appt in appointments:
            try:
                patient     = appt.get("patients") or {}
                clinic      = appt.get("clinics") or {}
                chat_id     = patient.get("telegram_chat_id")
                first_name  = patient.get("first_name", "ታካሚ")
                clinic_name = clinic.get("name", "ክሊኒክ")
                appt_time   = appt.get("appointment_time", "—")
                lang        = patient.get("language", "am")

                if not chat_id:
                    failed += 1
                    continue

                updated = db.mark_appointment_reminded(appt["id"])
                if not updated:
                    failed += 1
                    continue

                reminder_messages = {
                    'am': f'ሰላም {first_name}! ነገ በ{appt_time} በ{clinic_name} ቀጠሮ አለዎት። እንደምትመጡ ተስፋ እናደርጋለን!',
                    'en': f'Hello {first_name}! You have an appointment tomorrow at {appt_time} at {clinic_name}. We look forward to seeing you!',
                    'or': f'Akkam {first_name}! Boru sa\'a {appt_time} irratti {clinic_name} beellama qabda. Isin arguu hawwina!'
                }
                msg = reminder_messages.get(lang, reminder_messages['am'])

                result = tg.send_message(chat_id, msg)
                if result:
                    sent += 1
                else:
                    failed += 1

            except Exception as inner_e:
                logger.error(f"reminder inner error for appt {appt.get('id')}: {inner_e}")
                failed += 1

    except Exception as e:
        logger.error(f"cron_send_reminders error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "sent": sent, "failed": failed}), 200


# ---------------------------------------------------------------------------
# Cron: Slot Recovery
# ---------------------------------------------------------------------------

@app.route("/cron/recover-slots", methods=["POST"])
def cron_recover_slots():
    """Find today's cancelled slots and offer them to waitlist patients."""
    offered = 0
    try:
        clinics = db.get_all_clinics()
        for clinic in clinics:
            try:
                clinic_id   = clinic["id"]
                clinic_name = clinic.get("name", "ክሊኒክ")
                slots       = db.get_cancelled_slots_today(clinic_id)

                for slot in slots:
                    try:
                        next_entry = db.get_next_waitlist_entry(clinic_id)
                        if not next_entry:
                            break

                        patient    = next_entry.get("patients") or {}
                        chat_id    = patient.get("telegram_chat_id")
                        slot_time  = slot.get("appointment_time", "—")

                        if not chat_id:
                            continue

                        db.mark_slot_recovery_offered(slot["id"])

                        inline_buttons = [[
                            {"text": "✅ አዎ፣ ቦታ ይዣለሁ",
                             "callback_data": f"claim_slot:{next_entry['id']}:{slot['id']}:{slot_time}"},
                            {"text": "❌ አይ፣ አመሰግናለሁ",
                             "callback_data": "decline_slot"},
                        ]]
                        tg.send_inline_keyboard(
                            chat_id,
                            tg.msg_slot_available(clinic_name, slot_time),
                            inline_buttons,
                        )
                        offered += 1

                    except Exception as slot_e:
                        logger.error(f"slot recovery inner error: {slot_e}")

            except Exception as clinic_e:
                logger.error(f"slot recovery clinic error: {clinic_e}")

    except Exception as e:
        logger.error(f"cron_recover_slots error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "offered": offered}), 200


# ---------------------------------------------------------------------------
# Cron: Weekly Report
# ---------------------------------------------------------------------------

@app.route("/cron/weekly-report", methods=["POST"])
def cron_weekly_report():
    """Computes last 7 days stats per clinic and sends a summary."""
    try:
        now       = datetime.now(timezone.utc)
        week_ago  = (now - timedelta(days=7)).isoformat()
        now_iso   = now.isoformat()
        clinics   = db.get_all_clinics()

        if not clinics:
            tg.send_message(OWNER_CHAT_ID, "📊 ሪፖርት: ምንም ክሊኒክ አልተገኘም።")
            return jsonify({"ok": True, "clinics_reported": 0}), 200

        reports_sent = 0
        for clinic in clinics:
            try:
                clinic_id   = clinic["id"]
                clinic_name = clinic.get("name", "ክሊኒክ")
                appts       = db.get_appointments_for_weekly_report(clinic_id, week_ago, now_iso)

                total     = len(appts)
                completed = sum(1 for a in appts if a.get("status") == "completed")
                cancelled = sum(1 for a in appts if a.get("status") == "cancelled")
                confirmed = sum(1 for a in appts if a.get("status") == "confirmed")

                tg.send_message(
                    OWNER_CHAT_ID,
                    tg.msg_weekly_report(clinic_name, total, completed, cancelled, confirmed),
                )
                reports_sent += 1

            except Exception as clinic_e:
                logger.error(f"weekly report clinic error: {clinic_e}")

        return jsonify({"ok": True, "clinics_reported": reports_sent}), 200

    except Exception as e:
        logger.error(f"cron_weekly_report error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Cron: Billing
# ---------------------------------------------------------------------------

@app.route("/cron/run-billing", methods=["POST"])
def cron_run_billing():
    """Mark all completed-but-unbilled appointments as billed."""
    try:
        clinics     = db.get_all_clinics()
        total_billed = 0

        for clinic in clinics:
            try:
                clinic_id   = clinic["id"]
                clinic_name = clinic.get("name", "ክሊኒክ")
                unbilled    = db.get_unbilled_appointments(clinic_id)

                billed_count  = 0
                total_amount  = 0.0

                for appt in unbilled:
                    try:
                        amount = appt.get("fee", DEFAULT_BILL_AMOUNT) or DEFAULT_BILL_AMOUNT
                        success = db.mark_appointment_billed(appt["id"], float(amount))
                        if success:
                            billed_count  += 1
                            total_amount  += float(amount)
                            total_billed  += 1
                    except Exception as appt_e:
                        logger.error(f"billing appt error: {appt_e}")

                if billed_count > 0:
                    tg.send_message(
                        OWNER_CHAT_ID,
                        tg.msg_billing_summary(clinic_name, billed_count, total_amount),
                    )

            except Exception as clinic_e:
                logger.error(f"billing clinic error: {clinic_e}")

        return jsonify({"ok": True, "total_billed": total_billed}), 200

    except Exception as e:
        logger.error(f"cron_run_billing error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Cron: Master Data Logger
# ---------------------------------------------------------------------------

@app.route("/cron/log-masterdata", methods=["POST"])
def cron_log_masterdata():
    """Snapshot all key table counts into master_data."""
    try:
        snapshot = db.get_master_data_snapshot()
        if not snapshot:
            return jsonify({"ok": False, "error": "empty snapshot"}), 500

        saved = db.log_master_data(snapshot)
        if not saved:
            return jsonify({"ok": False, "error": "db insert failed"}), 500

        logger.info(f"Master data logged: {snapshot}")
        return jsonify({"ok": True, "snapshot": snapshot}), 200

    except Exception as e:
        logger.error(f"cron_log_masterdata error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "dende-bot",
        "utc": db.now_utc(),
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
