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
        
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            if PROPWIRE_COOKIE:
                logging.info("🍪 Injecting active Propwire session cookie...")
                context.add_cookies([{
                    "name": "propwire_session",
                    "value": PROPWIRE_COOKIE,
                    "domain": ".propwire.com",
                    "path": "/"
                }])
            
            logging.info("📥 Navigating to Propwire Activity Downloads...")
            page.goto("https://propwire.com/activity", wait_until="domcontentloaded", timeout=60000)
            
            # Wait 5 seconds for React/Vue dynamic activity table to hydrate
            page.wait_for_timeout(5000)

            logging.info("🔍 Scanning Activity table for CSV export buttons...")
            
            # Multi-selector matching for Propwire activity download links
            download_selectors = [
                "a:has-text('Download')",
                "button:has-text('Download')",
                "a[href*='download']",
                "a[download]",
                "svg[data-icon='download']"
            ]

            download_btn = None
            for sel in download_selectors:
                loc = page.locator(sel).first
                if loc.is_visible():
                    download_btn = loc
                    logging.info(f"✅ Found download target using selector: '{sel}'")
                    break

            if download_btn:
                logging.info("⚡ Triggering Propwire CSV download...")
                with page.expect_download(timeout=60000) as download_info:
                    download_btn.click()
                download = download_info.value

                output_path = f"batches/propwire_export_{int(time.time())}.csv"
                download.save_as(output_path)
                logging.info(f"🎉 SUCCESS: Saved fresh Propwire export to '{output_path}'")
            else:
                logging.warning("⚠️ No download links found. Ensure you have an active completed export at https://propwire.com/activity")

        except Exception as e:
            logging.error(f"❌ Propwire Auto-Fetch Error: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    scrape_and_download_propwire()
