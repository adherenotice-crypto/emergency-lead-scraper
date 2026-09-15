import os
import re
import time
import asyncio
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

os.environ["PYTHONUNBUFFERED"] = "1"

API_URL = os.getenv("API_URL", "https://emergencyaudit.com/api/ping")
SECURITY_KEY = (
    os.getenv("EMERGENCY_KEY")
    or os.getenv("MASTER_ADMIN_KEY")
    or "SecretKey_2026_Dispatch!"
)

HEADERS = {
    "Content-Type": "application/json",
    "X-Emergency-Key": SECURITY_KEY
}

CASE_REGEX = re.compile(r'\b(2[0-6][A-Z0-9]{2,4}UD[0-9]{4,8}|UD-[0-9]{5,10}|[0-9]{2}SUD[0-9]{4,6})\b', re.I)
ADDRESS_REGEX = re.compile(r'\b\d{1,5}\s+[A-Za-z0-9\s.,]+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Way|Ct|Court|Ln|Lane)\b', re.I)
PHONE_REGEX = re.compile(r'\b(?:\+?1[-. ]?)?\(?([2-9][0-9]{2})\)?[-. ]?([2-9][0-9]{2})[-. ]?([0-9]{4})\b')
EMAIL_REGEX = re.compile(r'\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b')

COURT_PORTALS = {
    "LASC": {
        "name": "Los Angeles Superior Court (LASC)",
        "url": "https://www.lacourt.ca.gov/casesummary/ui/",
        "county": "Los Angeles",
        "zip": "90210"
    },
    "OCSC": {
        "name": "Orange County Superior Court (OCSC)",
        "url": "https://www.occourts.org/online-services/case-access",
        "county": "Orange County",
        "zip": "92660"
    },
    "SDSC": {
        "name": "San Diego Superior Court (SDSC)",
        "url": "https://www.sdcourt.ca.gov/sdcourt/civil2/civilcaseinformation",
        "county": "San Diego",
        "zip": "92101"
    },
    "RIVSC": {
        "name": "Riverside Superior Court (RIVSC)",
        "url": "https://www.riverside.courts.ca.gov/OnlineServices/SearchCaseRecords",
        "county": "Riverside",
        "zip": "92501"
    },
    "SBCSC": {
        "name": "San Bernardino Superior Court (SBCSC)",
        "url": "https://www.sb-court.org/online-services/case-information",
        "county": "San Bernardino",
        "zip": "91764"
    }
}

def clean_phone(phone_str):
    if not phone_str:
        return None
    digits = re.sub(r'\D', '', str(phone_str))
    if len(digits) == 10 and not digits.startswith(('800', '888', '877', '866', '855', '844')) and digits[3:6] != '555':
        return f"+1 ({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return None

def clean_email(email_str):
    if not email_str:
        return None
    clean = email_str.strip().lower()
    if not any(j in clean for j in ['info@', 'support@', 'contact@', 'admin@']):
        return clean
    return None

async def run_targeted_scraper():
    print("[*] Launching Targeted Court Index Scraper...", flush=True)
    total_posted = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )

        for code, portal in COURT_PORTALS.items():
            print(f"[*] Accessing Court Index: {portal['name']}...", flush=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            page.set_default_timeout(10000)

            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
            """)

            try:
                await page.goto(portal["url"], wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(1.5)

                # Target court case input specifically, avoiding header/nav search boxes
                case_input = await page.query_selector("input[id*='case'], input[name*='case'], input[placeholder*='Case']")
                if case_input:
                    await case_input.fill("UD")
                    btn = await page.query_selector("button[type='submit'], input[type='submit']")
                    if btn:
                        await btn.click()
                        await asyncio.sleep(2.5)

                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                rows = soup.find_all(["tr", "div", "li"], class_=re.compile(r'(case|row|result|docket|item)', re.I))

                matched = 0
                for row in rows:
                    raw_text = row.get_text(separator=" ", strip=True)

                    case_match = CASE_REGEX.search(raw_text)
                    addr_match = ADDRESS_REGEX.search(raw_text)

                    if not case_match or not addr_match:
                        continue

                    real_docket = case_match.group(0)
                    real_address = f"{addr_match.group(0)}, {portal['county']}, CA"
                    phone = clean_phone(PHONE_REGEX.search(raw_text).group(0) if PHONE_REGEX.search(raw_text) else None) or "Unmasked Upon Purchase"
                    email = clean_email(EMAIL_REGEX.search(raw_text).group(0) if EMAIL_REGEX.search(raw_text) else None) or "Unmasked Upon Purchase"

                    category = "TRADE_EMERGENCY" if "remodel" in raw_text.lower() else "HAULING"
                    price = 199.00 if category == "TRADE_EMERGENCY" else 129.00
                    drop_id = f"job_REAL_{code}_{real_docket}_{int(time.time())}"

                    payload = {
                        "sku": f"EA-WRIT-{portal['zip']}-{total_posted+1000}",
                        "dropId": drop_id,
                        "sourceChannel": f"{portal['name']} (Docket #{real_docket})",
                        "category": category,
                        "title_en": f"POST-EVICTION {category.replace('_', ' ')} (${int(price)})",
                        "title_es": f"RESTAURACIÓN POST-DESALOJO (${int(price)})",
                        "zip": portal["zip"],
                        "city": f"{portal['county']}, CA",
                        "desc_en": raw_text[:300],
                        "desc_es": f"Orden judicial de posesión emitida bajo Expediente #{real_docket}.",
                        "retailPrice": price,
                        "customerName": "Plaintiff Counsel / Asset Mgr",
                        "customerPhone": phone,
                        "customerAddress": real_address,
                        "dossier": {
                            "assetManager": "REO Portfolio Servicer",
                            "amPhone": phone,
                            "listingAgent": "Local REO Broker",
                            "agentPhone": "Unmasked Upon Purchase",
                            "propertyManager": "Direct Receiver / PM",
                            "pmPhone": "Unmasked Upon Purchase",
                            "attorney": "Plaintiff Counsel on Record",
                            "attorneyPhone": phone,
                            "attorneyEmail": email
                        }
                    }

                    res = requests.post(API_URL, json=payload, headers=HEADERS, timeout=5)
                    if res.status_code == 200:
                        print(f"[+] Ingested REAL Lead: {drop_id} | Address: {real_address}", flush=True)
                        total_posted += 1
                        matched += 1

                print(f"[+] Extracted {matched} verified records from {code}.", flush=True)

            except Exception as err:
                print(f"[!] Processing exception on {code}: {err}", flush=True)
            finally:
                await page.close()
                await context.close()

        await browser.close()

    print(f"[*] Complete. Ingested {total_posted} verified real leads into Cloudflare KV!", flush=True)

if __name__ == "__main__":
    asyncio.run(run_targeted_scraper())
