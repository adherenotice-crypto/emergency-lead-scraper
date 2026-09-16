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
from datetime import datetime

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
        "url": "https://data.lacity.org/resource/u82d-eh7z.json?$limit=100",
        "category": "COMMERCIAL_REPAIR"
    },
    {
        "name": "LA Building & Safety - Vacant Abatement",
        "url": "https://data.lacity.org/resource/q3ak-s5hy.json?$limit=100",
        "category": "LOT_CLEANUP"
    }
]

# Assessor & Entity Regex Patterns
ENTITY_PATTERNS = r"\b(LLC|INC|CORP|CORPORATION|HOLDINGS|PROPERTIES|TRUST|LP|PARTNERSHIP|REALTY)\b"

# ============================================================================
# STAGE 1: EXTRACT PROPERTY ADDRESS FROM MUNICIPAL DATA
# ============================================================================
def extract_address(item):
    """Parses various Socrata address schemas into a clean, normalized string."""
    direct_fields = ["address", "primary_address", "prop_address", "site_address", "location_address"]
    for field in direct_fields:
        if item.get(field) and isinstance(item[field], str) and len(item[field].strip()) >= 5:
            return item[field].strip().upper()

    house = item.get("house_number") or item.get("street_number") or ""
    street = item.get("street_name") or item.get("street") or ""
    st_type = item.get("street_type") or ""
    combined = f"{house} {street} {st_type}".strip()
    return combined.upper() if len(combined) >= 5 else None

# ============================================================================
# STAGE 2: CROSS-REFERENCE TAX ASSESSOR DATA (LA COUNTY OPEN DATA)
# ============================================================================
def lookup_tax_assessor(address):
    """
    Queries LA County Assessor Portal to match property address to tax roll owner.
    Returns: dict(owner_name, mail_address, apn)
    """
    try:
        # Sanitize address for search query
        clean_addr = re.sub(r"[^\w\s]", "", address).split()[0:3]
        search_query = " ".join(clean_addr)
        
        url = f"https://data.lacounty.gov/resource/28ee-2bgz.json?$where=situsaddress%20like%20'%25{search_query}%25'&$limit=1"
        res = requests.get(url, timeout=6)
        if res.status_code == 200:
            records = res.json()
            if records and len(records) > 0:
                rec = records[0]
                return {
                    "owner_name": rec.get("owner1", "PROPERTY OWNER / MANAGER").upper(),
                    "mail_address": rec.get("mail_address", address).upper(),
                    "apn": rec.get("ain", "N/A")
                }
    except Exception as e:
        print(f"⚠️ Assessor lookup exception for {address}: {e}")

    return {
        "owner_name": "PROPERTY OWNER / MANAGER",
        "mail_address": address,
        "apn": "N/A"
    }

# ============================================================================
# STAGE 3: UNMASK CORPORATE SHELLS (LLC / TRUST / INC)
# ============================================================================
def unmask_entity_owner(owner_name, mail_address):
    """
    Extracts individual human decision-makers from LLCs, Trusts, and Corporations.
    """
    # 1. Check for 'C/O' (In Care Of) Trustee/Manager on tax records
    if "C/O" in owner_name or "C/O" in mail_address:
        parts = owner_name.split("C/O") if "C/O" in owner_name else mail_address.split("C/O")
        possible_human = parts[-1].strip().split(",")[0]
        if len(possible_human) > 3 and not re.search(ENTITY_PATTERNS, possible_human):
            return possible_human

    # 2. Check OpenCorporates API for LLC Managing Officer if corporate string detected
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

    # Fall back to cleaned owner name
    return owner_name

# ============================================================================
# STAGE 4: TRACERFY SKIP TRACING (CONDITIONAL / WAIT STATE SAFE)
# ============================================================================
def skip_trace(human_name, address, city="Los Angeles", state="CA", zip_code="90001"):
    """
    Executes Tracerfy skip-trace only if ENABLE_TRACERFY=true and API Key exists.
    Otherwise returns pending verification placeholders.
    """
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
# STAGE 5: CLOUDFLARE WORKER INGESTION (/api/ping)
# ============================================================================
def push_to_cloudflare(lead_payload):
    """Dispatches fully scrubbed lead payload to production Cloudflare Worker."""
    url = f"{WORKER_URL}/api/ping"
    headers = {
        "Content-Type": "application/json",
        "X-Emergency-Key": MASTER_ADMIN_KEY
    }
    
    try:
        res = requests.post(url, json=lead_payload, headers=headers, timeout=8)
        if res.status_code == 200:
            print(f"✅ INJECTED SUCCESS: SKU {lead_payload.get('sku')} -> {WORKER_URL}")
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
    print("⚡ EMERGENCYAUDIT.COM! SCRUBBING & DISPATCH ENGINE STARTING")
    print(f"Target Endpoint: {WORKER_URL}")
    print(f"Tracerfy Integration Enabled: {ENABLE_TRACERFY}")
    print("=================================================================")

    processed_count = 0

    for feed in SOCRATA_FEEDS:
        print(f"\n🔍 Sweeping Municipal Feed: {feed['name']}")
        try:
            res = requests.get(feed["url"], timeout=10)
            if res.status_code != 200:
                print(f"⚠️ Feed error {res.status_code}. Skipping.")
                continue

            records = res.json()
            print(f"📥 Pulled {len(records)} raw municipal records. Scrubbing...")

            for item in records:
                # 1. Extract Address
                address = extract_address(item)
                if not address:
                    continue  # Filter out broken junk records missing addresses

                zip_code = item.get("zip_code") or item.get("zipcode") or item.get("zip") or "90001"
                case_no = item.get("case_number") or item.get("apno") or f"CASE-{int(time.time() * 1000) % 100000}"
                violation = item.get("primary_violation") or item.get("description") or "Municipal Citation Order"

                # 2. Tax Assessor Lookup
                assessor_data = lookup_tax_assessor(address)
                raw_owner = item.get("owner_name") or assessor_data["owner_name"]

                # 3. Unmask Entity / Corporate Shell
                human_decision_maker = unmask_entity_owner(raw_owner, assessor_data["mail_address"])

                # 4. Skip Trace via Tracerfy (Safe holding mode if toggle is off)
                trace_data = skip_trace(human_decision_maker, address, "Los Angeles", "CA", zip_code)

                # 5. Build Scrubbed Production Payload
                payload = {
                    "sku": f"EA-JOB-{zip_code}-{int(time.time()) % 9000 + 1000}",
                    "dropId": f"job_SCRUBBED_{case_no.replace(' ', '_')}",
                    "partnerId": "github_pipeline_v1",
                    "sourceChannel": f"City Record (Case #{case_no})",
                    "category": feed["category"],
                    "zip": zip_code,
                    "city": "Los Angeles, CA",
                    "customerName": human_decision_maker,
                    "customerAddress": f"{address}, Los Angeles, CA {zip_code}",
                    "customerPhone": trace_data["phone"],
                    "customerEmail": trace_data["email"],
                    "desc_en": f"City Notice: {violation[:160]}",
                    "skipTrace": False,  # Already processed
                    "dossier": {
                        "ownerManager": human_decision_maker,
                        "directPhone": trace_data["phone"],
                        "contactEmail": trace_data["email"],
                        "cityNotice": violation[:180],
                        "apnNumber": assessor_data["apn"],
                        "taxMailingAddress": assessor_data["mail_address"]
                    }
                }

                # 6. Inject into Live Cloudflare Engine
                if push_to_cloudflare(payload):
                    processed_count += 1

                time.sleep(0.1) # Rate limit protection

        except Exception as err:
            print(f"❌ Error processing feed {feed['name']}: {err}")

    print("\n=================================================================")
    print(f"🏁 PIPELINE RUN COMPLETE: {processed_count} Scrubbed Leads Dispatched.")
    print("=================================================================")

if __name__ == "__main__":
    run_pipeline()
