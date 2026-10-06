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
    "User-Agent": "EmergencyAudit Ingress Engine v25.3 (Nationwide)",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# FULL 50-STATE STATUTORY SURPLUS LEGAL CITATION DATABASE
# =====================================================================
STATE_STATUTES = {
    "AL": "ALA. CODE § 40-10-28 (Tax Sale Excess)",
    "AK": "ALASKA STAT. § 29.45.480 (Tax Foreclosure Overplus)",
    "AZ": "A.R.S. § 33-812 (Trustee Sale Surplus) / § 42-18205",
    "AR": "ARK. CODE § 26-37-205 (Tax Sale Excess Proceeds)",
    "CA": "CA REV & TAX CODE § 4675 / CIVIL CODE § 2924J",
    "CO": "C.R.S. § 39-11-115 (Tax Sale Overbid Funds)",
    "CT": "CONN. GEN. STAT. § 12-157 (Tax Sale Surplus)",
    "DE": "9 DEL. C. § 8729 (Tax Sale Excess Monies)",
    "FL": "FL STATUTES § 197.582 & § 45.032 (Tax & Mortgage Surplus)",
    "GA": "O.C.G.A. § 48-4-5 (Tax Sale Excess Funds)",
    "HI": "HAWAII REV. STAT. § 246-56 (Tax Sale Excess)",
    "ID": "IDAHO CODE § 31-808 (Tax Sale Excess Proceeds)",
    "IL": "35 ILCS 200/21-295 (Tax Sale Indemnity & Surplus)",
    "IN": "IND. CODE § 6-1.1-24-7 (Tax Sale Surplus Fund)",
    "IA": "IOWA CODE § 446.30 (Tax Sale Surplus)",
    "KS": "K.S.A. § 79-2803 (Tax Foreclosure Surplus)",
    "KY": "KRS § 134.545 & KRS § 426.500 (Execution Sale Surplus)",
    "LA": "LA CONST. ART. VII § 25 / LA R.S. 47:2196",
    "ME": "36 M.R.S. § 949 (Tax Deeded Property Excess)",
    "MD": "MD TAX-PROP CODE § 14-844 (Tax Sale Excess Proceeds)",
    "MA": "M.G.L. c. 60 § 43 (Tax Collector Sale Surplus)",
    "MI": "MCL § 211.78t (Tax Foreclosure Remaining Proceeds)",
    "MN": "MINN. STAT. § 282.08 (Apportionment of Proceeds)",
    "MS": "MISS. CODE § 27-41-77 (Tax Sale Excess)",
    "MO": "R.S.MO. § 140.230 (Tax Sale Surplus Funds)",
    "MT": "MCA § 15-18-211 (Tax Deed Surplus Proceeds)",
    "NE": "NEB. REV. STAT. § 77-1830 (Tax Sale Excess)",
    "NV": "NRS § 361.595 (Tax Sale Excess Proceeds)",
    "NH": "RSA 80:88 (Tax Deeded Property Excess)",
    "NJ": "N.J.S.A. 54:5-84 / N.J. COURT RULE 4:57 (Surplus Money)",
    "NM": "NMSA 1978 § 7-38-71 (Excess Proceeds Sales)",
    "NY": "NY CPLR § 5236 / REAL PROPERTY TAX LAW § 1136",
    "NC": "N.C.G.S. § 105-374(q) & § 1-339.70 (Tax Foreclosure Surplus)",
    "ND": "N.D.C.C. § 57-28-20 (Tax Deed Sale Surplus)",
    "OH": "R.C. § 5721.20 & R.C. § 2329.44 (Tax & Sheriff Sale Excess)",
    "OK": "68 O.S. § 3131 (Tax Resale Excess Funds)",
    "OR": "ORS § 312.120 & ORS § 18.950 (Execution Sale Surplus)",
    "PA": "72 P.S. § 5860.205 (Real Estate Tax Sale Excess)",
    "RI": "R.I. GEN. LAWS § 44-9-27 (Tax Title Surplus)",
    "SC": "S.C. CODE § 12-51-130 (Tax Collector Surplus)",
    "SD": "S.D.C.L. § 10-23-28 (Tax Sale Excess)",
    "TN": "T.C.A. § 67-5-2702 (Tax Sale Excess Proceeds)",
    "TX": "TX TAX CODE § 34.04 & PROPERTY CODE § 51.002",
    "UT": "UTAH CODE § 59-2-1351.1 (Tax Sale Surplus)",
    "VT": "32 V.S.A. § 5258 (Tax Sale Excess)",
    "VA": "VA CODE § 58.1-3967 (Tax Foreclosure Surplus)",
    "WA": "RCW 84.64.080 (Tax Foreclosure Excess Funds)",
    "WV": "W. VA. CODE § 11A-3-28 (Tax Sale Surplus)",
    "WI": "WIS. STAT. § 75.36 (Tax Deeded Property Surplus)",
    "WY": "WYO. STAT. § 39-13-108 (Tax Sale Surplus)"
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
# NATIONWIDE CSV & OVERBID LIST LOADER
# =====================================================================
def process_local_csv(file_path):
    """Parses PropStream, Propwire, BatchLeads, and County Surplus CSVs into standardized schema."""
    logging.info(f"📁 Processing Nationwide CSV File: {file_path}")
    if not os.path.exists(file_path):
        return []

    try:
        df = pd.read_csv(file_path).fillna("")
        raw_items = []

        for _, row in df.iterrows():
            row_dict = {str(k).upper().strip(): str(v).strip() for k, v in row.items()}

            fn = row_dict.get("OWNER 1 FIRST NAME") or row_dict.get("FIRST NAME") or row_dict.get("OWNER FIRST NAME") or ""
            ln = row_dict.get("OWNER 1 LAST NAME") or row_dict.get("LAST NAME") or row_dict.get("OWNER LAST NAME") or ""
            company = row_dict.get("COMPANY NAME") or row_dict.get("CORPORATE OWNER") or row_dict.get("COMPANY") or ""
            combined_name = f"{fn} {ln}".strip() or company

            owner = (
                row_dict.get("OWNER_NAME") or
                row_dict.get("OWNER NAME") or
                row_dict.get("LEADNAME") or
                row_dict.get("HOLDER NAME") or
                row_dict.get("CLAIMANT") or
                row_dict.get("OWNER") or
                combined_name
            )

            address = (
                row_dict.get("PROPERTY ADDRESS") or
                row_dict.get("PROPERTY ST ADDRESS") or
                row_dict.get("ADDRESS") or
                row_dict.get("SITUS_ADDRESS") or
                row_dict.get("PROPERTY_ADDRESS") or
                row_dict.get("MAILING ADDRESS") or
                row_dict.get("STREET") or ""
            )

            amount_raw = (
                row_dict.get("SURPLUS AMOUNT") or
                row_dict.get("OVERBID AMOUNT") or
                row_dict.get("EXCESS FUNDS") or
                row_dict.get("OVERBID") or
                row_dict.get("ESTIMATED EQUITY") or
                row_dict.get("EST. EQUITY") or
                row_dict.get("EXACTAMOUNT") or
                row_dict.get("AMOUNT") or
                row_dict.get("ESTIMATED VALUE") or
                "0"
            )

            phone = (
                row_dict.get("PHONE 1") or
                row_dict.get("PHONE_1") or
                row_dict.get("PHONE") or
                row_dict.get("MOBILE") or
                row_dict.get("CELL") or
                row_dict.get("WIRELESS") or
                "PENDING UNMASK"
            )

            state = row_dict.get("PROPERTY STATE") or row_dict.get("STATE") or row_dict.get("ST") or "US"
            county = row_dict.get("PROPERTY COUNTY") or row_dict.get("COUNTY") or "County"
            apn = (
                row_dict.get("APN - FORMATTED") or
                row_dict.get("APN") or
                row_dict.get("PARCEL ID") or
                row_dict.get("PARCEL NUMBER") or
                row_dict.get("PARCEL") or
                "PENDING VERIFICATION"
            )

            case_num = (
                row_dict.get("CASE NUMBER") or
                row_dict.get("CASE_NUMBER") or
                row_dict.get("CASE ID") or
                row_dict.get("DOCKET NUMBER") or
                ""
            )

            raw_items.append({
                "owner_name": owner,
                "situs_address": address,
                "amount": parse_amount(amount_raw),
                "county": county,
                "state": state,
                "apn": apn,
                "phone": phone,
                "case_number": case_num,
                "holder_type": "NATIONWIDE SURPLUS RECOVERY"
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

        phone = str(item.get("phone") or "PENDING UNMASK").strip()
        clean_phone_digits = re.sub(r"[^\d]", "", phone)
        has_valid_phone = len(clean_phone_digits) >= 10

        if REQUIRE_PHONE_TO_UPLOAD and not has_valid_phone:
            rejected_count += 1
            continue

        state = str(item.get("state") or "US").strip().upper()
        county = str(item.get("county") or "County").strip().title()

        fp_str = f"{owner}|{situs_addr}|{amt_val:.2f}|{state}|{county}"
        fingerprint = hashlib.md5(fp_str.encode("utf-8")).hexdigest()[:12]
        case_id = f"AUD-{state}-{county[:4].upper()}-{fingerprint}"

        if fingerprint in seen_fingerprints or case_id in existing_kv_keys:
            duplicate_count += 1
            continue
        seen_fingerprints.add(fingerprint)

        tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000 else "TIER 3 BRONZE ($10k+)")

        # State Statutory Citation Lookup across all 50 states
        statute = STATE_STATUTES.get(state, f"State Statutory Recovery Laws ({state})")

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
            "category": "TAX & MORTGAGE SURPLUS PROCEEDS",
            "statutory_citation": statute,
            "value_tier": tier,
            "phone": phone if has_valid_phone else "PENDING UNMASK",
            "status": "READY_FOR_DISPATCH" if has_valid_phone else "PENDING_UNMASK",
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
    logging.info("🚀 Launching Master Nationwide Ingress Engine...")
    existing_keys = get_existing_kv_keys()

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
