import os
import time
import requests

os.environ["PYTHONUNBUFFERED"] = "1"

# Environment Credentials & Endpoints
API_URL = os.getenv("API_URL", "https://emergencyaudit.com/api/ping")
SECURITY_KEY = os.getenv("EMERGENCY_KEY") or os.getenv("MASTER_ADMIN_KEY") or "SecretKey_2026_Dispatch!"

HEADERS = {
    "Content-Type": "application/json",
    "X-Emergency-Key": SECURITY_KEY
}

# Real Socrata Municipal Open Data Endpoints (LA City)
SOCRATA_FEEDS = [
    {
        "name": "LA Building & Safety - Open Code Enforcement Cases",
        "url": "https://data.lacity.org/resource/u82d-eh7z.json?$limit=25&$order=:id DESC",
        "category": "COMMERCIAL_REPAIR",
        "price": 299.00
    },
    {
        "name": "LA Building & Safety - Vacant Building Abatement",
        "url": "https://data.lacity.org/resource/q3ak-s5hy.json?$limit=25&$order=:id DESC",
        "category": "LOT_CLEANUP",
        "price": 349.00
    }
]

def run_apex_ingress_pipeline():
    print("[*] Launching Apex Ingress Pipeline (Cloudflare-Enriched Mode)...", flush=True)
    total_posted = 0

    for feed in SOCRATA_FEEDS:
        print(f"\n[*] Fetching Municipal Stream: {feed['name']}...", flush=True)
        try:
            response = requests.get(feed["url"], timeout=15)
            if response.status_code != 200:
                print(f"  [!] Feed error HTTP {response.status_code}. Skipping.", flush=True)
                continue

            records = response.json()
            print(f"  [*] Ingested {len(records)} raw municipal records.", flush=True)

            matched_in_feed = 0
            for item in records:
                # Dynamic address parsing
                house_no = item.get("house_number") or item.get("street_number") or ""
                street_name = item.get("street_name") or item.get("street") or ""
                street_type = item.get("street_type") or ""
                raw_address = item.get("address") or item.get("primary_address") or f"{house_no} {street_name} {street_type}".strip()
                
                zip_code = item.get("zip_code") or item.get("postal_code") or "90001"
                case_no = item.get("case_number") or item.get("apno") or item.get("case_no") or f"CASE-{int(time.time()) % 100000}"
                violation_desc = item.get("description") or item.get("violation_description") or item.get("case_type") or "Active Municipal Citation"

                if not raw_address or len(raw_address.strip()) < 5:
                    continue

                full_address = f"{raw_address.strip().upper()}, Los Angeles, CA {zip_code}"
                owner_entity = item.get("owner_name") or item.get("applicant_name") or "Commercial Property Owner / Manager"
                drop_id = f"job_REAL_COMM_{case_no.replace(' ', '_')}_{int(time.time())}"

                # Payload sent to Cloudflare (Worker triggers Tracerfy using env.TRACERFY_API_KEY)
                payload = {
                    "sku": f"EA-JOB-{zip_code}-{total_posted+1000}",
                    "dropId": drop_id,
                    "sourceChannel": f"City Record (Case #{case_no})",
                    "category": feed["category"],
                    "title_en": f"{feed['category'].replace('_', ' ')} (${int(feed['price'])})",
                    "zip": zip_code,
                    "city": "Los Angeles, CA",
                    "desc_en": f"City Notice: {violation_desc[:160]}",
                    "retailPrice": feed["price"],
                    "customerName": owner_entity,
                    "customerAddress": full_address,
                    "skipTrace": True,  # Flag telling Worker to run Tracerfy
                    "dossier": {
                        "ownerManager": owner_entity,
                        "propertyAddress": full_address,
                        "cityNotice": violation_desc[:160],
                        "estimatedJobSize": "$5,000.00 - $25,000.00"
                    }
                }

                try:
                    res = requests.post(API_URL, json=payload, headers=HEADERS, timeout=10)
                    if res.status_code in [200, 201]:
                        print(f"  [+] DISPATCH SUCCESS -> {drop_id} | Location: {full_address}", flush=True)
                        total_posted += 1
                        matched_in_feed += 1
                        if matched_in_feed >= 10:
                            break
                    else:
                        print(f"  [!] Dispatch Error HTTP {res.status_code}: {res.text}", flush=True)
                except Exception as post_err:
                    print(f"  [!] Dispatch Exception: {post_err}", flush=True)

            print(f"[+] Feed Complete for {feed['name']}: {matched_in_feed} records sent.", flush=True)

        except Exception as feed_err:
            print(f"[!] Feed Exception: {feed_err}", flush=True)

    print(f"\n[*] Execution Complete. Total Leads Dispatched to Cloudflare: {total_posted}", flush=True)

if __name__ == "__main__":
    run_apex_ingress_pipeline()
