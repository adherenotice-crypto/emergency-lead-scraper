import os
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import json
import csv
import io
import pandas as pd
import logging
import urllib.parse
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION, SAFEGUARDS & FILTERS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL") or "https://emergencyaudit.com"
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or "EmergencyAudit_Master_Key_2026!"
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

# Set to True ONLY when you explicitly want to unmask phone numbers via Apify
ENABLE_AUTO_SKIP_TRACE = (os.getenv("ENABLE_AUTO_SKIP_TRACE") or "false").lower() == "true"

# Minimum Surplus Thresholds (Filter out junk data)
MIN_COUNTY_SURPLUS = 10000.00   # $10k+ for County Foreclosures / Tax Overbids
MIN_STATE_SURPLUS = 50000.00    # $50k+ for State Controller Unclaimed Property

# Statutory Lookback Windows
MAX_COUNTY_DAYS = 365           # 1 Year CA Rev & Tax § 4675 Limit (Target: 30-90 days)
MAX_STATE_DAYS = 1095           # 3 Years max for optimal contactability

MAX_TEST_LEADS = None
PAUSE_PIPELINE = (os.getenv("PAUSE_PIPELINE") or "false").lower() == "true"
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]
STAGING_MODE = (os.getenv("STAGING_MODE") or "false").lower() == "true"

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 2. REMOTE DEDUPLICATION (PREVENTS CLOUDFLARE KV OVERFLOW)
# =====================================================================
def fetch_existing_kv_record_ids():
    """
    Queries Cloudflare Worker for currently stored case IDs to avoid duplicate writes.
    """
    endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
    headers = {"X-Emergency-Key": MASTER_ADMIN_KEY}
    
    try:
        logging.info("🔍 Checking Cloudflare KV for existing ledger records...")
        res = session.get(endpoint, headers=headers, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                existing_ids = {
                    item.get("record_id") or item.get("caseId") or item.get("citation_id")
                    for item in data if isinstance(item, dict)
                }
                logging.info(f"📊 Found {len(existing_ids)} existing record(s) in Cloudflare KV ledger.")
                return existing_ids
    except Exception as e:
        logging.warning(f"⚠️ Could not fetch existing KV ledger state: {e}. Proceeding with local dedup.")
    
    return set()

# =====================================================================
# 3. DATE & THRESHOLD VALIDATION ENGINE
# =====================================================================
def parse_surplus_amount(raw_amt):
    if not raw_amt:
        return None, 0.0
    clean_str = re.sub(r"[^\d.]", "", str(raw_amt))
    try:
        val = float(clean_str)
        if val > 0:
            return f"${val:,.2f} Surplus Credit", val
    except ValueError:
        pass
    return None, 0.0

def is_within_target_window(record_date_str, source_type):
    """
    Filters out records outside statutory recovery deadlines.
    """
    if not record_date_str or str(record_date_str).upper() in ["N/A", "NONE", "UNKNOWN", ""]:
        return True
        
    try:
        record_date = datetime.strptime(str(record_date_str).strip(), "%Y-%m-%d")
        age_in_days = (datetime.now() - record_date).days
        
        if source_type == "STATE_SCO":
            return age_in_days <= MAX_STATE_DAYS
        else:  # County Overbids
            return 30 <= age_in_days <= MAX_COUNTY_DAYS
    except Exception:
        return True

def validate_surplus_record(record):
    address = str(record.get("address") or "").strip().upper()
    apn = str(record.get("apn") or "").strip().upper()
    category = str(record.get("category") or "").upper()
    
    if not address and not apn:
        return False, "BLOCKED: Missing both Property Address and APN"
    if address in ["N/A", "NONE", "RECORDED PARCEL LOCATION", ""] and apn in ["N/A", "NONE", "ON FILE", "PENDING VERIFICATION", ""]:
        return False, "BLOCKED: Placeholder location data"
    
    amt_str, amt_val = parse_surplus_amount(record.get("default_amount"))
    source_type = "STATE_SCO" if "STATE" in category or "UNCLAIMED" in category else "COUNTY_OVERBID"
    
    # Enforce Dollar Minimums
    if source_type == "STATE_SCO" and amt_val < MIN_STATE_SURPLUS:
        return False, f"BLOCKED: State asset below ${MIN_STATE_SURPLUS:,.2f} threshold (${amt_val:,.2f})"
    elif source_type == "COUNTY_OVERBID" and amt_val < MIN_COUNTY_SURPLUS:
        return False, f"BLOCKED: County overbid below ${MIN_COUNTY_SURPLUS:,.2f} threshold (${amt_val:,.2f})"

    # Enforce Statutory Date Window
    record_date = record.get("sale_date") or record.get("record_date")
    if not is_within_target_window(record_date, source_type):
        return False, f"BLOCKED: Outside statutory lookback window ({record_date})"

    return True, "VALID_SURPLUS"

# =====================================================================
# 4. ENTITY DETECTOR & CANONICAL CASE ID GENERATOR
# =====================================================================
ENTITY_KEYWORDS = ["LLC", "INC", "CORP", "CORPORATION", "HOLDINGS", "PROPERTIES", "INVESTMENTS", "LTD", "LP", "GROUP", "PARTNERS", "REALTY", "COMPANY", "CO"]
TRUST_KEYWORDS = ["TRUST", "TRUSTEE", "FAMILY TRUST", "REVOCABLE", "LIVING TRUST", "ESTATE"]

def classify_owner_type(owner_name):
    clean_name = re.sub(r"[^\w\s]", "", str(owner_name).upper())
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in TRUST_KEYWORDS):
        return "TRUST"
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in ENTITY_KEYWORDS):
        return "CORPORATE_ENTITY"
    return "INDIVIDUAL"

def generate_deterministic_case_id(apn, address):
    clean_apn = re.sub(r"[^\w]", "", str(apn)).upper()
    if clean_apn and clean_apn not in ["PENDINGVERIFICATION", "NONE", "NA", ""] and len(clean_apn) >= 5:
        return f"AUD-APN-{clean_apn}"
    clean_addr = re.sub(r"[^\w]", "", str(address)).upper()
    if clean_addr and clean_addr not in ["RECORDEDPARCELLOCATION", "NONE", "NA", ""]:
        return f"AUD-{clean_addr[:12]}"
    return f"AUD-REF-{int(time.time())}"

# =====================================================================
# 5. LIVE AUTOMATED SCRAPER FEEDS (SCO & SOCAL COUNTIES)
# =====================================================================
def fetch_fresh_ca_sco_leads():
    """
    Fetches fresh unclaimed property records from public State Controller Open Data feeds.
    """
    logging.info("🌐 Fetching fresh State Controller (SCO) unclaimed records...")
    sco_leads = []
    ca_open_data_url = "https://data.ca.gov/resource/unclaimed-property.json?$where=amount>=50000&$limit=200"
    
    try:
        res = session.get(ca_open_data_url, timeout=15)
        if res.status_code == 200:
            records = res.json()
            for r in records if isinstance(records, list) else []:
                amt = float(r.get("amount", 0))
                if amt >= MIN_STATE_SURPLUS:
                    owner = r.get("owner_name") or r.get("holder_name") or "RECORDED OWNER"
                    addr = r.get("address") or "RECORDED PROPERTY LOCATION"
                    city = r.get("city") or "Los Angeles"
                    zip_code = r.get("zip") or "90012"
                    
                    sco_leads.append({
                        "owner_name": owner,
                        "address": f"{addr}, {city}, CA {zip_code}".strip(", "),
                        "city": city,
                        "state": "CA",
                        "zip": zip_code,
                        "apn": r.get("property_id") or f"SCO-{r.get('case_id', int(time.time()))}",
                        "default_amount": f"${amt:,.2f} Surplus Credit",
                        "category": "STATE UNCLAIMED FINANCIAL ASSET",
                        "phone": "PENDING UNMASK",
                        "violation": "Unclaimed financial property held in trust by CA State Controller."
                    })
            logging.info(f"✅ Extracted {len(sco_leads)} fresh State Controller leads >= $50k.")
    except Exception as e:
        logging.warning(f"⚠️ Live SCO endpoint scan bypassed: {e}")
        
    return sco_leads

def fetch_fresh_socal_county_leads():
    """
    Scrapes fresh excess proceeds from Southern California public portals.
    """
    logging.info("🌐 Fetching fresh SoCal County Excess Proceeds lists...")
    county_leads = []
    # Placeholder for live county scrapers (LA, OC, Riverside, San Bernardino, San Diego)
    return county_leads

# =====================================================================
# 6. APIFY TRUEPEOPLESEARCH SKIP TRACING ENGINE
# =====================================================================
def extract_phone_from_raw_row(raw_dict):
    if not isinstance(raw_dict, dict):
        return None
    found_phones = []

    def search_obj(obj):
        if isinstance(obj, str):
            matches = re.findall(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", obj)
            for m in matches:
                digits = re.sub(r"\D", "", m)
                if len(digits) == 10 and not digits.startswith(("800", "888", "877", "866", "900", "000")):
                    found_phones.append("+1" + digits)
                elif len(digits) == 11 and digits.startswith("1"):
                    found_phones.append("+" + digits)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                search_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                search_obj(item)

    search_obj(raw_dict)
    return found_phones[0] if found_phones else None

def apify_bulk_skip_trace(lead_batch):
    if not APIFY_TOKEN or not ENABLE_AUTO_SKIP_TRACE:
        logging.info("ℹ️ Skip tracing skipped (ENABLE_AUTO_SKIP_TRACE is False).")
        return {}

    logging.info(f"⚡ [SKIP TRACE ENGINE] Unmasking contacts for {len(lead_batch)} surplus record(s)...")
    results_map = {}
    CHUNK_SIZE = 25

    for i in range(0, len(lead_batch), CHUNK_SIZE):
        chunk = lead_batch[i:i + CHUNK_SIZE]
        search_queries, start_urls, structured_queries, chunk_order_cids, lookup_query_map = [], [], [], [], {}

        for item in chunk:
            raw_name = item.get("owner_name", "")
            owner_type = classify_owner_type(raw_name)
            target_name = re.sub(r"\b(TRUST|TRUSTEE|TTEE|FAMILY|REVOCABLE|LIVING|DATED|\d+)\b", "", raw_name, flags=re.I).strip() if owner_type == "TRUST" else raw_name

            name_parts = target_name.strip().split()
            first_name = name_parts[0] if len(name_parts) >= 1 else target_name
            last_name = " ".join(name_parts[1:]) if len(name_parts) >= 2 else ""
            city = item.get("city", "Los Angeles")
            state = item.get("state", "CA")

            if first_name and last_name:
                query_str = f"{first_name} {last_name}, {city}, {state}"
                search_queries.append(query_str)
                cid = item.get("record_id")
                chunk_order_cids.append(cid)
                
                encoded_name = urllib.parse.quote(f"{first_name} {last_name}")
                encoded_loc = urllib.parse.quote(f"{city}, {state}")
                start_urls.append({"url": f"https://www.truepeoplesearch.com/results?name={encoded_name}&citystatezip={encoded_loc}"})

                structured_queries.append({"name": f"{first_name} {last_name}", "cityStateZip": f"{city}, {state}", "location": f"{city}, {state}", "city": city, "state": state})
                lookup_query_map[query_str.upper()] = cid

        if not search_queries:
            continue

        start_endpoint = f"https://api.apify.com/v2/acts/memo23~truepeoplesearch-people-search-scraper/runs?token={APIFY_TOKEN}"
        payload = {"startUrls": start_urls, "searchQueries": search_queries, "queries": structured_queries, "proxyConfiguration": {"useApifyProxy": True}, "maxResults": 1}

        try:
            run_res = session.post(start_endpoint, json=payload, timeout=25)
            if run_res.status_code not in [200, 201]:
                continue

            run_data = run_res.json().get("data", {})
            run_id, dataset_id = run_data.get("id"), run_data.get("defaultDatasetId")
            status_endpoint = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
            run_succeeded = False

            for _ in range(10):
                time.sleep(4)
                poll_res = session.get(status_endpoint, timeout=8)
                if poll_res.status_code == 200 and poll_res.json().get("data", {}).get("status") == "SUCCEEDED":
                    run_succeeded = True
                    break

            if not run_succeeded:
                continue

            dataset_endpoint = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}"
            items_res = session.get(dataset_endpoint, timeout=15)
            if items_res.status_code == 200:
                for idx, record in enumerate(items_res.json()):
                    phone = extract_phone_from_raw_row(record)
                    if not phone:
                        continue

                    matched_cid = None
                    sq = str(record.get("searchQuery") or record.get("query") or record.get("url") or "").upper().strip()
                    if sq:
                        for q_key, cid in lookup_query_map.items():
                            if q_key in sq or sq in q_key:
                                matched_cid = cid
                                break

                    if not matched_cid and idx < len(chunk_order_cids):
                        matched_cid = chunk_order_cids[idx]

                    if matched_cid:
                        results_map[matched_cid] = phone
        except Exception as e:
            logging.warning(f"⚠️ Apify Engine Exception Handled: {e}")

    logging.info(f"✅ Skip tracing complete. Unmasked {len(results_map)} live number(s).")
    return results_map

# =====================================================================
# 7. DATA NORMALIZATION & LOCAL FILE PARSING
# =====================================================================
def normalize_lead_dict(raw_dict):
    norm = {}
    for k, v in raw_dict.items():
        if k is not None and not (isinstance(v, float) and pd.isna(v)):
            clean_k = re.sub(r'[^a-z0-9]', '', str(k).lower())
            val_str = str(v).strip()
            if val_str.lower() != 'nan':
                norm[clean_k] = val_str

    apn_val = norm.get("apn") or norm.get("parcel") or norm.get("parcelid") or norm.get("pin") or norm.get("id") or "PENDING VERIFICATION"
    addr_val = norm.get("address") or norm.get("propertyaddress") or norm.get("siteaddress") or norm.get("situs") or "Recorded Property Location"
    city_val = norm.get("city") or norm.get("propertycity") or norm.get("situscity") or "Los Angeles"
    state_val = norm.get("state") or norm.get("propertystate") or norm.get("situsstate") or "CA"
    zip_val = norm.get("zip") or norm.get("zipcode") or "90012"

    owner_val = (
        norm.get("ownername") or norm.get("owner") or norm.get("claimant") or 
        norm.get("ownerfullname") or norm.get("entity") or "RECORDED PROPERTY OWNER"
    )

    raw_amount = (
        norm.get("surplusamount") or norm.get("overbid") or norm.get("excessproceeds") or 
        norm.get("surplus") or norm.get("amount") or norm.get("defaultamount") or "$18,450.00"
    )
    formatted_amt, _ = parse_surplus_amount(raw_amount)

    category = "TAX SALE EXCESS PROCEEDS" if "tax" in str(raw_dict).lower() else "FORECLOSURE SURPLUS PROCEEDS"
    citation = norm.get("caseid") or norm.get("fileid") or norm.get("recordid") or generate_deterministic_case_id(apn_val, addr_val)
    phone_val = extract_phone_from_raw_row(raw_dict) or norm.get("phone") or "PENDING UNMASK"

    return {
        "record_id": citation,
        "citation_id": citation,
        "caseId": citation,
        "owner_name": owner_val,
        "leadName": owner_val,
        "address": f"{addr_val}, {city_val}, {state_val} {zip_val}".strip(", "),
        "city": city_val,
        "state": state_val,
        "zip": zip_val,
        "apn": apn_val,
        "category": category,
        "default_amount": formatted_amt or "$18,450.00 Surplus Credit",
        "property_type": norm.get("propertytype") or norm.get("propertyuse") or "Single Family / Commercial Real Estate",
        "violation": f"Unclaimed excess proceeds generated post-auction in {city_val}, {state_val}.",
        "phone": phone_val,
        "email": norm.get("email") or "N/A",
        "sale_date": norm.get("saledate") or norm.get("recorddate") or norm.get("date")
    }

def parse_any_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    raw_records = []
    try:
        if ext == ".csv":
            with open(file_path, mode="r", encoding="utf-8-sig") as f:
                raw_records = list(csv.DictReader(f))
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path).fillna("")
            raw_records = df.to_dict(orient="records")
        elif ext == ".json":
            with open(file_path, mode="r", encoding="utf-8") as f:
                data = json.load(f)
                raw_records = data if isinstance(data, list) else [data]
    except Exception as e:
        logging.error(f"❌ Error reading file {file_path}: {e}")
        return []
    return [normalize_lead_dict(rec) for rec in raw_records if isinstance(rec, dict)]

def load_all_lead_datasets():
    all_leads = []
    
    # 1. Pull Live Scraped Leads
    all_leads.extend(fetch_fresh_ca_sco_leads())
    all_leads.extend(fetch_fresh_socal_county_leads())
    
    # 2. Parse Repository CSV/Excel Datasets
    valid_exts = (".csv", ".xlsx", ".xls", ".json")
    ignored_files = {"package.json", "package-lock.json", "tsconfig.json", "metadata.json"}

    root_files = [
        f for f in os.listdir(".") 
        if f.lower().endswith(valid_exts) 
        and not f.startswith("temp_")
        and f.lower() not in ignored_files
    ]
    
    for f in root_files:
        logging.info(f"📁 Parsing surplus dataset file: {f}")
        all_leads.extend(parse_any_file(f))
        
    return all_leads

# =====================================================================
# 8. MAIN EXECUTION LOOP WITH DEDUPLICATION & DISPATCH
# =====================================================================
if __name__ == "__main__":
    if PAUSE_PIPELINE:
        logging.info("⏸️ PAUSE_PIPELINE is set to true. Exiting cleanly.")
        exit(0)

    logging.info("🚀 Nationwide Surplus Funds Ingress Engine Active.")
    
    # 1. Fetch existing keys from Cloudflare KV to prevent duplicate writes
    existing_kv_ids = fetch_existing_kv_record_ids()

    # 2. Load and aggregate all feeds (Live Scrapers + Local Files)
    real_leads = load_all_lead_datasets()
    logging.info(f"📥 Total Aggregated Feed: {len(real_leads)} raw record(s). Filtering...")

    passed_count = 0
    seen_identifiers = set()
    needs_unmask_batch = []
    prepared_records = []
    new_lead_counter = 0

    current_timestamp = time.strftime("%Y-%m-%d %H:%M:%S PST")

    for parcel in real_leads:
        if MAX_TEST_LEADS and passed_count >= MAX_TEST_LEADS:
            break
            
        apn = parcel.get("apn")
        addr = parcel.get("address")
        dedup_key = apn if (apn and apn != "PENDING VERIFICATION") else addr
        
        # Local deduplication
        if dedup_key in seen_identifiers:
            continue
        seen_identifiers.add(dedup_key)

        # Enforce Dollar & Date Statutory Safeguards
        is_valid, reason = validate_surplus_record(parcel)
        if not is_valid:
            continue

        cid = parcel.get("record_id") or generate_deterministic_case_id(apn, addr)
        parcel["record_id"] = cid

        # DEDUPLICATION CHECK: Skip if already live in Cloudflare KV
        if cid in existing_kv_ids:
            continue

        # Tag explicitly as a NEW lead
        parcel["is_new"] = True
        parcel["ingested_at"] = current_timestamp
        new_lead_counter += 1

        existing_phone = parcel.get("phone")
        if ENABLE_AUTO_SKIP_TRACE and (not existing_phone or existing_phone in ["PENDING UNMASK", "Unmasked Upon Purchase"]):
            needs_unmask_batch.append(parcel)

        prepared_records.append(parcel)
        passed_count += 1

    # 3. Exit immediately if no new leads exist (Saves Cloudflare KV quota)
    if not prepared_records:
        logging.info("🛡️ SAFEGUARD ACTIVE: 0 new leads found. All records already exist in Cloudflare KV.")
        logging.info("⚡ Exiting in 1 second. Zero write operations made to Cloudflare KV!")
        exit(0)

    logging.info(f"✨ Found {new_lead_counter} BRAND NEW lead(s) meeting all dollar/date thresholds!")

    unmasked_phones = {}
    if needs_unmask_batch:
        unmasked_phones = apify_bulk_skip_trace(needs_unmask_batch)

    dispatch_queue = []
    for parcel in prepared_records:
        cid = parcel["record_id"]
        phone = parcel.get("phone")
        if not phone or phone in ["PENDING UNMASK", "Unmasked Upon Purchase"]:
            phone = unmasked_phones.get(cid, "PENDING UNMASK")

        dispatch_queue.append({
            "record_id": cid,
            "citation_id": cid,
            "caseId": cid,
            "address": parcel.get("address"),
            "owner_name": parcel.get("owner_name"),
            "leadName": parcel.get("owner_name"),
            "phone": phone,
            "email": parcel.get("email", "N/A"),
            "apn": parcel.get("apn"),
            "category": parcel.get("category", "FORECLOSURE SURPLUS PROCEEDS"),
            "default_amount": parcel.get("default_amount"),
            "property_type": parcel.get("property_type"),
            "violation": parcel.get("violation"),
            "is_new": True,
            "ingested_at": parcel.get("ingested_at"),
            "status": "NEW_LEAD" if not STAGING_MODE else "PENDING_REVIEW"
        })

    # 4. Dispatch ONLY new leads to Cloudflare KV
    if dispatch_queue:
        logging.info(f"🚀 Dispatching {len(dispatch_queue)} NEW surplus record(s) to Cloudflare KV...")
        endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
        headers = {"Content-Type": "application/json", "X-Emergency-Key": MASTER_ADMIN_KEY}

        POST_CHUNK_SIZE = 25
        successful_dispatches = 0
        for j in range(0, len(dispatch_queue), POST_CHUNK_SIZE):
            post_chunk = dispatch_queue[j:j + POST_CHUNK_SIZE]
            if DRY_RUN:
                logging.info(f"🧪 [DRY RUN] Would post chunk of {len(post_chunk)} items")
            else:
                try:
                    res = session.post(endpoint, json=post_chunk, headers=headers, timeout=30)
                    if res.status_code == 200:
                        successful_dispatches += len(post_chunk)
                        logging.info(f"✅ Batch [{j//POST_CHUNK_SIZE + 1}] Stored {len(post_chunk)} NEW surplus records in KV.")
                    else:
                        logging.error(f"❌ Worker Error [{res.status_code}]: {res.text}")
                except Exception as e:
                    logging.error(f"⚠️ Dispatch Exception: {e}")

        logging.info(f"🎉 Ingress Complete! {successful_dispatches}/{len(dispatch_queue)} NEW surplus records live on dashboard.")
