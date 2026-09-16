#!/usr/bin/env python3
"""
============================================================================
EmergencyAudit.com! // AUTOMATED B2B SCRUBBING & DISPATCH PIPELINE
============================================================================
Architecture : GitHub Actions -> Socrata Municipal -> LA Assessor -> SOS Unmask -> Tracerfy -> Cloudflare
Target Domain: emergencyaudit.com
============================================================================
"""

import os
import re
import json
import time
import requests

WORKER_URL = os.getenv("WORKER_URL", "https://emergencyaudit.com")
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY", "SecretKey_2026_Dispatch!")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY", "")
ENABLE_TRACERFY = os.getenv("ENABLE_TRACERFY", "false").lower() == "true"

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

VALUATION_MAP = {
    "COMMERCIAL_REPAIR": "$5,000.00 - $25,000.00+",
    "LOT_CLEANUP": "$1,500.00 - $5,000.00",
    "TRADE_EMERGENCY": "$1,000.00 - $4,000.00",
    "HANDYMAN": "$400.00 - $1,800.00",
    "HAULING": "$500.00 - $2,200.00",
    "CLEANING": "$300.00 - $1,200.00"
}

def extract_address(item):
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
    for field in ["zip_code", "zipcode", "zip", "postal_code", "site_zip", "prop_zip", "zip_code_1"]:
        val = str(item.get(field, "")).strip()
        if re.match(r"^9\d{4}$", val):
            return val

    match = re.search(r"\b(9\d{4})\b", str(address_text))
    if match:
        return match.group(1)

    fallback_zips = ["90210", "90001", "90028", "91401", "91101", "90802", "90501", "91764"]
    return fallback_zips[abs(hash(address_text)) % len(fallback_zips)]

def extract_violation_desc(item):
    """Deep search across Socrata text keys to pull real violation detail."""
    for key in ["primary_violation", "violation_description", "order_type", "sub_type", "description", "case_type", "comments"]:
        val = item.get(key)
        if val and isinstance(val, str) and len(val.strip()) > 5:
            return val.strip()
    return "Commercial Code Compliance & Maintenance Notice"

def categorize_job(text, default_cat):
    t = str(text).lower()
    if re.search(r"\b(lot|vacant|brush|fire hazard|abatement|clearing|dumping|yard|weeds|debris)\b", t):
        return "LOT_CLEANUP"
    if re.search(r"\b(lock|locksmith|key|re-key|rekey|secure|board-up|door|handyman|gate|window|latch)\b", t):
        return "HANDYMAN"
    if re.search(r"\b(trash-out|junk|haul|hauling|clearout|dumpster|trash|accumulated)\b", t):
        return "HAULING"
    if re.search(r"\b(clean|cleaning|sanitized|deep clean|carpet|mold|sanitation|unsanitary)\b", t):
        return "CLEANING"
    if re.search(r"\b(remodel|drywall|paint|flooring|restoration|renovation|plumbing|electrical)\b", t):
        return "TRADE_EMERGENCY"
    return "COMMERCIAL_REPAIR"

def lookup_tax_assessor(address):
    try:
        parts = re.sub(r"[^\w\s]", "", address).split()
        if len(parts) >= 2:
            street_num, street_name = parts[0], parts[1]
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
        print(f"⚠️ Assessor query exception for {address}: {e}")

    return {"owner_name": "PROPERTY OWNER / MANAGER", "mail_address": address, "apn": "N/A", "zip": None}

def unmask_entity_owner(owner_name, mail_address):
    if "C/O" in owner_name or "C/O" in mail_address:
        parts = owner_name.split("C/O") if "C/O" in owner_name else mail_address.split("C/O")
        possible_human = parts[-1].strip().split(",")[0]
        if len(possible_human) > 3 and not re.search(ENTITY_PATTERNS, possible_human):
            return possible_human
    return owner_name

def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90001"):
    if not ENABLE_TRACERFY or not TRACERFY_API_KEY:
        return {"phone": "Unmasked Upon Purchase", "email": "Unmasked Upon Purchase", "status": "HOLDING_MODE"}

    try:
        url = "https://tracerfy.com/v1/api/trace/lookup/"
        headers = {"Authorization": f"Bearer {TRACERFY_API_KEY}", "Content-Type": "application/json"}
        payload = {"find_owner": human_name == "PROPERTY OWNER / MANAGER", "address": address, "city": city, "state": state, "zip": zip_code}
        
        if human_name != "PROPERTY OWNER / MANAGER":
            parts = human_name.split()
            payload["first_name"] = parts[0]
            payload["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else parts[0]

        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            phones = data.get("phone_numbers") or data.get("phones") or []
            emails = data.get("emails") or []
            phone_val = phones[0].get("number") if phones and isinstance(phones[0], dict) else (phones[0] if phones else None)
            return {"phone": phone_val or "Unmasked Upon Purchase", "email": emails[0] if emails else "Unmasked Upon Purchase", "status": "VERIFIED"}
    except Exception as e:
        print(f"⚠️ Tracerfy Exception: {e}")

    return {"phone": "Unmasked Upon Purchase", "email": "Unmasked Upon Purchase", "status": "FAILED"}

def push_to_cloudflare(lead_payload):
    try:
        res = requests.post(f"{WORKER_URL}/api/ping", json=lead_payload, headers={"Content-Type": "application/json", "X-Emergency-Key": MASTER_ADMIN_KEY}, timeout=8)
        return res.status_code == 200
    except Exception:
        return False

def run_pipeline():
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
                category = categorize_job(violation, feed["default_cat"])
                case_no = item.get("case_number") or item.get("apno") or f"CASE-{int(time.time() * 1000) % 100000}"
                human_owner = unmask_entity_owner(assessor_data["owner_name"], assessor_data["mail_address"])
                trace_data = skip_trace(human_owner, address, "Los Angeles", "CA", zip_code)

                payload = {
                    "sku": f"EA-JOB-{zip_code}-{int(time.time() * 1000) % 9000 + 1000}",
                    "dropId": f"job_SCRUBBED_{str(case_no).replace(' ', '_')}_{int(time.time())}",
                    "partnerId": "github_pipeline_v3",
                    "sourceChannel": f"City Record (Case #{case_no})",
                    "category": category,
                    "zip": zip_code,
                    "city": "Los Angeles, CA",
                    "customerName": human_owner,
                    "customerAddress": f"{address}, Los Angeles, CA {zip_code}",
                    "customerPhone": trace_data["phone"],
                    "customerEmail": trace_data["email"],
                    "desc_en": f"City Notice: {violation[:160]}",
                    "estimatedValue": VALUATION_MAP.get(category, "$5,000.00 - $25,000.00+"),
                    "dossier": {
                        "ownerManager": human_owner,
                        "directPhone": trace_data["phone"],
                        "contactEmail": trace_data["email"],
                        "cityNotice": violation[:180],
                        "apnNumber": assessor_data["apn"],
                        "taxMailingAddress": assessor_data["mail_address"]
                    }
                }
                if push_to_cloudflare(payload):
                    processed_count += 1
                time.sleep(0.04)
        except Exception as e:
            print(f"Feed exception: {e}")

if __name__ == "__main__":
    run_pipeline()
