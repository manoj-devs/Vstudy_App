import os
import json
import re
import shutil
import subprocess
import tempfile
import traceback
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from config import (
    CHROME_RUNTIME_DIR,
    HEADLESS,
    SAVE_DEBUG_ARTIFACTS,
    UNATTENDED,
    VSTUDY_URL,
    VSTUDY_PROFILE_DIR,
)

DASHBOARD_URL = "https://vstudy.saveetha.com/dashboard"
PROFILE_URL = "https://vstudy.saveetha.com/dashboard/profile"


class AuthenticationRequiredError(RuntimeError):
    """Raised when VStudy redirects the scraper to its login page."""


class VStudyScraper:
    def __init__(self):
        self.driver = None
        self.wait_timeout = 20
        print("[✓] VStudy scraper ready")

    def _log_chrome_diagnostics(self, chromium_binary, chromedriver_binary):
        for label, binary in (
            ("Chromium", chromium_binary),
            ("ChromeDriver", chromedriver_binary),
        ):
            if not binary:
                print(f"[DEBUG] {label} path: not found")
                continue
            try:
                result = subprocess.run(
                    [binary, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                version = (result.stdout or result.stderr).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                version = f"unavailable ({exc})"
            print(f"[DEBUG] {label} path: {binary}")
            print(f"[DEBUG] {label} version: {version}")

    def _build_chrome_options(self, profile_dir, runtime_dir, chromium_binary):
        options = Options()
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument(f"--disk-cache-dir={runtime_dir}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--start-maximized")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-blink-features=AutomationControlled")
        if chromium_binary:
            options.binary_location = chromium_binary
        if HEADLESS:
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            options.add_argument("--remote-debugging-port=9222")
        options.add_argument("--enable-logging=stderr")
        options.add_argument("--v=1")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        return options

    def _cleanup_stale_profile_locks(self, profile_dir):
        print("[DEBUG] Checking for active Chromium process using profile...")
        proc_dir = "/proc"
        process_check_complete = True
        active_process_found = False

        try:
            process_ids = os.listdir(proc_dir)
        except OSError:
            process_check_complete = False
            process_ids = []

        for process_id in process_ids:
            if not process_id.isdigit():
                continue

            try:
                with open(os.path.join(proc_dir, process_id, "cmdline"), "rb") as process_file:
                    command = process_file.read().replace(b"\x00", b" ").decode(errors="replace")
            except (OSError, UnicodeError):
                process_check_complete = False
                continue

            executable = os.path.basename(command.split(" ", 1)[0]).lower()
            if executable in {"chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"} and profile_dir in command:
                active_process_found = True
                break

        if active_process_found:
            print("[DEBUG] Active Chromium process found; keeping singleton locks")
            return
        if not process_check_complete:
            print("[DEBUG] Could not complete active Chromium process check; keeping singleton locks")
            return

        print("[DEBUG] No active Chromium process found; cleaning stale singleton locks")
        for filename in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = os.path.join(profile_dir, filename)
            if not os.path.lexists(lock_path):
                print(f"[DEBUG] No stale lock found: {filename}")
                continue
            try:
                os.remove(lock_path)
                print(f"[DEBUG] Removed stale lock: {filename}")
            except OSError as exc:
                print(f"[DEBUG] Could not remove stale lock {filename}: {exc}")

    def _log_profile_startup_preflight(self, profile_dir, runtime_dir):
        print("[DEBUG] Persistent Chrome profile startup preflight:")
        print(f"[DEBUG] Profile directory exists: {os.path.isdir(profile_dir)}")
        print(f"[DEBUG] Profile directory writable: {os.access(profile_dir, os.W_OK)}")

        default_dir = os.path.join(profile_dir, "Default")
        local_state_path = os.path.join(profile_dir, "Local State")
        preferences_path = os.path.join(default_dir, "Preferences")
        print(f"[DEBUG] Default profile directory exists: {os.path.isdir(default_dir)}")
        print(f"[DEBUG] Local State exists: {os.path.isfile(local_state_path)}")
        print(f"[DEBUG] Preferences exists: {os.path.isfile(preferences_path)}")

        for filename in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = os.path.join(profile_dir, filename)
            print(f"[DEBUG] {filename} exists: {os.path.lexists(lock_path)}")

        for label, path in (
            ("profile filesystem", profile_dir),
            ("/tmp/chrome filesystem", runtime_dir),
        ):
            try:
                available_bytes = shutil.disk_usage(path).free
                print(f"[DEBUG] Available disk space for {label}: {available_bytes} bytes")
            except OSError as exc:
                print(f"[DEBUG] Available disk space for {label}: unavailable ({exc})")

        for label, path in (
            ("Local State", local_state_path),
            ("Preferences", preferences_path),
        ):
            if not os.path.isfile(path):
                continue
            try:
                if os.path.getsize(path) == 0:
                    print(f"[DEBUG] Possible corruption indicator: {label} is empty")
                    continue
                with open(path, "r", encoding="utf-8") as profile_file:
                    json.load(profile_file)
                print(f"[DEBUG] {label} JSON is readable")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                print(f"[DEBUG] Possible corruption indicator: {label} is not valid JSON ({exc})")

    def _create_driver(self):
        profile_dir = os.path.abspath(VSTUDY_PROFILE_DIR)
        runtime_dir = os.path.abspath(CHROME_RUNTIME_DIR)
        os.makedirs(profile_dir, exist_ok=True)
        os.makedirs(runtime_dir, exist_ok=True)

        if not os.access(profile_dir, os.W_OK):
            raise PermissionError(f"Chrome profile directory is not writable: {profile_dir}")
        if not os.access(runtime_dir, os.W_OK):
            raise PermissionError(f"Chrome runtime directory is not writable: {runtime_dir}")

        chromium_binary = next(
            (shutil.which(name) for name in ("chromium", "chromium-browser", "google-chrome") if shutil.which(name)),
            None,
        )
        chromedriver_binary = shutil.which("chromedriver")
        self._log_chrome_diagnostics(chromium_binary, chromedriver_binary)
        options = self._build_chrome_options(profile_dir, runtime_dir, chromium_binary)
        chromedriver_log_path = None
        try:
            if chromedriver_binary:
                log_fd, chromedriver_log_path = tempfile.mkstemp(
                    prefix="chromedriver-",
                    suffix=".log",
                    dir=runtime_dir,
                )
                os.close(log_fd)
            service = (
                Service(chromedriver_binary, log_output=chromedriver_log_path)
                if chromedriver_binary
                else None
            )
            self._cleanup_stale_profile_locks(profile_dir)
            self._log_profile_startup_preflight(profile_dir, runtime_dir)
            self.driver = webdriver.Chrome(service=service, options=options)
        except WebDriverException as exc:
            print("[✗] ChromeDriver failed to start Chromium with the persistent profile")
            print(f"[DEBUG] Chrome user-data directory: {profile_dir}")
            print(f"[DEBUG] Chrome runtime/cache directory: {runtime_dir}")
            print(f"[DEBUG] Chrome options: {options.arguments}")
            print(f"[DEBUG] ChromeDriver exception message: {str(exc) or '<empty>'}")
            print(f"[DEBUG] Complete ChromeDriver exception: {exc!r}")
            print("[DEBUG] ChromeDriver traceback:")
            print(traceback.format_exc())
            if chromedriver_log_path:
                try:
                    with open(chromedriver_log_path, "r", encoding="utf-8", errors="replace") as log_file:
                        print("[DEBUG] ChromeDriver log:")
                        print(log_file.read())
                except OSError as log_exc:
                    print(f"[DEBUG] Could not read ChromeDriver log: {log_exc}")
            raise
        finally:
            if chromedriver_log_path:
                try:
                    os.remove(chromedriver_log_path)
                except OSError:
                    pass

        print(f"[*] Using Chrome user-data directory: {profile_dir}")
        return self.driver

    def _is_dashboard_visible(self, driver):
        url = driver.current_url.lower()
        if "/dashboard" in url:
            return True
        title = (driver.title or "").lower()
        return "dashboard" in title

    def _is_authentication_required(self, driver):
        current_url = driver.current_url.lower()
        if "/dashboard" in current_url or "/profile" in current_url:
            return False

        page_text = (driver.page_source or "").lower()
        login_markers = [
            "continue with google",
            "sign in with google",
            "welcome back",
            "login required",
        ]
        if any(marker in page_text for marker in login_markers):
            return True

        if "login" in current_url or "auth" in current_url:
            return True

        return False

    def _log_profile_page_failure(self, driver):
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            page_text = driver.page_source or ""
        normalized_text = page_text.lower()
        current_url = driver.current_url
        normalized_url = current_url.lower()
        login_present = "login" in normalized_url or "login" in normalized_text
        google_present = "google" in normalized_url or "google" in normalized_text
        dashboard_present = "/dashboard" in normalized_url or "dashboard" in normalized_text
        print(f"[DEBUG] Profile failure login present: {login_present}")
        print(f"[DEBUG] Profile failure Google present: {google_present}")
        print(f"[DEBUG] Profile failure dashboard present: {dashboard_present}")
        print(f"[DEBUG] Profile failure URL: {current_url}")
        print(f"[DEBUG] Profile failure title: {driver.title}")
        print(f"[DEBUG] Profile failure page text: {page_text[:1000]}")

    def ensure_authenticated(self, driver=None):
        driver = driver or self.driver
        if driver is None:
            driver = self._create_driver()

        print("[*] Opening VStudy...")
        driver.get(VSTUDY_URL)

        try:
            WebDriverWait(driver, self.wait_timeout).until(
                lambda d: "vstudy.saveetha.com" in d.current_url.lower()
            )
        except TimeoutException:
            print("[✗] VStudy unavailable or timed out")
            raise

        if self._is_authentication_required(driver):
            if UNATTENDED:
                raise AuthenticationRequiredError(
                    "VStudy authentication is required, but UNATTENDED mode cannot perform human login. "
                    "Authenticate the persistent Chrome profile before deployment."
                )
            print("[!] Authentication required.")
            print("Complete Google login manually in this browser.")
            input("\nPress ENTER after the dashboard is visible... ")

            WebDriverWait(driver, 60).until(self._is_dashboard_visible)
            print("[✓] VStudy authenticated")
            return True

        if self._is_dashboard_visible(driver):
            print("[✓] VStudy authenticated")
        return True

    def open_profile_page(self, driver=None):
        driver = driver or self.driver
        if driver is None:
            raise RuntimeError("No browser driver available")

        print("[*] Opening profile...")

        def _profile_page_ready(d):
            url = (d.current_url or "").lower()
            page = (d.page_source or "").lower()
            if "/dashboard/profile" not in url:
                return False
            markers = [
                "student profile",
                "view details",
                "academic progress",
                "student progress",
                "profile summary",
            ]
            return any(marker in page for marker in markers)

        for attempt in range(2):
            driver.get(PROFILE_URL)

            if self._is_authentication_required(driver):
                self._log_profile_page_failure(driver)
                raise AuthenticationRequiredError(
                    "VStudy authentication is required before the profile page can be opened."
                )

            try:
                WebDriverWait(driver, self.wait_timeout).until(_profile_page_ready)
                print("[✓] Profile page loaded")
                return True
            except TimeoutException:
                if "/dashboard" in (driver.current_url or "").lower() and attempt == 0:
                    print("[!] Profile route redirected to the dashboard; using the Profile control...")
                    profile_button = WebDriverWait(driver, self.wait_timeout).until(
                        lambda d: d.find_element(By.XPATH, "//button[.//img[@alt='Profile']]")
                    )
                    try:
                        profile_button.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", profile_button)
                    try:
                        WebDriverWait(driver, self.wait_timeout).until(_profile_page_ready)
                        print("[✓] Profile page loaded")
                        return True
                    except TimeoutException:
                        pass
                break

        self._log_profile_page_failure(driver)
        print("[✗] Profile page unavailable")
        raise TimeoutException("VStudy profile page did not load")

    def _find_view_details_element(self, driver):
        actionable = driver.find_elements(
            By.XPATH,
            "//*[self::button or self::a][normalize-space()='View Details']"
        )
        for element in actionable:
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except StaleElementReferenceException:
                continue

        candidates = driver.find_elements(
            By.XPATH,
            "//*[self::button or self::a or self::div or self::span or self::p or self::li]"
        )
        matches = []
        for element in candidates:
            try:
                text = self._safe_text(element)
                if text.lower() == "view details":
                    matches.append((element, text))
            except StaleElementReferenceException:
                continue
        for element, _ in matches:
            try:
                if element.is_displayed():
                    return element
            except StaleElementReferenceException:
                continue
        return matches[0][0] if matches else None

    def _debug_view_details_lookup(self, driver):
        print(f"[DEBUG] URL before View Details: {driver.current_url}")
        print(f"[DEBUG] Title before View Details: {driver.title}")
        clickable = driver.find_elements(By.XPATH, "//*[self::button or self::a or self::div or self::span or self::p or self::li]")
        print(f"[DEBUG] Clickable elements found: {len(clickable)}")
        matches = []
        for element in clickable:
            text = self._safe_text(element)
            if 'view details' in text.lower():
                matches.append((element.tag_name, text))
        if matches:
            print(f"[DEBUG] View Details matches: {matches[:10]}")
        else:
            print("[DEBUG] No View Details matches on this page")
        return clickable, matches

    def _ensure_profile_page_ready(self, driver):
        current_url = (driver.current_url or "").lower()
        page_source = driver.page_source or ""
        markers = [
            "student profile",
            "view details",
            "academic progress",
            "student progress",
            "profile summary",
        ]

        if "/dashboard/profile" in current_url and any(marker in page_source.lower() for marker in markers):
            print("[✓] Profile page already loaded")
            return True

        print("[*] Ensuring browser is on the profile page before View Details lookup...")
        self.open_profile_page(driver)
        try:
            WebDriverWait(driver, self.wait_timeout).until(
                lambda d: "/dashboard/profile" in (d.current_url or "").lower()
                and any(marker in (d.page_source or "").lower() for marker in markers)
            )
        except TimeoutException:
            print("[✗] Profile page did not finish loading")
            raise
        print("[✓] Profile page loaded")
        return True

    def open_detailed_profile(self, driver=None):
        driver = driver or self.driver
        if driver is None:
            raise RuntimeError("No browser driver available")

        self._ensure_profile_page_ready(driver)

        print('[*] Looking for "View Details"...')
        try:
            self._debug_view_details_lookup(driver)
            view_details = WebDriverWait(driver, 20).until(
                lambda d: self._find_view_details_element(d)
            )
            if view_details is None:
                raise RuntimeError('[✗] "View Details" not found')
        except Exception as exc:
            if SAVE_DEBUG_ARTIFACTS:
                screenshot_path = os.path.join(os.getcwd(), 'vstudy_view_details_missing.png')
                try:
                    driver.save_screenshot(screenshot_path)
                    print(f"[DEBUG] Saved missing View Details screenshot to {screenshot_path}")
                except Exception:
                    pass
            print(f"[DEBUG] URL when View Details failed: {driver.current_url}")
            print(f"[DEBUG] Title when View Details failed: {driver.title}")
            raise

        print('[✓] "View Details" found')
        original_url = driver.current_url
        print('[*] Clicking "View Details"...')
        try:
            view_details.click()
        except Exception:
            driver.execute_script("arguments[0].click();", view_details)

        def _detailed_page_ready(d):
            page_text = (d.page_source or "").lower()
            course_text = any(x in page_text for x in ["student progress", "course name", "applied mathematics", "course gpa"])
            url_changed = d.current_url != original_url
            return url_changed or course_text

        try:
            WebDriverWait(driver, 25).until(_detailed_page_ready)
        except TimeoutException:
            print('[✗] Detailed profile did not load after clicking "View Details"')
            raise

        final_url = driver.current_url
        print(f"[✓] Detailed profile opened: {final_url}")
        return True

    def _list_student_progress_filters(self, driver):
        print("[*] Listing Student Progress filter buttons...")
        filters = []
        for el in driver.find_elements(By.XPATH, "//*[self::button or self::a or self::div or self::span or self::li]"):
            text = self._safe_text(el)
            if not text:
                continue
            if re.search(r"^(All|University Core|University Elective|Program Core|Other)(\s*\d+)?$", text.strip(), flags=re.IGNORECASE):
                attrs = {key: el.get_attribute(key) for key in ['class', 'aria-selected', 'data-state', 'role', 'id', 'tabindex', 'type'] if el.get_attribute(key) is not None}
                filters.append((el, text, attrs))
                print(f"[FILTER] tag={el.tag_name} text={text!r} attrs={attrs}")
                try:
                    html = el.get_attribute('outerHTML')
                    print(f"[FILTER_HTML] {html[:300]}")
                except Exception:
                    pass
        return filters

    def _find_student_progress_all_filter(self, driver):
        candidates = []
        for el in driver.find_elements(By.XPATH, "//*[self::button or self::a or self::div or self::span or self::li]"):
            text = self._safe_text(el)
            if not text:
                continue
            if re.fullmatch(r"All\s*17", text.strip(), flags=re.IGNORECASE):
                candidates.append(el)
            elif text.strip().lower() == "all" and any(ch.isdigit() for ch in text):
                candidates.append(el)
        for el in candidates:
            if el.is_displayed():
                return el

        for el in driver.find_elements(By.XPATH, "//*[@role='tab' or self::button or self::a or self::div or self::span]"):
            text = self._safe_text(el)
            if text and "all" in text.lower() and any(ch.isdigit() for ch in text):
                if el.is_displayed():
                    return el
        return None

    def _select_all_courses_filter(self, driver):
        print("[*] Selecting \"All\" courses...")
        self._list_student_progress_filters(driver)
        all_filter = WebDriverWait(driver, 20).until(
            lambda d: self._find_student_progress_all_filter(d)
        )
        if all_filter is None:
            print("[!] All filter not found")
            return False

        before_attrs = {key: all_filter.get_attribute(key) for key in ['class', 'aria-selected', 'data-state', 'role', 'id', 'tabindex', 'type'] if all_filter.get_attribute(key) is not None}
        print(f"[DEBUG] All filter BEFORE tag={all_filter.tag_name} text={self._safe_text(all_filter)!r} attrs={before_attrs}")

        try:
            all_filter.click()
        except Exception:
            driver.execute_script("arguments[0].click();", all_filter)

        print('[✓] "All" filter selected')
        print("[*] Waiting for all courses to render...")

        try:
            WebDriverWait(driver, 30).until(
                lambda d: self._find_student_progress_all_filter(d) is not None and self._find_student_progress_all_filter(d).get_attribute('aria-selected') == 'true'
                or 'applied mathematics' in (d.page_source or '').lower()
            )
        except TimeoutException:
            print("[!] UI did not confirm All selection")
            return False

        after_filter = self._find_student_progress_all_filter(driver)
        after_text = self._safe_text(after_filter) if after_filter is not None else None
        after_attrs = {}
        if after_filter is not None:
            after_attrs = {key: after_filter.get_attribute(key) for key in ['class', 'aria-selected', 'data-state', 'role', 'id', 'tabindex', 'type'] if after_filter.get_attribute(key) is not None}
        print(f"[DEBUG] All filter AFTER tag={after_filter.tag_name if after_filter else None} text={after_text!r} attrs={after_attrs}")

        if SAVE_DEBUG_ARTIFACTS:
            screenshot_path = os.path.join(os.getcwd(), 'vstudy_after_all.png')
            driver.save_screenshot(screenshot_path)
            print(f"[*] Screenshot saved to {screenshot_path}")

        rows = driver.find_elements(By.XPATH, "//tr")
        first_rows = []
        for row in rows[:12]:
            text = self._safe_text(row)
            if text:
                first_rows.append(text)
        print(f"[DEBUG] First visible rows after All: {first_rows[:8]}")

        try:
            WebDriverWait(driver, 30).until(
                lambda d: len(self._collect_unique_courses(d)) >= 17
            )
            return True
        except TimeoutException:
            print("[!] All courses did not render after selecting All")
            return False

    def _save_debug_html(self, driver, filename="vstudy_debug.html"):
        if not SAVE_DEBUG_ARTIFACTS:
            return None
        html = driver.page_source or ""
        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"[*] Saved debug HTML to {path}")
        return path

    def _debug_page_state(self, driver):
        html = driver.page_source or ""
        tables = driver.find_elements(By.TAG_NAME, "table")
        tbodies = driver.find_elements(By.TAG_NAME, "tbody")
        rows = driver.find_elements(By.XPATH, "//tr")
        codes = [
            "UBA06", "UBA30", "BTA01", "UBA04", "UBA54", "UBA49", "UBA01",
            "UBA48", "UBA53", "UBA28"
        ]
        print(f"[DEBUG] URL: {driver.current_url}")
        print(f"[DEBUG] Title: {driver.title}")
        print(f"[DEBUG] tables={len(tables)}")
        print(f"[DEBUG] tbody={len(tbodies)}")
        print(f"[DEBUG] tr={len(rows)}")
        for code in codes:
            status = "FOUND" if code in html else "MISSING"
            print(f"[DEBUG] {code}: {status}")
        return len(tables), len(tbodies), len(rows)

    def _find_scroll_containers(self, driver):
        containers = []
        selectors = [
            "//*[self::div or self::table or self::tbody or self::section][contains(@style, 'overflow') or contains(@class, 'overflow') or contains(@style, 'scroll') or contains(@class, 'scroll') or contains(@class, 'virtual') or contains(@class, 'table-container') or @role='table' or @role='grid']",
            "//div[contains(@class, 'overflow') or contains(@class, 'scroll') or contains(@class, 'virtual') or contains(@class, 'table') or contains(@class, 'content')]"
        ]
        for selector in selectors:
            for el in driver.find_elements(By.XPATH, selector):
                if el.is_displayed():
                    try:
                        overflow = driver.execute_script(
                            "return arguments[0].scrollHeight > arguments[0].clientHeight;",
                            el,
                        )
                    except Exception:
                        overflow = False
                    if overflow:
                        containers.append(el)
        unique = []
        seen = set()
        for el in containers:
            key = id(el)
            if key not in seen:
                seen.add(key)
                unique.append(el)
        return unique

    def _scroll_dynamic_content(self, driver):
        containers = self._find_scroll_containers(driver)
        if not containers:
            return False

        scrolled = False
        for container in containers[:8]:
            try:
                current = driver.execute_script("return arguments[0].scrollTop;", container)
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
                WebDriverWait(driver, 1).until(lambda d: driver.execute_script("return arguments[0].scrollTop", container) != current)
                scrolled = True
            except Exception:
                pass
        return scrolled

    def _collect_unique_courses(self, driver):
        unique = {}
        for row in driver.find_elements(By.XPATH, "//tr"):
            parsed = self._extract_row(row)
            if not parsed or not parsed.get("course_code"):
                continue
            code = parsed["course_code"].upper().strip()
            if not code:
                continue
            unique[code] = parsed
        return unique

    def _normalize_text(self, value):
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip().lower()

    def _safe_text(self, element):
        if element is None:
            return ""
        try:
            return re.sub(r"\s+", " ", (element.text or "")).strip()
        except StaleElementReferenceException:
            return ""

    def _extract_course_name_code(self, cell):
        text = self._safe_text(cell)
        if not text:
            return "", ""

        divs = cell.find_elements(By.TAG_NAME, "div")
        if len(divs) >= 2:
            course_name = self._safe_text(divs[0]).strip()
            course_code = self._safe_text(divs[1]).strip()
            if course_name or course_code:
                return course_name, course_code

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]

        parts = re.split(r"\s+", text)
        if len(parts) >= 2:
            last_part = parts[-1]
            if len(last_part) <= 15 and re.fullmatch(r"[A-Za-z0-9]+", last_part):
                return " ".join(parts[:-1]), last_part

        return text, ""

    def _table_headers(self, table):
        headers = []
        for row in table.find_elements(By.XPATH, ".//tr")[:10]:
            cells = row.find_elements(By.XPATH, ".//th | .//td")
            for cell in cells:
                text = self._normalize_text(self._safe_text(cell))
                if text:
                    headers.append(text)
        return headers

    def _row_text(self, row):
        cells = row.find_elements(By.XPATH, ".//th | .//td")
        text_parts = []
        for cell in cells:
            cleaned = self._safe_text(cell)
            if cleaned:
                text_parts.append(self._normalize_text(cleaned))
        return " ".join(text_parts)

    def _looks_like_course_table(self, table):
        headers = self._table_headers(table)
        joined = " ".join(headers)
        if not headers:
            return False

        has_course_name = any(("course" in h and "name" in h) for h in headers) or "course name" in joined
        has_type = "type" in joined
        has_status = "status" in joined or any("status" in h for h in headers)
        has_grade = "grade" in joined
        has_attendance = "attendance" in joined
        has_assessments = "assessments" in joined
        has_other_reqs = "other req" in joined or "other reqs" in joined
        has_course_gpa = "course gpa" in joined or "gpa" in joined

        if (
            has_course_name and has_type and has_status and has_grade and
            (has_attendance or has_assessments or has_other_reqs or has_course_gpa)
        ):
            return True

        rows = table.find_elements(By.XPATH, ".//tr")
        if len(rows) < 2:
            return False

        all_text = " ".join(self._row_text(row) for row in rows[:10])
        basics = [
            "course name" in all_text,
            "type" in all_text,
            "status" in all_text,
            "grade" in all_text,
            "attendance" in all_text,
            "assessments" in all_text,
            "course gpa" in all_text or "gpa" in all_text,
        ]
        return sum(basics) >= 4 and ("completed" in all_text or "active" in all_text or "applied mathematics" in all_text)

    def _find_student_progress_table(self, driver):
        tables = driver.find_elements(By.TAG_NAME, "table")
        print(f"[*] Found {len(tables)} table(s)")

        for idx, table in enumerate(tables):
            rows = table.find_elements(By.XPATH, ".//tr")
            headers = self._table_headers(table)
            print(f"[*] Inspecting table {idx}...")
            print(f"[TABLE {idx}] rows={len(rows)}")
            print(f"[TABLE {idx}] headers={headers[:20]}")

            if self._looks_like_course_table(table):
                print("[✓] Student Progress table found")
                return table

        all_rows = driver.find_elements(By.XPATH, "//tr")
        print(f"[*] Inspecting {len(all_rows)} row(s) across the page")
        for row in all_rows:
            text = self._row_text(row)
            if text and any(k in text for k in ["course name", "status", "grade", "attendance", "assessments", "course gpa", "applied mathematics"]):
                print(f"[DEBUG] Candidate row: {text[:200]}")

        for row in all_rows:
            text = self._row_text(row)
            norm = self._normalize_text(text)
            if not norm:
                continue
            if (
                "course name" in norm
                or ("course" in norm and "status" in norm)
                or "applied mathematics" in norm
            ):
                ancestor_tables = row.find_elements(By.XPATH, "./ancestor::table")
                if ancestor_tables:
                    print("[✓] Student Progress table found via row fallback")
                    return ancestor_tables[0]

        print("[✗] Course table not found")
        return None

    def _extract_row(self, row):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) < 2:
            return None

        course_name = ""
        course_code = ""
        first_cell = cells[0]
        if first_cell:
            course_name, course_code = self._extract_course_name_code(first_cell)

        remaining = [self._safe_text(cell) for cell in cells[1:9]]
        while len(remaining) < 7:
            remaining.append("")

        result = {
            "course_name": course_name,
            "course_code": course_code,
            "course_type": remaining[0] if len(remaining) > 0 else "",
            "status": remaining[1] if len(remaining) > 1 else "",
            "grade": remaining[2] if len(remaining) > 2 else "",
            "attendance": remaining[3] if len(remaining) > 3 else "",
            "assessments": remaining[4] if len(remaining) > 4 else "",
            "other_requirements": remaining[5] if len(remaining) > 5 else "",
            "course_gpa": remaining[6] if len(remaining) > 6 else "",
        }

        if not result["course_name"] and not result["course_code"]:
            return None
        if not any(value.strip() for value in result.values()):
            return None
        return result

    def scrape_course_results(self, driver=None):
        driver = driver or self.driver
        if driver is None:
            driver = self._create_driver()

        self.ensure_authenticated(driver)
        self.open_profile_page(driver)
        self.open_detailed_profile(driver)
        self._save_debug_html(driver)
        self._debug_page_state(driver)
        self._select_all_courses_filter(driver)

        print("[*] Detecting Student Progress table...")
        WebDriverWait(driver, 30).until(
            lambda d: any(
                len(table.find_elements(By.XPATH, ".//tr")) >= 2
                for table in d.find_elements(By.TAG_NAME, "table")
            )
            or "student progress" in (d.page_source or "").lower()
            or "applied mathematics" in (d.page_source or "").lower()
            or any(
                "course name" in (cell.text or "").lower() or "attendance" in (cell.text or "").lower()
                for cell in d.find_elements(By.XPATH, "//td | //th")
            )
        )

        found_courses = {}
        for _ in range(10):
            current_courses = self._collect_unique_courses(driver)
            if len(current_courses) > len(found_courses):
                found_courses = current_courses
            if len(found_courses) >= 17:
                break
            if not self._scroll_dynamic_content(driver):
                break

        if len(found_courses) < 17:
            table = self._find_student_progress_table(driver)
            if table is not None:
                rows = table.find_elements(By.XPATH, ".//tr")
                for row in rows[1:]:
                    parsed = self._extract_row(row)
                    if parsed and parsed.get("course_code"):
                        found_courses[parsed["course_code"].upper()] = parsed

        if not found_courses:
            table = self._find_student_progress_table(driver)
            if table is None:
                print("[✗] Course table not found")
                return []
            rows = table.find_elements(By.XPATH, ".//tr")
            print(f"[*] Found {len(rows) - 1} course rows")
            for row in rows[1:]:
                parsed = self._extract_row(row)
                if parsed:
                    found_courses[parsed["course_code"].upper()] = parsed

        results = list(found_courses.values())
        missing = []
        expected = [
            "UBA06", "UBA30", "BTA01", "UBA04", "UBA54", "UBA49", "UBA01",
            "UBA48", "UBA53", "UBA28", "CSA02", "CSA07", "CSA05", "CSA11",
            "ECA47", "CSA09", "CSA08"
        ]
        actual_codes = {item.get("course_code", "").upper() for item in results}
        for code in expected:
            if code not in actual_codes:
                missing.append(code)

        if len(results) < 17:
            print("[!] Expected 17 courses but found %s" % len(results))
            print("[!] Missing course codes:")
            for code in missing:
                print(f"    {code}")

        print(f"[✓] Total unique courses found: {len(results)}")
        for item in results:
            course_name = item.get('course_name', '').strip()
            course_code = item.get('course_code', '').strip()
            course_type = item.get('course_type', '').strip() or ""
            status = item.get('status', '').strip() or ""
            grade = item.get('grade', '').strip() or ""
            course_gpa = item.get('course_gpa', '').strip() or ""
            print(f"[✓] {course_name} | {course_code} | {course_type} | {status} | {grade} | {course_gpa}")

        if results:
            return results

        print("[✗] Course table not found")
        return []

    def scrape_results(self):
        try:
            if self.driver is None:
                self.driver = self._create_driver()
            results = self.scrape_course_results(self.driver)
            return results
        except WebDriverException as exc:
            print(f"[✗] ChromeDriver error: {exc}")
            raise
        except TimeoutException as exc:
            print(f"[✗] Network timeout while loading VStudy: {exc}")
            raise
        except Exception as exc:
            print(f"[✗] Scrape error: {exc}")
            raise
        finally:
            self.close()

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
                print("[✓] Browser closed")
            except Exception:
                pass
            finally:
                self.driver = None
