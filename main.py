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

MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 1000.00)  # Filters out trash under $1k
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Universal Ingress Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 1. DUAL STATUTORY & ASSET CLASSIFIER
# =====================================================================
STATE_STATUTES = {
    "CA": {
        "UNCLAIMED": "CA Code of Civil Procedure § 1500 et seq. (Unclaimed Property Law)",
        "TAX": "CA Rev & Tax Code § 4675 (Tax Collector Excess Proceeds)",
        "MORTGAGE": "CA Civil Code § 2924j (Trustee Foreclosure Surplus)"
    },
    "FL": {
        "UNCLAIMED": "FL Statutes Chapter 717 (Disposition of Unclaimed Property)",
        "TAX": "FL Statutes § 197.582 (Tax Deed Surplus)",
        "MORTGAGE": "FL Statutes § 45.032 (Judicial Foreclosure Surplus)"
    },
    "TX": {
        "UNCLAIMED": "TX Property Code Title 6, Chapter 72-74",
        "TAX": "TX Tax Code § 34.04 (Tax Sale Excess Proceeds)",
        "MORTGAGE": "TX Property Code § 51.002 (Foreclosure Surplus)"
    },
    "GA": {
        "UNCLAIMED": "O.C.G.A. Title 44, Chapter 12, Article 5",
        "TAX": "O.C.G.A. § 48-4-5 (Tax Sale Excess Funds)",
        "MORTGAGE": "O.C.G.A. § 44-14-190 (Mortgage Foreclosure Surplus)"
    },
    "NY": {
        "UNCLAIMED": "NY Abandoned Property Law (APL)",
        "TAX": "NY Real Property Tax Law § 1136",
        "MORTGAGE": "NY RPAPL § 1354 / CPLR § 5236"
    }
}

def get_statute(state, asset_category):
    state_dict = STATE_STATUTES.get(state, {})
    if isinstance(state_dict, dict):
        return state_dict.get(asset_category, f"State Statutory Recovery Laws ({state})")
    return f"State Statutory Recovery Laws ({state})"

# =====================================================================
# 2. MULTI-SOURCE SCRAPER SUITE (APIFY, SCRAPERAPI, DIRECT COURTS)
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
                for row in rows[:15]:
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
    all_raw = []
    all_raw.extend(fetch_apify_all_actors())
    all_raw.extend(fetch_scraperapi_multi_portals())
    logging.info(f"📥 [TOTAL RAW HARVEST]: {len(all_raw)} record(s) collected across all scrapers.")
    return all_raw

# =====================================================================
# 3. UNIVERSAL RECORD NORMALIZER (MAPS CSV HEADERS & LIVE SCRAPERS)
# =====================================================================
def normalize_scraped_record(raw_item):
    """Maps 100% of headers from State Unclaimed Funds, Tax Surplus, and Mortgage Files."""
    
    owner_name = str(
        raw_item.get("Holder Name") or 
        raw_item.get("owner_name") or 
        raw_item.get("leadName") or 
        "RECORDED CLAIMANT"
    ).strip().upper()

    raw_amt = (
        raw_item.get("Surplus Amount") or 
        raw_item.get("Cash Reported") or 
        raw_item.get("amount") or 
        raw_item.get("exactAmount") or 
        0.0
    )
    clean_amt_str = re.sub(r"[^\d.]", "", str(raw_amt))
    try:
        amt_val = float(clean_amt_str)
    except ValueError:
        amt_val = 0.0

    real_case = str(
        raw_item.get("Case Number") or 
        raw_item.get("docket_no") or 
        raw_item.get("case_no") or 
        raw_item.get("real_case_number") or 
        ""
    ).strip()

    situs_addr = str(
        raw_item.get("Property Address") or 
        raw_item.get("situs_address") or 
        raw_item.get("address") or 
        "Recorded Property Location"
    ).strip().upper()

    city_state_zip = str(raw_item.get("City State Zip") or "").strip().upper()
    city, state, zip_code = "LOCAL MUNICIPALITY", "CA", "00000"

    if city_state_zip:
        match = re.search(r"^(.*?),\s*([A-Z]{2})\s*(\d{5})?", city_state_zip)
        if match:
            city = match.group(1).title()
            state = match.group(2).upper()
            zip_code = match.group(3) or "00000"

    state = str(raw_item.get("state") or state).upper().strip()
    county_source = str(
        raw_item.get("County Source") or 
        raw_item.get("county") or 
        raw_item.get("jurisdiction") or 
        "CALIFORNIA STATE CONTROLLER"
    ).strip().upper()

    holder_type = str(raw_item.get("Holder Type") or raw_item.get("category") or "").strip().upper()
    sec_name = str(raw_item.get("Securities Name") or "").strip().upper()
    shares = str(raw_item.get("Shares Reported") or "0").strip()

    if "IRA" in holder_type or "SECURITIES" in holder_type or sec_name:
        category_code = "UNCLAIMED"
        category_label = f"SECURITIES / STOCKS ({sec_name})" if sec_name else "UNCLAIMED SECURITIES / IRA"
    elif "SAVINGS" in holder_type or "ACCOUNTS" in holder_type or "BANK" in county_source:
        category_code = "UNCLAIMED"
        category_label = f"UNCLAIMED BANK FUNDS ({holder_type or 'BANK ACCOUNT'})"
    elif "TAX" in holder_type or "TAX" in county_source:
        category_code = "TAX"
        category_label = "TAX SALE EXCESS PROCEEDS"
    elif "MORTGAGE" in holder_type or "FORECLOSURE" in holder_type or "TRUSTEE" in holder_type:
        category_code = "MORTGAGE"
        category_label = "MORTGAGE FORECLOSURE SURPLUS"
    else:
        category_code = "UNCLAIMED"
        category_label = f"STATE UNCLAIMED FUNDS ({holder_type or 'UNCOLLECTED BALANCE'})"

    clean_apn_digits = re.sub(r"[^\d]", "", str(raw_item.get("apn") or raw_item.get("parcel_id") or ""))
    if real_case and real_case.upper() != "NONE":
        clean_case = re.sub(r"[^\w]", "", real_case).upper()
        case_id = f"AUD-{state}-{clean_case[:16]}"
    elif len(clean_apn_digits) >= 5:
        case_id = f"AUD-{state}-{county_source[:4].upper()}-{clean_apn_digits}"
    else:
        case_id = f"AUD-{state}-{int(time.time())}"

    tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000.0 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000.0 else "TIER 3 BRONZE ($5k+)")

    return {
        "record_id": case_id,
        "caseId": case_id,
        "citation_id": case_id,
        "real_case_number": real_case or case_id,
        "owner_name": owner_name,
        "leadName": owner_name,
        "situs_address": situs_addr,
        "address": situs_addr,
        "mailing_address": raw_item.get("mailing_address") or situs_addr,
        "city": city,
        "county": county_source,
        "state": state,
        "zip": zip_code,
        "apn": str(raw_item.get("apn") or raw_item.get("parcel_id") or "PENDING VERIFICATION"),
        "holding_agency": county_source,
        "county_source": county_source,
        "holder_type": holder_type or "STATE UNCLAIMED FUNDS",
        "category": category_label,
        "statutory_citation": get_statute(state, category_code),
        "num_owners": str(raw_item.get("Num Owners") or "1"),
        "pending_claims": str(raw_item.get("Pending Claims") or "0"),
        "paid_claims": str(raw_item.get("Paid Claims") or "0"),
        "shares_reported": shares,
        "securities_name": sec_name or "N/A",
        "cash_reported": f"${amt_val:,.2f}",
        "exactAmount": amt_val,
        "default_amount": f"${amt_val:,.2f}",
        "value_tier": tier,
        "phone": str(raw_item.get("phone") or "PENDING UNMASK"),
        "email": str(raw_item.get("email") or "N/A"),
        "status": "UNSOLD_LEAD",
        "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
    }

# =====================================================================
# 4. GARBAGE DISPOSAL FILTER
# =====================================================================
def clean_and_filter_records(raw_list):
    logging.info("🧹 [GARBAGE DISPOSAL] Filtering out junk data, low balances, and duplicates...")
    clean_records = []
    seen_case_ids = set()

    for item in raw_list:
        norm = normalize_scraped_record(item)

        if norm["exactAmount"] < MIN_SURPLUS_THRESHOLD:
            continue

        if not norm["owner_name"] or norm["owner_name"] in ["N/A", "UNKNOWN", "NONE", "NULL"]:
            continue

        if norm["record_id"] in seen_case_ids:
            continue
        seen_case_ids.add(norm["record_id"])

        clean_records.append(norm)

    logging.info(f"✨ [DISPOSAL COMPLETE] Kept {len(clean_records)} pristine record(s). Discarded {len(raw_list) - len(clean_records)} trash entries.")
    return clean_records

# =====================================================================
# 5. TRACERFY SKIP-TRACING ENGINE (UNMASK CONTACTS ONLY)
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
# 6. CLOUDFLARE WORKER INGESTION
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
    logging.info("🚀 Launching Universal Ingress & Trash Disposal Engine...")
    
    raw_harvest = collect_all_sources()

    if not raw_harvest:
        logging.info("ℹ️ Scrapers completed with 0 new records.")
        return

    clean_batch = clean_and_filter_records(raw_harvest)
    enriched_batch = skip_trace_clean_leads(clean_batch)
    upload_to_cloudflare_kv(enriched_batch)

    logging.info("🎉 Ingress Run Completed. Leads are active on your dashboard for review!")

if __name__ == "__main__":
    run_nationwide_pipeline()
