import os
import re
import time
import requests

os.environ["PYTHONUNBUFFERED"] = "1"

API_URL = os.getenv("API_URL", "https://emergencyaudit.com/api/ping")
SECURITY_KEY = os.getenv("EMERGENCY_KEY") or os.getenv("MASTER_ADMIN_KEY") or "SecretKey_2026_Dispatch!"

HEADERS = {
    "Content-Type": "application/json",
    "X-Emergency-Key": SECURITY_KEY,
    "User-Agent": "EmergencyAudit-ApexIngress/2026.50.0"
}

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

def sanitize_key(val: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', '_', str(val)).strip('_')

def run_apex_ingress_pipeline():
    print("[*] Launching Apex Ingress Pipeline (Enhanced Socrata Parsing)...", flush=True)
    total_posted = 0

    for feed in SOCRATA_FEEDS:
        print(f"\n[*] Fetching Municipal Stream: {feed['name']}...", flush=True)
        try:
            res = requests.get(feed["url"], timeout=15)
            if res.status_code != 200:
                print(f"  [!] Feed HTTP Error {res.status_code}. Skipping.", flush=True)
                continue

            records = res.json()
            matched_in_feed = 0

            for item in records:
                # Robust Address Extraction
                house_no = item.get("house_number") or item.get("street_number") or ""
                street_name = item.get("street_name") or item.get("street") or ""
                street_type = item.get("street_type") or ""
                raw_address = item.get("address") or item.get("primary_address") or f"{house_no} {street_name} {street_type}".strip()

                if not raw_address or len(raw_address.strip()) < 5:
                    continue

                # Multi-key Zip Code Fallback
                zip_code = item.get("zip_code") or item.get("zipcode") or item.get("zip") or item.get("postal_code") or "90001"
                
                # Multi-key Violation Detail Resolution
                violation_desc = (
                    item.get("primary_violation") or 
                    item.get("violation_description") or 
                    item.get("order_type") or 
                    item.get("description") or 
                    item.get("case_type") or 
                    item.get("status_description") or 
                    "Vacant Structure & Municipal Abatement Citation"
                )

                raw_case = item.get("case_number") or item.get("apno") or item.get("case_no") or f"CASE-{int(time.time()*1000)%100000}"
                case_no = sanitize_key(raw_case)
                owner_entity = item.get("owner_name") or item.get("applicant_name") or "Commercial Property Owner / Manager"
                full_address = f"{raw_address.strip().upper()}, Los Angeles, CA {zip_code}"
                drop_id = f"job_REAL_COMM_{case_no}_{int(time.time()*1000)}"

                payload = {
                    "sku": f"EA-JOB-{zip_code}-{total_posted+1000}",
                    "dropId": drop_id,
                    "partnerId": "apex_python_scraper",
                    "sourceChannel": f"City Record (Case #{raw_case})",
                    "category": feed["category"],
                    "title_en": f"{feed['category'].replace('_', ' ')} (${int(feed['price'])})",
                    "zip": zip_code,
                    "city": "Los Angeles, CA",
                    "desc_en": f"City Notice: {violation_desc[:160]}",
                    "retailPrice": feed["price"],
                    "customerName": owner_entity,
                    "customerAddress": full_address,
                    "skipTrace": True,
                    "dossier": {
                        "ownerManager": owner_entity,
                        "propertyAddress": full_address,
                        "cityNotice": violation_desc[:160],
                        "estimatedJobSize": "$5,000.00 - $25,000.00+"
                    }
                }

                try:
                    p_res = requests.post(API_URL, json=payload, headers=HEADERS, timeout=12)
                    if p_res.status_code in [200, 201]:
                        print(f"  [+] DISPATCH SUCCESS -> {drop_id} | Zip: {zip_code} | Issue: {violation_desc[:40]}...", flush=True)
                        total_posted += 1
                        matched_in_feed += 1
                        time.sleep(0.2)
                        if matched_in_feed >= 10:
                            break
                    else:
                        print(f"  [!] Dispatch Error HTTP {p_res.status_code}: {p_res.text}", flush=True)
                except Exception as post_err:
                    print(f"  [!] Post Error: {post_err}", flush=True)

        except Exception as feed_err:
            print(f"[!] Feed Error: {feed_err}", flush=True)

    print(f"\n[*] Complete. Total Dispatched: {total_posted}", flush=True)

if __name__ == "__main__":
    run_apex_ingress_pipeline()
