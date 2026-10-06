import os
import re
import io
import time
import json
import glob
import math
import logging
import hashlib
import urllib.parse
import requests
import pandas as pd
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Robust PDF Engine Fallback
try:
    import pdfplumber
    PDF_ENGINE = "pdfplumber"
except ImportError:
    try:
        import pypdf
        PDF_ENGINE = "pypdf"
    except ImportError:
        import PyPDF2
        PDF_ENGINE = "pypdf2"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# CONFIGURATION & ENVIRONMENT BINDINGS
# =====================================================================
WORKER_URL = (os.getenv("WORKER_URL") or "https://emergencyaudit.com").rstrip('/')
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or os.getenv("EMERGENCY_KEY") or "recovery2026"
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 10000.00)
MAX_SURPLUS_CEILING = float(os.getenv("MAX_SURPLUS_CEILING") or 10000000.00)
REQUIRE_PHONE_TO_UPLOAD = (os.getenv("REQUIRE_PHONE_TO_UPLOAD") or "false").lower() in ["true", "1", "yes"]

DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]
KV_TTL_SECONDS = int(os.getenv("KV_TTL_SECONDS") or 2592000)

TEXT_BLACKLIST = [
    "COUNT(S)", "CONVICTED", "FELONY", "FELON", "CRIMINAL", "HIJACKING", "CLERK NO",
    "HAVING BEEN", "COMMISSION", "PARTICIPATION", "DOCKET", "JUDGMENT", "O.C.G.A",
    "ROBBERY", "MURDER", "ATTEMPTED", "VIOLATION", "STATUTE", "COURT", "SUPERIOR",
    "UNKNOWN", "RECORDED CLAIMANT", "COUNTY CLERK", "TREASURER", "N/A", "NULL",
    "SERVICES", "DEPARTMENT", "DEPT", "EDUCATION", "PUBLIC SAFETY", "FUND",
    "INTRAGOVERNMENTAL", "DISTRICT", "AUTHORITY", "COMMISSION", "BOARD", "DIVISION",
    "GOVERNMENT", "EXECUTIVE", "OFFICE OF", "STATE OF", "COMMONWEALTH", "CITY OF",
    "COUNTY OF", "CTHRU", "RECORDED PROPERTY LOCATION", "PENDING VERIFICATION"
]

WORKER_HEADERS = {
    "User-Agent": "EmergencyAudit Ingress Engine v25.3",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

STATE_STATUTES = {
    "AL": "ALA. CODE § 40-10-28 (Tax Sale Excess)",
    "CA": "CA REV & TAX CODE § 4675 / CIVIL CODE § 2924J",
    "FL": "FL STATUTES § 197.582 & § 45.032",
    "GA": "O.C.G.A. § 48-4-5 (Tax Sale Excess Funds)",
    "NY": "NY CPLR § 5236 / REAL PROPERTY TAX LAW § 1136",
    "TX": "TX TAX CODE § 34.04 & PROPERTY CODE § 51.002"
}

def get_existing_kv_keys():
    """Queries Cloudflare Worker to pull all existing case IDs and avoid duplicate writes."""
    logging.info("🔍 Pre-flight Check: Querying Cloudflare KV for existing lead keys...")
    existing_keys = set()
    endpoint = f"{WORKER_URL}/api/inbound-lead-hook?key={MASTER_ADMIN_KEY}"
    try:
        res = session.get(endpoint, headers=WORKER_HEADERS, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                for item in data:
                    if cid := (item.get("caseId") or item.get("record_id")):
                        existing_keys.add(str(cid).upper())
            logging.info(f"✅ KV Pre-flight Check Complete: {len(existing_keys)} existing record(s) indexed.")
        else:
            logging.warning(f"⚠️ Pre-flight Check Warning: Worker returned status {res.status_code}.")
    except Exception as e:
        logging.warning(f"⚠️ Could not reach Worker for KV pre-check: {e}")
    return existing_keys

def parse_amount(text):
    clean_str = re.sub(r"[^\d.]", "", str(text))
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

def is_blacklisted(text):
    text_upper = str(text).upper()
    return any(bad_word in text_upper for bad_word in TEXT_BLACKLIST)

def is_valid_address(address):
    if not address or is_blacklisted(address):
        return False
    clean_addr = address.strip().upper()
    if clean_addr in ["RECORDED PROPERTY LOCATION", "N/A", "NONE", "UNKNOWN", "PENDING VERIFICATION"]:
        return False
    has_digit = bool(re.search(r"\d+", clean_addr))
    has_letter = bool(re.search(r"[A-Z]+", clean_addr))
    return len(clean_addr) >= 5 and has_digit and has_letter

# =====================================================================
# MULTI-PROVIDER CSV LOADER (PROPSTREAM, PROPWIRE, BATCHLEADS & CUSTOM)
# =====================================================================
def process_local_csv(file_path):
    """Parses local CSV exports across PropStream, Propwire, and county lists into standardized schema."""
    logging.info(f"📁 Processing CSV File: {file_path}")
    if not os.path.exists(file_path):
        return []

    try:
        df = pd.read_csv(file_path).fillna("")
        raw_items = []

        for _, row in df.iterrows():
            row_dict = {str(k).upper().strip(): str(v).strip() for k, v in row.items()}

            # PropStream split-name reconstruction vs single name field
            fn = row_dict.get("OWNER 1 FIRST NAME") or row_dict.get("FIRST NAME") or ""
            ln = row_dict.get("OWNER 1 LAST NAME") or row_dict.get("LAST NAME") or ""
            combined_name = f"{fn} {ln}".strip()

            owner = (
                row_dict.get("OWNER_NAME") or
                row_dict.get("OWNER NAME") or
                row_dict.get("LEADNAME") or
                row_dict.get("HOLDER NAME") or
                row_dict.get("OWNER") or
                combined_name
            )

            # Property address matching across schemas
            address = (
                row_dict.get("PROPERTY ADDRESS") or
                row_dict.get("ADDRESS") or
                row_dict.get("SITUS_ADDRESS") or
                row_dict.get("PROPERTY_ADDRESS") or
                row_dict.get("STREET") or ""
            )

            # Value / Surplus amount parsing
            amount_raw = (
                row_dict.get("ESTIMATED EQUITY") or
                row_dict.get("EXACTAMOUNT") or
                row_dict.get("SURPLUS AMOUNT") or
                row_dict.get("AMOUNT") or
                row_dict.get("ESTIMATED VALUE") or
                "18450"
            )

            # Contact details
            phone = (
                row_dict.get("PHONE 1") or
                row_dict.get("PHONE") or
                row_dict.get("MOBILE") or
                row_dict.get("CELL") or
                "PENDING UNMASK"
            )

            state = row_dict.get("PROPERTY STATE") or row_dict.get("STATE") or "US"
            county = row_dict.get("PROPERTY COUNTY") or row_dict.get("COUNTY") or "County"
            apn = row_dict.get("APN - FORMATTED") or row_dict.get("APN") or row_dict.get("PARCEL ID") or row_dict.get("PARCEL") or "PENDING VERIFICATION"

            raw_items.append({
                "owner_name": owner,
                "situs_address": address,
                "amount": parse_amount(amount_raw),
                "county": county,
                "state": state,
                "apn": apn,
                "phone": phone,
                "holder_type": "PROPSTREAM / SURPLUS PROCEEDS"
            })

        logging.info(f"✅ Ingested {len(raw_items)} record(s) from {file_path}")
        return raw_items
    except Exception as e:
        logging.error(f"❌ Error reading {file_path}: {e}")
        return []

def validate_and_normalize(raw_items, existing_kv_keys=set()):
    logging.info(f"🧹 Normalizing records & screening against ${MIN_SURPLUS_THRESHOLD:,.2f} threshold...")
    qualified_leads = []
    rejected_count = 0
    duplicate_count = 0
    seen_fingerprints = set()

    for idx, item in enumerate(raw_items):
        amt_val = float(item.get("amount") or 0.0)
        if not (MIN_SURPLUS_THRESHOLD <= amt_val <= MAX_SURPLUS_CEILING):
            rejected_count += 1
            continue

        owner = str(item.get("owner_name") or "").strip().upper()
        if not owner or len(owner) < 3 or re.match(r"^[\d\-\.]+$", owner) or is_blacklisted(owner):
            rejected_count += 1
            continue

        situs_addr = str(item.get("situs_address") or "").strip().upper()
        if not is_valid_address(situs_addr):
            rejected_count += 1
            continue

        state = str(item.get("state") or "CA").strip().upper()
        county = str(item.get("county") or "County").strip().title()

        fp_str = f"{owner}|{situs_addr}|{amt_val:.2f}|{state}|{county}"
        fingerprint = hashlib.md5(fp_str.encode("utf-8")).hexdigest()[:12]
        case_id = f"AUD-{state}-{county[:4].upper()}-{fingerprint}"

        if fingerprint in seen_fingerprints or case_id in existing_kv_keys:
            duplicate_count += 1
            continue
        seen_fingerprints.add(fingerprint)

        tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000 else "TIER 3 BRONZE ($10k+)")

        lead_record = {
            "record_id": case_id,
            "caseId": case_id,
            "citation_id": case_id,
            "real_case_number": item.get("case_number") or f"CS-{state}-{int(time.time())}-{idx}",
            "owner_name": owner,
            "leadName": owner,
            "situs_address": situs_addr,
            "address": situs_addr,
            "city": f"{county} Area",
            "county": county,
            "state": state,
            "zip": "00000",
            "apn": str(item.get("apn") or "PENDING VERIFICATION"),
            "exactAmount": amt_val,
            "default_amount": f"${amt_val:,.2f}",
            "category": "TAX SALE EXCESS PROCEEDS",
            "statutory_citation": STATE_STATUTES.get(state, f"State Statutory Recovery Laws ({state})"),
            "value_tier": tier,
            "phone": item.get("phone") or "PENDING UNMASK",
            "status": "UNSOLD_LEAD",
            "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST"),
            "fingerprint": fingerprint,
            "ttl_seconds": KV_TTL_SECONDS
        }

        qualified_leads.append(lead_record)

    logging.info(f"📊 Normalization Summary: Total Ingested={len(raw_items)} | Qualified={len(qualified_leads)} | Duplicates Skipped={duplicate_count} | Rejected={rejected_count}")
    return qualified_leads

def upload(leads, kv_batch_size=50):
    if not leads:
        logging.info("ℹ️ Zero new leads to upload.")
        return

    df = pd.DataFrame(leads)
    df.to_csv("master_surplus_leads.csv", index=False)
    logging.info(f"📁 Saved {len(leads)} normalized lead(s) to 'master_surplus_leads.csv'.")

    if DRY_RUN:
        logging.info("🛡️ [DRY RUN ACTIVE]: Cloudflare KV upload bypassed.")
        return

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook?key={MASTER_ADMIN_KEY}"
    total_uploaded = 0
    total_batches = (len(leads) + kv_batch_size - 1) // kv_batch_size

    logging.info(f"🚀 Uploading {len(leads)} lead(s) to Cloudflare KV in {total_batches} batch(es)...")

    for idx in range(0, len(leads), kv_batch_size):
        batch = leads[idx:idx + kv_batch_size]
        current_batch_num = (idx // kv_batch_size) + 1
        try:
            res = session.post(endpoint, json=batch, headers=WORKER_HEADERS, timeout=25)
            if res.status_code == 200:
                total_uploaded += len(batch)
                logging.info(f"   ✅ Batch [{current_batch_num}/{total_batches}] Ingested ({len(batch)} leads).")
            else:
                logging.warning(f"   ⚠️ Batch [{current_batch_num}/{total_batches}] Failed (Status {res.status_code}).")
        except Exception as e:
            logging.error(f"   ⚠️ Batch [{current_batch_num}/{total_batches}] Connection Error: {e}")

    logging.info(f"🎉 INGESTION COMPLETE: {total_uploaded}/{len(leads)} leads active in Cloudflare KV.")

if __name__ == "__main__":
    logging.info("🚀 Launching Master Ingress Engine...")
    existing_keys = get_existing_kv_keys()

    # Automatically scan all CSV files in workspace and batches/ folder
    target_csvs = glob.glob("*.csv") + glob.glob("batches/*.csv")
    target_csvs = [f for f in target_csvs if not f.endswith("master_surplus_leads.csv")]

    raw_data = []
    if target_csvs:
        for csv_file in target_csvs:
            raw_data.extend(process_local_csv(csv_file))
    else:
        logging.info("ℹ️ No local CSV files found in workspace root or batches/ directory.")

    clean_data = validate_and_normalize(raw_data, existing_kv_keys=existing_keys)
    upload(clean_data)
