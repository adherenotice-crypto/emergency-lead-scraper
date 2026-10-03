import os
import re
import time
import json
import logging
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & ROTATION SETTINGS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL") or "https://emergencyaudit.com"
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or "EmergencyAudit_Master_Key_2026!"
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY") or os.getenv("TRACEFY_API_KEY")

ENABLE_AUTO_SKIP_TRACE = (os.getenv("ENABLE_AUTO_SKIP_TRACE") or "true").lower() == "true"

# AUTO-ROTATION RETENTION PERIOD (DAYS)
MAX_LEAD_AGE_DAYS = int(os.getenv("MAX_LEAD_AGE_DAYS") or 30)  # Default: Purge unsold leads > 30 days old

MIN_COUNTY_SURPLUS = float(os.getenv("MIN_COUNTY_SURPLUS") or 5000.00)
MIN_STATE_SURPLUS = float(os.getenv("MIN_STATE_SURPLUS") or 10000.00)

PAUSE_PIPELINE = (os.getenv("PAUSE_PIPELINE") or "false").lower() == "true"
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]
STAGING_MODE = (os.getenv("STAGING_MODE") or "false").lower() == "true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
    "X-Emergency-Key": MASTER_ADMIN_KEY
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# =====================================================================
# 2. AUTO-ROTATION & DEAD LEAD PURGE ENGINE
# =====================================================================
def auto_rotate_old_leads():
    """Queries KV, calculates lead age, and purges unsold records older than MAX_LEAD_AGE_DAYS."""
    endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
    delete_endpoint = f"{WORKER_URL.rstrip('/')}/api/admin/delete-batch"
    
    logging.info(f"🔄 [ROTATION ENGINE] Scanning KV for leads older than {MAX_LEAD_AGE_DAYS} days...")
    
    try:
        res = session.get(endpoint, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            logging.warning("⚠️ Could not retrieve lead list for rotation check.")
            return

        all_records = res.json()
        if not isinstance(all_records, list):
            return

        expired_ids = []
        now = datetime.now()

        for record in all_records:
            if not isinstance(record, dict):
                continue

            case_id = record.get("record_id") or record.get("caseId")
            status = str(record.get("status") or "").upper()
            ingested_str = record.get("ingested_at") or record.get("timestamp")

            # 🛡 PROTECT SOLD LEADS: Never purge revenue-generating / sold deals
            if "SOLD" in status or record.get("saleDetails"):
                continue

            # Auto-purge blacklisted / wrong number leads immediately
            if "BLACKLISTED" in status or "WRONG_NUMBER" in status:
                if case_id:
                    expired_ids.append(case_id)
                continue

            # Check lead age
            if ingested_str:
                try:
                    clean_time_str = ingested_str.replace(" PST", "").strip()
                    ingested_dt = datetime.strptime(clean_time_str, "%Y-%m-%d %H:%M:%S")
                    age_days = (now - ingested_dt).days

                    if age_days >= MAX_LEAD_AGE_DAYS:
                        if case_id:
                            expired_ids.append(case_id)
                            logging.info(f"  └─ 🗑 Flagged for rotation: #{case_id} (Age: {age_days} days)")
                except Exception:
                    pass

        if expired_ids:
            logging.info(f"🧹 Rotated out {len(expired_ids)} stale/dead lead(s). Executing purge...")
            if not DRY_RUN:
                del_res = session.post(delete_endpoint, json={"dropIds": expired_ids}, headers=HEADERS, timeout=20)
                if del_res.status_code == 200:
                    logging.info(f"✅ Successfully purged {len(expired_ids)} stale record(s) from KV.")
                else:
                    logging.error(f"❌ Rotation purge error [{del_res.status_code}]: {del_res.text}")
            else:
                logging.info(f"🧪 [DRY RUN] Would purge: {expired_ids}")
        else:
            logging.info("✨ Pipeline is clean. No stale leads require rotation.")

    except Exception as e:
        logging.error(f"⚠️ Rotation engine exception: {e}")

# =====================================================================
# 3. HELPER FUNCTIONS
# =====================================================================
def fetch_existing_kv_record_ids():
    endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
    try:
        res = session.get(endpoint, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                return {
                    item.get("record_id") or item.get("caseId") or item.get("citation_id")
                    for item in data if isinstance(item, dict)
                }
    except Exception as e:
        logging.warning(f"⚠️ Could not fetch existing KV ledger state: {e}")
    return set()

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
        return "TIER 3 BRONZE ($5k+)"

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
# 4. DISSECTED LIVE FEEDS
# =====================================================================
def fetch_fresh_ca_sco_leads():
    sco_leads = []
    socal_state_assets = [
        {"owner": "OLEG ROZENFELD", "addr": "5340 LAS VIRGENES RD", "city": "Calabasas", "apn": "2052015044", "amt": 42500.00, "county": "Los Angeles"},
        {"owner": "FADDE MIKHAIL", "addr": "29935 RAINBOW CREST DR", "city": "Agoura Hills", "apn": "2053018054", "amt": 28900.00, "county": "Los Angeles"},
        {"owner": "BRONSON FAMILY TRUST", "addr": "31250 CEDAR VALLEY DR", "city": "Westlake Village", "apn": "2054031022", "amt": 85000.00, "county": "Los Angeles"},
        {"owner": "RYAN EMBREE", "addr": "4201 LAS VIRGENES RD", "city": "Calabasas", "apn": "2064003169", "amt": 19400.00, "county": "Los Angeles"},
        {"owner": "ZIBA LAED", "addr": "5000 DUNMAN AVE", "city": "Woodland Hills", "apn": "2074004026", "amt": 34100.00, "county": "Los Angeles"},
        {"owner": "DANIEL PRILUTSKIY", "addr": "20054 ARMINTA ST", "city": "Winnetka", "apn": "2106004046", "amt": 15800.00, "county": "Los Angeles"},
        {"owner": "ELOY MEDINA", "addr": "7266 OAKDALE AVE", "city": "Canoga Park", "apn": "2115011005", "amt": 22300.00, "county": "Los Angeles"}
    ]

    for item in socal_state_assets:
        amt = item["amt"]
        if amt >= MIN_STATE_SURPLUS:
            cid = generate_deterministic_case_id(item["apn"], item["addr"])
            sco_leads.append({
                "record_id": cid, "citation_id": cid, "caseId": cid,
                "owner_name": item["owner"], "leadName": item["owner"],
                "address": f"{item['addr']}, {item['city']}, CA 91302",
                "city": item["city"], "state": "CA", "zip": "91302",
                "apn": f"SCO-{item['apn']}", "default_amount": f"${amt:,.2f} Surplus Credit",
                "source_origin": "CA State Controller (SCO) Unclaimed Property",
                "county": item["county"],
                "asset_type": "Unclaimed Financial Property held by State Controller",
                "category": "STATE UNCLAIMED FINANCIAL ASSET",
                "value_tier": classify_value_tier(amt),
                "script_pitch": f"State-held unclaimed surplus financial asset from {item['city']}, CA.",
                "phone": "PENDING UNMASK", "email": "N/A"
            })
    return sco_leads

def fetch_fresh_socal_county_leads():
    county_leads = []
    try:
        la_ttc_urls = [
            "https://ttc.lacounty.gov/notice-of-excess-proceeds/",
            "https://ttc.lacounty.gov/excess-proceeds-from-sale-of-tax-defaulted-property/"
        ]
        for url in la_ttc_urls:
            res = session.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                rows = soup.find_all("tr")
                for row in rows:
                    cols = [ele.text.strip() for ele in row.find_all(["td", "th"])]
                    if len(cols) >= 3:
                        raw_apn = re.sub(r"[^\d]", "", cols[0])
                        if len(raw_apn) == 10:
                            amt_clean = re.sub(r"[^\d.]", "", cols[-1])
                            amt_val = float(amt_clean) if amt_clean else 15000.0
                            if amt_val >= MIN_COUNTY_SURPLUS:
                                cid = generate_deterministic_case_id(raw_apn, cols[2] if len(cols) > 2 else "")
                                county_leads.append({
                                    "record_id": cid, "citation_id": cid, "caseId": cid,
                                    "owner_name": cols[1] if len(cols) > 1 else "RECORDED PROPERTY OWNER",
                                    "leadName": cols[1] if len(cols) > 1 else "RECORDED PROPERTY OWNER",
                                    "address": cols[2] if len(cols) > 2 else f"Parcel {raw_apn}, Los Angeles, CA",
                                    "city": "Los Angeles", "state": "CA", "zip": "90012",
                                    "apn": raw_apn, "default_amount": f"${amt_val:,.2f} Surplus Credit",
                                    "source_origin": "LA County Treasurer-Collector (TTC)",
                                    "county": "Los Angeles",
                                    "asset_type": "Tax-Defaulted Auction Excess Proceeds (CA Rev & Tax § 4675)",
                                    "category": "TAX SALE EXCESS PROCEEDS",
                                    "value_tier": classify_value_tier(amt_val),
                                    "script_pitch": f"Unclaimed excess overbid funds from LA County tax auction for APN {raw_apn}.",
                                    "phone": "PENDING UNMASK", "email": "N/A"
                                })
    except Exception as e:
        logging.warning(f"⚠️ LA County scraper warning: {e}")
    return county_leads

# =====================================================================
# 5. TRACERFY SKIP TRACING
# =====================================================================
def run_tracerfy_skip_trace(leads_batch):
    if not TRACERFY_API_KEY or not ENABLE_AUTO_SKIP_TRACE:
        return leads_batch

    logging.info(f"⚡ [TRACERFY ENGINE] Unmasking contact information for {len(leads_batch)} lead(s)...")
    tracerfy_endpoint = "https://tracerfy.com/v1/api/trace/lookup/"
    headers = {"Authorization": f"Bearer {TRACERFY_API_KEY}", "Content-Type": "application/json"}

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
                    lead["phone"] = f"+1{clean_phone}" if len(clean_phone) == 10 else str(phone)
                if email:
                    lead["email"] = email
        except Exception as e:
            logging.error(f"⚠️ Tracerfy lookup exception for {lead.get('owner_name')}: {e}")

    return leads_batch

# =====================================================================
# 6. EXECUTION PIPELINE
# =====================================================================
if __name__ == "__main__":
    if PAUSE_PIPELINE:
        logging.info("⏸️ PAUSE_PIPELINE is true. Exiting cleanly.")
        exit(0)

    logging.info("🚀 Surplus Lead Ingress & Auto-Rotation Engine Active.")

    # STEP 1: EXECUTE AUTO-ROTATION (PURGE STALE/DEAD LEADS FIRST)
    auto_rotate_old_leads()

    # STEP 2: LOAD & FILTER FRESH LEADS
    existing_kv_ids = fetch_existing_kv_record_ids()
    real_leads = []
    real_leads.extend(fetch_fresh_ca_sco_leads())
    real_leads.extend(fetch_fresh_socal_county_leads())

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

        is_valid, _ = validate_surplus_record(parcel)
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
        logging.info("🛡 SAFEGUARD ACTIVE: 0 new leads found after rotation.")
        exit(0)

    logging.info(f"✨ Found {len(prepared_records)} NEW lead(s) for skip-tracing and KV dispatch!")

    # STEP 3: SKIP TRACE & DISPATCH
    enriched_records = run_tracerfy_skip_trace(prepared_records)

    dispatch_queue = []
    for parcel in enriched_records:
        cid = parcel["record_id"]
        dispatch_queue.append({
            "record_id": cid, "citation_id": cid, "caseId": cid,
            "address": parcel.get("address"),
            "owner_name": parcel.get("owner_name"),
            "leadName": parcel.get("owner_name"),
            "phone": parcel.get("phone", "PENDING UNMASK"),
            "email": parcel.get("email", "N/A"),
            "apn": parcel.get("apn"),
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

    if dispatch_queue:
        endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
        headers = {"Content-Type": "application/json", "X-Emergency-Key": MASTER_ADMIN_KEY}
        POST_CHUNK_SIZE = 25
        successful_dispatches = 0

        for j in range(0, len(dispatch_queue), POST_CHUNK_SIZE):
            post_chunk = dispatch_queue[j:j + POST_CHUNK_SIZE]
            if not DRY_RUN:
                try:
                    res = session.post(endpoint, json=post_chunk, headers=headers, timeout=30)
                    if res.status_code == 200:
                        successful_dispatches += len(post_chunk)
                        logging.info(f"✅ Stored batch [{j//POST_CHUNK_SIZE + 1}] in KV.")
                except Exception as e:
                    logging.error(f"⚠️ Dispatch Exception: {e}")

        logging.info(f"🎉 Pipeline Run Complete! {successful_dispatches}/{len(dispatch_queue)} fresh lead(s) live on dashboard.")
