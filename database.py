import logging
# Force deploy v2 - May 27
from supabase import create_client

logger = logging.getLogger(__name__)

SUPABASE_URL = "https://wjcmvfdlnqvtckssswht.supabase.co"
SUPABASE_KEY = "sb_secret_oWTaagovsY5gaLhrhpQoKw_3Wn8zbZt"

def get_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_all_clinics():
    try:
        client = get_client()
        res = client.table("clinics").select("*").execute()
        logger.info(f"Clinics found: {len(res.data)}")
        return res.data or []
    except Exception as e:
        logger.error(f"get_all_clinics error: {e}")
        return []

def get_patient_by_chat_id(chat_id):
    try:
        client = get_client()
        res = client.table("patients").select("*").eq("chat_id", chat_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_patient_by_chat_id error: {e}")
        return None

def create_patient(chat_id, first_name, last_name, phone=None, clinic_id=None, language="am"):
    try:
        client = get_client()
        payload = {
            "chat_id": chat_id,
            "name": f"{first_name} {last_name}".strip(),
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone or chat_id,
            "clinic_id": clinic_id,
            "language": language,
            "created_at": "now()"
        }
        res = client.table("patients").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_patient error: {e}")
        return None

def now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def get_upcoming_appointments_needing_reminder():
    return []

def mark_appointment_reminded(appointment_id):
    return True

def get_appointments_for_weekly_report(clinic_id, start_iso, end_iso):
    return []

def create_appointment(patient_id, clinic_id, appointment_time, notes=None):
    return None

def get_next_waitlist_entry(clinic_id):
    return None

def atomic_claim_waitlist_slot(waitlist_id, appointment_time):
    return False

def get_cancelled_slots_today(clinic_id):
    return []

def mark_slot_recovery_offered(appointment_id):
    return True

def log_recovery_event(clinic_id, appointment_id, waitlist_id, patient_id, slot_time):
    return True

def get_unbilled_appointments(clinic_id):
    return []

def mark_appointment_billed(appointment_id, amount):
    return True

def get_master_data_snapshot():
    return {"patients": 0, "appointments": 0, "waitlist_active": 0, "active_clinics": 0}

def log_master_data(snapshot):
    return True
