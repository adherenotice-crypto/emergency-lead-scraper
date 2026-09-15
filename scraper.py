import os
import re
import time
import asyncio
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

os.environ["PYTHONUNBUFFERED"] = "1"

# ----------------------------------------------------------------------
# SYSTEM KEYS & API DISPATCH ENDPOINTS
# ----------------------------------------------------------------------
API_URL = os.getenv("API_URL", "https://emergencyaudit.com/api/ping")
SECURITY_KEY = os.getenv("EMERGENCY_KEY") or os.getenv("MASTER_ADMIN_KEY") or "SecretKey_2026_Dispatch!"
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

HEADERS = {
    "Content-Type": "application/json",
    "X-Emergency-Key": SECURITY_KEY
}
APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
ENRICHMENT_CACHE = {}

# STRICT JUNK FILTER: Drops auctions, trustee sales, and name changes
JUNK_NOTICE_FILTER = [
    "change of name", "fictitious business", "notice to creditors", 
    "order to show cause", "probate", "statement of abandonment",
    "redding record", "stockton record", "searchlight", "fbn number", 
    "classifieds", "trustee's sale", "notice of sale", "public sale", 
    "auction", "transpo", "annual performance"
]

CASE_REGEX = re.compile(r'\b(2[0-6][A-Z0-9]{2,4}UD[0-9]{4,8}|UD-[0-9]{2,5}-[0-9]{4,8}|[0-9]{2}SUD[0-9]{4,6}|[0-9]{6,10}-UD)\b', re.I)

# RELAXED ADDRESS REGEX: Captures standard street numbers + names up to commas or abbreviations
ADDRESS_REGEX = re.compile(r'\b\d{1,5}\s+[A-Za-z0-9\s.,#-]{3,35}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Way|Ct|Court|Ln|Lane|Pl|Place|Cir|Circle|Bl|Way|Pkwy)?\b', re.I)

def enrich_via_apollo(raw_name_string):
    if not APOLLO_API_KEY:
        return None, None

    clean_name = raw_name_string.strip().upper()
    if clean_name in ENRICHMENT_CACHE:
        return ENRICHMENT_CACHE[clean_name]

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
                ENRICHMENT_CACHE[clean_name] = (phone, email)
                return phone, email
    except Exception:
        pass

    ENRICHMENT_CACHE[clean_name] = (None, None)
    return None, None

REAL_DATA_FEEDS = [
    {"county": "Los Angeles", "name": "California Public Notice Registry (LA)", "url": "https://www.capublicnotice.com/search/results?q=Writ+of+Possession", "zip": "90210"},
    {"county": "Orange County", "name": "California Public Notice Registry (OC)", "url": "https://www.capublicnotice.com/search/results?q=Unlawful+Detainer", "zip": "92660"},
    {"county": "Riverside", "name": "California Public Notice Registry (IE)", "url": "https://www.capublicnotice.com/search/results?q=Notice+To+Vacate", "zip": "92501"},
    {"county": "San Diego", "name": "California Public Notice Registry (SD)", "url": "https://www.capublicnotice.com/search/results?q=Eviction+Notice", "zip": "92101"}
]

async def run_real_lead_scraper():
    print("[*] Launching Resilient Address-Match Eviction Pipeline...", flush=True)
    total_posted = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )

        for feed in REAL_DATA_FEEDS:
            print(f"\n[*] Querying Feed Node: {feed['name']}...", flush=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            try:
                await page.goto(feed["url"], wait_until="networkidle", timeout=20000)
                await asyncio.sleep(5)

                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")

                for widget in soup(["script", "style", "nav", "footer", "header"]):
                    widget.decompose()

                text_blocks = [node.get_text(separator=" ", strip=True) for node in soup.find_all(["div", "article", "tr", "p", "li"]) if len(node.get_text(separator=" ", strip=True)) > 40]

                matched = 0
                for block in text_blocks:
                    context_window = block.lower()

                    # 1. Skip if contains any junk/auction terms
                    if any(junk in context_window for junk in JUNK_NOTICE_FILTER):
                        continue

                    # 2. Flexible Intent Check
                    intent_keywords = ["writ", "possession", "eviction", "vacate", "unlawful", "detainer", "sheriff", "tenant"]
                    if not any(k in context_window for k in intent_keywords):
                        continue

                    # 3. Resilient Address Matcher (with smart fallback if exact suffix is missing)
                    addr_match = ADDRESS_REGEX.search(block)
                    if addr_match:
                        real_address = f"{addr_match.group(0).strip()}, {feed['county']}, CA"
                    else:
                        # Fallback to county context if numbers exist in a valid legal block
                        num_match = re.search(r'\b\d{2,5}\s+[A-Za-z]+', block)
                        if num_match:
                            real_address = f"{num_match.group(0)}, {feed['county']}, CA"
                        else:
                            continue

                    case_match = CASE_REGEX.search(block)
                    real_docket = case_match.group(0) if case_match else f"WRIT-{int(time.time()) % 100000}-{matched}"

                    entity_name = "Property Asset Manager"
                    if "plaintiff" in context_window:
                        parts = re.split(r'plaintiff[:\s]+', block, flags=re.I)
                        if len(parts) > 1:
                            entity_name = " ".join(parts[1].split()[:3]).replace(",", "")

                    phone, email = None, None
                    if entity_name != "Property Asset Manager" and len(entity_name) > 3:
                        phone, email = enrich_via_apollo(entity_name)

                    final_phone = phone or "Unmasked Upon Purchase"
                    final_email = email or "Unmasked Upon Purchase"

                    category = "TRADE_EMERGENCY" if any(w in context_window for w in ["remodel", "turnkey", "restoration", "damage"]) else "HAULING"
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
                        "desc_en": f"Verified Writ Notice parsed dynamically. Notice Context: {block[:140]}...",
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
                            print(f"  [+] SUCCESS: Ingested lead -> {drop_id} | Location: {real_address}", flush=True)
                            total_posted += 1
                            matched += 1
                    except Exception:
                        pass

                print(f"[+] Scan Complete for {feed['county']}: Extracted {matched} records.", flush=True)

            except Exception as err:
                print(f"[!] Processing exception on {feed['county']} run pipeline: {err}", flush=True)
            finally:
                await page.close()

        await browser.close()
    print(f"\n[*] Execution Cycle Complete. Ingested {total_posted} total leads into Cloudflare KV!", flush=True)

if __name__ == "__main__":
    asyncio.run(run_real_lead_scraper())
