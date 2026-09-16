#!/usr/bin/env python3
"""
============================================================================
EmergencyAudit.com | AUTOMATED PAY-PER-CALL PIPELINE & SCRAPER
============================================================================
Architecture : GitHub Actions -> Socrata Municipal -> LA Assessor -> Tracerfy -> EmergencyAudit/case
============================================================================
"""

import os
import re
import json
import time
import requests
from urllib.parse import quote

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & REMOTE KILL-SWITCH
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL", "https://emergencyaudit.com")
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY", "SecretKey_2026_Dispatch!")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY", "")
ENABLE_TRACERFY = os.getenv("ENABLE_TRACERFY", "false").lower() == "true"

# PIPELINE KILL SWITCH: Set PAUSE_PIPELINE="true" in GitHub Secrets / Env to pause execution instantly
PAUSE_PIPELINE = os.getenv("PAUSE_PIPELINE", "false").lower() == "true"

NETWORK_1800_NUMBER = os.getenv("NETWORK_1800_NUMBER", "18005550199")

# Active SoCal Municipal Endpoints
SOCRATA_FEEDS = [
    {
        "name": "LA Building & Safety - Code Enforcement",
        "url": "https://data.lacity.org/resource/u82d-eh7z.json?$limit=150",
        "default_cat": "COMMERCIAL_REPAIR"
    },
    {
        "name": "LA Building & Safety - Vacant Abatement",
        "url": "https://data.lacity.org/resource/q3ak-s5hy.json?$limit=150",
        "default_cat": "LOT_CLEANUP"
    },
    {
        "name": "LA City Active Code Citations & Orders",
        "url": "https://data.lacity.org/resource/2n62-383m.json?$limit=150",
        "default_cat": "TRADE_EMERGENCY"
    }
]

ENTITY_PATTERNS = r"\b(LLC|INC|CORP|CORPORATION|HOLDINGS|PROPERTIES|TRUST|LP|PARTNERSHIP|REALTY)\b"

# =====================================================================
# 2. DATA EXTRACTION & FIXES
# =====================================================================
def extract_address(item):
    """Extracts clean street address from Socrata record."""
    for field in ["address", "primary_address", "prop_address", "site_address", "location_address", "street_address"]:
        val = item.get(field)
        if val and isinstance(val, str) and len(val.strip()) >= 5:
            return val.strip().upper()

    house = item.get("house_number") or item.get("street_number") or item.get("house_no") or ""
    street = item.get("street_name") or item.get("street") or item.get("st_name") or ""
    st_type = item.get("street_type") or item.get("st_type") or ""
    combined = f"{house} {street} {st_type}".strip()
    return combined.upper() if len(combined) >= 5 else None

def extract_zip(item, address_text=""):
    """Extracts valid 5-digit California ZIP code without random hash fallbacks."""
    for field in ["zip_code", "zipcode", "zip", "postal_code", "site_zip", "prop_zip", "zip_code_1"]:
        val = str(item.get(field, "")).strip()
        if re.match(r"^9\d{4}$", val):
            return val

    match = re.search(r"\b(9\d{4})\b", str(address_text))
    if match:
        return match.group(1)

    # Clean default for central LA instead of randomized fake zip assignment
    return "90012"

def extract_violation_desc(item):
    """Search text fields to pull actual violation details."""
    for key in ["primary_violation", "violation_description", "order_type", "sub_type", "description", "case_type", "comments"]:
        val = item.get(key)
        if val and isinstance(val, str) and len(val.strip()) > 5:
            return val.strip()
    return "Municipal Hazard & Compliance Order"

def lookup_tax_assessor(address):
    """Fixes LA Assessor query logic for multi-word street names."""
    try:
        clean_addr = re.sub(r"[^\w\s]", "", address).strip()
        parts = clean_addr.split()
        if len(parts) >= 2:
            street_num = parts[0]
            # Skip directionals (N, S, E, W) to match street name accurately
            street_name = parts[2] if parts[1] in ["N", "S", "E", "W"] and len(parts) > 2 else parts[1]
            
            url = "https://data.lacounty.gov/resource/28ee-2bgz.json"
            params = {"$where": f"situshouse_no='{street_num}' AND situsstreetname LIKE '%{street_name}%'", "$limit": "1"}
            res = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            
            if res.status_code == 200 and "json" in res.headers.get("Content-Type", ""):
                records = res.json()
                if isinstance(records, list) and len(records) > 0:
                    rec = records[0]
                    return {
                        "owner_name": str(rec.get("owner1") or rec.get("ain_owner1") or "PROPERTY OWNER / MANAGER").upper(),
                        "mail_address": str(rec.get("mail_address") or address).upper(),
                        "apn": str(rec.get("ain") or rec.get("apn") or "N/A"),
                        "zip": rec.get("situszip") or rec.get("zip") or None
                    }
    except Exception as e:
        print(f"[Assessor Query Warning] {address}: {e}")

    return {"owner_name": "PROPERTY OWNER / MANAGER", "mail_address": address, "apn": "N/A", "zip": None}

def unmask_entity_owner(owner_name, mail_address):
    if "C/O" in owner_name or "C/O" in mail_address:
        parts = owner_name.split("C/O") if "C/O" in owner_name else mail_address.split("C/O")
        possible_human = parts[-1].strip().split(",")[0]
        if len(possible_human) > 3 and not re.search(ENTITY_PATTERNS, possible_human):
            return possible_human
    return owner_name

def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90012"):
    """Tracerfy Skip-tracing API call."""
    if not ENABLE_TRACERFY or not TRACERFY_API_KEY:
        return {"phone": None, "status": "HOLDING_MODE"}

    try:
        url = "https://api.tracerfy.com/v1/skip-trace"
        payload = {"address": address, "name": human_name, "api_key": TRACERFY_API_KEY}
        res = requests.post(url, json=payload, timeout=8)
        if res.status_code == 200:
            data = res.json()
            phones = data.get("mobile_phones") or data.get("phones") or []
            return {"phone": phones[0] if phones else None, "status": "VERIFIED"}
    except Exception as e:
        print(f"[Tracerfy Warning] Exception: {e}")

    return {"phone": None, "status": "FAILED"}

# =====================================================================
# 3. PAY-PER-CALL ENGINE DISPATCH & REMOTE CHECK
# =====================================================================
def is_remote_paused():
    """Checks Cloudflare Worker endpoint to see if Pause Toggle is active on back office."""
    if PAUSE_PIPELINE:
        return True
    try:
        res = requests.get(f"{WORKER_URL}/api/status", timeout=4)
        if res.status_code == 200 and res.json().get("paused") is True:
            return True
    except Exception:
        pass
    return False

def run_pipeline():
    # 1. Kill switch evaluation
    if is_remote_paused():
        print("=====================================================")
        print(" PIPELINE STATUS: PAUSED (Kill-Switch Active)")
        print(" Execution stopped. No records processed or SMS sent.")
        print("=====================================================")
        return

    print("[Pipeline] Running municipal extraction & routing...")
    processed_count = 0

    for feed in SOCRATA_FEEDS:
        try:
            res = requests.get(feed["url"], timeout=10)
            if res.status_code != 200:
                continue
                
            for item in res.json():
                address = extract_address(item)
                if not address:
                    continue

                assessor_data = lookup_tax_assessor(address)
                zip_code = assessor_data["zip"] or extract_zip(item, address)
                violation = extract_violation_desc(item)
                case_no = item.get("case_number") or item.get("apno") or f"AUD-{int(time.time() * 1000) % 100000}"
                human_owner = unmask_entity_owner(assessor_data["owner_name"], assessor_data["mail_address"])
                trace_data = skip_trace(human_owner, address, "Los Angeles", "CA", zip_code)

                # 2. Build Pay-Per-Call Dynamic URL
                encoded_address = quote(address)
                case_url = f"{WORKER_URL}/case?id={case_no}&address={encoded_address}&phone={NETWORK_1800_NUMBER}"

                # 3. Payload for EmergencyAudit Pay-Per-Call System
                payload = {
                    "citation_id": case_no,
                    "address": f"{address}, Los Angeles, CA {zip_code}",
                    "owner_name": human_owner,
                    "phone": trace_data["phone"],
                    "violation": violation[:180],
                    "case_url": case_url,
                    "apn": assessor_data["apn"]
                }

                # Push to Cloudflare Edge / Engine
                try:
                    res_push = requests.post(
                        f"{WORKER_URL}/api/dispatch", 
                        json=payload, 
                        headers={"X-Emergency-Key": MASTER_ADMIN_KEY}, 
                        timeout=5
                    )
                    if res_push.status_code == 200:
                        processed_count += 1
                except Exception:
                    pass

                time.sleep(0.05)
        except Exception as e:
            print(f"[Feed Exception] {feed['name']}: {e}")

    print(f"[Batch Complete] Successfully processed {processed_count} municipal leads.")

if __name__ == "__main__":
    run_pipeline()
