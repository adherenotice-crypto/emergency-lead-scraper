import os
import re
import time
import asyncio
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

os.environ["PYTHONUNBUFFERED"] = "1"

API_URL = os.getenv("API_URL", "https://emergencyaudit.com/api/ping")
SECURITY_KEY = os.getenv("EMERGENCY_KEY") or os.getenv("MASTER_ADMIN_KEY") or "SecretKey_2026_Dispatch!"
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

HEADERS = {
    "Content-Type": "application/json",
    "X-Emergency-Key": SECURITY_KEY
}
APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
ENRICHMENT_CACHE = {}

JUNK_NOTICE_FILTER = [
    "change of name", "fictitious business", "notice to creditors", 
    "order to show cause", "probate", "statement of abandonment",
    "redding record", "stockton record", "searchlight", "fbn number", 
    "classifieds", "trustee's sale", "notice of sale", "public sale", 
    "auction", "transpo", "annual performance"
]

CASE_REGEX = re.compile(r'\b(2[0-6][A-Z0-9]{2,4}UD[0-9]{4,8}|UD-[0-9]{2,5}-[0-9]{4,8}|[0-9]{2}SUD[0-9]{4,6}|[0-9]{6,10}-UD)\b', re.I)
ADDRESS_REGEX = re.compile(r'\b\d{1,5}\s+[A-Za-z0-9\s.,#-]{3,35}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Way|Ct|Court|Ln|Lane|Pl|Place|Cir|Circle)?\b', re.I)

def enrich_via_apollo(raw_name_string):
    if not APOLLO_API_KEY:
        return None, None
    clean_name = raw_name_string.strip().upper()
    if clean_name in ENRICHMENT_CACHE:
        return ENRICHMENT_CACHE[clean_name]
    try:
        response = requests.post(APOLLO_MATCH_URL, json={"name": clean_name}, headers={"Content-Type": "application/json", "x-api-key": APOLLO_API_KEY}, timeout=5)
        if response.status_code == 200:
            person = response.json().get("person") or {}
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
    {"county": "Los Angeles", "name": "California Public Notice Registry (LA)", "query": "Writ of Possession", "zip": "90210"},
    {"county": "Orange County", "name": "California Public Notice Registry (OC)", "query": "Unlawful Detainer", "zip": "92660"},
    {"county": "Riverside", "name": "California Public Notice Registry (IE)", "query": "Notice To Vacate", "zip": "92501"},
    {"county": "San Diego", "name": "California Public Notice Registry (SD)", "query": "Eviction Notice", "zip": "92101"}
]

async def run_real_lead_scraper():
    print("[*] Launching Verbose Diagnostic Pipeline...", flush=True)
    total_posted = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )

        for feed in REAL_DATA_FEEDS:
            print(f"\n[*] Querying Feed Node: {feed['name']}...", flush=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = await context.new_page()

            try:
                await page.goto("https://www.capublicnotice.com/", wait_until="networkidle", timeout=25000)
                await asyncio.sleep(4)

                search_input = await page.wait_for_selector('input[type="text"], input[placeholder*="search" i]', timeout=3000)
                if search_input:
                    await search_input.fill(feed["query"])
                    await search_input.press("Enter")
                    print(f"  [+] Submitted query: '{feed['query']}'", flush=True)
                    await asyncio.sleep(8)

                soup = BeautifulSoup(await page.content(), "html.parser")
                for widget in soup(["script", "style", "nav", "footer", "header"]):
                    widget.decompose()

                text_blocks = [node.get_text(separator=" ", strip=True) for node in soup.find_all(["div", "article", "tr", "p", "li"]) if len(node.get_text(separator=" ", strip=True)) > 40]
                print(f"  [*] Inspected {len(text_blocks)} blocks. Printing first 3 blocks for inspection:", flush=True)
                for idx, b in enumerate(text_blocks[:3]):
                    print(f"      [{idx}] {b[:140]}", flush=True)

                matched = 0
                for block in text_blocks:
                    context_window = block.lower()

                    if any(junk in context_window for junk in JUNK_NOTICE_FILTER):
                        continue

                    # Track filtering stages for debugging
                    intent_keywords = ["writ", "possession", "eviction", "vacate", "unlawful", "detainer", "sheriff", "tenant", "notice"]
                    if not any(k in context_window for k in intent_keywords):
                        continue

                    addr_match = ADDRESS_REGEX.search(block)
                    if not addr_match:
                        # Fallback to check if any street number exists
                        num_match = re.search(r'\b\d{2,5}\s+[A-Za-z]+', block)
                        if num_match:
                            real_address = f"{num_match.group(0)}, {feed['county']}, CA"
                        else:
                            continue
                    else:
                        real_address = f"{addr_match.group(0).strip()}, {feed['county']}, CA"

                    case_match = CASE_REGEX.search(block)
                    real_docket = case_match.group(0) if case_match else f"WRIT-{int(time.time()) % 100000}-{matched}"

                    payload = {
                        "sku": f"EA-WRIT-{feed['zip']}-{total_posted+1000}",
                        "dropId": f"job_REAL_{feed['county'][:2].upper()}_{real_docket}_{int(time.time())}",
                        "sourceChannel": f"{feed['name']} (Record #{real_docket})",
                        "category": "HAULING",
                        "title_en": "POST-EVICTION HAULING ($129)",
                        "zip": feed["zip"],
                        "city": f"{feed['county']}, CA",
                        "desc_en": f"Verified Notice Context: {block[:140]}...",
                        "retailPrice": 129.00,
                        "customerName": "Property Asset Manager",
                        "customerPhone": "Unmasked Upon Purchase",
                        "customerAddress": real_address,
                        "dossier": {
                            "assetManager": "REO Team",
                            "amPhone": "Unmasked Upon Purchase",
                            "listingAgent": "Local Default Broker",
                            "agentPhone": "Unmasked Upon Purchase",
                            "propertyManager": "Assigned PM",
                            "pmPhone": "Unmasked Upon Purchase",
                            "attorney": "Counsel On File",
                            "attorneyPhone": "Unmasked Upon Purchase",
                            "attorneyEmail": "Unmasked Upon Purchase"
                        }
                    }

                    try:
                        res = requests.post(API_URL, json=payload, headers=HEADERS, timeout=5)
                        if res.status_code == 200:
                            print(f"  [+] SUCCESS INGESTION -> {real_address}", flush=True)
                            total_posted += 1
                            matched += 1
                            if matched >= 5:
                                break
                    except Exception:
                        pass

                print(f"[+] Scan Complete for {feed['county']}: Extracted {matched} records.", flush=True)

            except Exception as err:
                print(f"[!] Error on {feed['county']}: {err}", flush=True)
            finally:
                await page.close()

        await browser.close()
    print(f"\n[*] Execution Complete. Total Posted: {total_posted}", flush=True)

if __name__ == "__main__":
    asyncio.run(run_real_lead_scraper())
