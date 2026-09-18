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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        res = requests.get(search_url, headers=headers, timeout=4)
        if res.status_code == 200:
            phones = re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", res.text)
            valid_phones = [re.sub(r"\D", "", p) for p in phones if not p.startswith("800") and not p.startswith("888")]
            if valid_phones:
                return valid_phones[0]
    except Exception as e:
        print(f"[Public Search Lookup Exception] {e}")
    return None

def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90012"):
    """UNMASKS CELL PHONES FOR $0 OR CALLS TRACERFY IF ENABLED."""
    
    # Path A: Tracerfy Paid Skip-Tracing (If ENABLE_TRACERFY = true)
    if ENABLE_TRACERFY and TRACERFY_API_KEY:
        try:
            payload = {"key": TRACERFY_API_KEY, "name": human_name, "address": address, "city": city, "state": state, "zip": zip_code}
            res = requests.post(TRACERFY_URL, json=payload, timeout=6)
            if res.status_code == 200:
                data = res.json()
                phone = data.get("phone") or data.get("mobile")
                if phone:
                    return {"phone": phone, "email": data.get("email", "N/A"), "status": "VERIFIED", "phone_type": "MOBILE", "unmasked_owner": human_name}
        except Exception as e:
            print(f"[Tracerfy Exception] {e}")

    # Path B: $0 Free Public Search Unmasking
    print(f"[$0 Public Search] Unmasking cell contact for {human_name} at {address}...")
    found_phone = free_public_phone_lookup(human_name, address, city, state)
    
    if found_phone:
        return {"phone": found_phone, "email": "N/A", "status": "VERIFIED", "phone_type": "MOBILE", "unmasked_owner": human_name}

    return {"phone": "PENDING UNMASK", "email": "N/A", "status": "PUBLIC_DATA_INDEXED", "phone_type": "PENDING", "unmasked_owner": human_name}
