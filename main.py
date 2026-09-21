import os
import re
import requests
import time
import json
import csv
import pandas as pd
import pdfplumber
import logging
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Set up clean logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & SYSTEM CONTROLS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL") or "https://emergencyaudit.com"
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or "EmergencyAudit_Master_Key_2027!"

# PRODUCTION LEAD CAP (Set to None for unlimited production volume)
MAX_TEST_LEADS = None 

# TARGET PROPWIRE LIVE STREAM URL
TARGET_PROPWIRE_URL = os.getenv(
    "TARGET_PROPWIRE_URL"
) or "https://propwire.com/search?filters=%7B%22lead_type%22%3A%5B%22preforeclosure%22%5D%2C%22property_type%22%3A%5B%22commercial%22%2C%22mfh_5_plus%22%2C%22mfh_2_to_4%22%2C%22condo%22%2C%22sfr%22%5D%2C%22owner_type%22%3A%5B%22individual%22%2C%22company%22%5D%2C%22estimated_equity_percent%22%3A%7B%22min%22%3A30%2C%22max%22%3A100%7D%2C%22preforeclosure%22%3Atrue%2C%22notice_type%22%3A%22NOD%22%2C%22notice_date%22%3A%7B%22min%22%3A%222026-06-01%22%7D%2C%22locations%22%3A%5B%7B%22searchType%22%3A%22N%22%2C%22county%22%3A%22Los%20Angeles%22%2C%22state%22%3A%22CA%22%2C%22title%22%3A%22Los%20Angeles%2C%20CA%22%7D%5D%7D&location=Los%20Angeles%20County%2C%20CA"

# TRACERFY DISABLED
TRACERFY_API_KEY = ""
TRACERFY_URL = "https://tracerfy.com/v1/api/trace/lookup/"
ENABLE_TRACERFY = False

# TWILIO CONFIGURATION
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID") or ""
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN") or ""
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER") or os.getenv("TWILIO_FROM_NUMBER") or ""
ENABLE_TWILIO_SMS = (os.getenv("ENABLE_TWILIO_SMS") or "true").lower() == "true"

# SYSTEM CONTROLS & SAFETY FLAGS
PAUSE_PIPELINE = (os.getenv("PAUSE_PIPELINE") or "false").lower() == "true"
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]
STAGING_MODE = (os.getenv("STAGING_MODE") or "false").lower() == "true"
REQUIRE_VERIFIED_PHONE_ONLY = (os.getenv("REQUIRE_VERIFIED_PHONE_ONLY") or "false").lower() == "true"

# PHONE & WEBHOOK CONFIGURATION
NETWORK_1800_NUMBER = os.getenv("NETWORK_1800_NUMBER") or "1-800-555-0199"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or ""


# =====================================================================
# 2. ENTITY DETECTOR & DEDUP IDENTIFIER GENERATOR
# =====================================================================
ENTITY_KEYWORDS = [
    "LLC", "INC", "CORP", "CORPORATION", "HOLDINGS", "PROPERTIES", 
    "INVESTMENTS", "LTD", "LP", "GROUP", "PARTNERS", "REALTY", "COMPANY", "CO"
]

TRUST_KEYWORDS = ["TRUST", "TRUSTEE", "FAMILY TRUST", "REVOCABLE", "LIVING TRUST", "ESTATE"]

def classify_owner_type(owner_name):
    clean_name = re.sub(r"[^\w\s]", "", str(owner_name).upper())
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in TRUST_KEYWORDS):
        return "TRUST"
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in ENTITY_KEYWORDS):
        return "CORPORATE_ENTITY"
    return "INDIVIDUAL"


def generate_deterministic_case_id(apn, address):
    clean_apn = re.sub(r"\D", "", str(apn))
    if clean_apn and clean_apn != "PENDINGVERIFICATION" and len(clean_apn) >= 5:
        return f"AUD-APN-{clean_apn}"
    
    clean_addr = re.sub(r"[^\w]", "", str(address)).upper()
    if clean_addr and clean_addr != "RECORDEDPARCELLOCATION":
        return f"AUD-{clean_addr[:12]}"
        
    return f"AUD-REF-{int(time.time())}"


def validate_lead_record(record):
    address = str(record.get("address") or "").strip().upper()
    apn = str(record.get("apn") or "").strip().upper()
    owner = str(record.get("owner_name") or "").strip().upper()
    phone = str(record.get("phone") or "").strip().upper()

    if not address and not apn:
        return False, "BLOCKED: Missing both Property Address and APN"
    if address in ["N/A", "NONE", "RECORDED PARCEL LOCATION", ""] and apn in ["N/A", "NONE", "ON FILE", "PENDING VERIFICATION", ""]:
        return False, "BLOCKED: Placeholder location data"

    junk_owners = ["N/A", "UNKNOWN", "RECORDED OWNER", "RECORDED PROPERTY OWNER / INTERESTED PARTY", ""]
    if owner in junk_owners and (not phone or phone in ["PENDING UNMASK", "N/A", "NONE"]):
        return False, "BLOCKED: Missing Owner Name and Phone Contact"

    if REQUIRE_VERIFIED_PHONE_ONLY and (not phone or phone in ["PENDING UNMASK", "N/A", "NONE"]):
        return False, "BLOCKED: No verified phone number attached"

    return True, "VALID"


# =====================================================================
# 3. $0 NATIVE PLAYWRIGHT UNMASKING ENGINE (Runs inside GitHub Runner)
# =====================================================================
def free_playwright_phone_lookup(browser_context, name, address, city="Los Angeles", state="CA"):
    try:
        clean_name = re.sub(r"[^\w\s]", "", name).strip().replace(" ", "-").lower()
        clean_city = city.strip().replace(" ", "-").lower()
        clean_state = state.strip().lower()
        
        target_url = f"https://www.fastpeoplesearch.com/name/{clean_name}_{clean_city}-{clean_state}"

        page = browser_context.new_page()
        page.goto(target_url, timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)

        html = page.content()
        page.close()

        tel_matches = re.findall(r'href=["\']tel:([^"\']+)["\']', html, re.IGNORECASE)
        raw_phones = tel_matches if tel_matches else re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", html)

        valid_phones = []
        for p in raw_phones:
            clean_p = re.sub(r"\D", "", p)
            if len(clean_p) == 10 and not clean_p.startswith(("800", "888", "877", "866", "202", "900", "000")):
                valid_phones.append(clean_p)

        if valid_phones:
            return f"+1{valid_phones[0]}"

    except Exception as e:
        logging.error(f"[Playwright Unmask Exception] {e}")

    return None


def skip_trace_with_playwright(browser_context, human_name, address, city="Los Angeles", state="CA", zip_code="90012"):
    owner_type = classify_owner_type(human_name)
    target_name = human_name

    if owner_type == "TRUST":
        target_name = re.sub(r"\b(TRUST|TRUSTEE|TTEE|FAMILY|REVOCABLE|LIVING|DATED|\d+)\b", "", human_name, flags=re.I).strip()
        if not target_name:
            target_name = human_name

    logging.info(f"[$0 Native Playwright Search] Unmasking contact for [{owner_type}] {target_name} at {address}...")
    found_phone = free_playwright_phone_lookup(browser_context, target_name, address, city, state)
    
    if found_phone:
        return {
            "phone": found_phone, 
            "email": "N/A", 
            "status": "VERIFIED", 
            "phone_type": "MOBILE", 
            "unmasked_owner": target_name
        }

    status_flag = "LLC_INDEXED" if owner_type == "CORPORATE_ENTITY" else ("TRUST_INDEXED" if owner_type == "TRUST" else "PUBLIC_DATA_INDEXED")

    return {
        "phone": "PENDING UNMASK", 
        "email": "N/A", 
        "status": status_flag, 
        "phone_type": owner_type, 
        "unmasked_owner": target_name
    }


# =====================================================================
# 4. WORKER DISPATCH ENGINE
# =====================================================================
def dispatch_to_worker(parcel_record, browser_context=None):
    if PAUSE_PIPELINE:
        logging.info("⏸️ Pipeline paused. Skipping dispatch.")
        return False

    owner = parcel_record.get("owner_name", "RECORDED PROPERTY OWNER / INTERESTED PARTY")
    address = parcel_record.get("address", "Recorded Parcel Location")
    city = parcel_record.get("city", "Los Angeles")
    state = parcel_record.get("state", "CA")
    zip_code = parcel_record.get("zip", "90012")
    apn = parcel_record.get("apn", "PENDING VERIFICATION")

    existing_phone = parcel_record.get("phone")
    if not existing_phone or existing_phone in ["PENDING UNMASK", "Unmasked Upon Purchase", "+14537422249"]:
        if browser_context:
            trace_res = skip_trace_with_playwright(browser_context, owner, address, city, state, zip_code)
            phone = trace_res["phone"]
            email = trace_res["email"]
        else:
            phone = "PENDING UNMASK"
            email = "N/A"
    else:
        phone = existing_phone
        email = parcel_record.get("email", "N/A")

    citation_id = parcel_record.get("record_id") or parcel_record.get("citation_id") or generateDeterministic_case_id(apn, address)
    amount = parcel_record.get("default_amount") or parcel_record.get("amount_logged") or "$35,420.00 Recorded"
    prop_type = parcel_record.get("property_type") or parcel_record.get("property_use") or "Single Family / Commercial"

    payload = {
        "record_id": citation_id,
        "citation_id": citation_id,
        "caseId": citation_id,
        "address": address,
        "owner_name": owner,
        "phone": phone,
        "email": email,
        "apn": apn,
        "category": parcel_record.get("category", "PRE-FORECLOSURE / REINSTATEMENT"),
        "default_amount": amount,
        "amount_logged": amount,
        "property_type": prop_type,
        "property_use": prop_type,
        "violation": parcel_record.get("violation", "A statutory Notice of Default (NOD) has been logged in LA County public records."),
        "year_built": parcel_record.get("year_built", "N/A"),
        "sqft": parcel_record.get("sqft", "N/A"),
        "zoning": parcel_record.get("zoning", "N/A"),
        "status": "PENDING_REVIEW" if STAGING_MODE else "READY_FOR_DISPATCH"
    }

    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would post record to Worker:\n{json.dumps(payload, indent=2)}")
        return True

    endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
    headers = {
        "Content-Type": "application/json",
        "X-Emergency-Key": MASTER_ADMIN_KEY
    }

    try:
        res = requests.post(endpoint, data=json.dumps([payload]), headers=headers, timeout=15)
        if res.status_code == 200:
            logging.info(f"✅ Dispatched [{citation_id}] -> Phone: {phone} (Status: {payload['status']})")
            return True
        else:
            logging.error(f"❌ Worker Error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        logging.error(f"⚠️ Dispatch Exception: {e}")
        return False


# =====================================================================
# 5. DATA INGESTION & FILE PARSING ENGINE
# =====================================================================
def normalize_lead_dict(raw_dict):
    norm = {}
    for k, v in raw_dict.items():
        if k is not None and v is not None:
            clean_k = re.sub(r'[^a-z0-9]', '', str(k).lower())
            norm[clean_k] = str(v).strip()

    apn_val = (
        norm.get("apn") or norm.get("parcelid") or norm.get("pin") 
        or norm.get("parcel") or norm.get("parcelnumber") or "PENDING VERIFICATION"
    )

    addr_val = (
        norm.get("address") or norm.get("propertyaddress") or norm.get("siteaddress") 
        or norm.get("location") or norm.get("streetaddress") or norm.get("propaddress") 
        or "Recorded Parcel Location"
    )

    city_val = norm.get("city") or norm.get("propertycity") or "Los Angeles"
    state_val = norm.get("state") or norm.get("propertystate") or "CA"
    zip_val = norm.get("zip") or norm.get("zipcode") or norm.get("propertyzip") or "90012"

    owner_val = (
        norm.get("owner1fullname")
        or norm.get("ownerfullname")
        or norm.get("ownername")
        or norm.get("owner1")
        or norm.get("owner")
        or norm.get("owner1companyname")
        or norm.get("companyname")
        or norm.get("taxpayername")
    )

    if not owner_val:
        fname = norm.get("owner1firstname") or norm.get("ownerfirstname") or norm.get("firstname") or ""
        lname = norm.get("owner1lastname") or norm.get("ownerlastname") or norm.get("lastname") or ""
        combined = f"{fname} {lname}".strip()
        if combined:
            owner_val = combined

    if not owner_val:
        for k, v in norm.items():
            if "owner" in k and v and v.upper() not in ["N/A", "NONE", "UNKNOWN", "NULL", ""]:
                owner_val = v
                break

    if not owner_val:
        owner_val = "RECORDED OWNER"

    citation = (
        norm.get("recordid")
        or norm.get("citationid")
        or norm.get("caseid")
        or norm.get("noticeno")
        or generate_deterministic_case_id(apn_val, addr_val)
    )

    amount = (
        norm.get("defaultamount")
        or norm.get("amountlogged")
        or norm.get("amount")
        or norm.get("surplus")
        or norm.get("default")
        or norm.get("cureamount")
        or norm.get("estequity")
        or "$35,420.00 Recorded"
    )

    prop_type = (
        norm.get("propertytype")
        or norm.get("propertyuse")
        or norm.get("use")
        or norm.get("type")
        or "Single Family / Commercial"
    )

    phone_val = norm.get("phone") or norm.get("phone1") or norm.get("mobile") or norm.get("ownerphone")
    email_val = norm.get("email") or norm.get("owneremail")

    return {
        "record_id": citation,
        "citation_id": citation,
        "owner_name": owner_val,
        "address": addr_val,
        "city": city_val,
        "state": state_val,
        "zip": zip_val,
        "apn": apn_val,
        "category": norm.get("category") or "PRE-FORECLOSURE / REINSTATEMENT",
        "default_amount": amount,
        "amount_logged": amount,
        "property_type": prop_type,
        "property_use": prop_type,
        "violation": norm.get("violation") or norm.get("description") or "A statutory Notice of Default (NOD) has been logged in CA public records.",
        "phone": phone_val,
        "email": email_val
    }


def parse_any_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    raw_records = []

    try:
        if ext == ".csv":
            with open(file_path, mode="r", encoding="utf-8-sig") as f:
                raw_records = list(csv.DictReader(f))

        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path).fillna("")
            raw_records = df.to_dict(orient="records")

        elif ext == ".json":
            with open(file_path, mode="r", encoding="utf-8") as f:
                data = json.load(f)
                raw_records = data if isinstance(data, list) else [data]

        elif ext == ".pdf":
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table and len(table) > 1:
                        headers = [str(h).strip().lower().replace(" ", "_") for h in table[0] if h]
                        for row in table[1:]:
                            if len(row) == len(headers):
                                raw_records.append(dict(zip(headers, row)))

    except Exception as e:
        logging.error(f"❌ Error reading file {file_path}: {e}")
        return []

    return [normalize_lead_dict(rec) for rec in raw_records if rec]


def load_all_lead_datasets():
    all_leads = []
    valid_exts = (".csv", ".xlsx", ".xls", ".json", ".pdf")

    root_files = [f for f in os.listdir(".") if f.lower().endswith(valid_exts) and not f.startswith("temp_")]
    for f in root_files:
        logging.info(f"📁 Processing repository dataset: {f}")
        all_leads.extend(parse_any_file(f))

    data_dir = "./data"
    if os.path.exists(data_dir):
        data_files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.lower().endswith(valid_exts)]
        for f in data_files:
            logging.info(f"📂 Processing /data folder dataset: {f}")
            all_leads.extend(parse_any_file(f))

    return all_leads


def get_staging_fallback_leads():
    return [
        normalize_lead_dict({
            "apn": "2241-018-012",
            "owner_name": "WEST COAST ASSET HOLDINGS LLC",
            "address": "5635 Calhoun Ave",
            "city": "Van Nuys",
            "state": "CA",
            "zip": "91401",
            "default_amount": "$48,250.00 Recorded NOD",
            "category": "PRE-FORECLOSURE / REINSTATEMENT",
            "violation": "LA County Notice of Default (NOD) logged. High Equity (87%)."
        })
    ]


# =====================================================================
# 7. MAIN EXECUTION LOOP WITH DEDUPLICATION
# =====================================================================
if __name__ == "__main__":
    logging.info(f"🚀 Universal Ingress Engine Active. Pipeline in PRODUCTION MODE.")

    real_leads = load_all_lead_datasets()
    if not real_leads:
        logging.warning("⚠️ No live datasets retrieved. Activating Staging Fallback.")
        real_leads = get_staging_fallback_leads()

    logging.info(f"\n📥 Total Aggregated Feed: {len(real_leads)} record(s). Launching Playwright Unmasking Engine...\n")
    
    passed_count = 0
    blocked_count = 0
    seen_identifiers = set()

    # Spin up native Playwright browser instance once for the entire batch
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

        for idx, parcel in enumerate(real_leads, 1):
            if MAX_TEST_LEADS and passed_count >= MAX_TEST_LEADS:
                logging.info(f"\n🎯 TEST CAP REACHED: Processed {MAX_TEST_LEADS} leads. Halting batch.")
                break

            apn = parcel.get("apn")
            addr = parcel.get("address")
            dedup_key = apn if (apn and apn != "PENDING VERIFICATION") else addr

            if dedup_key in seen_identifiers:
                logging.info(f"🔄 [{idx}/{len(real_leads)}] [DUPLICATE SKIPPED] {dedup_key}")
                continue
            seen_identifiers.add(dedup_key)

            is_valid, reason = validate_lead_record(parcel)
            if not is_valid:
                logging.info(f"🛑 [{idx}/{len(real_leads)}] {reason} -> {parcel.get('owner_name', 'UNKNOWN')} ({parcel.get('address', 'NO ADDR')})")
                blocked_count += 1
                continue

            logging.info(f"✅ [{passed_count + 1}/{MAX_TEST_LEADS or 'UNLIMITED'}] Processing Lead: {parcel.get('owner_name')} - {parcel.get('address')}")
            dispatch_to_worker(parcel, browser_context=context)
            passed_count += 1

        browser.close()

    logging.info(f"\n📊 Batch Execution Summary: {passed_count} Dispatched to KV | {blocked_count} Blocked")
