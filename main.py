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
# CONFIGURATION & ENVIRONMENT BINDINGS ($10k MANDATED FLOOR)
# =====================================================================
WORKER_URL = (os.getenv("WORKER_URL") or "https://emergencyaudit.com").rstrip('/')
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or os.getenv("EMERGENCY_KEY") or "recovery2026"

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 10000.00)  # Rejects anything under $10,000
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Pure Ingress Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 1. STATUTORY CITATION MAPPER
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

def get_statutory_citation(state_abbr):
    st = str(state_abbr or "CA").upper().strip()
    return STATE_STATUTES.get(st, f"State Unclaimed Property Statutes ({st})")

# =====================================================================
# 2. REAL PUBLIC COURTHOUSE & CLERK SCRAPERS
# =====================================================================
def fetch_apify_live_dataset():
    if not APIFY_TOKEN:
        logging.info("ℹ️ APIFY_TOKEN not configured. Skipping Apify ingestion.")
        return []

    logging.info("⚡ [APIFY ENGINE] Querying active courthouse scraper datasets...")
    endpoint = f"https://api.apify.com/v2/acts/apify~cheerio-scraper/runs/last/dataset/items?token={APIFY_TOKEN}"
    try:
        res = session.get(endpoint, timeout=20)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                logging.info(f"✅ [APIFY] Harvested {len(data)} raw live court record(s).")
                return data
    except Exception as e:
        logging.warning(f"⚠️ Apify live extraction exception: {e}")

    return []

def fetch_real_public_portals():
    """Crawls live public court surplus listings."""
    logging.info("⚡ [SCRAPER ENGINE] Crawling live public court surplus ledgers...")
    harvested = []

    target_portals = [
        ("FL", "Hillsborough", "https://www.hillsclerk.com/Court-Records/Foreclosure-Sales/Surplus-List"),
        ("GA", "Fulton", "https://www.fultonclerk.org/302/Unclaimed-Funds-Surplus"),
        ("TX", "Harris", "https://www.hctx.net/Tax-Assessor/ExcessProceeds")
    ]

    for state, county, url in target_portals:
        request_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}&render=true" if SCRAPERAPI_KEY else url
        try:
            res = session.get(request_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                rows = soup.find_all("tr")
                for row in rows:
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cols) >= 3 and cols[0]:
                        harvested.append({
                            "owner_name": cols[0],
                            "situs_address": cols[1] if len(cols) > 1 else "Recorded Parcel Location",
                            "county": county,
                            "state": state,
                            "amount": cols[-1]
                        })
        except Exception as e:
            logging.warning(f"⚠️ Portal scrape bypass ({state}/{county}): {e}")

    logging.info(f"✅ [PORTALS] Harvested {len(harvested)} raw public portal record(s).")
    return harvested

def collect_pure_raw_leads():
    raw_batch = []
    raw_batch.extend(fetch_apify_live_dataset())
    raw_batch.extend(fetch_real_public_portals())
    logging.info(f"📊 [TOTAL REAL LEADS HARVESTED]: {len(raw_batch)}")
    return raw_batch

# =====================================================================
# 3. NORMALIZER & $10k STRICT GARBAGE FILTER
# =====================================================================
def normalize_scraped_record(raw_item):
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
        amt_val = 0.0

    tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000.0 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000.0 else "TIER 3 BRONZE ($10k+)")
    situs_addr = raw_item.get("situs_address") or raw_item.get("address") or "Recorded Parcel Location"

    return {
        "record_id": case_id,
        "caseId": case_id,
        "citation_id": case_id,
        "real_case_number": real_case or case_id,
        "owner_name": str(raw_item.get("owner_name") or raw_item.get("leadName") or "RECORDED CLAIMANT").upper().strip(),
        "leadName": str(raw_item.get("owner_name") or raw_item.get("leadName") or "RECORDED CLAIMANT").upper().strip(),
        "situs_address": situs_addr,
        "address": situs_addr,
        "mailing_address": raw_item.get("mailing_address") or situs_addr,
        "city": raw_item.get("city") or "Local Municipality",
        "county": county,
        "state": state,
        "zip": str(raw_item.get("zip") or "00000"),
        "apn": apn or "PENDING VERIFICATION",
        "holding_agency": raw_item.get("holding_agency") or f"{county} County Clerk / Treasurer",
        "exactAmount": amt_val,
        "default_amount": f"${amt_val:,.2f}",
        "category": raw_item.get("category") or "TAX SALE EXCESS PROCEEDS",
        "statutory_citation": get_statutory_citation(state),
        "value_tier": tier,
        "phone": str(raw_item.get("phone") or "PENDING UNMASK"),
        "email": str(raw_item.get("email") or "N/A"),
        "status": "UNSOLD_LEAD",
        "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
    }

def clean_and_filter_records(raw_batch):
    logging.info(f"🧹 [GARBAGE DISPOSAL] Enforcing strict ${MIN_SURPLUS_THRESHOLD:,.2f} minimum floor...")
    clean_records = []
    seen_ids = set()

    for item in raw_batch:
        norm = normalize_scraped_record(item)

        # MANDATED BUYER FEE CHECK ($10,000+)
        if norm["exactAmount"] < MIN_SURPLUS_THRESHOLD:
            continue

        if not norm["owner_name"] or norm["owner_name"] in ["N/A", "UNKNOWN", "NONE", "NULL"]:
            continue

        if norm["record_id"] in seen_ids:
            continue
        seen_ids.add(norm["record_id"])

        clean_records.append(norm)

    logging.info(f"✨ [FILTER COMPLETE] Retained {len(clean_records)} high-value lead(s) ($10k+). Purged {len(raw_batch) - len(clean_records)} low-value entries.")
    return clean_records

# =====================================================================
# 4. TRACERFY SKIP-TRACING ENGINE
# =====================================================================
def skip_trace_nationwide_leads(leads):
    if not TRACERFY_API_KEY or not leads:
        return leads

    logging.info(f"⚡ [TRACERFY] Unmasking contact details for {len(leads)} lead(s)...")
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
# 5. WORKER KV INGESTION ENGINE
# =====================================================================
def upload_to_cloudflare_kv(leads_chunk):
    if not leads_chunk:
        logging.info("ℹ️️ Zero $10k+ records to ingest this cycle.")
        return False

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads_chunk)} live record(s) to Cloudflare KV.")
        return True

    try:
        res = session.post(endpoint, json=leads_chunk, headers=HEADERS, timeout=30)
        if res.status_code == 200:
            logging.info(f"✅ [SUCCESS] Written {len(leads_chunk)} high-value lead(s) ($10k+) directly to Executive Dashboard.")
            return True
        else:
            logging.error(f"❌ Worker Ingestion Error [{res.status_code}]: {res.text}")
            return False
    except Exception as e:
        logging.error(f"⚠️ Connection error posting to Worker: {e}")
        return False

# =====================================================================
# PURE LIVE INGRESS EXECUTION
# =====================================================================
def run_ingress():
    logging.info("🚀 Launching $10k+ High-Yield Courthouse Scraper Pipeline...")

    raw_batch = collect_pure_raw_leads()
    if not raw_batch:
        logging.info("ℹ️ Scrapers returned 0 new live records this run. Pipeline complete.")
        return

    filtered_batch = clean_and_filter_records(raw_batch)
    enriched_batch = skip_trace_nationwide_leads(filtered_batch)
    upload_to_cloudflare_kv(enriched_batch)

    logging.info("🎉 Ingress Run Finished. Fresh leads populated on dashboard!")

if __name__ == "__main__":
    run_ingress()
