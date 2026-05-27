"""
database.py — Supabase/PostgreSQL layer for Dende dental clinic bot.
All timestamps stored as UTC ISO-8601 strings.
"""

import logging
from datetime import datetime, timezone
from supabase import create_client, Client

logger = logging.getLogger(__name__)

SUPABASE_URL = "https://wjcmvfdlnqvtckssswht.supabase.co"
SUPABASE_KEY = "sb_secret_vLvzADMIbwDXRSMq1VruEQ_6X2gj_ON"

# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def now_utc() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------

def get_patient_by_chat_id(chat_id: str) -> dict | None:
    try:
        res = get_client().table("patients").select("*").eq("chat_id", chat_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_patient_by_chat_id error: {e}")
        return None


def create_patient(chat_id: str, first_name: str, last_name: str, phone: str | None = None,
                   clinic_id: str | None = None, language: str = "am") -> dict | None:
    try:
        payload = {
            "chat_id": chat_id,
            "name": f"{first_name} {last_name}".strip(),
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone or chat_id,
            "clinic_id": clinic_id,
            "language": language,
            "created_at": now_utc(),
        }
        res = get_client().table("patients").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_patient error: {e}")
        return None


# ---------------------------------------------------------------------------
# Clinics
# ---------------------------------------------------------------------------

def get_all_clinics() -> list[dict]:
    try:
        res = get_client().table("clinics").select("*").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"get_all_clinics error: {e}")
        return []


def get_clinic_by_id(clinic_id: str) -> dict | None:
    try:
        res = get_client().table("clinics").select("*").eq("id", clinic_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_clinic_by_id error: {e}")
        return None


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

def get_upcoming_appointments_needing_reminder() -> list[dict]:
    try:
        now = datetime.now(timezone.utc)
        window_end = now.replace(hour=23, minute=59, second=59).isoformat()
        res = (
            get_client()
            .table("appointments")
            .select("*, patients(chat_id, first_name, language), clinics(name)")
            .gte("appointment_time", now.isoformat())
            .lte("appointment_time", window_end)
            .eq("reminder_sent", False)
            .eq("status", "PENDING")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_upcoming_appointments_needing_reminder error: {e}")
        return []


def mark_appointment_reminded(appointment_id: str) -> bool:
    try:
        get_client().table("appointments").update({"reminder_sent": True}).eq("id", appointment_id).execute()
        return True
    except Exception as e:
        logger.error(f"mark_appointment_reminded error: {e}")
        return False


def get_appointments_for_weekly_report(clinic_id: str, start_iso: str, end_iso: str) -> list[dict]:
    try:
        res = (
            get_client()
            .table("appointments")
            .select("*")
            .eq("clinic_id", clinic_id)
            .gte("appointment_time", start_iso)
            .lt("appointment_time", end_iso)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_appointments_for_weekly_report error: {e}")
        return []


def create_appointment(patient_id: str, clinic_id: str, appointment_time: str,
                       notes: str | None = None) -> dict | None:
    try:
        payload = {
            "patient_id": patient_id,
            "clinic_id": clinic_id,
            "appointment_time": appointment_time,
            "status": "confirmed",
            "notes": notes,
            "created_at": now_utc(),
        }
        res = get_client().table("appointments").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_appointment error: {e}")
        return None


# ---------------------------------------------------------------------------
# Waitlist
# ---------------------------------------------------------------------------

def get_next_waitlist_entry(clinic_id: str) -> dict | None:
    try:
        res = (
            get_client()
            .table("waitlist")
            .select("*, patients(chat_id, first_name)")
            .eq("clinic_id", clinic_id)
            .order("date_added", desc=False)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_next_waitlist_entry error: {e}")
        return None


def atomic_claim_waitlist_slot(waitlist_id: str, appointment_time: str) -> bool:
    try:
        res = (
            get_client()
            .table("waitlist")
            .delete()
            .eq("id", waitlist_id)
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error(f"atomic_claim_waitlist_slot error: {e}")
        return False


def get_cancelled_slots_today(clinic_id: str) -> list[dict]:
    try:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        day_end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        res = (
            get_client()
            .table("appointments")
            .select("*")
            .eq("clinic_id", clinic_id)
            .eq("status", "CANCELLED")
            .gte("appointment_time", day_start)
            .lte("appointment_time", day_end)
            .eq("recovery_offered", False)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_cancelled_slots_today error: {e}")
        return []


def mark_slot_recovery_offered(appointment_id: str) -> bool:
    try:
        get_client().table("appointments").update({"recovery_offered": True}).eq("id", appointment_id).execute()
        return True
    except Exception as e:
        logger.error(f"mark_slot_recovery_offered error: {e}")
        return False


# ---------------------------------------------------------------------------
# Recovery Log
# ---------------------------------------------------------------------------

def log_recovery_event(clinic_id: str, appointment_id: str, waitlist_id: str,
                       patient_id: str, slot_time: str) -> bool:
    try:
        payload = {
            "clinic_id": clinic_id,
            "date": datetime.now(timezone.utc).date().isoformat(),
            "original_slot": slot_time,
            "filled_by": patient_id,
            "revenue_recovered": 0,
            "procedure_type": "",
        }
        get_client().table("recovery_log").insert(payload).execute()
        return True
    except Exception as e:
        logger.error(f"log_recovery_event error: {e}")
        return False


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

def get_unbilled_appointments(clinic_id: str) -> list[dict]:
    try:
        res = (
            get_client()
            .table("appointments")
            .select("*")
            .eq("clinic_id", clinic_id)
            .eq("status", "completed")
            .eq("billed", False)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_unbilled_appointments error: {e}")
        return []


def mark_appointment_billed(appointment_id: str, amount: float) -> bool:
    try:
        get_client().table("appointments").update({
            "billed": True,
            "fee": amount,
        }).eq("id", appointment_id).execute()
        return True
    except Exception as e:
        logger.error(f"mark_appointment_billed error: {e}")
        return False


# ---------------------------------------------------------------------------
# Master Data
# ---------------------------------------------------------------------------

def get_master_data_snapshot() -> dict:
    try:
        client = get_client()
        patients_count = client.table("patients").select("id", count="exact").execute().count or 0
        appointments_count = client.table("appointments").select("id", count="exact").execute().count or 0
        waitlist_count = client.table("waitlist").select("id", count="exact").execute().count or 0
        clinics_count = client.table("clinics").select("id", count="exact").execute().count or 0
        return {
            "patients": patients_count,
            "appointments": appointments_count,
            "waitlist_active": waitlist_count,
            "active_clinics": clinics_count,
        }
    except Exception as e:
        logger.error(f"get_master_data_snapshot error: {e}")
        return {}


def log_master_data(snapshot: dict) -> bool:
    try:
        payload = {"snapshot": snapshot, "logged_at": now_utc()}
        get_client().table("master_data").insert(payload).execute()
        return True
    except Exception as e:
        logger.error(f"log_master_data error: {e}")
        return False
