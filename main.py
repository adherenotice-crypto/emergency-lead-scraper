import os
import re
import requests
import time
import json
import csv
import pandas as pd
import pdfplumber
import logging

# Set up clean logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & SYSTEM CONTROLS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL") or "https://emergencyaudit.com"
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or "EmergencyAudit_Master_Key_2027!"
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

# PRODUCTION LEAD CAP (Set to None for unlimited production volume)
MAX_TEST_LEADS = None

# SYSTEM CONTROLS & SAFETY FLAGS
PAUSE_PIPELINE = (os.getenv("PAUSE_PIPELINE") or "false").lower() == "true"
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]
STAGING_MODE = (os.getenv("STAGING_MODE") or "false").lower() == "true"
REQUIRE_VERIFIED_PHONE_ONLY = (os.getenv("REQUIRE_VERIFIED_PHONE_ONLY") or "false").lower() == "true"


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
# 3. APIFY CHUNKED SKIP-TRACING ENGINE (Prevents 400 Timeout Errors)
# =====================================================================
def apify_bulk_skip_trace(lead_batch):
    if not APIFY_TOKEN:
        logging.warning("⚠️ APIFY_TOKEN secret not found in environment. Skipping Apify unmasking.")
        return {}

    logging.info(f"⚡ [APIFY ENGINE] Submitting batch of {len(lead_batch)} lead(s) for chunked unmasking...")

    results_map = {}
    CHUNK_SIZE = 15  # Process 15 leads per synchronous payload to prevent Apify memory/timeout errors

    for i in range(0, len(lead_batch), CHUNK_SIZE):
        chunk = lead_batch[i:i + CHUNK_SIZE]
        apify_searches = []
        lookup_map = {}

        for item in chunk:
            raw_name = item.get("owner_name", "")
            owner_type = classify_owner_type(raw_name)

            if owner_type == "TRUST":
                target_name = re.sub(r"\b(TRUST|TRUSTEE|TTEE|FAMILY|REVOCABLE|LIVING|DATED|\d+)\b", "", raw_name, flags=re.I).strip()
            else:
                target_name = raw_name

            name_parts = target_name.strip().split()
            if len(name_parts) >= 2:
                first_name = name_parts[0]
                last_name = " ".join(name_parts[1:])
            else:
                first_name = target_name
                last_name = ""

            city = item.get("city", "Los Angeles")
            state = item.get("state", "CA")

            match_key = f"{first_name.upper()}_{last_name.upper()}_{city.upper()}"
            lookup_map[match_key] = item.get("record_id")

            apify_searches.append({
                "firstName": first_name,
                "lastName": last_name,
                "city": city,
                "state": state
            })

        actor_endpoint = f"https://api.apify.com/v2/acts/memo23~fastpeoplesearch-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        payload = {
            "searches": apify_searches,
            "maxItemsPerSearch": 1,
            "flattenedOutput": True
        }

        try:
            res = requests.post(actor_endpoint, json=payload, timeout=120)
            if res.status_code in [200, 201]:
                extracted_data = res.json()
                for record in extracted_data:
                    fn = (record.get("firstName") or record.get("search_firstName") or "").upper()
                    ln = (record.get("lastName") or record.get("search_lastName") or "").upper()
                    ct = (record.get("city") or record.get("search_city") or "").upper()
                    match_key = f"{fn}_{ln}_{ct}"

                    phone = record.get("Phone-1") or record.get("primaryPhone") or record.get("phone")
                    if not phone and record.get("phones"):
                        phone_objs = record.get("phones", [])
                        if phone_objs and isinstance(phone_objs, list):
                            phone = phone_objs[0].get("number") if isinstance(phone_objs[0], dict) else str(phone_objs[0])

                    if phone:
                        clean_p = re.sub(r"\D", "", str(phone))
                        if len(clean_p) == 10:
                            clean_p = f"+1{clean_p}"
                        elif len(clean_p) == 11 and clean_p.startswith("1"):
                            clean_p = f"+{clean_p}"
                        
                        citation_id = lookup_map.get(match_key)
                        if citation_id:
                            results_map[citation_id] = clean_p
            else:
                logging.error(f"❌ Apify Chunk Error [{res.status_code}]: {res.text}")
        except Exception as e:
            logging.error(f"⚠️ Apify Chunk Exception: {e}")

        time.sleep(1)

    logging.info(f"✅ [APIFY ENGINE] Successfully unmasked {len(results_map)} live phone number(s) across chunks!")
    return results_map


# =====================================================================
# 4. DATA INGESTION & FILE PARSING ENGINE
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
# 5. MAIN EXECUTION LOOP (BULK UNMASK & SINGLE-REQUEST DISPATCH)
# =====================================================================
if __name__ == "__main__":
    logging.info("🚀 Universal Ingress Engine Active. Pipeline in PRODUCTION MODE.")

    real_leads = load_all_lead_datasets()
    if not real_leads:
        logging.warning("⚠️ No live datasets retrieved. Activating Staging Fallback.")
        real_leads = get_staging_fallback_leads()

    logging.info(f"\n📥 Total Aggregated Feed: {len(real_leads)} record(s). Processing...\n")
    
    passed_count = 0
    blocked_count = 0
    seen_identifiers = set()
    needs_unmask_batch = []
    prepared_records = []

    for idx, parcel in enumerate(real_leads, 1):
        if MAX_TEST_LEADS and passed_count >= MAX_TEST_LEADS:
            break

        apn = parcel.get("apn")
        addr = parcel.get("address")
        dedup_key = apn if (apn and apn != "PENDING VERIFICATION") else addr

        if dedup_key in seen_identifiers:
            continue
        seen_identifiers.add(dedup_key)

        is_valid, reason = validate_lead_record(parcel)
        if not is_valid:
            blocked_count += 1
            continue

        citation_id = parcel.get("record_id") or parcel.get("citation_id") or generate_deterministic_case_id(apn, addr)
        parcel["record_id"] = citation_id

        existing_phone = parcel.get("phone")
        if not existing_phone or existing_phone in ["PENDING UNMASK", "Unmasked Upon Purchase", "+14537422249", "+13333333333"]:
            needs_unmask_batch.append(parcel)
        
        prepared_records.append(parcel)
        passed_count += 1

    # Execute Apify Bulk Unmasking in chunks
    unmasked_phones = {}
    if needs_unmask_batch:
        unmasked_phones = apify_bulk_skip_trace(needs_unmask_batch)

    # Build final KV payload
    dispatch_queue = []
    for parcel in prepared_records:
        cid = parcel["record_id"]
        phone = parcel.get("phone")

        if not phone or phone in ["PENDING UNMASK", "Unmasked Upon Purchase", "+14537422249", "+13333333333"]:
            phone = unmasked_phones.get(cid, "PENDING UNMASK")

        dispatch_queue.append({
            "record_id": cid,
            "citation_id": cid,
            "caseId": cid,
            "address": parcel.get("address"),
            "owner_name": parcel.get("owner_name"),
            "phone": phone,
            "email": parcel.get("email", "N/A"),
            "apn": parcel.get("apn"),
            "category": parcel.get("category", "PRE-FORECLOSURE / REINSTATEMENT"),
            "default_amount": parcel.get("default_amount") or "$35,420.00 Recorded",
            "property_type": parcel.get("property_type") or "Single Family / Commercial",
            "violation": parcel.get("violation") or "A statutory Notice of Default (NOD) has been logged in LA County public records.",
            "status": "PENDING_REVIEW" if STAGING_MODE else "READY_FOR_DISPATCH"
        })

    # Dispatch Single Bulk Request to Cloudflare Worker
    if dispatch_queue:
        logging.info(f"\n🚀 Dispatching {len(dispatch_queue)} unmasked record(s) to Cloudflare KV...")
        endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
        headers = {
            "Content-Type": "application/json",
            "X-Emergency-Key": MASTER_ADMIN_KEY
        }

        if DRY_RUN:
            logging.info(f"🧪 [DRY RUN] Would post bulk payload to Worker:\n{json.dumps(dispatch_queue[:2], indent=2)}")
        else:
            try:
                res = requests.post(endpoint, data=json.dumps(dispatch_queue), headers=headers, timeout=30)
                if res.status_code == 200:
                    logging.info(f"✅ Bulk Dispatch Successful! All {len(dispatch_queue)} records stored in KV.")
                else:
                    logging.error(f"❌ Worker Error [{res.status_code}]: {res.text}")
            except Exception as e:
                logging.error(f"⚠️ Dispatch Exception: {e}")

    logging.info(f"\n📊 Batch Execution Summary: {passed_count} Processed & Dispatched | {blocked_count} Blocked")
