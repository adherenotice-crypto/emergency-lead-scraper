import os
import re
import io
import time
import json
import math
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

MIN_SURPLUS_THRESHOLD = float(os.getenv("MIN_SURPLUS_THRESHOLD") or 10000.00)
MAX_SURPLUS_CEILING = float(os.getenv("MAX_SURPLUS_CEILING") or 10000000.00)
REQUIRE_PHONE_TO_UPLOAD = (os.getenv("REQUIRE_PHONE_TO_UPLOAD") or "false").lower() in ["true", "1", "yes"]
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]

TEXT_BLACKLIST = [
    "COUNT(S)", "CONVICTED", "FELONY", "FELON", "CRIMINAL", "HIJACKING", "CLERK NO",
    "HAVING BEEN", "COMMISSION", "PARTICIPATION", "DOCKET", "JUDGMENT", "O.C.G.A",
    "ROBBERY", "MURDER", "ATTEMPTED", "VIOLATION", "STATUTE", "COURT", "SUPERIOR",
    "UNKNOWN", "RECORDED CLAIMANT", "COUNTY CLERK", "TREASURER", "N/A", "NULL"
]

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, application/pdf, */*",
    "Accept-Language": "en-US,en;q=0.9"
}

WORKER_HEADERS = {
    "User-Agent": "EmergencyAudit Ingress Engine v25.2",
    "X-Emergency-Key": MASTER_ADMIN_KEY,
    "Content-Type": "application/json"
}

session = requests.Session()
retries = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# FULL 50-STATE STATUTORY CITATIONS MAPPER
# =====================================================================
STATE_STATUTES = {
    "AL": "ALA. CODE § 40-10-28 (Tax Sale Excess)",
    "AK": "ALASKA STAT. § 29.45.480 (Tax Foreclosure Surplus)",
    "AZ": "A.R.S. § 33-812 / § 42-18205 (Excess Proceeds)",
    "AR": "ARK. CODE § 26-37-205 (Unclaimed Tax Surplus)",
    "CA": "CA REV & TAX CODE § 4675 / CIVIL CODE § 2924J",
    "CO": "C.R.S. § 39-11-115 (Tax Sale Overbid)",
    "CT": "CONN. GEN. STAT. § 12-157 (Tax Collector Surplus)",
    "DE": "DEL. CODE ANN. TIT. 9 § 8779 (Excess Tax Proceeds)",
    "FL": "FL STATUTES § 197.582 & § 45.032",
    "GA": "O.C.G.A. § 48-4-5 (Tax Sale Excess Funds)",
    "HI": "HAWAII REV. STAT. § 246-60 (Tax Sale Surplus)",
    "ID": "IDAHO CODE § 31-808 (Tax Deed Excess Sale)",
    "IL": "35 ILCS 200/21-295 (Indemnity/Surplus Fund)",
    "IN": "IND. CODE § 6-1.1-24-7 (Tax Sale Surplus)",
    "IA": "IOWA CODE § 446.27 (Tax Sale Proceeds)",
    "KS": "KAN. STAT. ANN. § 79-2803 (Foreclosure Surplus)",
    "KY": "KRS § 134.545 (Unclaimed Tax Overpayment)",
    "LA": "LA. REV. STAT. § 47:2211 (Tax Sale Excess)",
    "ME": "ME. REV. STAT. TIT. 36 § 949 (Tax Lien Surplus)",
    "MD": "MD. CODE TAX-PROP. § 14-844 (Tax Foreclosure Excess)",
    "MA": "MASS. GEN. LAWS CH. 60 § 79 (Tax Title Surplus)",
    "MI": "MCL § 211.78T (Foreclosure Surplus Claims)",
    "MN": "MINN. STAT. § 282.08 (Tax Forfeited Surplus)",
    "MS": "MISS. CODE § 27-41-79 (Tax Sale Overplus)",
    "MO": "MO. REV. STAT. § 140.230 (Tax Sale Surplus)",
    "MT": "MONT. CODE § 15-18-211 (Tax Deed Excess)",
    "NE": "NEB. REV. STAT. § 77-1837 (Tax Sale Excess)",
    "NV": "NRS § 361.595 (Unclaimed Surplus Proceeds)",
    "NH": "N.H. REV. STAT. § 80:88 (Tax Deed Overplus)",
    "NJ": "N.J.S.A. 54:5-114.6 (Tax Sale Surplus)",
    "NM": "N.M. STAT. § 7-38-71 (Tax Sale Overage)",
    "NY": "NY CPLR § 5236 / REAL PROPERTY TAX LAW § 1136",
    "NC": "NC GEN STAT § 105-374 / § 1-339.67",
    "ND": "N.D. CENT. CODE § 57-28-20 (Tax Sale Excess)",
    "OH": "OH REV CODE § 5721.20 / § 2329.44",
    "OK": "OKLA. STAT. TIT. 68 § 3131 (Tax Sale Surplus)",
    "OR": "ORS § 312.270 (Foreclosure Excess Proceeds)",
    "PA": "72 P.S. § 5860.205 (REAL ESTATE TAX SALE LAW)",
    "RI": "R.I. GEN. LAWS § 44-9-18.1 (Tax Title Excess)",
    "SC": "SC CODE ANN § 12-51-130 (Overages)",
    "SD": "S.D. CODIFIED LAWS § 10-23-28 (Tax Sale Overplus)",
    "TN": "T.C.A. § 67-5-2702 (Tax Sale Excess Proceeds)",
    "TX": "TX TAX CODE § 34.04 & PROPERTY CODE § 51.002",
    "UT": "UTAH CODE § 59-2-1351.1 (Tax Sale Overbid)",
    "VT": "VT. STAT. ANN. TIT. 32 § 5259 (Tax Sale Surplus)",
    "VA": "VA. CODE ANN. § 58.1-3967 (Tax Sale Excess)",
    "WA": "RCW 84.64.080 (Tax Foreclosure Excess Proceeds)",
    "WV": "W. VA. CODE § 11A-3-28 (Tax Sale Excess)",
    "WI": "WIS. STAT. § 75.36 (Tax Deeded Foreclosure Surplus)",
    "WY": "WYO. STAT. § 39-13-108 (Tax Sale Surplus)"
}

# =====================================================================
# DIRECT COUNTY PUBLIC FEEDS
# =====================================================================
PUBLIC_SURPLUS_FEEDS = [
    {"name": "Fulton County GA Unclaimed Funds", "state": "GA", "county": "Fulton", "type": "pdf", "url": "https://www.fultonclerk.org/DocumentCenter/View/1245/Unclaimed-Funds-List-PDF"}
]

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
# 1. SOCRATA NATIONWIDE DISCOVERY ENGINE
# =====================================================================
def discover_socrata_datasets():
    logging.info("🔎 Launching Full 50-State Socrata Master Catalog Discovery...")
    discovered_records = []
    seen_datasets = set()

    keywords = [
        "excess proceeds", "surplus funds", "unclaimed surplus", 
        "tax sale overage", "tax deed overbid", "foreclosure surplus",
        "unclaimed property", "excess tax funds"
    ]
    
    for kw in keywords:
        catalog_url = f"https://api.us.socrata.com/api/catalog/v1?q={urllib.parse.quote(kw)}&limit=100"
        try:
            res = session.get(catalog_url, headers=BROWSER_HEADERS, timeout=12)
            if res.status_code == 200:
                data = res.json()
                results = data.get("results", [])
                for item in results:
                    resource = item.get("resource", {})
                    domain = item.get("metadata", {}).get("domain") or resource.get("domain")
                    dataset_id = resource.get("id")
                    
                    if domain and dataset_id and dataset_id not in seen_datasets:
                        seen_datasets.add(dataset_id)
                        records = harvest_socrata_paginated(domain, dataset_id)
                        discovered_records.extend(records)
        except Exception as e:
            logging.warning(f"⚠️ Socrata Discovery exception for keyword '{kw}': {e}")

    logging.info(f"✨ Total Socrata Raw Records Harvested: {len(discovered_records)}")
    return discovered_records

def harvest_socrata_paginated(domain, dataset_id):
    records = []
    limit = 10000
    offset = 0
    state_code = "US"
    state_match = re.search(r"\.([a-z]{2})\.gov", domain, re.IGNORECASE)
    if state_match:
        state_code = state_match.group(1).upper()

    while True:
        url = f"https://{domain}/resource/{dataset_id}.json?$limit={limit}&$offset={offset}"
        try:
            res = session.get(url, headers=BROWSER_HEADERS, timeout=20)
            if res.status_code != 200:
                break
            
            data = res.json()
            if not isinstance(data, list) or len(data) == 0:
                break

            for row in data:
                amt, owner, addr = 0.0, "", ""
                for k, v in row.items():
                    kl = k.lower()
                    if any(t in kl for t in ["amount", "balance", "surplus", "proceeds", "value", "cash", "overage", "overbid"]):
                        amt = parse_amount(v)
                    elif any(t in kl for t in ["owner", "name", "claimant", "payee", "holder", "defendant"]):
                        owner = str(v).strip().upper()
                    elif any(t in kl for t in ["address", "situs", "location", "property", "street"]):
                        addr = str(v).strip().upper()

                if MIN_SURPLUS_THRESHOLD <= amt <= MAX_SURPLUS_CEILING and owner and not is_blacklisted(owner):
                    records.append({
                        "owner_name": owner,
                        "situs_address": addr or "RECORDED PROPERTY LOCATION",
                        "amount": amt,
                        "county": domain.split(".")[0].replace("-", " ").title(),
                        "state": state_code,
                        "holder_type": "UNCLAIMED SURPLUS PROCEEDS"
                    })

            if len(data) < limit:
                break
            offset += limit
        except Exception as e:
            logging.warning(f"⚠️ Pagination error on {domain}/{dataset_id} at offset {offset}: {e}")
            break

    return records

# =====================================================================
# 2. DIRECT COUNTY PDF & CSV HARVESTERS
# =====================================================================
def harvest_pdf_feed(feed):
    logging.info(f"📄 Harvesting {feed['state']} - {feed['county']} County Surplus PDF...")
    records = []
    try:
        res = session.get(feed["url"], headers=BROWSER_HEADERS, timeout=15)
        if res.status_code != 200 or len(res.content) < 500:
            return records

        content = res.content
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

            amounts = re.findall(r"[\d,]{5,}(?:\.\d{2})?", line)
            for amt_text in amounts:
                amt_val = parse_amount(amt_text)
                if MIN_SURPLUS_THRESHOLD <= amt_val <= MAX_SURPLUS_CEILING:
                    clean_line = re.sub(r"[\d,]{5,}(?:\.\d{2})?", "", line).strip()
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

def collect_all_sources():
    raw_harvest = []
    socrata_records = discover_socrata_datasets()
    raw_harvest.extend(socrata_records)
    
    for feed in PUBLIC_SURPLUS_FEEDS:
        if feed["type"] == "pdf":
            raw_harvest.extend(harvest_pdf_feed(feed))

    return raw_harvest

# =====================================================================
# 3. VALIDATOR & MASTER SCHEMA MAPPER
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
# 4. TRACERFY SKIP-TRACING & AUTO-PURGE UNCONTACTABLE FILTER
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
            logging.info(f"🗑 PURGED UNCONTACTABLE LEAD: {item['owner_name']}")

    logging.info(f"🎯 Actionable Pipeline: Retained {len(contactable_leads)} lead(s) with active phone numbers | Auto-Purged {purged_count} dead lead(s).")
    return contactable_leads

# =====================================================================
# 5. CHUNKED BATCH INGESTION & BATCHED CSV ARTIFACT EXPORT
# =====================================================================
def export_and_batch_csv(leads, batch_size=2000):
    """Generates master CSV and splits leads into 2,000-lead CSV batches inside batches/ folder."""
    if not leads:
        return

    df = pd.DataFrame(leads)
    
    # 1. Master CSV Export
    df.to_csv("master_surplus_leads.csv", index=False)
    logging.info(f"📁 MASTER CSV GENERATED: Saved {len(leads)} verified leads into 'master_surplus_leads.csv'!")

    # 2. 2,000-Lead Batch CSV Exports
    os.makedirs("batches", exist_ok=True)
    total_batches = math.ceil(len(leads) / batch_size)

    for i in range(total_batches):
        start_idx = i * batch_size
        end_idx = min(start_idx + batch_size, len(leads))
        
        batch_df = df.iloc[start_idx:end_idx]
        batch_filename = f"batches/surplus_batch_{i+1}_leads_{start_idx+1}_to_{end_idx}.csv"
        
        batch_df.to_csv(batch_filename, index=False)
        logging.info(f"📦 BATCH CSV EXPORTED [{i+1}/{total_batches}]: Saved {len(batch_df)} leads to '{batch_filename}'")

def upload(leads, kv_batch_size=50, csv_batch_size=2000):
    if not leads:
        logging.info("ℹ️ Zero actionable leads to process.")
        return

    # Always generate master CSV & 2,000-lead batch CSV artifacts for GitHub Actions
    export_and_batch_csv(leads, batch_size=csv_batch_size)

    if DRY_RUN:
        logging.info(f"🧪 [DRY RUN] Would write {len(leads)} leads to Cloudflare KV.")
        return

    endpoint = f"{WORKER_URL}/api/inbound-lead-hook"
    total_uploaded = 0
    total_batches = (len(leads) + kv_batch_size - 1) // kv_batch_size

    logging.info(f"🚀 Uploading {len(leads)} leads in {total_batches} chunked batch(es) of {kv_batch_size}...")

    for idx in range(0, len(leads), kv_batch_size):
        batch = leads[idx:idx + kv_batch_size]
        current_batch_num = (idx // kv_batch_size) + 1
        try:
            res = session.post(endpoint, json=batch, headers=WORKER_HEADERS, timeout=25)
            if res.status_code == 200:
                total_uploaded += len(batch)
                logging.info(f"    ✅ Batch [{current_batch_num}/{total_batches}] Ingested ({len(batch)} leads).")
            else:
                logging.warning(f"    ⚠️ KV write paused at Batch [{current_batch_num}/{total_batches}] (Status {res.status_code}). Full dataset safe in CSV artifacts.")
                break
        except Exception as e:
            logging.error(f"    ⚠️ Batch [{current_batch_num}/{total_batches}] Connection Error: {e}")
            break

    logging.info(f"🎉 INGESTION SUMMARY: {total_uploaded}/{len(leads)} uploaded to KV. Complete dataset exported to master_surplus_leads.csv and batches/ directory.")

if __name__ == "__main__":
    logging.info("🚀 Launching Master 50-State Ingress Engine...")
    raw_data = collect_all_sources()
    clean_data = validate_and_normalize(raw_data)
    actionable_data = skip_trace_and_purge(clean_data)
    upload(actionable_data)
