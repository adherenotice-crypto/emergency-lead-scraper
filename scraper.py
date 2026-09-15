import os
import re
import time
import requests
from bs4 import BeautifulSoup

os.environ["PYTHONUNBUFFERED"] = "1"

# ----------------------------------------------------------------------
# SYSTEM KEYS & API ENDPOINTS
# ----------------------------------------------------------------------
API_URL = os.getenv("API_URL", "https://emergencyaudit.com/api/ping")
SECURITY_KEY = os.getenv("EMERGENCY_KEY") or os.getenv("MASTER_ADMIN_KEY") or "SecretKey_2026_Dispatch!"
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

HEADERS = {
    "Content-Type": "application/json",
    "X-Emergency-Key": SECURITY_KEY
}

APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"

# Deduplication cache to prevent redundant API queries
ENRICHMENT_CACHE = {}

# ----------------------------------------------------------------------
# REGEX PARSING SCHEMAS
# ----------------------------------------------------------------------
CASE_REGEX = re.compile(r'\b(2[0-6][A-Z0-9]{2,4}UD[0-9]{4,8}|UD-[0-9]{2,5}-[0-9]{4,8}|[0-9]{2}SUD[0-9]{4,6}|[0-9]{6,10}-UD)\b', re.I)
ADDRESS_REGEX = re.compile(r'\b\d{1,5}\s+[A-Za-z0-9\s.,#]+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Way|Ct|Court|Ln|Lane|Pl|Place|Cir|Circle)\b', re.I)

# ----------------------------------------------------------------------
# AUTOMATED APOLLO B2B ENRICHMENT ENGINE
# ----------------------------------------------------------------------
def enrich_via_apollo(raw_name_string):
    """Queries Apollo.io Person Match API with caching and free-plan error handling."""
    if not APOLLO_API_KEY:
        return None, None

    clean_name = raw_name_string.strip().upper()
    if clean_name in ENRICHMENT_CACHE:
        return ENRICHMENT_CACHE[clean_name]

    print(f"  [*] Initializing Apollo Lookup for: '{clean_name}'...", flush=True)

    payload = {"name": clean_name}
    apollo_headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "x-api-key": APOLLO_API_KEY
    }

    try:
        response = requests.post(APOLLO_MATCH_URL, json=payload, headers=apollo_headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            person = data.get("person") or {}
            email = person.get("email")
            phone = person.get("sanitized_phone") or (person.get("phone_numbers", [{}])[0].get("sanitized_number") if person.get("phone_numbers") else None)

            if email or phone:
                print(f"  [+] Apollo Match Success! Phone: {phone} | Email: {email}", flush=True)
                ENRICHMENT_CACHE[clean_name] = (phone, email)
                return phone, email
        elif response.status_code == 403:
            print("  [!] Apollo Free Tier Limitation: Match API requires paid plan. Bypassing enrichment.", flush=True)
            ENRICHMENT_CACHE[clean_name] = (None, None)
            return None, None
    except Exception as e:
        print(f"  [!] Apollo Lookup exception: {e}", flush=True)

    ENRICHMENT_CACHE[clean_name] = (None, None)
    return None, None

# ----------------------------------------------------------------------
# CORE EXECUTION PIPELINE
# ----------------------------------------------------------------------
REAL_DATA_FEEDS = [
    {"county": "Los Angeles", "name": "California Public Notice Registry (LA)", "url": "https://www.capublicnotice.com/search/results?q=Writ+of+Possession", "zip": "90210"},
    {"county": "Orange County", "name": "California Public Notice Registry (OC)", "url": "https://www.capublicnotice.com/search/results?q=Unlawful+Detainer", "zip": "92660"},
    {"county": "Riverside", "name": "California Public Notice Registry (IE)", "url": "https://www.capublicnotice.com/search/results?q=Notice+To+Vacate", "zip": "92501"},
    {"county": "San Diego", "name": "California Public Notice Registry (SD)", "url": "https://www.capublicnotice.com/search/results?q=Eviction+Notice", "zip": "92101"}
]

def run_real_lead_scraper():
    print("[*] Launching Real SoCal Civil Execution Pipeline with Apollo Data Enrichment...", flush=True)
    total_posted = 0

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    for feed in REAL_DATA_FEEDS:
        print(f"\n[*] Querying Target Feed: {feed['name']}...", flush=True)
        try:
            res = requests.get(feed["url"], headers=req_headers, timeout=15)
            if res.status_code != 200:
                print(f"[!] HTTP {res.status_code} on feed {feed['county']}", flush=True)
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            nodes = soup.find_all(["tr", "div", "li", "p", "article", "td"])

            matched = 0
            for node in nodes:
                raw_text = node.get_text(separator=" ", strip=True)

                if not any(k in raw_text.lower() for k in ["writ", "possession", "eviction", "vacate", "execution", "unlawful detainer", "trustee"]):
                    continue

                case_match = CASE_REGEX.search(raw_text)
                addr_match = ADDRESS_REGEX.search(raw_text)

                if not addr_match:
                    continue

                real_docket = case_match.group(0) if case_match else f"EXEC-{int(time.time()) % 100000}"
                real_address = f"{addr_match.group(0)}, {feed['county']}, CA"
                
                entity_name = "Plaintiff Representative"
                if "plaintiff" in raw_text.lower():
                    parts = re.split(r'plaintiff[:\s]+', raw_text, flags=re.I)
                    if len(parts) > 1:
                        entity_name = " ".join(parts[1].split()[:3]).replace(",", "")
                elif "attorney" in raw_text.lower():
                    parts = re.split(r'attorney[:\s]+', raw_text, flags=re.I)
                    if len(parts) > 1:
                        entity_name = " ".join(parts[1].split()[:3]).replace(",", "")

                phone, email = None, None
                if entity_name != "Plaintiff Representative" and len(entity_name) > 3:
                    phone, email = enrich_via_apollo(entity_name)

                final_phone = phone or "Unmasked Upon Purchase"
                final_email = email or "Unmasked Upon Purchase"

                category = "TRADE_EMERGENCY" if any(w in raw_text.lower() for w in ["remodel", "turnkey", "restoration", "damage"]) else "HAULING"
                price = 199.00 if category == "TRADE_EMERGENCY" else 129.00
                drop_id = f"job_REAL_{feed['county'][:2].upper()}_{real_docket}_{int(time.time())}"

                payload = {
                    "sku": f"EA-WRIT-{feed['zip']}-{total_posted+1000}",
                    "dropId": drop_id,
                    "sourceChannel": f"{feed['name']} (Record #{real_docket})",
                    "category": category,
                    "title_en": f"POST-EVICTION {category.replace('_', ' ')} (${int(price)})",
                    "zip": feed["zip"],
                    "city": f"{feed['county']}, CA",
                    "desc_en": f"Live structural writ activity parsed. Target entity: {entity_name}. Preview: {raw_text[:150]}...",
                    "retailPrice": price,
                    "customerName": entity_name,
                    "customerPhone": final_phone,
                    "customerAddress": real_address,
                    "dossier": {
                        "assetManager": "REO Portfolio Real Estate Team",
                        "amPhone": final_phone,
                        "listingAgent": "Local Default Broker Assignment",
                        "agentPhone": "Unmasked Upon Purchase",
                        "propertyManager": "Assigned Receiver / Property PM",
                        "pmPhone": "Unmasked Upon Purchase",
                        "attorney": entity_name,
                        "attorneyPhone": final_phone,
                        "attorneyEmail": final_email
                    }
                }

                try:
                    post_res = requests.post(API_URL, json=payload, headers=HEADERS, timeout=5)
                    if post_res.status_code == 200:
                        print(f"  [+] SUCCESS: Ingested enriched lead -> {drop_id}", flush=True)
                        total_posted += 1
                        matched += 1
                except Exception as post_err:
                    print(f"  [!] Dispatch API Error: {post_err}", flush=True)

            print(f"[+] Scan Complete for {feed['county']}: Extracted {matched} leads.", flush=True)
        except Exception as err:
            print(f"[!] Feed Exception on {feed['county']}: {err}", flush=True)

    print(f"\n[*] Execution Cycle Complete. Ingested {total_posted} total enriched leads!", flush=True)

if __name__ == "__main__":
    run_real_lead_scraper()
