import os
import re
import time
import requests

os.environ["PYTHONUNBUFFERED"] = "1"

# Environment Credentials & Endpoints
API_URL = os.getenv("API_URL", "https://emergencyaudit.com/api/ping")
SECURITY_KEY = os.getenv("EMERGENCY_KEY") or os.getenv("MASTER_ADMIN_KEY") or "SecretKey_2026_Dispatch!"
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY")

HEADERS = {
    "Content-Type": "application/json",
    "X-Emergency-Key": SECURITY_KEY
}

# Real Socrata Municipal Open Data Endpoints (LA City)
SOCRATA_FEEDS = [
    {
        "name": "LA Building & Safety - Open Code Enforcement Cases",
        "url": "https://data.lacity.org/resource/u82d-eh7z.json?$limit=25&$order=:id DESC",
        "category": "COMMERCIAL_VIOLATION",
        "price": 299.00
    },
    {
        "name": "LA Building & Safety - Vacant Building Abatement",
        "url": "https://data.lacity.org/resource/q3ak-s5hy.json?$limit=25&$order=:id DESC",
        "category": "TRADE_EMERGENCY",
        "price": 349.00
    }
]

def skip_trace_tracerfy(name_or_entity, address, city, state="CA", zip_code=""):
    """
    Queries Tracerfy's live Skip Trace API for direct mobile phone and email enrichment.
    """
    if not TRACERFY_API_KEY:
        print("  [!] Tracerfy API Key missing from environment. Skipping paid enrichment.", flush=True)
        return None, None

    tracerfy_endpoint = "https://api.tracerfy.com/v1/skip-trace"
    payload = {
        "api_key": TRACERFY_API_KEY,
        "full_name": name_or_entity,
        "address": address,
        "city": city,
        "state": state,
        "zip": zip_code
    }

    try:
        res = requests.post(tracerfy_endpoint, json=payload, timeout=8)
        if res.status_code == 200:
            data = res.json()
            phone = data.get("phone") or data.get("mobile_phone") or (data.get("phones", [None])[0])
            email = data.get("email") or (data.get("emails", [None])[0])
            if phone or email:
                print(f"  [+] Tracerfy Match Found -> Phone: {phone} | Email: {email}", flush=True)
                return phone, email
    except Exception as err:
        print(f"  [!] Tracerfy API Query Error: {err}", flush=True)

    return None, None

def run_apex_ingress_pipeline():
    print("[*] Launching Operation Apex Ingress (Real Municipal Ingestion Pipeline)...", flush=True)
    total_posted = 0

    for feed in SOCRATA_FEEDS:
        print(f"\n[*] Connecting to Municipal Stream: {feed['name']}...", flush=True)
        try:
            response = requests.get(feed["url"], timeout=15)
            if response.status_code != 200:
                print(f"  [!] Feed error HTTP {response.status_code}. Skipping stream.", flush=True)
                continue

            records = response.json()
            print(f"  [*] Ingested {len(records)} raw JSON records from municipal API.", flush=True)

            matched_in_feed = 0
            for item in records:
                # Dynamically parse address fields across different municipal schemas
                house_no = item.get("house_number") or item.get("street_number") or ""
                street_name = item.get("street_name") or item.get("street") or ""
                street_type = item.get("street_type") or ""
                raw_address = item.get("address") or item.get("primary_address") or f"{house_no} {street_name} {street_type}".strip()
                
                zip_code = item.get("zip_code") or item.get("postal_code") or "90001"
                case_no = item.get("case_number") or item.get("apno") or item.get("case_no") or f"CASE-{int(time.time()) % 100000}"
                violation_desc = item.get("description") or item.get("violation_description") or item.get("case_type") or "Active Municipal Code Enforcement Citation"

                if not raw_address or len(raw_address.strip()) < 5:
                    continue

                full_address = f"{raw_address.strip().upper()}, Los Angeles, CA {zip_code}"

                # Extract entity or fallback to Property Owner On File
                owner_entity = item.get("owner_name") or item.get("applicant_name") or "Commercial Property Owner / Asset Manager"
                
                # Execute Live Skip Tracing via Tracerfy
                phone, email = skip_trace_tracerfy(owner_entity, raw_address, "Los Angeles", "CA", zip_code)

                final_phone = phone or "Unmasked Upon Purchase"
                final_email = email or "Unmasked Upon Purchase"
                drop_id = f"job_REAL_COMM_{case_no.replace(' ', '_')}_{int(time.time())}"

                payload = {
                    "sku": f"EA-COMM-{zip_code}-{total_posted+1000}",
                    "dropId": drop_id,
                    "sourceChannel": f"{feed['name']} (Case #{case_no})",
                    "category": feed["category"],
                    "title_en": f"COMMERCIAL CITATION - {feed['category'].replace('_', ' ')} (${int(feed['price'])})",
                    "zip": zip_code,
                    "city": "Los Angeles, CA",
                    "desc_en": f"Official Municipal Citation: {violation_desc[:160]}",
                    "retailPrice": feed["price"],
                    "customerName": owner_entity,
                    "customerPhone": final_phone,
                    "customerAddress": full_address,
                    "dossier": {
                        "assetManager": owner_entity,
                        "amPhone": final_phone,
                        "listingAgent": "Direct Municipal Record",
                        "agentPhone": "N/A - Direct Owner",
                        "propertyManager": "Assigned Receiver / Property PM",
                        "pmPhone": final_phone,
                        "attorney": "Compliance Counsel On File",
                        "attorneyPhone": final_phone,
                        "attorneyEmail": final_email
                    }
                }

                # Post real lead payload to Cloudflare KV Dispatch
                try:
                    res = requests.post(API_URL, json=payload, headers=HEADERS, timeout=8)
                    if res.status_code in [200, 201]:
                        print(f"  [+] LIVE INGEST SUCCESS -> {drop_id} | Location: {full_address}", flush=True)
                        total_posted += 1
                        matched_in_feed += 1
                        if matched_in_feed >= 10:  # Batch cap per feed
                            break
                    else:
                        print(f"  [!] Dispatch Error HTTP {res.status_code}: {res.text}", flush=True)
                except Exception as post_err:
                    print(f"  [!] Dispatch POST Exception: {post_err}", flush=True)

            print(f"[+] Feed Complete for {feed['name']}: Ingested {matched_in_feed} live records.", flush=True)

        except Exception as feed_err:
            print(f"[!] Critical Error on Feed {feed['name']}: {feed_err}", flush=True)

    print(f"\n[*] Execution Complete. Total Real Leads Dispatched to Storefront: {total_posted}", flush=True)

if __name__ == "__main__":
    run_apex_ingress_pipeline()
