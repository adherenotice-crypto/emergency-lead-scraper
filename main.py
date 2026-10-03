import os
import re
import time
import json
import logging
import requests
from bs4 import BeautifulSoup
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

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 1000.00)  # Throws away trash under $1k
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Omni-Ingress Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 1. MULTI-SOURCE SCRAPER SUITE (APIFY, SCRAPERAPI, DIRECT COURTS)
# =====================================================================
def fetch_apify_all_actors():
    """Source 1: Pulls datasets from active Apify web scrapers."""
    if not APIFY_TOKEN:
        logging.info("ℹ️ [APIFY] Token not set. Bypassing Apify feed.")
        return []

    logging.info("⚡ [APIFY ENGINE] Harvesting multi-state datasets...")
    endpoint = f"https://api.apify.com/v2/acts/apify~cheerio-scraper/runs/last/dataset/items?token={APIFY_TOKEN}"
    try:
        res = session.get(endpoint, timeout=20)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                logging.info(f"✅ [APIFY] Pulled {len(data)} raw record(s).")
                return data
    except Exception as e:
        logging.warning(f"⚠️ Apify multi-feed bypass: {e}")
    return []

def fetch_scraperapi_multi_portals():
    """Source 2: Queries public county court portals via ScraperAPI proxies."""
    if not SCRAPERAPI_KEY:
        logging.info("ℹ️ [SCRAPERAPI] Key not set. Bypassing ScraperAPI proxies.")
        return []

    logging.info("⚡ [SCRAPERAPI ENGINE] Crawling public clerk portals across FL, TX, GA, CA...")
    harvested = []
    
    # Target County Endpoints
    target_urls = [
        ("FL", "Hillsborough", "https://www.flclerks.com/"),
        ("GA", "Fulton", "https://www.fultonclerk.org/"),
        ("TX", "Harris", "https://www.hcdistrictclerk.com/")
    ]

    for state, county, url in target_urls:
        proxy_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}&render=false"
        try:
            res = session.get(proxy_url, timeout=15)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                rows = soup.find_all("tr")
                for row in rows[:15]: # Process top table entries
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cols) >= 3:
                        harvested.append({
                            "owner_name": cols[0],
                            "situs_address": cols[1],
                            "county": county,
                            "state": state,
                            "amount": cols[-1]
                        })
        except Exception as e:
            logging.warning(f"⚠️ ScraperAPI portal skip ({state}/{county}): {e}")

    logging.info(f"✅ [SCRAPERAPI] Harvested {len(harvested)} raw portal record(s).")
    return harvested

def collect_all_sources():
    """Combines all enabled scraper pipelines into one stream."""
    all_raw = []
    all_raw.extend(fetch_apify_all_actors())
    all_raw.extend(fetch_scraperapi_multi_portals())
    logging.info(f"📥 [TOTAL RAW HARVEST]: {len(all_raw)} record(s) collected across all scrapers.")
    return all_raw

# =====================================================================
# 2. GARBAGE DISPOSAL FILTER & SCHEMA NORMALIZER
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

def clean_and_filter_records(raw_list):
    """Purges zero balances, duplicate APNs/Cases, and incomplete trash records."""
    logging.info("🧹 [GARBAGE DISPOSAL] Filtering out junk data, low balances, and duplicates...")
    clean_records = []
    seen_case_ids = set()

    for item in raw_list:
        # Extract financial amount
        raw_amt = item.get("amount") or item.get("exactAmount") or item.get("default_amount") or 0.0
        clean_amt_str = re.sub(r"[^\d.]", "", str(raw_amt))
        try:
            amt_val = float(clean_amt_str)
        except ValueError:
            amt_val = 0.0

        # TRASH FILTER 1: Throw away zero or low-value leads below threshold
        if amt_val < MIN_SURPLUS_THRESHOLD:
            continue

        owner_name = str(item.get("owner_name") or item.get("leadName") or "").strip()
        situs_addr = str(item.get("situs_address") or item.get("address") or "").strip()

        # TRASH FILTER 2: Reject empty names or empty property locations
        if not owner_name or owner_name.upper() in ["N/A", "UNKNOWN", "NONE", "NULL"] and not situs_addr:
            continue

        state = str(item.get("state") or item.get("st") or "CA").upper().strip()
        county = str(item.get("county") or item.get("jurisdiction") or "County").title().strip()
        real_case = str(item.get("docket_no") or item.get("case_no") or item.get("real_case_number") or "").strip()
        apn = str(item.get("apn") or item.get("parcel_id") or "").strip()

        # Generate canonical ID
        clean_apn_digits = re.sub(r"[^\d]", "", apn)
        if real_case and real_case.upper() != "NONE":
            clean_case = re.sub(r"[^\w]", "", real_case).upper()
            case_id = f"AUD-{state}-{clean_case[:16]}"
        elif len(clean_apn_digits) >= 5:
            case_id = f"AUD-{state}-{county[:4].upper()}-{clean_apn_digits}"
        else:
            case_id = f"AUD-{state}-{int(time.time())}"

        # TRASH FILTER 3: Deduplicate in-memory to prevent system clogging
        if case_id in seen_case_ids:
            continue
        seen_case_ids.add(case_id)

        tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000.0 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000.0 else "TIER 3 BRONZE ($5k+)")

        clean_records.append({
            "record_id": case_id,
            "caseId": case_id,
            "citation_id": case_id,
            "real_case_number": real_case or case_id,
            "owner_name": owner_name or "RECORDED CLAIMANT",
            "leadName": owner_name or "RECORDED CLAIMANT",
            "situs_address": situs_addr or "Recorded Parcel Location",
            "address": situs_addr or "Recorded Parcel Location",
            "mailing_address": item.get("mailing_address") or situs_addr or "Recorded Parcel Location",
            "city": item.get("city") or "Local Municipality",
            "county": county,
            "state": state,
            "zip": str(item.get("zip") or "00000"),
            "apn": apn or "PENDING VERIFICATION",
            "holding_agency": item.get("holding_agency") or f"{county} County Clerk of Court",
            "exactAmount": amt_val,
            "default_amount": f"${amt_val:,.2f}",
            "category": "TAX SALE EXCESS PROCEEDS",
            "statutory_citation": STATE_STATUTES.get(state, f"State Unclaimed Property Statutes ({state})"),
            "value_tier": tier,
            "phone": item.get("phone") or "PENDING UNMASK",
            "email": item.get("email") or "N/A",
            "status": "UNSOLD_LEAD",
            "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
        })

    logging.info(f"✨ [DISPOSAL COMPLETE] Kept {len(clean_records)} pristine record(s). Discarded {len(raw_list) - len(clean_records)} trash entries.")
    return clean_records

# =====================================================================
# 3. TRACERFY SKIP-TRACING ENGINE (UNMASK CONTACTS ONLY)
# =====================================================================
def skip_trace_clean_leads(leads):
    if not TRACERFY_API_KEY or not leads:
        return leads

    logging.info(f"⚡ [TRACERFY ENGINE] Unmasking phone & email for {len(leads)} verified record(s)...")
    tracerfy_url = "https://tracerfy.com/v1/api/trace/lookup/"
    headers = {"Authorization": f"Bearer {TRACERFY_API_KEY}", "Content-Type": "application/json"}

    for item in leads:
        if item.get("phone") and item["phone"] != "PENDING UNMASK":
            continue

        payload = {
            "address": item.get("mailing_address") or item.get("situs_address"),
            "city": item.get("city"),
            "state": item.get("state"),
            "zip": item.get("zip"),
            "owner_name": item.get("owner_name")
        }

        try:
            res = session.post(tracerfy_url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                phone = data.get("phone") or data.get("primary_phone")
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
        logging.info("ℹ️ Zero clean leads to upload this cycle.")
        return False

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads)} pristine lead(s) to Cloudflare KV.")
        return True

    try:
        res = session.post(endpoint, json=leads, headers=HEADERS, timeout=30)
        if res.status_code == 200:
            logging.info(f"✅ [SUCCESS] Ingested {len(leads)} verified lead(s) directly to Executive Dashboard!")
            return True
        else:
            logging.error(f"❌ Worker error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        logging.error(f"⚠️ Worker post error: {e}")
        return False

# =====================================================================
# MAIN CONTROL ENGINE (ZERO OUTBOUND CONTACT)
# =====================================================================
def run_nationwide_pipeline():
    logging.info("🚀 Launching Omni-Scraper Ingress & Trash Disposal Engine...")
    
    # Step 1: Collect from ALL scraper sources
    raw_harvest = collect_all_sources()

    if not raw_harvest:
        logging.info("ℹ️ Scrapers completed with 0 new records.")
        return

    # Step 2: Filter out trash, low balances, incomplete entries, and duplicates
    clean_batch = clean_and_filter_records(raw_harvest)

    # Step 3: Unmask phone/email for clean records
    enriched_batch = skip_trace_clean_leads(clean_batch)

    # Step 4: Write pristine leads to Executive Dashboard (NO TEXTS / NO CALLS)
    upload_to_cloudflare_kv(enriched_batch)

    logging.info("🎉 Ingress Run Completed. Leads are active on your dashboard for review!")

if __name__ == "__main__":
    run_nationwide_pipeline()
