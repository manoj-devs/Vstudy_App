import argparse
import shutil
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from config import VSTUDY_URL
from vstudy_auth_state import export_auth_state
from vstudy_scraper import VStudyScraper


def main():
    parser = argparse.ArgumentParser(description="Manually authenticate VStudy and export encrypted VStudy-only state")
    parser.add_argument(
        "--output",
        default="vstudy-auth-state.enc",
        help="Encrypted output path (default: vstudy-auth-state.enc)",
    )
    args = parser.parse_args()

    profile_dir = Path(tempfile.mkdtemp(prefix="vstudy-auth-bootstrap-"))
    driver = None
    try:
        options = Options()
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-notifications")
        driver = webdriver.Chrome(options=options)
        scraper = VStudyScraper()
        driver.get(VSTUDY_URL)
        print("A temporary Chrome profile is open. Complete the VStudy login manually.")
        input("After the VStudy dashboard is visible, press ENTER here: ")
        WebDriverWait(driver, 60).until(scraper._is_dashboard_visible)
        cookie_count, origin_count = export_auth_state(driver, args.output)
        print(f"Encrypted VStudy state written to {Path(args.output).resolve()}")
        print(f"Exported {cookie_count} saveetha.com cookie(s) and {origin_count} VStudy origin(s).")
        print("No full Chrome profile, Google cookies, passwords, or unrelated browser data was exported.")
    finally:
        if driver is not None:
            driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
