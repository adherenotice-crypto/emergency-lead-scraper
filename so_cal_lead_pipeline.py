#!/usr/bin/env python3
"""
============================================================================
EMERGENCYAUDIT.com! | AUTOMATED PAY-PER-CALL PIPELINE & SCRAPER
============================================================================
Architecture : GitHub Actions -> Socrata Municipal -> LA Assessor -> Tracerfy 
               -> Cloudflare Worker -> Twilio SMS -> EMERGENCYAUDIT.com! /case
============================================================================
"""

import os
import re
import json
import time
import datetime
import hashlib
from zoneinfo import ZoneInfo
import requests
from urllib.parse import quote

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & SYSTEM CONTROLS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL", "https://emergencyaudit.com")
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY", "EmergencyAudit_Master_Key_2027!")

# TRACERFY CONFIGURATION (SYNCHRONOUS LOOKUP ENDPOINT)
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY", "")
TRACERFY_URL = os.getenv("TRACERFY_URL", "https://tracerfy.com/v1/api/trace/lookup/")
ENABLE_TRACERFY = os.getenv("ENABLE_TRACERFY", "false").lower() in ["true", "1", "yes"]

# TWILIO CONFIGURATION
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "") or os.getenv("TWILIO_FROM_NUMBER", "")
ENABLE_TWILIO_SMS = os.getenv("ENABLE_TWILIO_SMS", "false").lower() == "true"

# SYSTEM CONTROLS & SAFETY FLAGS
PAUSE_PIPELINE = os.getenv("PAUSE_PIPELINE", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
STAGING_MODE = os.getenv("STAGING_MODE", "false").lower() == "true"

# PHONE & WEBHOOK CONFIGURATION
NETWORK_1800_NUMBER = os.getenv("NETWORK_1800_NUMBER", "18005550199")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # Discord/Slack Webhook for alerts

# ACTIVE SOCAL MUNICIPAL ENDPOINTS
SOCRATA_FEEDS = [
    {
        "name": "LA Building & Safety - Code Enforcement",
        "url": "https://data.lacity.org/resource/u82d-eh7z.json?$limit=150",
        "default_cat": "COMMERCIAL"
    },
    {
        "name": "LA Building & Safety - Vacant Abatement",
        "url": "https://data.lacity.org/resource/q3ak-s5hy.json?$limit=150",
        "default_cat": "EMERGENCY"
    },
    {
        "name": "LA City Active Code Citations & Orders",
        "url": "https://data.lacity.org/resource/2n62-383m.json?$limit=150",
        "default_cat": "TRADE"
    }
]

CORPORATE_KEYWORDS = [
    "LLC", "INC", "CORP", "CORPORATION", "HOLDINGS", "PROPERTIES", 
    "TRUST", "LP", "PARTNERSHIP", "REALTY", "BANK", "CITY OF", "DEPT"
]

MUNICIPAL_CODE_MAP = {
    "91.8104": {"desc": "Unsafe Building Maintenance & Structural Hazard Notice", "category": "COMMERCIAL"},
    "91.103.1": {"desc": "Unpermitted Construction & Alteration Order", "category": "COMMERCIAL"},
    "17920.3": {"desc": "Substandard Structure & Building Compliance Order", "category": "COMMERCIAL"},
    "57.105.1": {"desc": "Fire Safety Violation & Hazardous Material Storage Order", "category": "EMERGENCY"},
    "91.8903": {"desc": "Emergency Abatement & Secure Building Notice", "category": "EMERGENCY"},
    "64.70": {"desc": "Water Quality & Environmental Discharge Violation", "category": "TRADE"},
    "22.110": {"desc": "Zoning Non-Compliance & Overgrown Lot Clean-Up Order", "category": "TRADE"}
}


# =====================================================================
# 2. SYSTEM HEALTH & SAFETY GUARDRAILS
# =====================================================================
def send_webhook_alert(message):
    if not WEBHOOK_URL:
        return
    try:
        payload = {"content": f"🚨 **EMERGENCYAUDIT.com! Pipeline Alert:** {message}"}
        requests.post(WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[Webhook Exception] {e}")

def is_remote_paused():
    if PAUSE_PIPELINE:
        return True
    try:
        res = requests.get(f"{WORKER_URL}/api/status", timeout=4)
        if res.status_code == 200 and res.json().get("paused") is True:
            return True
    except Exception as e:
        print(f"[Warning] Could not fetch remote pause status: {e}")
    return False

def is_compliant_sms_window(tz_name="America/Los_Angeles", start_hour=8, end_hour=20):
    local_now = datetime.datetime.now(ZoneInfo(tz_name))
    return start_hour <= local_now.hour < end_hour

def purge_unmasked_kv_records():
    """Sweeps Back Office KV memory and deletes stale or unmasked records."""
    try:
        res = requests.get(f"{WORKER_URL}/api/leads", headers={"X-Emergency-Key": MASTER_ADMIN_KEY}, timeout=6)
        if res.status_code == 200:
            leads = res.json().get("leads", [])
            unmasked_keys = [
                l.get("citation_id") or l.get("id") 
                for l in leads 
                if l.get("phone") in ["PENDING UNMASK", "Unmasked Upon Purchase", "", None] or l.get("phone_type") != "MOBILE"
            ]
            if unmasked_keys:
                print(f"[KV Auto-Cleanup] Purging {len(unmasked_keys)} stale/unmasked records from Back Office...")
                requests.post(
                    f"{WORKER_URL}/api/delete-batch", 
                    json={"keys": unmasked_keys}, 
                    headers={"X-Emergency-Key": MASTER_ADMIN_KEY}, 
                    timeout=6
                )
    except Exception as e:
        print(f"[KV Auto-Cleanup Exception] {e}")


# =====================================================================
# 3. MUNICIPAL EXTRACTION & DATA PARSING
# =====================================================================
def extract_address(item):
    for field in ["address", "primary_address", "prop_address", "site_address", "location_address", "street_address"]:
        val = item.get(field)
        if val and isinstance(val, str) and len(val.strip()) >= 5:
            return val.strip().upper()

    house = item.get("house_number") or item.get("street_number") or item.get("house_no") or ""
    street = item.get("street_name") or item.get("street") or item.get("st_name") or ""
    st_type = item.get("street_type") or item.get("st_type") or ""
    combined = f"{house} {street} {st_type}".strip()
    return combined.upper() if len(combined) >= 5 else None

def extract_zip(item, address_text=""):
    for field in ["zip_code", "zipcode", "zip", "postal_code", "site_zip", "prop_zip", "zip_code_1"]:
        val = str(item.get(field, "")).strip()
        if re.match(r"^9\d{4}$", val):
            return val

    match = re.search(r"\b(9\d{4})\b", str(address_text))
    if match:
        return match.group(1)

    return "90012"

def extract_case_id(item, address):
    """Generates a guaranteed unique 6-digit Case Reference ID per parcel address using MD5 hashing."""
    for key in ["case_number", "order_number", "cn_id", "citation_number", "apno", "case_no"]:
        val = item.get(key)
        if val and isinstance(val, str) and len(val.strip()) > 3:
            return val.strip().upper()
    
    md5_hash = hashlib.md5(address.encode("utf-8")).hexdigest()
    unique_int = int(md5_hash[:8], 16) % 899999 + 100000
    return f"AUD-{unique_int}"

def extract_violation_desc(item):
    """Extracts exact municipal order details and ordinance codes from Socrata payloads."""
    primary = item.get("primary_violation") or item.get("order_type") or item.get("case_type") or ""
    secondary = item.get("violation_description") or item.get("description") or item.get("comments") or item.get("sub_type") or ""
    code_sec = item.get("code_section") or item.get("ordinance") or item.get("section") or ""

    details = []
    if primary:
        details.append(str(primary).strip())
    if secondary and secondary != primary:
        details.append(str(secondary).strip())
    if code_sec:
        details.append(f"(LAMC Sec. {str(code_sec).strip()})")

    if details:
        return " — ".join(details)
    
    search_fields = [
        "violation_code", "violation_type", "violation_detail", "order_title", 
        "notes", "prop_type", "case_type_desc", "order_type_desc", "sub_type_desc", 
        "action_taken", "reason"
    ]
    for key in search_fields:
        val = item.get(key)
        if val and isinstance(val, str) and len(val.strip()) > 3:
            return val.strip()

    return "Order to Comply & Notice of Fee Assessment"

def translate_municipal_code(raw_violation_string, default_category="COMMERCIAL"):
    if not raw_violation_string or raw_violation_string.strip() == "":
        return "Order to Comply & Notice of Fee Assessment", default_category

    for code, info in MUNICIPAL_CODE_MAP.items():
        if code in raw_violation_string:
            return info["desc"], info["category"]

    return raw_violation_string, default_category

def lookup_tax_assessor(address):
    """Queries LA County Assessor API with robust street direction parsing & formatted APN output."""
    try:
        clean_addr = re.sub(r"[^\w\s]", "", address).strip().upper()
        parts = clean_addr.split()
        if len(parts) >= 2:
            street_num = parts[0]
            street_name = parts[2] if parts[1] in ["N", "S", "E", "W"] and len(parts) > 2 else parts[1]
            
            url = "https://data.lacounty.gov/resource/28ee-2bgz.json"
            params = {
                "$where": f"situshouse_no='{street_num}' AND situsstreetname LIKE '%{street_name}%'", 
                "$limit": "1"
            }
            res = requests.get(url, params=params, headers={"User-Agent": "EmergencyAudit/2.0"}, timeout=6)
            
            if res.status_code == 200:
                records = res.json()
                if isinstance(records, list) and len(records) > 0:
                    rec = records[0]
                    apn_raw = str(rec.get("ain") or rec.get("apn") or "").strip()
                    formatted_apn = f"{apn_raw[:4]}-{apn_raw[4:7]}-{apn_raw[7:]}" if len(apn_raw) == 10 else (apn_raw if apn_raw else "N/A")
                    
                    owner_raw = str(rec.get("owner1") or rec.get("ain_owner1") or "").strip().upper()
                    return {
                        "owner_name": owner_raw if len(owner_raw) > 2 else "PROPERTY OWNER / MANAGER",
                        "mail_address": str(rec.get("mail_address") or address).upper(),
                        "apn": formatted_apn if len(formatted_apn) > 3 else "N/A",
                        "zip": rec.get("situszip") or rec.get("zip") or None
                    }
    except Exception as e:
        print(f"[Assessor Error] {e}")

    return {"owner_name": "PROPERTY OWNER / MANAGER", "mail_address": address, "apn": "N/A", "zip": None}

def unmask_entity_owner(owner_name, mail_address):
    if "C/O" in owner_name or "C/O" in mail_address:
        parts = owner_name.split("C/O") if "C/O" in owner_name else mail_address.split("C/O")
        possible_human = parts[-1].strip().split(",")[0]
        if len(possible_human) > 3 and not any(kw in possible_human.upper() for kw in CORPORATE_KEYWORDS):
            return possible_human
    return owner_name

def is_corporate_entity(name):
    if not name or name == "PROPERTY OWNER / MANAGER":
        return False
    upper_name = name.upper()
    return any(keyword in upper_name for keyword in CORPORATE_KEYWORDS)


# =====================================================================
# 4. SKIP-TRACING & TWILIO DISPATCH ENGINES
# =====================================================================
def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90012"):
    if not (ENABLE_TRACERFY or TRACERFY_API_KEY):
        return {"phone": None, "status": "HOLDING_MODE", "phone_type": "UNKNOWN", "unmasked_owner": human_name}

    headers = {
        "Authorization": f"Bearer {TRACERFY_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "address": address,
        "city": city,
        "state": state,
        "zip": str(zip_code) if zip_code else "90012"
    }

    clean_name = human_name.strip() if human_name else ""
    if clean_name and clean_name != "PROPERTY OWNER / MANAGER":
        name_parts = clean_name.split()
        if len(name_parts) >= 2:
            payload["find_owner"] = False
            payload["first_name"] = name_parts[0]
            payload["last_name"] = name_parts[-1]
        else:
            payload["find_owner"] = True
    else:
        payload["find_owner"] = True

    try:
        res = requests.post(TRACERFY_URL, json=payload, headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            if data.get("hit") and data.get("persons"):
                for person in data["persons"]:
                    unmasked_name = person.get("full_name") or human_name
                    phones = person.get("phones", [])
                    
                    for p in phones:
                        p_num = p.get("number")
                        p_type = str(p.get("type", "")).lower()
                        
                        if p_num and (p_type in ["mobile", "cell"] or not p_type):
                            return {
                                "phone": p_num,
                                "status": "VERIFIED",
                                "phone_type": "MOBILE",
                                "unmasked_owner": unmasked_name
                            }
        else:
            print(f"[Tracerfy HTTP {res.status_code}] {res.text[:200]}")
    except Exception as e:
        print(f"[Tracerfy Exception] {e}")

    return {"phone": None, "status": "FAILED", "phone_type": "NO_MOBILE", "unmasked_owner": human_name}

def send_twilio_sms(to_phone, case_no, case_url):
    if not ENABLE_TWILIO_SMS:
        print(f"[SMS Skipped] ENABLE_TWILIO_SMS=false for Case #{case_no}")
        return False

    if not is_compliant_sms_window():
        print(f"[TCPA Quiet Hours] Outside legal sending window. Holding SMS for Case #{case_no}")
        return False

    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        print("[Twilio Error] Missing Twilio credentials.")
        send_webhook_alert("Twilio dispatch failed due to missing API credentials.")
        return False

    if not to_phone or len(to_phone.strip()) < 10:
        return False

    try:
        clean_phone = re.sub(r"[^\d+]", "", to_phone)
        if not clean_phone.startswith("+"):
            clean_phone = f"+1{clean_phone}" if len(clean_phone) == 10 else f"+{clean_phone}"

        sms_body = f"Public Record Advisory: Citation #{case_no} indexed. Review parcel status & resolution options: {case_url} Reply STOP to opt out."

        twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        data = {
            "From": TWILIO_PHONE_NUMBER,
            "To": clean_phone,
            "Body": sms_body
        }

        res = requests.post(
            twilio_url,
            data=data,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=8
        )

        if res.status_code in [200, 201]:
            print(f"[Twilio Success] Sent SMS to {clean_phone} for Case #{case_no}")
            return True
        else:
            print(f"[Twilio Error {res.status_code}] {res.text}")
            send_webhook_alert(f"Twilio API rejected message for Case #{case_no}: {res.text}")
    except Exception as e:
        print(f"[Twilio Exception] {e}")
        send_webhook_alert(f"Twilio API exception for Case #{case_no}: {e}")

    return False


# =====================================================================
# 5. MASTER PIPELINE EXECUTION ENGINE
# =====================================================================
def run_pipeline():
    if is_remote_paused():
        print("=====================================================")
        print(" PIPELINE STATUS: PAUSED (Kill-Switch Active)")
        print(" Execution stopped. No records processed or SMS sent.")
        print("=====================================================")
        send_webhook_alert("Pipeline execution skipped because System Remote Kill-Switch is ACTIVE.")
        return

    # STEP 1: AUTO-PURGE UNMASKED/STALE RECORDS
    purge_unmasked_kv_records()

    print("[Pipeline] Running municipal extraction & routing...")
    processed_count = 0
    sms_sent_count = 0

    grouped_properties = {}

    for feed in SOCRATA_FEEDS:
        try:
            res = requests.get(feed["url"], timeout=10)
            if res.status_code != 200:
                send_webhook_alert(f"Socrata Feed Failure [{res.status_code}]: {feed['name']}")
                continue

            for item in res.json():
                address = extract_address(item)
                if not address or len(address) < 5:
                    continue

                raw_violation = extract_violation_desc(item)
                plain_violation, category = translate_municipal_code(raw_violation, feed["default_cat"])
                case_no = extract_case_id(item, address)

                if address not in grouped_properties:
                    grouped_properties[address] = {
                        "address": address,
                        "case_no": case_no,
                        "raw_items": [item],
                        "violations": [plain_violation],
                        "raw_codes": [raw_violation],
                        "category": category,
                        "zip_code": extract_zip(item, address)
                    }
                else:
                    if plain_violation not in grouped_properties[address]["violations"]:
                        grouped_properties[address]["violations"].append(plain_violation)
                        grouped_properties[address]["raw_codes"].append(raw_violation)

        except Exception as e:
            print(f"[Feed Exception] {feed['name']}: {e}")
            send_webhook_alert(f"Feed exception in {feed['name']}: {e}")

    print(f"[Deduplication Complete] Grouped into {len(grouped_properties)} unique property parcels.")

    # STEP 2: PROCESS & DISPATCH ONLY VERIFIED UNMASKED LEADS
    for address, prop in grouped_properties.items():
        case_no = prop["case_no"]
        assessor_data = lookup_tax_assessor(address)
        zip_code = assessor_data["zip"] or prop["zip_code"]
        human_owner = unmask_entity_owner(assessor_data["owner_name"], assessor_data["mail_address"])

        if is_corporate_entity(human_owner):
            print(f"[Pre-Scrub] Skipping corporate entity '{human_owner}' for Case #{case_no}")
            continue

        trace_data = skip_trace(human_owner, address, "Los Angeles", "CA", zip_code)
        phone_number = trace_data["phone"]
        phone_type = trace_data["phone_type"]
        final_owner = trace_data.get("unmasked_owner") or human_owner

        # STRICT GUARDRAIL: Skip any property without a verified mobile phone number
        if not phone_number or phone_number in ["PENDING UNMASK", "Unmasked Upon Purchase"] or phone_type != "MOBILE":
            print(f"[Clean Data Guardrail] Dropping lead without verified mobile number for Case #{case_no}")
            continue

        encoded_address = quote(f"{address}, Los Angeles, CA {zip_code}")
        combined_violations = " • ".join(prop["violations"])
        combined_raw_codes = " | ".join(prop["raw_codes"])
        encoded_violation = quote(combined_violations[:250])

        # CONSTRUCT DYNAMIC DOOR 2 URL
        case_url = f"{WORKER_URL}/case?id={case_no}&address={encoded_address}&apn={assessor_data['apn']}&violation={encoded_violation}&phone={NETWORK_1800_NUMBER}"

        initial_status = "PENDING_REVIEW" if STAGING_MODE else "INDEXED"

        payload = {
            "citation_id": case_no,
            "address": f"{address}, Los Angeles, CA {zip_code}",
            "owner_name": final_owner,
            "phone": phone_number,
            "customerPhone": phone_number,
            "phone_type": phone_type,
            "violation": combined_violations[:250],
            "raw_code": combined_raw_codes[:250],
            "category": prop["category"],
            "case_url": case_url,
            "apn": assessor_data["apn"],
            "status": initial_status
        }

        if DRY_RUN:
            print(f"[DRY-RUN EXECUTION] Case #{case_no} | Category: {prop['category']} | Phone: {phone_number} ({phone_type})")
            continue

        try:
            res_push = requests.post(
                f"{WORKER_URL}/api/dispatch", 
                json=payload, 
                headers={"X-Emergency-Key": MASTER_ADMIN_KEY}, 
                timeout=5
            )
            if res_push.status_code == 200:
                processed_count += 1

                if not STAGING_MODE and phone_number and phone_type == "MOBILE":
                    if send_twilio_sms(phone_number, case_no, case_url):
                        sms_sent_count += 1
                        payload["status"] = "SENT"
                        requests.post(
                            f"{WORKER_URL}/api/dispatch", 
                            json=payload, 
                            headers={"X-Emergency-Key": MASTER_ADMIN_KEY}, 
                            timeout=5
                        )
            else:
                print(f"❌ DISPATCH REJECTED [{res_push.status_code}]: {res_push.text}")

        except Exception as e:
            print(f"[Dispatch Error] {e}")

        time.sleep(0.05)

    print(f"[Batch Complete] Processed {processed_count} verified records | Sent {sms_sent_count} SMS notices.")

if __name__ == "__main__":
    run_pipeline()
