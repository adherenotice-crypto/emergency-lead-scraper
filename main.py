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
# CONFIGURATION & API KEYS
# =====================================================================
WORKER_URL = (os.getenv("WORKER_URL") or "https://emergencyaudit.com").rstrip('/')
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or os.getenv("EMERGENCY_KEY") or "recovery2026"

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Courthouse Ingress Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 1. COURTHOUSE & COUNTY SCRAPER SOURCES
# =====================================================================
def fetch_apify_court_leads():
    """Pulls live real estate tax sale & foreclosure overbid records from Apify actors."""
    if not APIFY_TOKEN:
        logging.info("ℹ️ APIFY_TOKEN not set. Skipping Apify scraper execution.")
        return []

    logging.info("⚡ [APIFY ENGINE] Crawling courthouse overbid datasets...")
    apify_url = f"https://api.apify.com/v2/acts/apify~cheerio-scraper/runs/last/dataset/items?token={APIFY_TOKEN}"
    
    try:
        res = session.get(apify_url, timeout=30)
        if res.status_code == 200:
            items = res.json()
            if isinstance(items, list) and len(items) > 0:
                logging.info(f"✅ [APIFY] Fetched {len(items)} live court record(s).")
                return items
    except Exception as e:
        logging.error(f"⚠️ Apify fetch error: {e}")

    return []

def fetch_scraperapi_county_feeds():
    """Queries county court portal listings directly via ScraperAPI proxy."""
    if not SCRAPERAPI_KEY:
        logging.info("ℹ️ SCRAPERAPI_KEY not set. Skipping ScraperAPI county crawl.")
        return []

    logging.info("⚡ [SCRAPERAPI ENGINE] Querying public county clerk lists...")
    target_portal = "https://www.miami-dadeclerk.com/api/foreclosure/surplus"
    scraper_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={target_portal}&render=true"

    try:
        res = session.get(scraper_url, timeout=30)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                logging.info(f"✅ [SCRAPERAPI] Fetched {len(data)} county surplus records.")
                return data
    except Exception as e:
        logging.warning(f"⚠️ ScraperAPI county fetch bypass: {e}")

    return []

def collect_all_courthouse_records():
    """Aggregates all scraped records from active scraper endpoints."""
    raw_leads = []
    
    # 1. Pull from Apify actors
    raw_leads.extend(fetch_apify_court_leads())

    # 2. Pull from ScraperAPI county proxies
    raw_leads.extend(fetch_scraperapi_county_feeds())

    logging.info(f"📊 Total Raw Courthouse Records Harvested: {len(raw_leads)}")
    return raw_leads

# =====================================================================
# 2. STATUTORY MAPPING & SCHEMA NORMALIZER
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
    "AZ": "AZ Rev Stat § 33-812 / § 42-18205"
}

def normalize_scraped_record(raw_item):
    """Formats raw scraper outputs into standardized Worker KV ledger schema."""
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

    tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000.0 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000.0 else "TIER 3 BRONZE ($5k+)")

    situs_addr = raw_item.get("situs_address") or raw_item.get("address") or "Recorded Property Location"
    mailing_addr = raw_item.get("mailing_address") or raw_item.get("owner_address") or situs_addr
    agency = raw_item.get("holding_agency") or raw_item.get("court") or f"{county} County Treasurer / Clerk of Court"

    return {
        "record_id": case_id,
        "caseId": case_id,
        "citation_id": case_id,
        "real_case_number": real_case or case_id,
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
        "exactAmount": amt_val,
        "default_amount": f"${amt_val:,.2f}",
        "category": raw_item.get("category") or "TAX SALE EXCESS PROCEEDS",
        "statutory_citation": STATE_STATUTES.get(state, f"State Unclaimed Property Statutes ({state})"),
        "value_tier": tier,
        "phone": raw_item.get("phone") or "PENDING UNMASK",
        "email": raw_item.get("email") or "N/A",
        "status": "UNSOLD_LEAD",
        "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
    }

# =====================================================================
# 3. TRACERFY SKIP-TRACER (UNMASK CONTACT DATA ONLY)
# =====================================================================
def skip_trace_leads(leads):
    """Passes owner name & mailing address to Tracerfy to attach phone/email."""
    if not TRACERFY_API_KEY:
        logging.info("ℹ️ Tracerfy key not present. Ingesting raw unmasked addresses.")
        return leads

    logging.info(f"⚡ [TRACERFY ENGINE] Unmasking phone & email for {len(leads)} harvested lead(s)...")
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
# 4. CLOUDFLARE WORKER INGESTION HOOK
# =====================================================================
def upload_to_cloudflare_kv(leads):
    """Pushes unmasked leads directly into Cloudflare Worker KV ledger."""
    if not leads:
        logging.info("ℹ️ No leads harvested this run.")
        return False

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads)} lead(s) to Worker KV.")
        return True

    try:
        res = session.post(endpoint, json=leads, headers=HEADERS, timeout=30)
        if res.status_code == 200:
            logging.info(f"✅ [SUCCESS] Ingested {len(leads)} lead(s) straight into Executive Dashboard.")
            return True
        else:
            logging.error(f"❌ Worker error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        logging.error(f"⚠️ Worker connection error: {e}")
        return False

# =====================================================================
# MAIN PIPELINE EXECUTION (NO OUTREACH / NO CONTACT)
# =====================================================================
def run_pipeline():
    logging.info("🚀 Launching Courthouse Lead Ingestion Pipeline...")
    
    # Step 1: Harvest Raw Courthouse & Tax Sale Data
    raw_leads = collect_all_courthouse_records()

    if not raw_leads:
        logging.info("ℹ️ Scrapers completed with 0 new records.")
        return

    # Step 2: Normalize Schema
    normalized_leads = [normalize_scraped_record(item) for item in raw_leads]

    # Step 3: Unmask Phones & Emails via Tracerfy
    enriched_leads = skip_trace_leads(normalized_leads)

    # Step 4: Write Clean Lead Data directly to Dashboard KV
    upload_to_cloudflare_kv(enriched_leads)
    
    logging.info("🎉 Lead acquisition complete. Zero outbound contact initiated.")

if __name__ == "__main__":
    run_pipeline()
