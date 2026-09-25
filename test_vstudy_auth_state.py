import argparse
import os
import shutil
import tempfile
import time
from pathlib import Path

from vstudy_auth_state import inject_auth_state, load_auth_state


def main():
    parser = argparse.ArgumentParser(
        description="Validate encrypted VStudy state in a fresh Chromium profile"
    )
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

        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    (() => {
                        const STORAGE_KEY = "__vstudyNetworkLog";

                        const readLog = () => {
                            try {
                                return JSON.parse(
                                    localStorage.getItem(STORAGE_KEY) || "[]"
                                );
                            } catch (_) {
                                return [];
                            }
                        };

                        const writeLog = (log) => {
                            try {
                                localStorage.setItem(
                                    STORAGE_KEY,
                                    JSON.stringify(log.slice(-100))
                                );
                            } catch (_) {}
                        };

                        const record = (entry) => {
                            try {
                                const url = String(entry.url || "");

                                if (
                                    url.includes("admission.saveetha.com") ||
                                    url.includes("/api/auth/")
                                ) {
                                    const log = readLog();
                                    log.push(entry);
                                    writeLog(log);
                                }
                            } catch (_) {}
                        };

                        const originalFetch = window.fetch;

                        window.fetch = async function(input, init = {}) {
                            const url =
                                typeof input === "string"
                                    ? input
                                    : (input && input.url) || "";

                            const method =
                                (init && init.method) ||
                                (input && input.method) ||
                                "GET";

                            try {
                                const response =
                                    await originalFetch.apply(this, arguments);

                                record({
                                    kind: "fetch",
                                    url,
                                    method,
                                    status: response.status
                                });

                                return response;
                            } catch (error) {
                                record({
                                    kind: "fetch-error",
                                    url,
                                    method,
                                    error: String(error)
                                });

                                throw error;
                            }
                        };

                        const OriginalXHR = window.XMLHttpRequest;

                        window.XMLHttpRequest = function() {
                            const xhr = new OriginalXHR();
                            let method = "GET";
                            let url = "";

                            const originalOpen = xhr.open;

                            xhr.open = function(m, u) {
                                method = m || "GET";
                                url = String(u || "");
                                return originalOpen.apply(this, arguments);
                            };

                            xhr.addEventListener("loadend", () => {
                                record({
                                    kind: "xhr",
                                    url,
                                    method,
                                    status: xhr.status
                                });
                            });

                            xhr.addEventListener("error", () => {
                                record({
                                    kind: "xhr-error",
                                    url,
                                    method,
                                    error: "network error"
                                });
                            });

                            return xhr;
                        };
                    })();
                """
            },
        )

        inject_auth_state(driver, state)

        authentication_error = None

        try:
            scraper.ensure_authenticated(driver)
        except Exception as exc:
            authentication_error = exc
            print(f"[DEBUG] ensure_authenticated raised: {exc}")

        # Allow VStudy time to finish auth/API requests even when authentication fails.
        time.sleep(5)

        try:
            network_log = driver.execute_script(
                """
                return JSON.parse(
                    localStorage.getItem("__vstudyNetworkLog") || "[]"
                );
                """
            )

            print("[DEBUG] Captured VStudy auth/API requests:")

            if not network_log:
                print("[DEBUG] No captured VStudy auth/API requests.")

            for entry in network_log:
                print(entry)

        except Exception as exc:
            print(
                f"[DEBUG] Could not read captured VStudy network requests: {exc}"
            )

        print(f"[DEBUG] Root URL: {driver.current_url}")
        print(f"[DEBUG] Root title: {driver.title}")

        if authentication_error is not None:
            raise authentication_error

        if scraper._is_authentication_required(driver):
            raise AuthenticationRequiredError(
                "Authentication state was not recognized by the fresh Chromium profile"
            )

        print("[PASS] Fresh Chromium profile recognized VStudy authentication")

        try:
            scraper.open_profile_page(driver)
        except AuthenticationRequiredError:
            print("[DEBUG] Browser cookies (metadata only):")
            try:
                print(
                    [
                        {
                            k: cookie.get(k)
                            for k in (
                                "name",
                                "domain",
                                "path",
                                "secure",
                                "httpOnly",
                                "sameSite",
                                "expiry",
                            )
                        }
                        for cookie in driver.get_cookies()
                        if "saveetha.com" in str(cookie.get("domain", "")).lower()
                    ]
                )
            except Exception as exc:
                print(f"[DEBUG] Could not read browser cookie metadata: {exc}")

            try:
                resources = driver.execute_script(
                    """
                    return performance.getEntriesByType('resource')
                        .map((entry) => entry.name)
                        .filter((url) => /admission\.saveetha\.com|\/api\/auth\//i.test(url));
                    """
                )
                print(f"[DEBUG] Auth-related resource URLs: {resources}")
            except Exception as exc:
                print(f"[DEBUG] Could not read performance resource URLs: {exc}")
            raise

        print(f"[DEBUG] Profile URL: {driver.current_url}")
        print(f"[DEBUG] Profile title: {driver.title}")
        print("[PASS] Profile page opened")

        scraper.open_detailed_profile(driver)
        print("[PASS] View Details flow opened the detailed profile")

        results = scraper.scrape_course_results(driver)

        if not results:
            raise RuntimeError(
                "Authenticated session reached the profile flow but returned no course results"
            )

        print(f"[PASS] Existing scraper returned {len(results)} course result(s)")
        print(f"[PASS] VStudy auth-state experiment completed at {VSTUDY_URL}")

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

        shutil.rmtree(profile_dir, ignore_errors=True)
        shutil.rmtree(runtime_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] VStudy auth-state experiment failed: {exc}")
        raise SystemExit(1) from exc
