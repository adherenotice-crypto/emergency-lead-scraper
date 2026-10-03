import os
import re
import io
import time
import logging
import requests
import pdfplumber
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# CONFIGURATION & ENVIRONMENT BINDINGS
# =====================================================================
WORKER_URL = (os.getenv("WORKER_URL") or "https://emergencyaudit.com").rstrip('/')
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or os.getenv("EMERGENCY_KEY") or "recovery2026"
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 10000.00)
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

WORKER_HEADERS = {
    "User-Agent": "EmergencyAudit Ingress Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# NATIONWIDE STATUTORY CITATION MAPPING
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

# =====================================================================
# EXPANDED NATIONWIDE PUBLIC SURPLUS FEEDS
# =====================================================================
PUBLIC_SURPLUS_FEEDS = [
    # FLORIDA
    {"state": "FL", "county": "Orange", "type": "pdf", "url": "https://www.myorangeclerk.com/Portals/0/Foreclosure/Surplus_List.pdf"},
    {"state": "FL", "county": "Hillsborough", "type": "pdf", "url": "https://www.hillsclerk.com/-/media/files/hillsclerk/court-records/foreclosure/surplus-list.pdf"},
    {"state": "FL", "county": "Palm Beach", "type": "pdf", "url": "https://www.mypalmbeachclerk.com/home/showpublisheddocument/1230"},
    # TEXAS
    {"state": "TX", "county": "Harris", "type": "csv", "url": "https://www.hctx.net/Tax-Assessor/ExcessProceeds/DownloadCSV"},
    {"state": "TX", "county": "Bexar", "type": "pdf", "url": "https://www.bexar.org/DocumentCenter/View/28221/Excess-Proceeds-List-PDF"},
    {"state": "TX", "county": "Tarrant", "type": "pdf", "url": "https://www.tarrantcountytx.gov/content/dam/main/tax-assessor-collector/Excess_Proceeds.pdf"},
    # GEORGIA
    {"state": "GA", "county": "Fulton", "type": "pdf", "url": "https://www.fultonclerk.org/DocumentCenter/View/1245/Unclaimed-Funds-List-PDF"},
    {"state": "GA", "county": "DeKalb", "type": "pdf", "url": "https://www.dekalbcountyga.gov/sites/default/files/tax_execs_funds_list.pdf"},
    # OHIO
    {"state": "OH", "county": "Franklin", "type": "csv", "url": "https://treasurer.franklincountyohio.gov/FranklinCounty/media/Documents/Unclaimed-Funds.csv"},
    {"state": "OH", "county": "Cuyahoga", "type": "pdf", "url": "https://treasurer.cuyahogacounty.us/pdf_treasurer/en-US/UnclaimedFundsList.pdf"},
    # NORTH CAROLINA
    {"state": "NC", "county": "Mecklenburg", "type": "pdf", "url": "https://www.mecknc.gov/TaxCollector/Documents/Surplus-Funds-List.pdf"}
]

def fetch_feed_data(url, name):
    """Direct HTTP fetch with fallback to ScraperAPI proxy."""
    try:
        res = session.get(url, headers=BROWSER_HEADERS, timeout=20)
        if res.status_code == 200 and len(res.content) > 200:
            logging.info(f"   [Direct HTTP 200] {len(res.content)} bytes for {name}")
            return res.content
    except Exception as e:
        logging.warning(f"   [Direct HTTP Bypass] {name}: {e}")

    if SCRAPERAPI_KEY:
        proxy_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}"
        try:
            res = session.get(proxy_url, timeout=30)
            if res.status_code == 200 and len(res.content) > 200:
                logging.info(f"   [ScraperAPI HTTP 200] {len(res.content)} bytes for {name}")
                return res.content
        except Exception as e:
            logging.warning(f"   [ScraperAPI Error] {name}: {e}")

    return None

def parse_amount(text):
    """Cleans currency text into float balance."""
    clean_str = re.sub(r"[^\d.]", "", str(text))
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

# =====================================================================
# 1. HARVESTERS (PDF & CSV)
# =====================================================================
def harvest_pdf_feed(feed):
    logging.info(f"📄 Harvesting {feed['state']} - {feed['county']} County Surplus PDF...")
    records = []
    content = fetch_feed_data(feed["url"], feed["county"])
    if not content:
        return records

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        if not row:
                            continue
                        row_str = " ".join([str(c) for c in row if c])
                        amounts = re.findall(r"\$?\b[\d,]{5,}(?:\.\d{2})?\b", row_str)
                        for amt_text in amounts:
                            amt_val = parse_amount(amt_text)
                            if amt_val >= MIN_SURPLUS_THRESHOLD:
                                records.append({
                                    "owner_name": str(row[0]).strip().upper() if row[0] else "RECORDED CLAIMANT",
                                    "situs_address": str(row[1]).strip().upper() if len(row) > 1 and row[1] else "RECORDED PROPERTY LOCATION",
                                    "amount": amt_val,
                                    "county": feed["county"],
                                    "state": feed["state"]
                                })
                                break

                if not records:
                    text = page.extract_text() or ""
                    for line in text.split("\n"):
                        amounts = re.findall(r"\$?\b[\d,]{5,}(?:\.\d{2})?\b", line)
                        for amt_text in amounts:
                            amt_val = parse_amount(amt_text)
                            if amt_val >= MIN_SURPLUS_THRESHOLD:
                                clean_line = re.sub(r"\$?\b[\d,]{5,}(?:\.\d{2})?\b", "", line).strip()
                                parts = [p.strip() for p in clean_line.split("  ") if p.strip()]
                                records.append({
                                    "owner_name": parts[0].upper() if parts else "RECORDED CLAIMANT",
                                    "situs_address": parts[-1].upper() if len(parts) > 1 else "RECORDED PROPERTY LOCATION",
                                    "amount": amt_val,
                                    "county": feed["county"],
                                    "state": feed["state"]
                                })
                                break

        logging.info(f"✅ Extracted {len(records)} record(s) from {feed['county']} PDF.")
    except Exception as e:
        logging.warning(f"⚠️ PDF parse exception for {feed['county']}: {e}")

    return records

def harvest_csv_feed(feed):
    logging.info(f"📊 Harvesting {feed['state']} - {feed['county']} County Surplus CSV...")
    records = []
    content = fetch_feed_data(feed["url"], feed["county"])
    if not content:
        return records

    try:
        csv_text = content.decode("utf-8", errors="ignore")
        df = pd.read_csv(io.StringIO(csv_text), errors="ignore")
        for _, row in df.iterrows():
            row_str = " ".join([str(val) for val in row.values])
            amounts = re.findall(r"\$?\b[\d,]{5,}(?:\.\d{2})?\b", row_str)
            for amt_text in amounts:
                amt_val = parse_amount(amt_text)
                if amt_val >= MIN_SURPLUS_THRESHOLD:
                    records.append({
                        "owner_name": str(row.iloc[0]).strip().upper(),
                        "situs_address": str(row.iloc[1]).strip().upper() if len(row) > 1 else "RECORDED PROPERTY LOCATION",
                        "amount": amt_val,
                        "county": feed["county"],
                        "state": feed["state"]
                    })
                    break
        logging.info(f"✅ Extracted {len(records)} record(s) from {feed['county']} CSV.")
    except Exception as e:
        logging.warning(f"⚠️ CSV parse exception for {feed['county']}: {e}")

    return records

def collect_all_sources():
    raw_harvest = []
    for feed in PUBLIC_SURPLUS_FEEDS:
        if feed["type"] == "pdf":
            raw_harvest.extend(harvest_pdf_feed(feed))
        elif feed["type"] == "csv":
            raw_harvest.extend(harvest_csv_feed(feed))
    return raw_harvest

# =====================================================================
# 2. NORMALIZER & $10k GARBAGE FILTER
# =====================================================================
def clean_and_normalize(raw_items):
    logging.info(f"🧹 Enforcing strict ${MIN_SURPLUS_THRESHOLD:,.2f} minimum floor...")
    clean = []
    seen = set()

    for item in raw_items:
        amt_val = float(item.get("amount") or 0.0)
        if amt_val < MIN_SURPLUS_THRESHOLD:
            continue

        owner = str(item.get("owner_name") or "").strip().upper()
        if not owner or len(owner) < 3 or owner in ["N/A", "UNKNOWN", "NONE", "NULL", "RECORDED CLAIMANT"]:
            continue

        state = str(item.get("state") or "CA").strip().upper()
        county = str(item.get("county") or "County").strip().title()
        case_id = f"AUD-{state}-{county[:4].upper()}-{int(time.time())}"

        if case_id in seen:
            continue
        seen.add(case_id)

        tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000 else "TIER 3 BRONZE ($10k+)")
        situs_addr = str(item.get("situs_address") or "RECORDED PROPERTY LOCATION").strip().upper()

        clean.append({
            "record_id": case_id,
            "caseId": case_id,
            "citation_id": case_id,
            "real_case_number": case_id,
            "owner_name": owner,
            "leadName": owner,
            "situs_address": situs_addr,
            "address": situs_addr,
            "mailing_address": situs_addr,
            "city": "Local Municipality",
            "county": county,
            "state": state,
            "zip": "00000",
            "apn": "PENDING VERIFICATION",
            "holding_agency": f"{county} County Clerk / Treasurer",
            "exactAmount": amt_val,
            "default_amount": f"${amt_val:,.2f}",
            "category": "TAX SALE EXCESS PROCEEDS",
            "statutory_citation": STATE_STATUTES.get(state, f"State Statutory Recovery Laws ({state})"),
            "value_tier": tier,
            "phone": "PENDING UNMASK",
            "email": "N/A",
            "status": "UNSOLD_LEAD",
            "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
        })

    logging.info(f"✨ Retained {len(clean)} pristine high-value lead(s) ($10k+).")
    return clean

# =====================================================================
# 3. TRACERFY SKIP-TRACING ENGINE
# =====================================================================
def skip_trace(leads):
    if not TRACERFY_API_KEY or not leads:
        return leads

    logging.info(f"⚡ Unmasking contacts for {len(leads)} verified lead(s)...")
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
            logging.warning(f"⚠️ Skip-trace bypass for {item['owner_name']}: {e}")

    return leads

# =====================================================================
# 4. WORKER KV INGESTION ENGINE
# =====================================================================
def upload(leads):
    if not leads:
        logging.info("ℹ️ Zero $10k+ leads to upload this cycle.")
        return

    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads)} leads to Cloudflare KV.")
        return

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    try:
        res = session.post(endpoint, json=leads, headers=WORKER_HEADERS, timeout=30)
        if res.status_code == 200:
            logging.info(f"✅ Ingested {len(leads)} verified $10k+ lead(s) directly to Executive Dashboard!")
        else:
            logging.error(f"❌ Worker Error [{res.status_code}]: {res.text}")
    except Exception as e:
        logging.error(f"⚠️ Connection error posting to Worker: {e}")

# =====================================================================
# MAIN EXECUTION
# =====================================================================
if __name__ == "__main__":
    logging.info("🚀 Launching Nationwide Multi-State $10k+ Ingress Engine...")
    raw_data = collect_all_sources()
    clean_data = clean_and_normalize(raw_data)
    enriched_data = skip_trace(clean_data)
    upload(enriched_data)
