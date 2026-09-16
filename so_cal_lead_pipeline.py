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

# ============================================================================
# CONFIGURATION & ENVIRONMENT VARIABLES
# ============================================================================
WORKER_URL = os.getenv("WORKER_URL", "https://emergencyaudit.com")
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY", "SecretKey_2026_Dispatch!")
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY", "")

# SET TO 'false' TO TEST PIPELINE FLOW WITHOUT SPENDING TRACERFY CREDITS
ENABLE_TRACERFY = os.getenv("ENABLE_TRACERFY", "false").lower() == "true"

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
        "name": "LA Housing - Code Violations & Systemic Inspection",
        "url": "https://data.lacity.org/resource/2n62-383m.json?$limit=150",
        "default_cat": "TRADE_EMERGENCY"
    }
]

ENTITY_PATTERNS = r"\b(LLC|INC|CORP|CORPORATION|HOLDINGS|PROPERTIES|TRUST|LP|PARTNERSHIP|REALTY)\b"

# ============================================================================
# STAGE 1: DYNAMIC ADDRESS & ZIP CODE EXTRACTION
# ============================================================================
def extract_address(item):
    """Parses various Socrata address schemas into a clean normalized string."""
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
    """Scans all Socrata location fields and address text for valid 5-digit SoCal zip codes."""
    for field in ["zip_code", "zipcode", "zip", "postal_code", "site_zip", "prop_zip", "zip_code_1"]:
        val = str(item.get(field, "")).strip()
        if re.match(r"^9\d{4}$", val):
            return val

    # Regex search across full address string
    match = re.search(r"\b(9\d{4})\b", str(address_text))
    if match:
        return match.group(1)

    # Varied regional fallback distribution based on address hash
    fallback_zips = ["90210", "90001", "90028", "91401", "91101", "90802", "90501", "91764"]
    idx = abs(hash(address_text)) % len(fallback_zips)
    return fallback_zips[idx]


# ============================================================================
# STAGE 2: DYNAMIC JOB CATEGORIZER (ROUTER)
# ============================================================================
def categorize_job(text, default_cat):
    """Categorizes violation text into trade buckets matching pricing tiers."""
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
    if re.search(r"\b(commercial|building|structure|unpermitted|violation|code enforcement|framing|fire safety|citation)\b", t):
        return "COMMERCIAL_REPAIR"

    return default_cat


# ============================================================================
# STAGE 3: TAX ASSESSOR LOOKUP (LA COUNTY OPEN DATA)
# ============================================================================
def lookup_tax_assessor(address):
    """Queries LA County Assessor Portal with structured parameters to retrieve owner & APN."""
    try:
        parts = re.sub(r"[^\w\s]", "", address).split()
        if len(parts) >= 2:
            street_num = parts[0]
            street_name = parts[1]

            url = "https://data.lacounty.gov/resource/28ee-2bgz.json"
            params = {
                "$where": f"situshouse_no='{street_num}' AND situsstreetname LIKE '%{street_name}%'",
                "$limit": "1"
            }

            res = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            if res.status_code == 200 and "json" in res.headers.get("Content-Type", ""):
                records = res.json()
                if isinstance(records, list) and len(records) > 0:
                    rec = records[0]
                    owner = rec.get("owner1") or rec.get("ain_owner1") or "PROPERTY OWNER / MANAGER"
                    mail = rec.get("mail_address") or address
                    apn = rec.get("ain") or rec.get("apn") or "N/A"
                    found_zip = rec.get("situszip") or rec.get("zip") or None
                    return {
                        "owner_name": str(owner).upper(),
                        "mail_address": str(mail).upper(),
                        "apn": str(apn),
                        "zip": found_zip
                    }
    except Exception as e:
        print(f"⚠️ Assessor query exception for {address}: {e}")

    return {
        "owner_name": "PROPERTY OWNER / MANAGER",
        "mail_address": address,
        "apn": "N/A",
        "zip": None
    }


# ============================================================================
# STAGE 4: UNMASK CORPORATE SHELLS (LLC / TRUST / INC)
# ============================================================================
def unmask_entity_owner(owner_name, mail_address):
    """Extracts individual decision-makers from LLCs, Trusts, and Corporations."""
    if "C/O" in owner_name or "C/O" in mail_address:
        parts = owner_name.split("C/O") if "C/O" in owner_name else mail_address.split("C/O")
        possible_human = parts[-1].strip().split(",")[0]
        if len(possible_human) > 3 and not re.search(ENTITY_PATTERNS, possible_human):
            return possible_human

    if re.search(ENTITY_PATTERNS, owner_name):
        try:
            clean_company = re.sub(r"[^\w\s]", "", owner_name).strip()
            url = f"https://api.opencorporates.com/v0.2/companies/search?q={clean_company}&jurisdiction_code=us_ca"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                companies = data.get("results", {}).get("companies", [])
                if companies:
                    officers = companies[0].get("company", {}).get("officers", [])
                    for officer in officers:
                        name = officer.get("officer", {}).get("name")
                        if name and not re.search(ENTITY_PATTERNS, name.upper()):
                            return name.upper()
        except Exception as e:
            print(f"⚠️ OpenCorporates unmask exception for {owner_name}: {e}")

    return owner_name


# ============================================================================
# STAGE 5: TRACERFY SKIP TRACING (HOLDING MODE SAFE)
# ============================================================================
def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90001"):
    """Executes Tracerfy skip-trace only if ENABLE_TRACERFY=true and API Key exists."""
    if not ENABLE_TRACERFY or not TRACERFY_API_KEY:
        print(f"⏳ TRACERFY IN HOLDING MODE: Bypassing API charge for {human_name}")
        return {
            "phone": "Unmasked Upon Purchase",
            "email": "Unmasked Upon Purchase",
            "status": "WAITING_FOR_TRACERFY"
        }

    try:
        res = requests.post(
            "https://api.tracerfy.com/v1/skip-trace",
            json={
                "api_key": TRACERFY_API_KEY,
                "full_name": human_name,
                "address": address,
                "city": city,
                "state": state,
                "zip": zip_code
            },
            headers={"Content-Type": "application/json"},
            timeout=8
        )
        if res.status_code == 200:
            data = res.json()
            return {
                "phone": data.get("phone") or data.get("mobile_phone") or "Unmasked Upon Purchase",
                "email": data.get("email") or "Unmasked Upon Purchase",
                "status": "VERIFIED_TRACERFY"
            }
    except Exception as e:
        print(f"⚠️ Tracerfy API Error for {human_name}: {e}")

    return {
        "phone": "Unmasked Upon Purchase",
        "email": "Unmasked Upon Purchase",
        "status": "FAILED_TRACERFY"
    }


# ============================================================================
# STAGE 6: CLOUDFLARE WORKER INGESTION (/api/ping)
# ============================================================================
def push_to_cloudflare(lead_payload):
    """Dispatches scrubbed lead payload to production Cloudflare Worker."""
    url = f"{WORKER_URL}/api/ping"
    headers = {
        "Content-Type": "application/json",
        "X-Emergency-Key": MASTER_ADMIN_KEY
    }

    try:
        res = requests.post(url, json=lead_payload, headers=headers, timeout=8)
        if res.status_code == 200:
            print(f"✅ INJECTED [{lead_payload.get('category')}]: SKU {lead_payload.get('sku')} ({lead_payload.get('zip')})")
            return True
        else:
            print(f"❌ INGESTION REJECTED [{res.status_code}]: {res.text}")
    except Exception as e:
        print(f"❌ WORKER HTTP EXCEPTION: {e}")

    return False


# ============================================================================
# MAIN PIPELINE RUNNER
# ============================================================================
def run_pipeline():
    print("=================================================================")
    print("⚡ EMERGENCYAUDIT.COM! DYNAMIC SCRUBBING & DISPATCH PIPELINE")
    print(f"Target Endpoint: {WORKER_URL}")
    print(f"Tracerfy Integration Enabled: {ENABLE_TRACERFY}")
    print("=================================================================")

    processed_count = 0

    for feed in SOCRATA_FEEDS:
        print(f"\n🔍 Sweeping Feed: {feed['name']}")
        try:
            res = requests.get(feed["url"], timeout=10)
            if res.status_code != 200:
                print(f"⚠️ Feed error {res.status_code}. Skipping.")
                continue

            records = res.json()
            print(f"📥 Pulled {len(records)} raw municipal records. Processing...")

            for item in records:
                address = extract_address(item)
                if not address:
                    continue  # Filter out broken records missing property address

                # Tax Assessor Lookup
                assessor_data = lookup_tax_assessor(address)

                # Zip Code Resolution
                zip_code = assessor_data["zip"] or extract_zip(item, address)

                # Dynamic Violation & Category Classification
                violation = (
                    item.get("primary_violation")
                    or item.get("violation_description")
                    or item.get("description")
                    or item.get("case_type")
                    or "Municipal Compliance Citation"
                )
                category = categorize_job(violation, feed["default_cat"])

                case_no = item.get("case_number") or item.get("apno") or f"CASE-{int(time.time() * 1000) % 100000}"
                raw_owner = item.get("owner_name") or assessor_data["owner_name"]

                human_decision_maker = unmask_entity_owner(raw_owner, assessor_data["mail_address"])
                trace_data = skip_trace(human_decision_maker, address, "Los Angeles", "CA", zip_code)

                payload = {
                    "sku": f"EA-JOB-{zip_code}-{int(time.time() * 1000) % 9000 + 1000}",
                    "dropId": f"job_SCRUBBED_{case_no.replace(' ', '_')}_{int(time.time())}",
                    "partnerId": "github_pipeline_v2",
                    "sourceChannel": f"City Record (Case #{case_no})",
                    "category": category,
                    "zip": zip_code,
                    "city": "Los Angeles, CA",
                    "customerName": human_decision_maker,
                    "customerAddress": f"{address}, Los Angeles, CA {zip_code}",
                    "customerPhone": trace_data["phone"],
                    "customerEmail": trace_data["email"],
                    "desc_en": f"City Notice: {violation[:160]}",
                    "skipTrace": False,
                    "dossier": {
                        "ownerManager": human_decision_maker,
                        "directPhone": trace_data["phone"],
                        "contactEmail": trace_data["email"],
                        "cityNotice": violation[:180],
                        "apnNumber": assessor_data["apn"],
                        "taxMailingAddress": assessor_data["mail_address"]
                    }
                }

                if push_to_cloudflare(payload):
                    processed_count += 1

                time.sleep(0.05)

        except Exception as err:
            print(f"❌ Error processing feed {feed['name']}: {err}")

    print("\n=================================================================")
    print(f"🏁 PIPELINE RUN COMPLETE: {processed_count} Multi-Trade Dispatched Leads.")
    print("=================================================================")


if __name__ == "__main__":
    run_pipeline()
