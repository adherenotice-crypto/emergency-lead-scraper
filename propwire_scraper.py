import os
import time
import logging
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

PROPWIRE_EMAIL = os.getenv("PROPWIRE_EMAIL")
PROPWIRE_PASSWORD = os.getenv("PROPWIRE_PASSWORD")
PROPWIRE_COOKIE = os.getenv("PROPWIRE_COOKIE")

os.makedirs("batches", exist_ok=True)

def scrape_and_download_propwire():
    logging.info("🚀 Launching Stealth Playwright Browser for Propwire Auto-Fetch...")

    with sync_playwright() as p:
        # Launch Chromium with anti-bot detection flags
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        # Mask automated webdriver signature
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            # Check if direct session cookie exists in secrets
            if PROPWIRE_COOKIE:
                logging.info("🍪 Injecting active Propwire session cookie...")
                context.add_cookies([{
                    "name": "propwire_session",
                    "value": PROPWIRE_COOKIE,
                    "domain": ".propwire.com",
                    "path": "/"
                }])
                page.goto("https://propwire.com/activity", wait_until="networkidle", timeout=60000)
            else:
                if not PROPWIRE_EMAIL or not PROPWIRE_PASSWORD:
                    logging.error("❌ Missing PROPWIRE_EMAIL or PROPWIRE_PASSWORD secrets.")
                    return

                logging.info(f"🔑 Navigating to Propwire Login as {PROPWIRE_EMAIL}...")
                page.goto("https://propwire.com/login", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                email_selector = "input[type='email'], input[name='email'], input[placeholder*='Email']"
                page.wait_for_selector(email_selector, timeout=30000)

                page.fill(email_selector, PROPWIRE_EMAIL)
                page.fill("input[type='password'], input[name='password']", PROPWIRE_PASSWORD)
                page.click("button[type='submit']")
                page.wait_for_timeout(5000)

                page.goto("https://propwire.com/activity", wait_until="domcontentloaded", timeout=60000)

            # Locate & trigger CSV download
            logging.info("📥 Scanning Activity page for latest CSV export...")
            download_btn = page.locator("a:has-text('Download'), button:has-text('Download')").first

            if download_btn.is_visible():
                logging.info("⚡ Triggering Propwire CSV download...")
                with page.expect_download(timeout=60000) as download_info:
                    download_btn.click()
                download = download_info.value

                output_path = f"batches/propwire_export_{int(time.time())}.csv"
                download.save_as(output_path)
                logging.info(f"🎉 SUCCESS: Saved fresh Propwire export to '{output_path}'")
            else:
                logging.warning("⚠️ No download links found on Propwire Activity page.")

        except Exception as e:
            logging.error(f"❌ Propwire Auto-Fetch Error: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    scrape_and_download_propwire()
