import os
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Environment Bindings (Uses defaults if secret not explicitly mapped)
WORKER_URL = (os.getenv("WORKER_URL") or "https://emergencyaudit.com").rstrip('/')
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or os.getenv("EMERGENCY_KEY") or "recovery2026"
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")
MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 10000.00)
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EmergencyAudit Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

STATE_STATUTES = {
    "CA": "CA Rev & Tax Code § 4675 / Civil Code § 2924j",
    "FL": "FL Statutes § 197.582 & § 45.032",
    "TX": "TX Tax Code § 34.04 & Property Code § 51.002",
    "GA": "O.C.G.A. § 48-4-5 (Tax Sale Excess Funds)",
    "NY": "NY CPLR § 5236 / Real Property Tax Law § 1136"
}

def fetch_live_portals():
    logging.info("⚡ Crawling live public court surplus ledgers...")
    harvested = []
    targets = [
        ("FL", "Hillsborough", "https://www.hillsclerk.com/Court-Records/Foreclosure-Sales/Surplus-List"),
        ("GA", "Fulton", "https://www.fultonclerk.org/302/Unclaimed-Funds-Surplus"),
        ("TX", "Harris", "https://www.hctx.net/Tax-Assessor/ExcessProceeds")
    ]
    for state, county, url in targets:
        req_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}&render=true" if SCRAPERAPI_KEY else url
        try:
            res = session.get(req_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                for row in soup.find_all("tr"):
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
            logging.warning(f"⚠️ Portal fetch skip ({state}/{county}): {e}")
    return harvested

def clean_and_normalize(raw_items):
    logging.info(f"🧹 Filtering records below ${MIN_SURPLUS_THRESHOLD:,.2f} minimum floor...")
    clean = []
    seen = set()
    for item in raw_items:
        raw_amt = item.get("amount") or 0.0
        clean_str = re.sub(r"[^\d.]", "", str(raw_amt))
        try:
            amt_val = float(clean_str)
        except ValueError:
            amt_val = 0.0
        
        # Enforce $10,000 Minimum Floor
        if amt_val < MIN_SURPLUS_THRESHOLD:
            continue
            
        owner = str(item.get("owner_name") or "").upper().strip()
        if not owner or owner in ["N/A", "UNKNOWN", "NONE", "NULL"]:
            continue
            
        state = str(item.get("state") or "CA").upper().strip()
        county = str(item.get("county") or "County").title().strip()
        case_id = f"AUD-{state}-{county[:4].upper()}-{int(time.time())}"
        
        if case_id in seen:
            continue
        seen.add(case_id)
        
        tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000 else "TIER 3 BRONZE ($10k+)")
        
        clean.append({
            "record_id": case_id,
            "caseId": case_id,
            "citation_id": case_id,
            "real_case_number": case_id,
            "owner_name": owner,
            "leadName": owner,
            "situs_address": item.get("situs_address", "Recorded Parcel Location"),
            "address": item.get("situs_address", "Recorded Parcel Location"),
            "mailing_address": item.get("situs_address", "Recorded Parcel Location"),
            "city": "Local Municipality",
            "county": county,
            "state": state,
            "zip": "00000",
            "apn": "PENDING VERIFICATION",
            "holding_agency": f"{county} County Clerk / Treasurer",
            "exactAmount": amt_val,
            "default_amount": f"${amt_val:,.2f}",
            "category": "TAX SALE EXCESS PROCEEDS",
            "statutory_citation": STATE_STATUTES.get(state, f"State Unclaimed Property Statutes ({state})"),
            "value_tier": tier,
            "phone": "PENDING UNMASK",
            "email": "N/A",
            "status": "UNSOLD_LEAD",
            "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
        })
    logging.info(f"✨ Retained {len(clean)} high-value lead(s) ($10k+).")
    return clean

def skip_trace(leads):
    if not TRACERFY_API_KEY or not leads:
        return leads
    logging.info(f"⚡ Unmasking contacts for {len(leads)} lead(s)...")
    url = "https://tracerfy.com/v1/api/trace/lookup/"
    headers = {"Authorization": f"Bearer {TRACERFY_API_KEY}", "Content-Type": "application/json"}
    for item in leads:
        try:
            res = session.post(url, json={
                "address": item["situs_address"],
                "owner_name": item["owner_name"]
            }, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if phone := (data.get("phone") or data.get("primary_phone")):
                    clean_p = re.sub(r"\D", "", str(phone))
                    item["phone"] = f"+1{clean_p}" if len(clean_p) == 10 else str(phone)
                if email := (data.get("email") or data.get("primary_email")):
                    item["email"] = email
        except Exception as e:
            logging.warning(f"⚠️ Skip-trace bypass: {e}")
    return leads

def upload(leads):
    if not leads:
        logging.info("ℹ️ Zero $10k+ leads to upload.")
        return
    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads)} leads.")
        return
    res = session.post(f"{WORKER_URL}/api/inbound-lead-hook", json=leads, headers=HEADERS, timeout=30)
    if res.status_code == 200:
        logging.info(f"✅ Ingested {len(leads)} high-value lead(s) ($10k+) straight to dashboard!")
    else:
        logging.error(f"❌ Worker Error [{res.status_code}]: {res.text}")

if __name__ == "__main__":
    logging.info("🚀 Launching $10k+ Ingress Scraper...")
    raw = fetch_live_portals()
    clean = clean_and_normalize(raw)
    enriched = skip_trace(clean)
    upload(enriched)
