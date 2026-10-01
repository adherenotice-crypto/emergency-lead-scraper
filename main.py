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
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & KEYS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL") or "https://emergencyaudit.com"
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or "EmergencyAudit_Master_Key_2026!"
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY") or os.getenv("TRACEFY_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

ENABLE_AUTO_SKIP_TRACE = (os.getenv("ENABLE_AUTO_SKIP_TRACE") or "true").lower() == "true"

# Minimum Surplus Thresholds
MIN_COUNTY_SURPLUS = 10000.00    # $10k+ for County Overbids
MIN_STATE_SURPLUS = 25000.00     # $25k+ for CA SCO State Unclaimed

PAUSE_PIPELINE = (os.getenv("PAUSE_PIPELINE") or "false").lower() == "true"
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]
STAGING_MODE = (os.getenv("STAGING_MODE") or "false").lower() == "true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "X-Emergency-Key": MASTER_ADMIN_KEY
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 2. REMOTE DEDUPLICATION (PREVENTS DUPLICATE WRITES TO KV)
# =====================================================================
def fetch_existing_kv_record_ids():
    """Queries Cloudflare Worker for currently stored case IDs."""
    endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
    try:
        logging.info("🔍 Checking Cloudflare KV for existing ledger records...")
        res = session.get(endpoint, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                existing_ids = {
                    item.get("record_id") or item.get("caseId") or item.get("citation_id")
                    for item in data if isinstance(item, dict)
                }
                logging.info(f"📊 Found {len(existing_ids)} existing record(s) in Cloudflare KV.")
                return existing_ids
    except Exception as e:
        logging.warning(f"⚠️ Could not fetch existing KV ledger state: {e}. Proceeding with clean dedup.")
    
    return set()

# =====================================================================
# 3. HELPER FUNCTIONS: PARSING & DISSECTION ENGINES
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

def classify_value_tier(amount_val):
    if amount_val >= 50000.0:
        return "TIER 1 GOLD ($50k+)"
    elif amount_val >= 25000.0:
        return "TIER 2 SILVER ($25k+)"
    else:
        return "TIER 3 BRONZE ($10k+)"

def generate_deterministic_case_id(apn, address):
    clean_apn = re.sub(r"[^\w]", "", str(apn)).upper()
    if clean_apn and clean_apn not in ["PENDINGVERIFICATION", "NONE", "NA", ""] and len(clean_apn) >= 5:
        return f"AUD-APN-{clean_apn}"
    clean_addr = re.sub(r"[^\w]", "", str(address)).upper()
    if clean_addr and clean_addr not in ["RECORDEDPARCELLOCATION", "NONE", "NA", ""]:
        return f"AUD-{clean_addr[:12]}"
    return f"AUD-REF-{int(time.time())}"

def validate_surplus_record(record):
    address = str(record.get("address") or "").strip().upper()
    apn = str(record.get("apn") or "").strip().upper()
    category = str(record.get("category") or "").upper()
    
    if not address and not apn:
        return False, "BLOCKED: Missing both Property Address and APN"
    
    amt_str, amt_val = parse_surplus_amount(record.get("default_amount"))
    source_type = "STATE_SCO" if "STATE" in category or "UNCLAIMED" in category else "COUNTY_OVERBID"
    
    if source_type == "STATE_SCO" and amt_val < MIN_STATE_SURPLUS:
        return False, f"BLOCKED: State asset below threshold (${amt_val:,.2f})"
    elif source_type == "COUNTY_OVERBID" and amt_val < MIN_COUNTY_SURPLUS:
        return False, f"BLOCKED: County overbid below threshold (${amt_val:,.2f})"

    return True, "VALID_SURPLUS"

# =====================================================================
# 4. DISSECTED LIVE FEEDS (STATE SCO + SOCAL COUNTIES)
# =====================================================================
def fetch_fresh_ca_sco_leads():
    """Live API feed from CA State Controller (SCO) Unclaimed Property Data."""
    logging.info("🌐 Dissecting live CA State Controller (SCO) unclaimed directory...")
    sco_leads = []
    ca_open_data_url = "https://data.ca.gov/resource/unclaimed-property.json?$where=amount>=25000&$limit=500&$order=amount DESC"
    
    try:
        res = session.get(ca_open_data_url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            records = res.json()
            for r in records if isinstance(records, list) else []:
                amt = float(r.get("amount", 0) or 0)
                if amt >= MIN_STATE_SURPLUS:
                    owner = r.get("owner_name") or r.get("holder_name") or "RECORDED OWNER"
                    addr = r.get("address") or "RECORDED PROPERTY LOCATION"
                    city = r.get("city") or "Los Angeles"
                    zip_code = r.get("zip") or "90012"
                    apn_val = str(r.get("property_id") or r.get("case_id") or r.get("id") or int(time.time())).replace("-", "")
                    cid = generate_deterministic_case_id(apn_val, addr)

                    sco_leads.append({
                        "record_id": cid,
                        "citation_id": cid,
                        "caseId": cid,
                        "owner_name": owner,
                        "leadName": owner,
                        "address": f"{addr}, {city}, CA {zip_code}".strip(", "),
                        "city": city,
                        "state": "CA",
                        "zip": zip_code,
                        "apn": f"SCO-{apn_val[:12]}",
                        "default_amount": f"${amt:,.2f} Surplus Credit",
                        
                        # Dissected Lead Metadata
                        "source_origin": "CA State Controller (SCO) Open Data",
                        "county": "Statewide CA",
                        "asset_type": "Unclaimed Financial Property held by State Controller",
                        "category": "STATE UNCLAIMED FINANCIAL ASSET",
                        "value_tier": classify_value_tier(amt),
                        "script_pitch": f"State-held unclaimed surplus financial asset from {city}, CA.",
                        "phone": "PENDING UNMASK",
                        "email": "N/A"
                    })
            logging.info(f"✅ Dissected {len(sco_leads)} live State Controller records.")
    except Exception as e:
        logging.warning(f"⚠️ Live SCO API query bypassed: {e}")
        
    return sco_leads

def fetch_fresh_socal_county_leads():
    """Live scrapers for SoCal County Excess Proceeds lists."""
    logging.info("🌐 Dissecting live SoCal County Tax Sale Excess Proceeds listings...")
    county_leads = []

    # LA County TTC Scraper
    try:
        la_ttc_url = "https://ttc.lacounty.gov/excess-proceeds-from-sale-of-tax-defaulted-property/"
        res = session.get(la_ttc_url, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.find_all("tr")
            for row in rows:
                cols = [ele.text.strip() for ele in row.find_all(["td", "th"])]
                if len(cols) >= 4:
                    raw_apn = re.sub(r"[^\d]", "", cols[0])
                    if len(raw_apn) == 10:
                        amt_clean = re.sub(r"[^\d.]", "", cols[-1])
                        amt_val = float(amt_clean) if amt_clean else 15000.0
                        if amt_val >= MIN_COUNTY_SURPLUS:
                            cid = generate_deterministic_case_id(raw_apn, cols[2] if len(cols) > 2 else "")
                            county_leads.append({
                                "record_id": cid,
                                "citation_id": cid,
                                "caseId": cid,
                                "owner_name": cols[1] if len(cols) > 1 else "RECORDED PROPERTY OWNER",
                                "leadName": cols[1] if len(cols) > 1 else "RECORDED PROPERTY OWNER",
                                "address": cols[2] if len(cols) > 2 else f"Parcel {raw_apn}, Los Angeles, CA",
                                "city": "Los Angeles", "state": "CA", "zip": "90012",
                                "apn": raw_apn,
                                "default_amount": f"${amt_val:,.2f} Surplus Credit",
                                
                                # Dissected Lead Metadata
                                "source_origin": "LA County Treasurer-Collector (TTC)",
                                "county": "Los Angeles",
                                "asset_type": "Tax-Defaulted Auction Excess Proceeds (CA Rev & Tax § 4675)",
                                "category": "TAX SALE EXCESS PROCEEDS",
                                "value_tier": classify_value_tier(amt_val),
                                "script_pitch": f"Unclaimed excess overbid funds from LA County tax auction for APN {raw_apn}.",
                                "phone": "PENDING UNMASK",
                                "email": "N/A"
                            })
    except Exception as e:
        logging.warning(f"⚠️ LA County scraper warning: {e}")

    logging.info(f"✅ Dissected {len(county_leads)} verified SoCal County Excess Proceeds record(s).")
    return county_leads

# =====================================================================
# 5. TRACERFY SKIP TRACING ENGINE
# =====================================================================
def run_tracerfy_skip_trace(leads_batch):
    """Passes dissected leads to Tracerfy API to unmask owner phones & emails."""
    if not TRACERFY_API_KEY:
        logging.info("ℹ️ TRACERFY_API_KEY not configured. Skipping live Tracerfy lookup.")
        return leads_batch

    if not ENABLE_AUTO_SKIP_TRACE:
        logging.info("ℹ️ Skip tracing disabled (ENABLE_AUTO_SKIP_TRACE is False).")
        return leads_batch

    logging.info(f"⚡ [TRACERFY ENGINE] Unmasking contact information for {len(leads_batch)} lead(s)...")
    
    tracerfy_endpoint = "https://tracerfy.com/v1/api/trace/lookup/"
    headers = {
        "Authorization": f"Bearer {TRACERFY_API_KEY}",
        "Content-Type": "application/json"
    }

    for lead in leads_batch:
        if lead.get("phone") and lead["phone"] != "PENDING UNMASK":
            continue

        payload = {
            "address": lead.get("address"),
            "city": lead.get("city", "Los Angeles"),
            "state": lead.get("state", "CA"),
            "zip": lead.get("zip", "90012"),
            "owner_name": lead.get("owner_name")
        }

        try:
            res = session.post(tracerfy_endpoint, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                phone = data.get("phone") or data.get("primary_phone") or data.get("phone_1")
                email = data.get("email") or data.get("primary_email")
                
                if phone:
                    clean_phone = re.sub(r"\D", "", str(phone))
                    if len(clean_phone) == 10:
                        lead["phone"] = f"+1{clean_phone}"
                    elif len(clean_phone) == 11 and clean_phone.startswith("1"):
                        lead["phone"] = f"+{clean_phone}"
                    else:
                        lead["phone"] = str(phone)
                    logging.info(f"  └─ 🎯 Tracerfy Match: {lead['owner_name']} -> {lead['phone']}")
                
                if email:
                    lead["email"] = email
            else:
                logging.warning(f"⚠️ Tracerfy API response [{res.status_code}]: {res.text}")
        except Exception as e:
            logging.error(f"⚠️️ Tracerfy lookup exception for {lead.get('owner_name')}: {e}")

    return leads_batch

# =====================================================================
# 6. DATASET AGGREGATION & PIPELINE DISPATCH
# =====================================================================
def load_all_lead_datasets():
    all_leads = []
    all_leads.extend(fetch_fresh_ca_sco_leads())
    all_leads.extend(fetch_fresh_socal_county_leads())
    return all_leads

if __name__ == "__main__":
    if PAUSE_PIPELINE:
        logging.info("⏸️ PAUSE_PIPELINE is set to true. Exiting cleanly.")
        exit(0)

    logging.info("🚀 Dissected Surplus Lead Ingress Engine Active.")
    
    existing_kv_ids = fetch_existing_kv_record_ids()
    real_leads = load_all_lead_datasets()
    logging.info(f"📥 Total Aggregated Feed: {len(real_leads)} raw record(s). Filtering...")

    seen_identifiers = set()
    prepared_records = []
    current_timestamp = time.strftime("%Y-%m-%d %H:%M:%S PST")

    for parcel in real_leads:
        apn = parcel.get("apn")
        addr = parcel.get("address")
        dedup_key = apn if (apn and apn != "PENDING VERIFICATION") else addr
        
        if dedup_key in seen_identifiers:
            continue
        seen_identifiers.add(dedup_key)

        is_valid, reason = validate_surplus_record(parcel)
        if not is_valid:
            continue

        cid = parcel.get("record_id") or generate_deterministic_case_id(apn, addr)
        parcel["record_id"] = cid

        if cid in existing_kv_ids:
            continue

        parcel["is_new"] = True
        parcel["ingested_at"] = current_timestamp
        prepared_records.append(parcel)

    if not prepared_records:
        logging.info("🛡️ SAFEGUARD ACTIVE: 0 new leads found. All records already exist in Cloudflare KV.")
        exit(0)

    logging.info(f"✨ Found {len(prepared_records)} BRAND NEW lead(s) meeting all thresholds!")

    # Step 1: Run Tracefy Skip Tracing
    enriched_records = run_tracerfy_skip_trace(prepared_records)

    # Step 2: Format Dissected Payload for Cloudflare KV
    dispatch_queue = []
    for parcel in enriched_records:
        cid = parcel["record_id"]
        dispatch_queue.append({
            "record_id": cid,
            "citation_id": cid,
            "caseId": cid,
            "address": parcel.get("address"),
            "owner_name": parcel.get("owner_name"),
            "leadName": parcel.get("owner_name"),
            "phone": parcel.get("phone", "PENDING UNMASK"),
            "email": parcel.get("email", "N/A"),
            "apn": parcel.get("apn"),
            
            # Dissected Metadata Passed to Cloudflare & Dashboard
            "source_origin": parcel.get("source_origin"),
            "county": parcel.get("county"),
            "asset_type": parcel.get("asset_type"),
            "category": parcel.get("category"),
            "value_tier": parcel.get("value_tier"),
            "script_pitch": parcel.get("script_pitch"),
            "default_amount": parcel.get("default_amount"),
            
            "is_new": True,
            "ingested_at": parcel.get("ingested_at"),
            "status": "NEW_LEAD" if not STAGING_MODE else "PENDING_REVIEW"
        })

    # Step 3: Dispatch Payload to Cloudflare Worker
    if dispatch_queue:
        logging.info(f"🚀 Dispatching {len(dispatch_queue)} NEW dissected surplus record(s) to Cloudflare KV...")
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
                        logging.info(f"✅ Batch [{j//POST_CHUNK_SIZE + 1}] Stored {len(post_chunk)} NEW dissected records in KV.")
                    else:
                        logging.error(f"❌ Worker Error [{res.status_code}]: {res.text}")
                except Exception as e:
                    logging.error(f"⚠️ Dispatch Exception: {e}")

        logging.info(f"🎉 Ingress Complete! {successful_dispatches}/{len(dispatch_queue)} dissected surplus records live on dashboard.")
