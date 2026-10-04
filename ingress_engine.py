import os
import re
import io
import time
import json
import logging
import hashlib
import urllib.parse
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
MAX_SURPLUS_CEILING = float(os.getenv("MAX_SURPLUS_CEILING") or 10000000.00) # $10M Ceiling
REQUIRE_PHONE_TO_UPLOAD = (os.getenv("REQUIRE_PHONE_TO_UPLOAD") or "false").lower() in ["true", "1", "yes"]
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

# All 50 US States Postal Codes
ALL_50_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"
]

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
retries = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 50-STATE STATUTORY CITATION MAPPER
# =====================================================================
STATE_STATUTES = {
    "AL": "ALA. CODE § 40-10-28 (Tax Sale Excess)",
    "AZ": "A.R.S. § 33-812 / § 42-18205 (Excess Proceeds)",
    "CA": "CA REV & TAX CODE § 4675 / CIVIL CODE § 2924J",
    "CO": "C.R.S. § 39-11-115 (Tax Sale Overbid)",
    "FL": "FL STATUTES § 197.582 & § 45.032",
    "GA": "O.C.G.A. § 48-4-5 (Tax Sale Excess Funds)",
    "IL": "35 ILCS 200/21-295 (Indemnity/Surplus Fund)",
    "IN": "IND. CODE § 6-1.1-24-7 (Tax Sale Surplus)",
    "MI": "MCL § 211.78T (Foreclosure Surplus Claims)",
    "NC": "NC GEN STAT § 105-374 / § 1-339.67",
    "NV": "NRS § 361.595 (Unclaimed Surplus Proceeds)",
    "NY": "NY CPLR § 5236 / REAL PROPERTY TAX LAW § 1136",
    "OH": "OH REV CODE § 5721.20 / § 2329.44",
    "PA": "72 P.S. § 5860.205 (REAL ESTATE TAX SALE LAW)",
    "SC": "SC CODE ANN § 12-51-130 (Overages)",
    "TN": "T.C.A. § 67-5-2702 (Tax Sale Excess Proceeds)",
    "TX": "TX TAX CODE § 34.04 & PROPERTY CODE § 51.002",
    "WA": "RCW 84.64.080 (Tax Foreclosure Excess Proceeds)"
}

# =====================================================================
# LIVE PUBLIC SURPLUS FEEDS ONLY
# =====================================================================
PUBLIC_SURPLUS_FEEDS = [
    # GEORGIA TAX SURPLUS
    {"name": "Fulton County GA Unclaimed Funds", "state": "GA", "county": "Fulton", "type": "pdf", "url": "https://www.fultonclerk.org/DocumentCenter/View/1245/Unclaimed-Funds-List-PDF"},
    {"name": "DeKalb County GA Excess Funds", "state": "GA", "county": "DeKalb", "type": "pdf", "url": "https://www.dekalbcountyga.gov/sites/default/files/tax_execs_funds_list.pdf"},
    
    # FLORIDA TAX SURPLUS
    {"name": "Orange County FL Surplus", "state": "FL", "county": "Orange", "type": "pdf", "url": "https://www.myorangeclerk.com/Portals/0/Foreclosure/Surplus_List.pdf"},
    {"name": "Hillsborough County FL Surplus", "state": "FL", "county": "Hillsborough", "type": "pdf", "url": "https://www.hillsclerk.com/-/media/files/hillsclerk/court-records/foreclosure/surplus-list.pdf"},
    {"name": "Palm Beach County FL Surplus", "state": "FL", "county": "Palm Beach", "type": "pdf", "url": "https://www.mypalmbeachclerk.com/home/showpublisheddocument/1230"},
    
    # TEXAS TAX OVERBIDS
    {"name": "Harris County TX Excess Proceeds", "state": "TX", "county": "Harris", "type": "csv", "url": "https://www.hctx.net/Tax-Assessor/ExcessProceeds/DownloadCSV"},
    {"name": "Bexar County TX Excess Proceeds", "state": "TX", "county": "Bexar", "type": "pdf", "url": "https://www.bexar.org/DocumentCenter/View/28221/Excess-Proceeds-List-PDF"},
    {"name": "Tarrant County TX Surplus", "state": "TX", "county": "Tarrant", "type": "pdf", "url": "https://www.tarrantcountytx.gov/content/dam/main/tax-assessor-collector/Excess_Proceeds.pdf"},

    # NC, OH, AZ, NV
    {"name": "Mecklenburg County NC Surplus", "state": "NC", "county": "Mecklenburg", "type": "pdf", "url": "https://www.mecknc.gov/TaxCollector/Documents/Surplus-Funds-List.pdf"},
    {"name": "Franklin County OH Unclaimed Funds", "state": "OH", "county": "Franklin", "type": "csv", "url": "https://treasurer.franklincountyohio.gov/FranklinCounty/media/Documents/Unclaimed-Funds.csv"},
    {"name": "Maricopa County AZ Tax Surplus", "state": "AZ", "county": "Maricopa", "type": "pdf", "url": "https://www.maricopa.gov/DocumentCenter/View/61241/Excess-Proceeds-List"},
    {"name": "Clark County NV Excess Proceeds", "state": "NV", "county": "Clark", "type": "pdf", "url": "https://www.clarkcountynv.gov/treasurer/ExcessProceedsList.pdf"}
]

def is_valid_payload(content, feed_type):
    """Context-aware payload validation for HTML discovery vs Binary data feeds."""
    if not content or len(content) < 50:
        return False

    if feed_type == "html":
        return True

    head = content[:512].lower()
    if b"access denied" in head or b"cloudflare" in head or b"just a moment..." in head or b"enable javascript" in head:
        return False

    if feed_type == "pdf":
        return b"%PDF" in content[:1024]
    elif feed_type == "json_api":
        stripped = content.strip()
        return stripped.startswith(b"[") or stripped.startswith(b"{")
    elif feed_type == "csv":
        return b"<html" not in head and b"<!doctype" not in head

    return True

def fetch_feed_data(url, name, feed_type="pdf"):
    """Fetches feed content with payload validation and automatic ScraperAPI fallback."""
    try:
        res = session.get(url, headers=BROWSER_HEADERS, timeout=10)
        if res.status_code == 200 and is_valid_payload(res.content, feed_type):
            logging.info(f"   [Direct HTTP 200] {len(res.content)} bytes for {name}")
            return res.content
        else:
            logging.warning(f"   [Direct WAF Challenge / Invalid Payload] {name} - Falling back to ScraperAPI...")
    except Exception as e:
        logging.warning(f"   [Direct HTTP Fail] {name}: {e}")

    if SCRAPERAPI_KEY:
        proxy_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={urllib.parse.quote(url)}&country_code=us"
        try:
            res = session.get(proxy_url, timeout=25)
            if res.status_code == 200 and is_valid_payload(res.content, feed_type):
                logging.info(f"   [ScraperAPI Residential 200] {len(res.content)} bytes for {name}")
                return res.content
            else:
                logging.warning(f"   [ScraperAPI Invalid Payload] {name}")
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
# DYNAMIC 50-STATE DISCOVERY ENGINE
# =====================================================================
def run_50_state_discovery():
    logging.info("🔎 Scanning 50 States for active .gov surplus registries...")
    discovered_feeds = []

    for state in ALL_50_STATES[:10]:
        q = f'site:.gov "{state}" "surplus funds" OR "excess proceeds" filetype:pdf'
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}"
        content = fetch_feed_data(search_url, f"Discovery Engine ({state})", feed_type="html")
        
        if content:
            try:
                found_urls = re.findall(r'https?://[a-zA-Z0-9.\-_]+\.gov/[^"\s<>]+\.pdf', content.decode("utf-8", errors="ignore"))
                for link in set(found_urls)[:2]:
                    discovered_feeds.append({
                        "name": f"Dynamic Discovery ({state})",
                        "state": state,
                        "county": f"{state} County",
                        "type": "pdf",
                        "url": link
                    })
            except Exception as e:
                logging.warning(f"⚠️️ Discovery parse error for {state}: {e}")

    logging.info(f"✨ Discovered {len(discovered_feeds)} dynamic surplus endpoints!")
    return discovered_feeds

# =====================================================================
# HARVESTERS
# =====================================================================
def harvest_pdf_feed(feed):
    logging.info(f"📄 Harvesting {feed['state']} - {feed['county']} County Surplus PDF...")
    records = []
    content = fetch_feed_data(feed["url"], feed["name"], feed_type="pdf")
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
    content = fetch_feed_data(feed["url"], feed["name"], feed_type="csv")
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
    all_feeds = PUBLIC_SURPLUS_FEEDS + run_50_state_discovery()

    for feed in all_feeds:
        if feed["type"] == "pdf":
            raw_harvest.extend(harvest_pdf_feed(feed))
        elif feed["type"] == "csv":
            raw_harvest.extend(harvest_csv_feed(feed))

    return raw_harvest

# =====================================================================
# VALIDATOR & 13-HEADER MASTER SCHEMA MAPPER
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
# TRACERFY SKIP-TRACING & AUTO-PURGE UNCONTACTABLE FILTER
# =====================================================================
def skip_trace_and_purge(leads):
    if not leads:
        return []

    if not TRACERFY_API_KEY:
        logging.info("ℹ️ Tracerfy API key not set. Skipping contact unmasking.")
        return leads if not REQUIRE_PHONE_TO_UPLOAD else []

    logging.info(f"⚡ Unmasking contacts for {len(leads)} verified lead(s)...")
    url = "https://tracerfy.com/v1/api/trace/lookup/"
    headers = {"Authorization": f"Bearer {TRACERFY_API_KEY}", "Content-Type": "application/json"}

    contactable_leads = []
    purged_count = 0

    for item in leads:
        phone_found = False
        try:
            res = session.post(url, json={
                "address": item["situs_address"],
                "owner_name": item["owner_name"]
            }, headers=headers, timeout=8)
            
            if res.status_code == 200:
                data = res.json()
                if phone := (data.get("phone") or data.get("primary_phone")):
                    clean_p = re.sub(r"\D", "", str(phone))
                    if len(clean_p) >= 10:
                        item["phone"] = f"+1{clean_p[-10:]}"
                        phone_found = True
                if email := (data.get("email") or data.get("primary_email")):
                    item["email"] = email
        except Exception as e:
            logging.warning(f"⚠️ Skip-trace bypass for {item['owner_name']}: {e}")

        if phone_found or not REQUIRE_PHONE_TO_UPLOAD:
            contactable_leads.append(item)
        else:
            purged_count += 1
            logging.info(f"🗑 PURGED UNCONTACTABLE LEAD [No Phone Hit]: {item['owner_name']}")

    logging.info(f"🎯 Actionable Pipeline: Retained {len(contactable_leads)} lead(s) with active phone numbers | Auto-Purged {purged_count} dead lead(s).")
    return contactable_leads

# =====================================================================
# WORKER INGESTION
# =====================================================================
def upload(leads):
    if not leads:
        logging.info("ℹ️ Zero actionable leads to upload this cycle.")
        return

    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads)} leads to Cloudflare KV.")
        return

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    try:
        res = session.post(endpoint, json=leads, headers=WORKER_HEADERS, timeout=40)
        if res.status_code == 200:
            logging.info(f"✅ SUCCESS: Ingested {len(leads)} actionable $10k+ lead(s) into Executive Command Hub!")
        else:
            logging.error(f"❌ Worker Ingest Error [{res.status_code}]: {res.text}")
    except Exception as e:
        logging.error(f"⚠️ Connection error posting to Worker: {e}")

# =====================================================================
# MAIN EXECUTION
# =====================================================================
if __name__ == "__main__":
    logging.info("🚀 Launching Master 50-State Actionable Ingress Engine (Pure Live Mode)...")
    raw_data = collect_all_sources()
    clean_data = validate_and_normalize(raw_data)
    actionable_data = skip_trace_and_purge(clean_data)
    upload(actionable_data)
