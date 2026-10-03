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
# CONFIGURATION & PRODUCTION SECRETS
# =====================================================================
WORKER_URL = (os.getenv("WORKER_URL") or "https://emergencyaudit.com").rstrip('/')
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or os.getenv("EMERGENCY_KEY") or "recovery2026"

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

SMS_BATCH_LIMIT = int(os.getenv("SMS_BATCH_LIMIT") or 50)
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Live Pipeline Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 1. LIVE DATA SCRAPER ENGINES (APIFY & SCRAPERAPI INTEGRATION)
# =====================================================================
def fetch_apify_surplus_leads():
    """Pulls live real estate tax sale & foreclosure overbid records using Apify Actor API."""
    if not APIFY_TOKEN:
        logging.warning("⚠️ APIFY_TOKEN not set. Skipping Apify scraper run.")
        return []

    logging.info("⚡ [APIFY ENGINE] Fetching live scraped surplus & auction leads...")
    
    # Query Apify dataset / actor runs for active scraped leads
    apify_url = f"https://api.apify.com/v2/acts/apify~cheerio-scraper/runs/last/dataset/items?token={APIFY_TOKEN}"
    
    try:
        res = session.get(apify_url, timeout=30)
        if res.status_code == 200:
            items = res.json()
            if isinstance(items, list) and len(items) > 0:
                logging.info(f"✅ [APIFY] Retrieved {len(items)} live lead(s) from Apify.")
                return items
        logging.info("ℹ️ Apify dataset empty or actor idle. Falling back to ScraperAPI endpoint...")
    except Exception as e:
        logging.error(f"⚠️ Apify fetch exception: {e}")

    return []

def fetch_scraperapi_county_feeds():
    """Pulls public court overbid listings via ScraperAPI directly."""
    if not SCRAPERAPI_KEY:
        logging.warning("⚠️ SCRAPERAPI_KEY not set. Skipping ScraperAPI public portal crawl.")
        return []

    logging.info("⚡ [SCRAPERAPI ENGINE] Querying public county overbid lists...")
    
    # Example target endpoint routing through ScraperAPI proxy
    target_portal = "https://www.miami-dadeclerk.com/api/foreclosure/surplus"
    scraper_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={target_portal}&render=true"

    try:
        res = session.get(scraper_url, timeout=30)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                logging.info(f"✅ [SCRAPERAPI] Pulled {len(data)} live county surplus records.")
                return data
    except Exception as e:
        logging.warning(f"⚠️ ScraperAPI live fetch bypass: {e}")

    return []

def collect_all_live_scraped_records():
    """Aggregates all live data sources into a unified pipeline batch."""
    records = []
    
    # Source 1: Apify Scrapers
    apify_data = fetch_apify_surplus_leads()
    records.extend(apify_data)

    # Source 2: ScraperAPI Public Portals
    scraperapi_data = fetch_scraperapi_county_feeds()
    records.extend(scraperapi_data)

    logging.info(f"📊 [TOTAL LIVE COLLECTED]: {len(records)} raw record(s) ready for normalization.")
    return records

# =====================================================================
# 2. STATUTORY CODE CLASSIFIER & NORMALIZER
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
    """Standardizes raw scraper dicts into Worker v25.2.3 canonical schema."""
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
        "statutory_citation": STATE_STATUTES.get(state, f"State Surplus Statutes ({state})"),
        "value_tier": tier,
        "phone": raw_item.get("phone") or "PENDING UNMASK",
        "email": raw_item.get("email") or "N/A",
        "status": "READY_FOR_DISPATCH",
        "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
    }

# =====================================================================
# 3. TRACERFY SKIP-TRACING ENGINE
# =====================================================================
def skip_trace_leads(leads):
    """Passes owner name & mailing address to Tracerfy to unmask mobile phone lines."""
    if not TRACERFY_API_KEY:
        logging.info("ℹ️ Tracerfy API key not active. Skipping skip-trace step.")
        return leads

    logging.info(f"⚡ [TRACERFY ENGINE] Unmasking contacts for {len(leads)} lead(s)...")
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
# 4. CLOUDFLARE WORKER INGESTION
# =====================================================================
def upload_to_cloudflare_kv(leads):
    if not leads:
        logging.info("ℹ️ No leads to upload to Worker.")
        return False

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would upload {len(leads)} lead(s) to Cloudflare KV.")
        return True

    try:
        res = session.post(endpoint, json=leads, headers=HEADERS, timeout=30)
        if res.status_code == 200:
            logging.info(f"✅ [WORKER KV INGESTION SUCCESS]: {len(leads)} lead(s) pushed to live dashboard.")
            return True
        else:
            logging.error(f"❌ Worker Ingestion Error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        logging.error(f"⚠️ Worker post error: {e}")
        return False

# =====================================================================
# 5. DIRECT SMS OUTREACH DISPATCH
# =====================================================================
def execute_sms_outreach(leads, limit=SMS_BATCH_LIMIT):
    sms_endpoint = f"{WORKER_URL}/api/send-sms-direct"
    sent_count = 0

    logging.info(f"📲 Executing SMS Outreach Sequence (Limit: {limit})...")

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
            except Exception as e:
                logging.error(f"⚠️ SMS Exception for {clean_phone}: {e}")
            time.sleep(0.5)

    logging.info(f"🎉 Pipeline Execution Complete. Delivered {sent_count} outreach message(s).")

# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================
def run_pipeline():
    logging.info("🚀 Launching EmergencyAudit Live Ingress Pipeline...")
    
    # Step 1: Collect Live Scraped Records via Apify / ScraperAPI
    raw_leads = collect_all_live_scraped_records()

    if not raw_leads:
        logging.info("ℹ️ No new live scraper feeds returned this cycle.")
        return

    # Step 2: Normalize Schema
    normalized = [normalize_scraped_record(item) for item in raw_leads]

    # Step 3: Skip-Trace Contacts via Tracerfy
    enriched = skip_trace_leads(normalized)

    # Step 4: Push Records to Cloudflare Worker KV
    upload_to_cloudflare_kv(enriched)

    # Step 5: Dispatch SMS Outreach
    execute_sms_outreach(enriched, limit=SMS_BATCH_LIMIT)

if __name__ == "__main__":
    run_pipeline()
    run_nationwide_pipeline(sample_github_scraped_leads)
