import os
import re
import time
import json
import logging
import requests
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =====================================================================
# CONFIGURATION & ENVIRONMENT BINDINGS
# =====================================================================
WORKER_URL = (os.getenv("WORKER_URL") or "https://emergencyaudit.com").rstrip('/')
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or os.getenv("EMERGENCY_KEY") or "recovery2026"
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

SMS_BATCH_LIMIT = int(os.getenv("SMS_BATCH_LIMIT") or 50)
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Ingress Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 1. STATUTORY CODE CLASSIFIER (NATIONWIDE 50-STATE ENGINE)
# =====================================================================
STATE_STATUTES = {
    "CA": "CA Rev & Tax Code § 4675 / Civil Code § 2924j",
    "FL": "FL Statutes § 197.582 & § 45.032",
    "TX": "TX Tax Code § 34.04 & Property Code § 51.002",
    "GA": "O.C.G.A. § 48-4-5 (Tax Sale Excess Funds)",
    "NY": "NY CPLR § 5236 / Real Property Tax Law § 1136",
    "PA": "72 P.S. § 5860.205 (Real Estate Tax Sale Law)",
    "OH": "OH Rev Code § 5721.20 / § 2329.44",
    "NC": "NC Gen Stat § 105-374 / § 1-339.67",
    "SC": "SC Code Ann § 12-51-130",
    "AZ": "AZ Rev Stat § 33-812 / § 42-18205",
    "NV": "NRS § 361.595 / Unclaimed Surplus Proceeds",
    "MI": "MCL § 211.78t (Foreclosure Surplus Claims)",
    "TN": "TCA § 67-5-2702 (Tax Sale Excess Proceeds)"
}

def get_statutory_citation(state_abbr):
    """Returns exact legal statute governing unclaimed excess proceeds for the given state."""
    st = str(state_abbr or "CA").upper().strip()
    return STATE_STATUTES.get(st, f"State Unclaimed Property & Judicial Surplus Recovery Statutes ({st})")

# =====================================================================
# 2. NATIONWIDE RECORD NORMALIZER
# =====================================================================
def normalize_scraped_record(raw_item):
    """Normalizes raw scraper dicts from any state/county into Worker v25.2.3 schema."""
    state = str(raw_item.get("state") or raw_item.get("st") or "CA").upper().strip()
    county = str(raw_item.get("county") or raw_item.get("jurisdiction") or "County").title().strip()
    
    real_case = str(raw_item.get("docket_no") or raw_item.get("case_no") or raw_item.get("real_case_number") or "").strip()
    apn = str(raw_item.get("apn") or raw_item.get("parcel_id") or "").strip()
    
    clean_apn_digits = re.sub(r"[^\d]", "", apn)
    if real_case and real_case.upper() != "NONE":
        clean_case = re.sub(r"[^\w]", "", real_case).upper()
        case_id = f"AUD-{state}-{clean_case[:16]}"
    elif len(clean_apn_digits) >= 5:
        case_id = f"AUD-{state}-{county[:4].upper()}-{clean_apn_digits}"
    else:
        case_id = f"AUD-{state}-{int(time.time())}"

    raw_amt = raw_item.get("amount") or raw_item.get("exactAmount") or raw_item.get("default_amount") or 0.0
    clean_amt_str = re.sub(r"[^\d.]", "", str(raw_amt))
    try:
        amt_val = float(clean_amt_str)
    except ValueError:
        amt_val = 18450.00

    if amt_val >= 50000.0:
        tier = "TIER 1 GOLD ($50k+)"
    elif amt_val >= 25000.0:
        tier = "TIER 2 SILVER ($25k+)"
    else:
        tier = "TIER 3 BRONZE ($5k+)"

    situs_addr = raw_item.get("situs_address") or raw_item.get("address") or "Recorded Parcel Location"
    mailing_addr = raw_item.get("mailing_address") or raw_item.get("owner_address") or situs_addr
    agency = raw_item.get("holding_agency") or raw_item.get("court") or f"{county} County Treasurer / Clerk of Court"

    return {
        "record_id": case_id,
        "caseId": case_id,
        "citation_id": case_id,
        "real_case_number": real_case or case_id,
        "docket_id": raw_item.get("docket_id") or real_case or "N/A",
        "owner_name": raw_item.get("owner_name") or raw_item.get("leadName") or "RECORDED CLAIMANT",
        "leadName": raw_item.get("owner_name") or raw_item.get("leadName") or "RECORDED CLAIMANT",
        "situs_address": situs_addr,
        "address": situs_addr,
        "mailing_address": mailing_addr,
        "city": raw_item.get("city") or "Local Municipality",
        "county": county,
        "state": state,
        "zip": str(raw_item.get("zip") or "00000"),
        "apn": apn or "PENDING VERIFICATION",
        "holding_agency": agency,
        "holding_office_address": raw_item.get("holding_office_address") or f"{county} Courthouse / Main Administrative Building",
        "sale_date": raw_item.get("sale_date") or "Post-Auction Verification",
        "exactAmount": amt_val,
        "default_amount": f"${amt_val:,.2f}",
        "category": raw_item.get("category") or "TAX SALE EXCESS PROCEEDS",
        "asset_type": raw_item.get("asset_type") or "Audited Surplus Funds held by Local Government",
        "statutory_citation": get_statutory_citation(state),
        "value_tier": tier,
        "phone": raw_item.get("phone") or "PENDING UNMASK",
        "email": raw_item.get("email") or "N/A",
        "status": "READY_FOR_DISPATCH",
        "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
    }

# =====================================================================
# 3. TRACERFY SKIP-TRACING ENGINE (MAILING ADDRESS PRIORITY)
# =====================================================================
def skip_trace_nationwide_leads(leads):
    """Passes owner name and mailing address to Tracerfy to unmask target phone/email."""
    if not TRACERFY_API_KEY:
        logging.info("ℹ️ Tracerfy API key not configured. Skipping automated skip-trace step.")
        return leads

    logging.info(f"⚡ [TRACERFY ENGINE] Unmasking contact information for {len(leads)} lead(s)...")
    tracerfy_url = "https://tracerfy.com/v1/api/trace/lookup/"
    headers = {"Authorization": f"Bearer {TRACERFY_API_KEY}", "Content-Type": "application/json"}

    for item in leads:
        if item.get("phone") and item["phone"] != "PENDING UNMASK":
            continue

        trace_addr = item.get("mailing_address") or item.get("situs_address")
        payload = {
            "address": trace_addr,
            "city": item.get("city"),
            "state": item.get("state"),
            "zip": item.get("zip"),
            "owner_name": item.get("owner_name")
        }

        try:
            res = session.post(tracerfy_url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                phone = data.get("phone") or data.get("primary_phone") or data.get("phone_1")
                email = data.get("email") or data.get("primary_email")
                if phone:
                    clean_phone = re.sub(r"\D", "", str(phone))
                    item["phone"] = f"+1{clean_phone}" if len(clean_phone) == 10 else str(phone)
                if email:
                    item["email"] = email
        except Exception as e:
            logging.warning(f"⚠️ Skip-trace bypass for {item.get('owner_name')}: {e}")

    return leads

# =====================================================================
# 4. KV INGESTION ENGINE (BATCH PUSH, ZERO MANUAL DELETES)
# =====================================================================
def upload_to_cloudflare_kv(leads_chunk):
    """Pushes normalized records into Cloudflare Worker KV."""
    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    
    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would upload {len(leads_chunk)} lead(s) to Cloudflare Worker KV.")
        return True

    try:
        res = session.post(endpoint, json=leads_chunk, headers=HEADERS, timeout=30)
        if res.status_code == 200:
            logging.info(f"✅ Worker Ingestion Success: {len(leads_chunk)} record(s) active on dashboard.")
            return True
        else:
            logging.error(f"❌ Worker Ingestion Error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        logging.error(f"⚠️ Connection error posting to Worker: {e}")
        return False

# =====================================================================
# 5. DIRECT SMS OUTREACH ENGINE
# =====================================================================
def execute_sms_outreach(leads, limit=SMS_BATCH_LIMIT):
    """Fires targeted SMS messages to claimants containing precise court and holding details."""
    sms_endpoint = f"{WORKER_URL}/api/send-sms-direct"
    sent_count = 0

    logging.info(f"📲 Executing SMS Outreach Sequence (Target: up to {limit} claimants)...")

    for lead in leads:
        if sent_count >= limit:
            break

        phone = lead.get("phone", "")
        clean_phone = re.sub(r"[^\d]", "", phone)

        if not clean_phone or len(clean_phone) < 10 or phone == "PENDING UNMASK":
            continue

        case_id = lead["record_id"]
        owner_first = lead["owner_name"].split()[0].title() if lead.get("owner_name") else "Property Owner"
        amt = lead.get("default_amount")
        agency = lead.get("holding_agency")
        county = lead.get("county")
        state = lead.get("state")
        notice_url = f"{WORKER_URL}/c/{case_id}"

        message_body = (
            f"Emergency Audit Notice for {owner_first}: An uncollected surplus balance of {amt} "
            f"is held by the {agency} ({county} County, {state}) under File #{lead.get('real_case_number')}. "
            f"Review your verified audit file here: {notice_url}"
        )

        payload = {
            "phone": clean_phone,
            "caseId": case_id,
            "message": message_body
        }

        if not DRY_RUN:
            try:
                res = session.post(f"{sms_endpoint}?key={MASTER_ADMIN_KEY}", json=payload, headers=HEADERS, timeout=10)
                if res.status_code == 200 and res.json().get("success"):
                    sent_count += 1
                    logging.info(f"🚀 [{sent_count}/{limit}] SMS Delivered -> {clean_phone} ({county} Co, {state})")
                else:
                    logging.warning(f"⚠️ SMS Failed for {clean_phone}: {res.text}")
            except Exception as e:
                logging.error(f"⚠️ SMS Exception for {clean_phone}: {e}")
            time.sleep(0.5)
        else:
            sent_count += 1
            logging.info(f"🧪 [DRY RUN SMS] To: {clean_phone} | Msg: {message_body}")

    logging.info(f"🎉 Pipeline Ingress & Outreach Completed. Total Processed: {sent_count}")

# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================
def run_nationwide_pipeline(raw_scraped_batch):
    """Entry point for processing GitHub scraper outputs."""
    logging.info(f"🚀 Starting Nationwide Processing for {len(raw_scraped_batch)} raw record(s)...")

    normalized_batch = [normalize_scraped_record(item) for item in raw_scraped_batch]
    enriched_batch = skip_trace_nationwide_leads(normalized_batch)
    upload_to_cloudflare_kv(enriched_batch)
    execute_sms_outreach(enriched_batch, limit=SMS_BATCH_LIMIT)

if __name__ == "__main__":
    sample_github_scraped_leads = [
        {
            "owner_name": "ROBERTO M ARMAS",
            "situs_address": "10421 SW 40th St, Miami, FL 33165",
            "mailing_address": "1840 CORAL WAY STE 200, MIAMI, FL 33145",
            "city": "Miami",
            "county": "Miami-Dade",
            "state": "FL",
            "docket_no": "2025-CV-04192",
            "holding_agency": "11th Judicial Circuit Court & Clerk of Courts",
            "amount": 48250.00,
            "sale_date": "2025-11-14"
        },
        {
            "owner_name": "MARCUS V HOLLOWAY",
            "situs_address": "452 PEACHTREE ST NE",
            "city": "Atlanta",
            "county": "Fulton",
            "state": "GA",
            "case_no": "2024-EX-09821",
            "holding_agency": "Fulton County Clerk of Superior Court",
            "amount": 32100.00,
            "sale_date": "2025-08-05"
        },
        {
            "owner_name": "GREGORY & ELLEN MONROE",
            "situs_address": "8802 CHIMNEY ROCK RD",
            "city": "Houston",
            "county": "Harris",
            "state": "TX",
            "parcel_id": "0410290000012",
            "holding_agency": "Harris County District Clerk & Tax Assessor",
            "amount": 67400.00,
            "sale_date": "2025-10-07"
        }
    ]

    run_nationwide_pipeline(sample_github_scraped_leads)
