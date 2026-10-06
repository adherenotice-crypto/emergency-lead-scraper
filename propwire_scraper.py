import os
import time
import logging
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

PROPWIRE_EMAIL = os.getenv("PROPWIRE_EMAIL")
PROPWIRE_PASSWORD = os.getenv("PROPWIRE_PASSWORD")

os.makedirs("batches", exist_ok=True)

def scrape_and_download_propwire():
    if not PROPWIRE_EMAIL or not PROPWIRE_PASSWORD:
        logging.error("❌ Missing PROPWIRE_EMAIL or PROPWIRE_PASSWORD in environment secrets.")
        return

    logging.info("🚀 Launching Headless Playwright Browser for Propwire Auto-Fetch...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            # 1. Log in to Propwire
            logging.info(f"🔑 Logging into Propwire as {PROPWIRE_EMAIL}...")
            page.goto("https://propwire.com/login", timeout=60000)
            page.wait_for_selector("input[type='email'], input[name='email']", timeout=15000)

            page.fill("input[type='email'], input[name='email']", PROPWIRE_EMAIL)
            page.fill("input[type='password'], input[name='password']", PROPWIRE_PASSWORD)
            
            page.click("button[type='submit']")
            page.wait_for_timeout(5000)

            # 2. Access Propwire Activity / Downloads page
            logging.info("📥 Navigating to Propwire Activity Downloads...")
            page.goto("https://propwire.com/activity", timeout=60000)
            page.wait_for_timeout(3000)

            # 3. Locate & trigger download of latest CSV export
            download_btn = page.locator("a:has-text('Download'), button:has-text('Download')").first

            if download_btn.is_visible():
                logging.info("⚡ Download button located. Downloading CSV export...")
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
