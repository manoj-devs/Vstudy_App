import argparse
import os
import shutil
import tempfile
from pathlib import Path

from vstudy_auth_state import inject_auth_state, load_auth_state


def main():
    parser = argparse.ArgumentParser(description="Validate encrypted VStudy state in a fresh Chromium profile")
    parser.add_argument("--state-file", required=True, help="Encrypted VStudy state file")
    args = parser.parse_args()

    profile_dir = Path(tempfile.mkdtemp(prefix="vstudy-fresh-profile-"))
    runtime_dir = Path(tempfile.mkdtemp(prefix="vstudy-chrome-runtime-"))
    driver = None
    try:
        os.environ["VSTUDY_PROFILE_DIR"] = str(profile_dir)
        os.environ["CHROME_RUNTIME_DIR"] = str(runtime_dir)
        os.environ["HEADLESS"] = "true"
        os.environ["UNATTENDED"] = "true"

        from config import VSTUDY_URL
        from vstudy_scraper import AuthenticationRequiredError, VStudyScraper

        state = load_auth_state(args.state_file)
        scraper = VStudyScraper()
        driver = scraper._create_driver()
        inject_auth_state(driver, state)

        scraper.ensure_authenticated(driver)
        print(f"[DEBUG] Root URL: {driver.current_url}")
        print(f"[DEBUG] Root title: {driver.title}")
        if scraper._is_authentication_required(driver):
            raise AuthenticationRequiredError(
                "Authentication state was not recognized by the fresh Chromium profile"
            )
        print("[PASS] Fresh Chromium profile recognized VStudy authentication")

        scraper.open_profile_page(driver)
        print(f"[DEBUG] Profile URL: {driver.current_url}")
        print(f"[DEBUG] Profile title: {driver.title}")
        print("[PASS] Profile page opened")
        scraper.open_detailed_profile(driver)
        print("[PASS] View Details flow opened the detailed profile")

        results = scraper.scrape_course_results(driver)
        if not results:
            raise RuntimeError("Authenticated session reached the profile flow but returned no course results")
        print(f"[PASS] Existing scraper returned {len(results)} course result(s)")
        print(f"[PASS] VStudy auth-state experiment completed at {VSTUDY_URL}")
    finally:
        if driver is not None:
            driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)
        shutil.rmtree(runtime_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] VStudy auth-state experiment failed: {exc}")
        raise SystemExit(1) from exc
