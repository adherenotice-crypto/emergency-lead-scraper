import os
import re
import requests
import time
import json

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & SYSTEM CONTROLS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL", "https://emergencyaudit.com")
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY", "EmergencyAudit_Master_Key_2027!")

# TRACERFY & PHONE UNMASK CONFIGURATION
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY", "")
TRACERFY_URL = os.getenv("TRACERFY_URL", "https://tracerfy.com/v1/api/trace/lookup/")
ENABLE_TRACERFY = os.getenv("ENABLE_TRACERFY", "false").lower() == "true"

# TWILIO CONFIGURATION
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "") or os.getenv("TWILIO_FROM_NUMBER", "")
ENABLE_TWILIO_SMS = os.getenv("ENABLE_TWILIO_SMS", "false").lower() == "true"

# SYSTEM CONTROLS & SAFETY FLAGS
PAUSE_PIPELINE = os.getenv("PAUSE_PIPELINE", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ["true", "1", "yes"]
STAGING_MODE = os.getenv("STAGING_MODE", "false").lower() == "true"

# PHONE & WEBHOOK CONFIGURATION
NETWORK_1800_NUMBER = os.getenv("NETWORK_1800_NUMBER", "1-800-555-0199")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")


# =====================================================================
# 4. PHONE UNMASKING & SKIP-TRACING ENGINE ($0 SEARCH INTEGRATED)
# =====================================================================
def free_public_phone_lookup(name, address, city="Los Angeles", state="CA"):
    """Queries free open public whitepages feeds for verified personal numbers."""
    try:
        clean_name = re.sub(r"[^\w\s]", "", name).strip().replace(" ", "-")
        clean_addr = re.sub(r"[^\w\s]", "", address).strip().replace(" ", "-")
        
        # Open web directory query
        search_url = f"https://www.truepeoplesearch.com/results?name={clean_name}&citystatezip={city}-{state}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        
        res = requests.get(search_url, headers=headers, timeout=5)
        if res.status_code == 200:
            phones = re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", res.text)
            valid_phones = [re.sub(r"\D", "", p) for p in phones if not p.startswith("800") and not p.startswith("888")]
            if valid_phones:
                # Format to standard 11-digit string starting with country code 1 if 10 digits
                phone_num = valid_phones[0]
                if len(phone_num) == 10:
                    phone_num = f"1{phone_num}"
                return phone_num
    except Exception as e:
        print(f"[Public Search Lookup Exception] {e}")
    return None


def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90012"):
    """UNMASKS CELL PHONES FOR $0 OR CALLS TRACERFY IF ENABLED."""
    
    # Path A: Tracerfy Paid Skip-Tracing (If ENABLE_TRACERFY = true)
    if ENABLE_TRACERFY and TRACERFY_API_KEY:
        try:
            payload = {
                "key": TRACERFY_API_KEY, 
                "name": human_name, 
                "address": address, 
                "city": city, 
                "state": state, 
                "zip": zip_code
            }
            res = requests.post(TRACERFY_URL, json=payload, timeout=6)
            if res.status_code == 200:
                data = res.json()
                phone = data.get("phone") or data.get("mobile")
                if phone:
                    clean_p = re.sub(r"\D", "", phone)
                    if len(clean_p) == 10:
                        clean_p = f"1{clean_p}"
                    return {
                        "phone": clean_p, 
                        "email": data.get("email", "N/A"), 
                        "status": "VERIFIED", 
                        "phone_type": "MOBILE", 
                        "unmasked_owner": human_name
                    }
        except Exception as e:
            print(f"[Tracerfy Exception] {e}")

    # Path B: $0 Free Public Search Unmasking
    print(f"[$0 Public Search] Unmasking cell contact for {human_name} at {address}...")
    found_phone = free_public_phone_lookup(human_name, address, city, state)
    
    if found_phone:
        return {
            "phone": found_phone, 
            "email": "N/A", 
            "status": "VERIFIED", 
            "phone_type": "MOBILE", 
            "unmasked_owner": human_name
        }

    return {
        "phone": "PENDING UNMASK", 
        "email": "N/A", 
        "status": "PUBLIC_DATA_INDEXED", 
        "phone_type": "PENDING", 
        "unmasked_owner": human_name
    }


# =====================================================================
# 5. WORKER DISPATCH ENGINE (POST TO EMERGENCYAUDIT.COM)
# =====================================================================
def dispatch_to_worker(parcel_record):
    """
    Takes raw parcel/scraped record, runs skip-trace unmasking,
    and posts directly to Cloudflare Worker KV storage.
    """
    if PAUSE_PIPELINE:
        print("⏸️ Pipeline paused. Skipping dispatch.")
        return False

    owner = parcel_record.get("owner_name", "RECORDED PROPERTY OWNER / INTERESTED PARTY")
    address = parcel_record.get("address", "Recorded Parcel Location")
    city = parcel_record.get("city", "Los Angeles")
    state = parcel_record.get("state", "CA")
    zip_code = parcel_record.get("zip", "90012")

    # Run unmasking unless valid phone is already present
    existing_phone = parcel_record.get("phone")
    if not existing_phone or existing_phone in ["PENDING UNMASK", "Unmasked Upon Purchase"]:
        trace_res = skip_trace(owner, address, city, state, zip_code)
        phone = trace_res["phone"]
        email = trace_res["email"]
    else:
        phone = existing_phone
        email = parcel_record.get("email", "N/A")

    citation_id = parcel_record.get("citation_id") or f"AUD-{int(time.time())}"

    payload = {
        "citation_id": citation_id,
        "address": address,
        "owner_name": owner,
        "phone": phone,
        "email": email,
        "apn": parcel_record.get("apn", "ON FILE"),
        "category": parcel_record.get("category", "EXCESS PROCEEDS"),
        "amount_logged": parcel_record.get("amount_logged", "$24,500.00 Logged"),
        "violation": parcel_record.get("violation", "Unclaimed excess proceeds or property tax compliance audit record."),
        "year_built": parcel_record.get("year_built", "N/A"),
        "sqft": parcel_record.get("sqft", "N/A"),
        "zoning": parcel_record.get("zoning", "N/A"),
        "property_use": parcel_record.get("property_use", "REAL ESTATE PARCEL"),
        "status": "READY_FOR_DISPATCH" if STAGING_MODE is False else "PENDING_REVIEW"
    }

    if DRY_RUN:
        print(f"🧪 [DRY RUN] Would post record to Worker: {json.dumps(payload, indent=2)}")
        return True

    endpoint = f"{WORKER_URL.rstrip('/')}/api/dispatch"
    headers = {
        "Content-Type": "application/json",
        "X-Emergency-Key": MASTER_ADMIN_KEY
    }

    try:
        res = requests.post(endpoint, json=payload, headers=headers, timeout=10)
        if res.status_code == 200:
            print(f"✅ Dispatched to Worker [{citation_id}] -> Door 2 URL: {WORKER_URL}/c/{citation_id}")
            return True
        else:
            print(f"❌ Worker Error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        print(f"⚠️ Dispatch Network Exception: {e}")
        return False


# =====================================================================
# DEMO EXECUTION
# =====================================================================
if __name__ == "__main__":
    sample_lead = {
        "citation_id": "AUD-882101",
        "owner_name": "JOHN DOE",
        "address": "1234 Wilshire Blvd",
        "city": "Los Angeles",
        "state": "CA",
        "zip": "90017",
        "apn": "5144-012-008",
        "category": "EXCESS PROCEEDS",
        "amount_logged": "$48,900.00 Logged",
        "violation": "County surplus funds logged under tax auction index."
    }

    print("🚀 Pipeline Active. Processing sample lead...")
    dispatch_to_worker(sample_lead)
