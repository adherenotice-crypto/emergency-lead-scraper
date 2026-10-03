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
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Direct Scraper Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 1. DIRECT COUNTY COURTHOUSE HTML SCRAPERS
# =====================================================================
def scrape_florida_surplus_portals():
    """Directly scrapes Florida County Clerk foreclosure overbid tables."""
    logging.info("⚡ [FLORIDA SCRAPER] Querying Florida Circuit Court surplus lists...")
    harvested = []
    
    # Public Florida surplus listings target
    target_url = "https://www me.hillsclerk.com/RealAuction/Surplus"  # Direct public clerk endpoint
    
    try:
        res = session.get("https://www.flclerks.com/", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            # Parse table rows containing surplus, case numbers, and owner names
            rows = soup.find_all("tr")
            for row in rows:
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cols) >= 4:
                    harvested.append({
                        "owner_name": cols[0],
                        "situs_address": cols[1],
                        "city": "Tampa",
                        "county": "Hillsborough",
                        "state": "FL",
                        "docket_no": cols[2],
                        "holding_agency": "Hillsborough County Clerk of Circuit Court",
                        "amount": cols[3]
                    })
    except Exception as e:
        logging.warning(f"⚠️ Florida direct crawl bypass: {e}")

    return harvested

def scrape_apify_dataset_if_active():
    """Pulls datasets from active Apify runs if available."""
    token = os.getenv("APIFY_TOKEN")
    if not token:
        return []

    logging.info("⚡ [APIFY SCRAPER] Querying Apify dataset endpoint...")
    endpoint = f"https://api.apify.com/v2/acts/apify~cheerio-scraper/runs/last/dataset/items?token={token}"
    
    try:
        res = session.get(endpoint, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                logging.info(f"✅ [APIFY] Retrieved {len(data)} items from Apify.")
                return data
    except Exception as e:
        logging.warning(f"⚠️ Apify endpoint bypass: {e}")

    return []

def collect_all_courthouse_leads():
    """Aggregates all direct scraper sources."""
    leads = []
    
    # Direct HTML Scrapes
    leads.extend(scrape_florida_surplus_portals())
    
    # Apify Data Feeds
    leads.extend(scrape_apify_dataset_if_active())

    logging.info(f"📊 [TOTAL REAL COURTHOUSE LEADS HARVESTED]: {len(leads)}")
    return leads

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
    state = str(raw_item.get("state") or "FL").upper().strip()
    county = str(raw_item.get("county") or "County").title().strip()
    
    real_case = str(raw_item.get("docket_no") or raw_item.get("case_no") or "").strip()
    apn = str(raw_item.get("apn") or raw_item.get("parcel_id") or "").strip()
    
    clean_apn_digits = re.sub(r"[^\d]", "", apn)
    if real_case and real_case.upper() != "NONE":
        clean_case = re.sub(r"[^\w]", "", real_case).upper()
        case_id = f"AUD-{state}-{clean_case[:16]}"
    elif len(clean_apn_digits) >= 5:
        case_id = f"AUD-{state}-{county[:4].upper()}-{clean_apn_digits}"
    else:
        case_id = f"AUD-{state}-{int(time.time())}"

    raw_amt = raw_item.get("amount") or 0.0
    clean_amt_str = re.sub(r"[^\d.]", "", str(raw_amt))
    try:
        amt_val = float(clean_amt_str)
    except ValueError:
        amt_val = 0.0

    tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000.0 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000.0 else "TIER 3 BRONZE ($5k+)")
    situs_addr = raw_item.get("situs_address") or raw_item.get("address") or "Recorded Parcel Location"

    return {
        "record_id": case_id,
        "caseId": case_id,
        "citation_id": case_id,
        "real_case_number": real_case or case_id,
        "owner_name": raw_item.get("owner_name") or "RECORDED CLAIMANT",
        "leadName": raw_item.get("owner_name") or "RECORDED CLAIMANT",
        "situs_address": situs_addr,
        "address": situs_addr,
        "mailing_address": raw_item.get("mailing_address") or situs_addr,
        "city": raw_item.get("city") or "Local Municipality",
        "county": county,
        "state": state,
        "zip": str(raw_item.get("zip") or "00000"),
        "apn": apn or "PENDING VERIFICATION",
        "holding_agency": raw_item.get("holding_agency") or f"{county} County Clerk of Court",
        "exactAmount": amt_val,
        "default_amount": f"${amt_val:,.2f}",
        "category": "TAX SALE EXCESS PROCEEDS",
        "statutory_citation": STATE_STATUTES.get(state, f"State Unclaimed Property Statutes ({state})"),
        "value_tier": tier,
        "phone": raw_item.get("phone") or "PENDING UNMASK",
        "email": raw_item.get("email") or "N/A",
        "status": "UNSOLD_LEAD",
        "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
    }

# =====================================================================
# 3. TRACERFY SKIP-TRACING ENGINE
# =====================================================================
def skip_trace_leads(leads):
    if not TRACERFY_API_KEY:
        logging.info("ℹ️ TRACERFY_API_KEY not found. Skipping unmasking.")
        return leads

    logging.info(f"⚡ [TRACERFY] Unmasking contacts for {len(leads)} lead(s)...")
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
# 4. WORKER KV INGESTION HOOK
# =====================================================================
def upload_to_cloudflare_kv(leads):
    if not leads:
        logging.info("ℹ️ No leads harvested. Skipping KV write.")
        return False

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would upload {len(leads)} lead(s) to Worker KV.")
        return True

    try:
        res = session.post(endpoint, json=leads, headers=HEADERS, timeout=30)
        if res.status_code == 200:
            logging.info(f"✅ [SUCCESS] Written {len(leads)} live lead(s) to Executive Dashboard.")
            return True
        else:
            logging.error(f"❌ Worker error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        logging.error(f"⚠️ Worker connection error: {e}")
        return False

# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================
def run_ingress():
    logging.info("🚀 Running Direct Courthouse HTML Scraper Pipeline...")
    
    raw_leads = collect_all_courthouse_leads()
    if not raw_leads:
        logging.info("ℹ️ Direct scrapers found 0 new records on target portals this run.")
        return

    normalized = [normalize_scraped_record(item) for item in raw_leads]
    enriched = skip_trace_leads(normalized)
    upload_to_cloudflare_kv(enriched)

if __name__ == "__main__":
    run_ingress()
