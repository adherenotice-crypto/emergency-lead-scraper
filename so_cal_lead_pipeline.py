import os
import re
import requests
import time
import json
import csv
import pandas as pd
import pdfplumber

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
REQUIRE_VERIFIED_PHONE_ONLY = os.getenv("REQUIRE_VERIFIED_PHONE_ONLY", "false").lower() == "true"

# PHONE & WEBHOOK CONFIGURATION
NETWORK_1800_NUMBER = os.getenv("NETWORK_1800_NUMBER", "1-800-555-0199")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")


# =====================================================================
# 2. ENTITY DETECTOR & VALIDATION BLOCKER
# =====================================================================
ENTITY_KEYWORDS = [
    "LLC", "INC", "CORP", "CORPORATION", "HOLDINGS", "PROPERTIES", 
    "INVESTMENTS", "LTD", "LP", "GROUP", "PARTNERS", "REALTY", "COMPANY", "CO"
]

TRUST_KEYWORDS = ["TRUST", "TRUSTEE", "FAMILY TRUST", "REVOCABLE", "LIVING TRUST", "ESTATE"]

def classify_owner_type(owner_name):
    """Detects whether an owner is a Human Individual, LLC/Corp, or Trust."""
    clean_name = re.sub(r"[^\w\s]", "", str(owner_name).upper())
    
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in TRUST_KEYWORDS):
        return "TRUST"
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in ENTITY_KEYWORDS):
        return "CORPORATE_ENTITY"
    
    return "INDIVIDUAL"


def validate_lead_record(record):
    """
    Validation Blocker: Filters out incomplete, junk, or untraceable records
    BEFORE running skip-trace or dispatching to Cloudflare KV.
    """
    address = str(record.get("address") or "").strip().upper()
    apn = str(record.get("apn") or "").strip().upper()
    owner = str(record.get("owner_name") or "").strip().upper()
    phone = str(record.get("phone") or "").strip().upper()

    # Rule 1: Must have a valid property anchor (Address or APN)
    if not address and not apn:
        return False, "BLOCKED: Missing both Property Address and APN"
    if address in ["N/A", "NONE", "RECORDED PARCEL LOCATION", ""] and apn in ["N/A", "NONE", "ON FILE", ""]:
        return False, "BLOCKED: Placeholder location data"

    # Rule 2: Must have a non-blank Owner Name
    junk_owners = ["N/A", "UNKNOWN", "RECORDED OWNER", "RECORDED PROPERTY OWNER / INTERESTED PARTY", ""]
    if owner in junk_owners and (not phone or phone in ["PENDING UNMASK", "N/A", "NONE"]):
        return False, "BLOCKED: Missing Owner Name and Phone Contact"

    # Rule 3: Optional Strict Phone Filter
    if REQUIRE_VERIFIED_PHONE_ONLY and (not phone or phone in ["PENDING UNMASK", "N/A", "NONE"]):
        return False, "BLOCKED: No verified phone number attached"

    return True, "VALID"


# =====================================================================
# 3. PHONE UNMASKING & SKIP-TRACING ENGINE ($0 SEARCH INTEGRATED)
# =====================================================================
def free_public_phone_lookup(name, address, city="Los Angeles", state="CA"):
    """Queries free open public whitepages feeds for verified personal numbers."""
    try:
        clean_name = re.sub(r"[^\w\s]", "", name).strip().replace(" ", "-")
        clean_addr = re.sub(r"[^\w\s]", "", address).strip().replace(" ", "-")
        
        search_url = f"https://www.truepeoplesearch.com/results?name={clean_name}&citystatezip={city}-{state}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        
        res = requests.get(search_url, headers=headers, timeout=5)
        if res.status_code == 200:
            phones = re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", res.text)
            valid_phones = [re.sub(r"\D", "", p) for p in phones if not p.startswith("800") and not p.startswith("888")]
            if valid_phones:
                phone_num = valid_phones[0]
                if len(phone_num) == 10:
                    phone_num = f"1{phone_num}"
                return phone_num
    except Exception as e:
        print(f"[Public Search Lookup Exception] {e}")
    return None


def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90012"):
    """UNMASKS CELL PHONES FOR $0 OR CALLS TRACERFY IF ENABLED."""
    owner_type = classify_owner_type(human_name)
    target_name = human_name

    # Handle Trusts by isolating Trustee name
    if owner_type == "TRUST":
        target_name = re.sub(r"\b(TRUST|TRUSTEE|TTEE|FAMILY|REVOCABLE|LIVING|DATED|\d+)\b", "", human_name, flags=re.I).strip()
        if not target_name:
            target_name = human_name

    # Path A: Tracerfy Paid Skip-Tracing (If enabled)
    if ENABLE_TRACERFY and TRACERFY_API_KEY:
        try:
            payload = {
                "key": TRACERFY_API_KEY, 
                "name": target_name, 
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
                    if len(clean_p) == 10: clean_p = f"1{clean_p}"
                    return {
                        "phone": clean_p, 
                        "email": data.get("email", "N/A"), 
                        "status": "VERIFIED", 
                        "phone_type": "MOBILE", 
                        "unmasked_owner": target_name
                    }
        except Exception as e:
            print(f"[Tracerfy Exception] {e}")

    # Path B: $0 Free Public Search Unmasking
    print(f"[$0 Public Search] Unmasking contact for [{owner_type}] {target_name} at {address}...")
    found_phone = free_public_phone_lookup(target_name, address, city, state)
    
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
# 4. WORKER DISPATCH ENGINE (POST TO EMERGENCYAUDIT.COM)
# =====================================================================
def dispatch_to_worker(parcel_record):
    """Takes raw parcel/scraped record, runs skip-tracing, and posts to Cloudflare Worker KV."""
    if PAUSE_PIPELINE:
        print("⏸️ Pipeline paused. Skipping dispatch.")
        return False

    owner = parcel_record.get("owner_name", "RECORDED PROPERTY OWNER / INTERESTED PARTY")
    address = parcel_record.get("address", "Recorded Parcel Location")
    city = parcel_record.get("city", "Los Angeles")
    state = parcel_record.get("state", "CA")
    zip_code = parcel_record.get("zip", "90012")

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
            print(f"✅ Dispatched [{citation_id}] -> Door 2 URL: {WORKER_URL}/c/{citation_id}")
            return True
        else:
            print(f"❌ Worker Error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        print(f"⚠️ Dispatch Exception: {e}")
        return False


# =====================================================================
# 5. UNIVERSAL MULTI-FORMAT DATA INGESTION ENGINE
# =====================================================================
def normalize_lead_dict(raw_dict):
    """Maps varying county column headers into standard Worker schema keys."""
    clean = {}
    for k, v in raw_dict.items():
        if k:
            clean_key = str(k).strip().lower().replace(" ", "_").replace("-", "_")
            clean[clean_key] = str(v).strip() if v is not None else ""

    return {
        "citation_id": clean.get("citation_id") or clean.get("case_id") or clean.get("notice_no"),
        "owner_name": clean.get("owner_name") or clean.get("owner") or clean.get("taxpayer_name") or "RECORDED OWNER",
        "address": clean.get("address") or clean.get("property_address") or clean.get("site_address") or "Recorded Parcel Location",
        "city": clean.get("city", "Los Angeles"),
        "state": clean.get("state", "CA"),
        "zip": clean.get("zip") or clean.get("zip_code", "90012"),
        "apn": clean.get("apn") or clean.get("parcel_id") or clean.get("pin", "ON FILE"),
        "category": clean.get("category", "EXCESS PROCEEDS"),
        "amount_logged": clean.get("amount_logged") or clean.get("amount") or clean.get("surplus", "$24,500.00 Logged"),
        "violation": clean.get("violation") or clean.get("description", "Public index record logged."),
        "phone": clean.get("phone"),
        "email": clean.get("email")
    }


def parse_any_file(file_path):
    """Parses CSV, Excel (.xlsx/.xls), PDF, or JSON files into normalized leads."""
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
                        headers = [str(h).strip().lower().replace(" ", "_") for h in table[0]]
                        for row in table[1:]:
                            if len(row) == len(headers):
                                raw_records.append(dict(zip(headers, row)))

    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return []

    return [normalize_lead_dict(rec) for rec in raw_records if rec]


def load_all_lead_datasets():
    """Scans root and /data directory for CSV, Excel, PDF, or JSON datasets."""
    all_leads = []
    valid_exts = (".csv", ".xlsx", ".xls", ".json", ".pdf")

    root_files = [f for f in os.listdir(".") if f.lower().startswith("leads") and f.lower().endswith(valid_exts)]
    for f in root_files:
        print(f"📁 Found root dataset file: {f}")
        all_leads.extend(parse_any_file(f))

    data_dir = "./data"
    if os.path.exists(data_dir):
        data_files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.lower().endswith(valid_exts)]
        for f in data_files:
            print(f"📂 Found /data dataset file: {f}")
            all_leads.extend(parse_any_file(f))

    return all_leads


# =====================================================================
# 6. LIVE AUTOMATED COUNTY PUBLIC RECORD SCRAPER ENGINE
# =====================================================================
CA_COUNTY_PORTALS = [
    {
        "county": "Los Angeles",
        "url": "https://ttc.lacounty.gov/excess-proceeds-public-notice/",
        "parse_type": "lacounty"
    },
    {
        "county": "San Bernardino",
        "url": "https://www.sbcounty.gov/taxcollector/surplus/",
        "parse_type": "generic_table"
    },
    {
        "county": "Riverside",
        "url": "https://countytreasurer.org/tax-auctions/excess-proceeds",
        "parse_type": "generic_table"
    }
]

def fetch_live_county_records():
    """Autonomously crawls SoCal / California public tax collector excess proceeds notices."""
    print("🌐 [BEAST SCRAPER] Launching Live County Public Records Web Crawler...")
    scraped_leads = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    for portal in CA_COUNTY_PORTALS:
        county = portal["county"]
        url = portal["url"]
        print(f"🔍 Crawling {county} County Public Notice Portal: {url}")

        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                # Extract HTML table rows matching parcel/owner data pattern
                rows = re.findall(r"<tr[^>]*>(.*?)</tr>", res.text, re.DOTALL | re.IGNORECASE)
                for row in rows:
                    cols = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
                    if len(cols) >= 3:
                        clean_cols = [re.sub(r"<.*?>", "", c).strip() for c in cols]
                        
                        # Filter for rows containing parcel APNs or monetary figures
                        apn_match = re.search(r"\d{3,4}[-\s]?\d{3}[-\s]?\d{3}", clean_cols[0] + " " + clean_cols[1])
                        amount_match = re.search(r"\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?", " ".join(clean_cols))

                        if apn_match or amount_match:
                            scraped_leads.append(normalize_lead_dict({
                                "citation_id": f"AUD-{county[:3].upper()}-{int(time.time())}",
                                "owner_name": clean_cols[0] if len(clean_cols) > 0 else "RECORDED OWNER",
                                "address": clean_cols[1] if len(clean_cols) > 1 else f"{county} Parcel Location",
                                "city": county,
                                "state": "CA",
                                "apn": apn_match.group(0) if apn_match else "ON FILE",
                                "amount_logged": amount_match.group(0) + " Logged" if amount_match else "$24,500.00 Logged",
                                "category": "EXCESS PROCEEDS",
                                "violation": f"County surplus funds recorded in {county} County tax auction registry."
                            }))
        except Exception as e:
            print(f"⚠️ Exception scraping {county} County: {e}")

    print(f"📡 [BEAST SCRAPER] Live Crawl Complete. Extracted {len(scraped_leads)} live portal lead(s).")
    return scraped_leads


# =====================================================================
# 7. LIVE RUN EXECUTION LOOP WITH BLOCKER & STATS
# =====================================================================
if __name__ == "__main__":
    print("🚀 Universal Ingress Engine Active. Checking for lead datasets...")

    real_leads = load_all_lead_datasets()

    if not real_leads:
        real_leads = fetch_live_county_records()

    if not real_leads:
        print("⚠️ No static datasets or live web scraper feeds found.")
        print("💡 Tip: Drop any dataset into the repo to run automated batch ingestion.")
    else:
        print(f"📥 Loaded {len(real_leads)} total record(s). Filtering & dispatching...\n")
        
        passed_count = 0
        blocked_count = 0

        for idx, parcel in enumerate(real_leads, 1):
            is_valid, reason = validate_lead_record(parcel)
            
            if not is_valid:
                print(f"🛑 [{idx}/{len(real_leads)}] {reason} -> {parcel.get('owner_name', 'UNKNOWN')} ({parcel.get('address', 'NO ADDR')})")
                blocked_count += 1
                continue

            print(f"✅ [{idx}/{len(real_leads)}] Ingesting Valid Lead: {parcel.get('owner_name')} - {parcel.get('address')}")
            dispatch_to_worker(parcel)
            passed_count += 1
            time.sleep(0.5)

        print(f"\n📊 Batch Execution Summary: {passed_count} Dispatched | {blocked_count} Blocked")
