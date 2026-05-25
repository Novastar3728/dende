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
        res = get_client().table("patients").select("*").eq("telegram_chat_id", chat_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_patient_by_chat_id error: {e}")
        return None


def create_patient(chat_id: str, first_name: str, last_name: str, phone: str | None = None,
                   clinic_id: str | None = None) -> dict | None:
    try:
        payload = {
            "telegram_chat_id": chat_id,
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "clinic_id": clinic_id,
            "registered_at": now_utc(),
            "is_active": True,
        }
        res = get_client().table("patients").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_patient error: {e}")
        return None


def update_patient(patient_id: str, updates: dict) -> dict | None:
    try:
        res = get_client().table("patients").update(updates).eq("id", patient_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"update_patient error: {e}")
        return None


# ---------------------------------------------------------------------------
# Clinics
# ---------------------------------------------------------------------------

def get_all_clinics() -> list[dict]:
    try:
        res = get_client().table("clinics").select("*").eq("is_active", True).execute()
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
    """
    Return appointments scheduled in the next 24 hours that have not been reminded.
    Expects columns: id, patient_id, clinic_id, appointment_time (UTC), reminded_at, status.
    """
    try:
        from dateutil.relativedelta import relativedelta
        now = datetime.now(timezone.utc)
        window_end = (now + relativedelta(hours=24)).isoformat()
        res = (
            get_client()
            .table("appointments")
            .select("*, patients(telegram_chat_id, first_name), clinics(name)")
            .gte("appointment_time", now.isoformat())
            .lte("appointment_time", window_end)
            .is_("reminded_at", "null")
            .eq("status", "confirmed")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_upcoming_appointments_needing_reminder error: {e}")
        return []


def mark_appointment_reminded(appointment_id: str) -> bool:
    """Update DB first, then caller sends message."""
    try:
        get_client().table("appointments").update({"reminded_at": now_utc()}).eq("id", appointment_id).execute()
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

def add_to_waitlist(patient_id: str, clinic_id: str, preferred_time: str | None = None) -> dict | None:
    try:
        payload = {
            "patient_id": patient_id,
            "clinic_id": clinic_id,
            "preferred_time": preferred_time,
            "status": "waiting",
            "added_at": now_utc(),
        }
        res = get_client().table("waitlist").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"add_to_waitlist error: {e}")
        return None


def get_next_waitlist_entry(clinic_id: str) -> dict | None:
    """FIFO: oldest waiting entry for a clinic."""
    try:
        res = (
            get_client()
            .table("waitlist")
            .select("*, patients(telegram_chat_id, first_name)")
            .eq("clinic_id", clinic_id)
            .eq("status", "waiting")
            .order("added_at", desc=False)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_next_waitlist_entry error: {e}")
        return None


def atomic_claim_waitlist_slot(waitlist_id: str, appointment_time: str) -> bool:
    """
    Atomically mark waitlist entry as 'claimed' only if still 'waiting'.
    Returns True on success, False on race-condition miss.
    """
    try:
        res = (
            get_client()
            .table("waitlist")
            .update({"status": "claimed", "claimed_at": now_utc(), "claimed_slot": appointment_time})
            .eq("id", waitlist_id)
            .eq("status", "waiting")   # optimistic lock — only update if still waiting
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error(f"atomic_claim_waitlist_slot error: {e}")
        return False


def get_cancelled_slots_today(clinic_id: str) -> list[dict]:
    """Return appointments cancelled today that can be offered to waitlist."""
    try:
        from dateutil.parser import parse as dtparse
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        day_end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        res = (
            get_client()
            .table("appointments")
            .select("*")
            .eq("clinic_id", clinic_id)
            .eq("status", "cancelled")
            .gte("appointment_time", day_start)
            .lte("appointment_time", day_end)
            .is_("recovery_offered_at", "null")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_cancelled_slots_today error: {e}")
        return []


def mark_slot_recovery_offered(appointment_id: str) -> bool:
    try:
        get_client().table("appointments").update({"recovery_offered_at": now_utc()}).eq("id", appointment_id).execute()
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
            "original_appointment_id": appointment_id,
            "waitlist_id": waitlist_id,
            "patient_id": patient_id,
            "slot_time": slot_time,
            "recovered_at": now_utc(),
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
            .is_("billed_at", "null")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_unbilled_appointments error: {e}")
        return []


def mark_appointment_billed(appointment_id: str, amount: float) -> bool:
    try:
        get_client().table("appointments").update({
            "billed_at": now_utc(),
            "billed_amount": amount,
        }).eq("id", appointment_id).execute()
        return True
    except Exception as e:
        logger.error(f"mark_appointment_billed error: {e}")
        return False


# ---------------------------------------------------------------------------
# Master Data
# ---------------------------------------------------------------------------

def get_master_data_snapshot() -> dict:
    """Aggregate counts for master_data logging."""
    try:
        client = get_client()
        patients_count = client.table("patients").select("id", count="exact").execute().count or 0
        appointments_count = client.table("appointments").select("id", count="exact").execute().count or 0
        waitlist_count = (
            client.table("waitlist").select("id", count="exact").eq("status", "waiting").execute().count or 0
        )
        clinics_count = client.table("clinics").select("id", count="exact").eq("is_active", True).execute().count or 0
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
