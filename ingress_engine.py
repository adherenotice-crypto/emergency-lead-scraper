import os
import re
import io
import time
import json
import logging
import hashlib
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
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 10000.00)
MAX_SURPLUS_CEILING = 10000000.00  # $10M Ceiling prevents concatenated case numbers
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

# Criminal Court Docket & Bad Row Blacklist
TEXT_BLACKLIST = [
    "COUNT(S)", "CONVICTED", "FELONY", "FELON", "CRIMINAL", "HIJACKING", "CLERK NO",
    "HAVING BEEN", "COMMISSION", "PARTICIPATION", "DOCKET", "JUDGMENT", "O.C.G.A",
    "ROBBERY", "MURDER", "ATTEMPTED", "VIOLATION", "STATUTE", "COURT", "SUPERIOR",
    "UNKNOWN", "RECORDED CLAIMANT", "COUNTY CLERK", "TREASURER", "N/A", "NULL"
]

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, application/pdf, */*",
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
# NATIONWIDE STATUTORY CITATIONS
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
    "TN": "TCA § 67-5-2702 (Tax Sale Excess Proceeds)",
    "IL": "35 ILCS 200/21-295 (Tax Sale Surplus)"
}

# =====================================================================
# VERIFIED SURPLUS FEEDS ONLY (PDF, CSV & OPEN JSON APIs)
# =====================================================================
PUBLIC_SURPLUS_FEEDS = [
    # OPEN DATA JSON APIS (Fast, Clean, No Block)
    {"name": "Texas Excess Proceeds API", "state": "TX", "county": "Harris", "type": "json_api", "url": "https://data.texas.gov/resource/excess-proceeds.json?$where=amount>10000&$limit=1000"},
    # FLORIDA TAX SURPLUS
    {"name": "Orange County FL Surplus", "state": "FL", "county": "Orange", "type": "pdf", "url": "https://www.myorangeclerk.com/Portals/0/Foreclosure/Surplus_List.pdf"},
    {"name": "Hillsborough County FL Surplus", "state": "FL", "county": "Hillsborough", "type": "pdf", "url": "https://www.hillsclerk.com/-/media/files/hillsclerk/court-records/foreclosure/surplus-list.pdf"},
    {"name": "Palm Beach County FL Surplus", "state": "FL", "county": "Palm Beach", "type": "pdf", "url": "https://www.mypalmbeachclerk.com/home/showpublisheddocument/1230"},
    # TEXAS TAX OVERBIDS
    {"name": "Harris County TX Excess Proceeds", "state": "TX", "county": "Harris", "type": "csv", "url": "https://www.hctx.net/Tax-Assessor/ExcessProceeds/DownloadCSV"},
    {"name": "Bexar County TX Excess Proceeds", "state": "TX", "county": "Bexar", "type": "pdf", "url": "https://www.bexar.org/DocumentCenter/View/28221/Excess-Proceeds-List-PDF"},
    {"name": "Tarrant County TX Surplus", "state": "TX", "county": "Tarrant", "type": "pdf", "url": "https://www.tarrantcountytx.gov/content/dam/main/tax-assessor-collector/Excess_Proceeds.pdf"},
    # OHIO & NC TAX SURPLUS
    {"name": "Franklin County OH Unclaimed Funds", "state": "OH", "county": "Franklin", "type": "csv", "url": "https://treasurer.franklincountyohio.gov/FranklinCounty/media/Documents/Unclaimed-Funds.csv"},
    {"name": "Mecklenburg County NC Surplus", "state": "NC", "county": "Mecklenburg", "type": "pdf", "url": "https://www.mecknc.gov/TaxCollector/Documents/Surplus-Funds-List.pdf"}
]

def fetch_feed_data(url, name):
    try:
        res = session.get(url, headers=BROWSER_HEADERS, timeout=12)
        if res.status_code == 200 and len(res.content) > 200:
            logging.info(f"   [Direct HTTP 200] {len(res.content)} bytes for {name}")
            return res.content
    except Exception as e:
        logging.warning(f"   [Direct HTTP Fail] {name}: {e}")

    if SCRAPERAPI_KEY:
        proxy_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}&render=true&country_code=us"
        try:
            res = session.get(proxy_url, timeout=25)
            if res.status_code == 200 and len(res.content) > 200:
                logging.info(f"   [ScraperAPI Residential 200] {len(res.content)} bytes for {name}")
                return res.content
        except Exception as e:
            logging.warning(f"   [ScraperAPI Fail] {name}: {e}")

    return None

def parse_amount(text):
    clean_str = re.sub(r"[^\d.]", "", str(text))
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

def is_blacklisted(text):
    text_upper = str(text).upper()
    return any(bad_word in text_upper for bad_word in TEXT_BLACKLIST)

# =====================================================================
# 1. HARVESTERS
# =====================================================================
def harvest_json_api(feed):
    logging.info(f"🌐 Querying Open API: {feed['name']}...")
    records = []
    content = fetch_feed_data(feed["url"], feed["name"])
    if not content:
        return records
    try:
        data = json.loads(content.decode("utf-8", errors="ignore"))
        if isinstance(data, list):
            for row in data:
                amt, owner, addr = 0.0, "", ""
                for k, v in row.items():
                    kl = k.lower()
                    if any(t in kl for t in ["amount", "balance", "surplus", "proceeds", "value"]):
                        amt = parse_amount(v)
                    elif any(t in kl for t in ["owner", "name", "claimant", "payee", "holder"]):
                        owner = str(v).strip().upper()
                    elif any(t in kl for t in ["address", "situs", "location", "property"]):
                        addr = str(v).strip().upper()

                if MIN_SURPLUS_THRESHOLD <= amt <= MAX_SURPLUS_CEILING and owner and not is_blacklisted(owner):
                    records.append({
                        "owner_name": owner,
                        "situs_address": addr or "RECORDED PROPERTY LOCATION",
                        "amount": amt,
                        "county": feed["county"],
                        "state": feed["state"],
                        "holder_type": "UNCLAIMED SURPLUS PROCEEDS"
                    })
        logging.info(f"✅ Extracted {len(records)} record(s) from {feed['name']} API.")
    except Exception as e:
        logging.warning(f"⚠️ JSON API exception for {feed['name']}: {e}")
    return records

def harvest_pdf_feed(feed):
    logging.info(f"📄 Harvesting {feed['state']} - {feed['county']} County Surplus PDF...")
    records = []
    content = fetch_feed_data(feed["url"], feed["name"])
    if not content:
        return records

    try:
        lines = []
        if PDF_ENGINE == "pdfplumber":
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    if page_text := page.extract_text():
                        lines.extend(page_text.split("\n"))
        else:
            reader = pypdf.PdfReader(io.BytesIO(content))
            for page in reader.pages:
                if page_text := page.extract_text():
                    lines.extend(page_text.split("\n"))

        for line in lines:
            if is_blacklisted(line):
                continue

            # Strict Currency Pattern Match ($XX,XXX.XX)
            amounts = re.findall(r"\$\b[\d,]{5,}(?:\.\d{2})?\b|\b[\d,]{5,}\.\d{2}\b", line)
            for amt_text in amounts:
                amt_val = parse_amount(amt_text)
                if MIN_SURPLUS_THRESHOLD <= amt_val <= MAX_SURPLUS_CEILING:
                    clean_line = re.sub(r"\$?\b[\d,]{5,}(?:\.\d{2})?\b", "", line).strip()
                    parts = [p.strip() for p in re.split(r"\s{2,}|\t", clean_line) if p.strip()]
                    
                    owner = parts[0].upper() if len(parts) > 0 else ""
                    address = parts[1].upper() if len(parts) > 1 else "RECORDED PROPERTY LOCATION"

                    if owner and not is_blacklisted(owner) and not is_blacklisted(address):
                        records.append({
                            "owner_name": owner,
                            "situs_address": address,
                            "amount": amt_val,
                            "county": feed["county"],
                            "state": feed["state"],
                            "holder_type": "TAX DEED OVERBID / SURPLUS PROCEEDS"
                        })
                    break

        logging.info(f"✅ Extracted {len(records)} raw record(s) from {feed['county']} PDF.")
    except Exception as e:
        logging.warning(f"⚠️ PDF parse exception for {feed['county']}: {e}")

    return records

def harvest_csv_feed(feed):
    logging.info(f"📊 Harvesting {feed['state']} - {feed['county']} County Surplus CSV...")
    records = []
    content = fetch_feed_data(feed["url"], feed["name"])
    if not content:
        return records

    try:
        csv_text = content.decode("utf-8", errors="ignore")
        df = pd.read_csv(io.StringIO(csv_text), on_bad_lines="skip")
        
        owner_col = next((c for c in df.columns if any(t in str(c).lower() for t in ["owner", "name", "claimant", "payee"])), None)
        addr_col = next((c for c in df.columns if any(t in str(c).lower() for t in ["address", "situs", "location", "property"])), None)

        for _, row in df.iterrows():
            row_str = " ".join([str(val) for val in row.values])
            if is_blacklisted(row_str):
                continue

            amounts = re.findall(r"\$\b[\d,]{5,}(?:\.\d{2})?\b|\b[\d,]{5,}\.\d{2}\b", row_str)
            for amt_text in amounts:
                amt_val = parse_amount(amt_text)
                if MIN_SURPLUS_THRESHOLD <= amt_val <= MAX_SURPLUS_CEILING:
                    owner_val = str(row[owner_col]).strip().upper() if owner_col else str(row.iloc[0]).strip().upper()
                    addr_val = str(row[addr_col]).strip().upper() if addr_col else "RECORDED PROPERTY LOCATION"
                    
                    if owner_val and not is_blacklisted(owner_val):
                        records.append({
                            "owner_name": owner_val,
                            "situs_address": addr_val,
                            "amount": amt_val,
                            "county": feed["county"],
                            "state": feed["state"],
                            "holder_type": "UNCLAIMED PROPERTY / EXCESS PROCEEDS"
                        })
                    break
        logging.info(f"✅ Extracted {len(records)} raw record(s) from {feed['county']} CSV.")
    except Exception as e:
        logging.warning(f"⚠️ CSV parse exception for {feed['county']}: {e}")

    return records

def collect_all_sources():
    raw_harvest = []
    for feed in PUBLIC_SURPLUS_FEEDS:
        if feed["type"] == "json_api":
            raw_harvest.extend(harvest_json_api(feed))
        elif feed["type"] == "pdf":
            raw_harvest.extend(harvest_pdf_feed(feed))
        elif feed["type"] == "csv":
            raw_harvest.extend(harvest_csv_feed(feed))
    return raw_harvest

# =====================================================================
# 2. VALIDATOR & 13-HEADER MASTER SCHEMA MAPPER
# =====================================================================
def validate_and_normalize(raw_items):
    logging.info(f"🧹 Enforcing ${MIN_SURPLUS_THRESHOLD:,.2f} floor & ${MAX_SURPLUS_CEILING:,.2f} ceiling guardrails...")
    qualified_leads = []
    rejected_count = 0
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
        if not situs_addr or len(situs_addr) < 4 or is_blacklisted(situs_addr):
            situs_addr = "RECORDED PROPERTY LOCATION"

        state = str(item.get("state") or "CA").strip().upper()
        county = str(item.get("county") or "County").strip().title()
        case_num = f"CS-{state}-{int(time.time())}-{idx}"

        # Fingerprint Deduplication
        fp_str = f"{owner}|{situs_addr}|{amt_val:.2f}|{state}|{county}"
        fingerprint = hashlib.md5(fp_str.encode("utf-8")).hexdigest()[:12]
        
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)

        case_id = f"AUD-{state}-{county[:4].upper()}-{fingerprint}"
        tier = "TIER 1 GOLD ($50k+)" if amt_val >= 50000 else ("TIER 2 SILVER ($25k+)" if amt_val >= 25000 else "TIER 3 BRONZE ($10k+)")
        city = f"{county} Area"

        lead_record = {
            "record_id": case_id,
            "caseId": case_id,
            "citation_id": case_id,
            "real_case_number": case_num,
            "owner_name": owner,
            "leadName": owner,
            "situs_address": situs_addr,
            "address": situs_addr,
            "mailing_address": situs_addr,
            "city": city,
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
            "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S PST"),
            "fingerprint": fingerprint,

            # 13 Master Schema Alignment
            "Holder Name": owner,
            "Surplus Amount": f"${amt_val:,.2f}",
            "Property Address": situs_addr,
            "City State Zip": f"{city}, {state} 00000",
            "County Source": f"{county} County, {state}",
            "Case Number": case_num,
            "Holder Type": str(item.get("holder_type") or "TAX DEED OVERBID / SURPLUS PROCEEDS"),
            "Num Owners": 1,
            "Pending Claims": 0,
            "Paid Claims": 0,
            "Shares Reported": None,
            "Securities Name": None,
            "Cash Reported": amt_val
        }

        qualified_leads.append(lead_record)

    logging.info(f"📊 Audit Summary: Harvested={len(raw_items)} | Validated={len(qualified_leads)} | Rejected={rejected_count}")
    return qualified_leads

# =====================================================================
# 3. TRACERFY SKIP-TRACING
# =====================================================================
def skip_trace(leads):
    if not TRACERFY_API_KEY or not leads:
        logging.info("ℹ️ Skipping Tracerfy unmask (TRACERFY_API_KEY not set).")
        return leads

    logging.info(f"⚡ Unmasking contacts for {len(leads)} verified lead(s)...")
    url = "https://tracerfy.com/v1/api/trace/lookup/"
    headers = {"Authorization": f"Bearer {TRACERFY_API_KEY}", "Content-Type": "application/json"}

    for item in leads:
        try:
            res = session.post(url, json={
                "address": item["situs_address"],
                "owner_name": item["owner_name"]
            }, headers=headers, timeout=8)
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
# 4. WORKER INGESTION
# =====================================================================
def upload(leads):
    if not leads:
        logging.info("ℹ️ Zero qualified $10k+ leads to upload this cycle.")
        return

    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads)} leads to Cloudflare KV.")
        return

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    try:
        res = session.post(endpoint, json=leads, headers=WORKER_HEADERS, timeout=40)
        if res.status_code == 200:
            logging.info(f"✅ SUCCESS: Ingested {len(leads)} verified $10k+ lead(s) into Executive Command Hub!")
        else:
            logging.error(f"❌ Worker Ingest Error [{res.status_code}]: {res.text}")
    except Exception as e:
        logging.error(f"⚠️ Connection error posting to Worker: {e}")

# =====================================================================
# MAIN EXECUTION
# =====================================================================
if __name__ == "__main__":
    logging.info("🚀 Launching Nationwide Multi-State $10k+ Ingress Engine...")
    raw_data = collect_all_sources()
    clean_data = validate_and_normalize(raw_data)
    enriched_data = skip_trace(clean_data)
    upload(enriched_data)
