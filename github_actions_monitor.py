import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from telegram_notifier import TelegramNotifier
from vstudy_auth_state import inject_auth_state, load_auth_state
from vstudy_scraper import AuthenticationRequiredError, VStudyScraper


STATE_VERSION = 1
TRACKED_FIELDS = (
    "course_name",
    "course_code",
    "course_type",
    "status",
    "grade",
    "attendance",
    "assessments",
    "other_requirements",
    "course_gpa",
)


def _fernet():
    key = os.getenv("VSTUDY_STATE_KEY", "").strip()
    if not key:
        raise RuntimeError("VSTUDY_STATE_KEY is required")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("VSTUDY_STATE_KEY is not a valid Fernet key") from exc


def _normalize_result(result):
    return {
        field: str(result.get(field) or "").strip()
        for field in TRACKED_FIELDS
    }


def _load_monitor_state(path):
    destination = Path(path)
    if not destination.exists():
        return None

    try:
        payload = _fernet().decrypt(destination.read_bytes())
        state = json.loads(payload.decode("utf-8"))
    except InvalidToken as exc:
        raise RuntimeError(
            "VStudy monitor state could not be decrypted with VSTUDY_STATE_KEY"
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("VStudy monitor state is unreadable or invalid") from exc

    if state.get("version") != STATE_VERSION:
        raise RuntimeError("Unsupported VStudy monitor state version")

    if not isinstance(state.get("results"), dict):
        raise RuntimeError("VStudy monitor state has an invalid results structure")

    return state


def _save_monitor_state(path, state):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _fernet().encrypt(
        json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    destination.write_bytes(encrypted)
    try:
        destination.chmod(0o600)
    except OSError:
        pass


def _results_by_code(results):
    current = {}
    for result in results:
        normalized = _normalize_result(result)
        code = normalized["course_code"].upper()
        if code:
            current[code] = normalized
    return current


def _changed(previous, current):
    return any(
        previous.get(field, "") != current.get(field, "")
        for field in TRACKED_FIELDS
    )


def _run_scraper(auth_state_file):
    profile_dir = Path(tempfile.mkdtemp(prefix="vstudy-monitor-profile-"))
    runtime_dir = Path(tempfile.mkdtemp(prefix="vstudy-monitor-runtime-"))
    scraper = VStudyScraper()
    driver = None

    os.environ["VSTUDY_PROFILE_DIR"] = str(profile_dir)
    os.environ["CHROME_RUNTIME_DIR"] = str(runtime_dir)
    os.environ["HEADLESS"] = "true"
    os.environ["UNATTENDED"] = "true"

    try:
        state = load_auth_state(auth_state_file)
        driver = scraper._create_driver()
        inject_auth_state(driver, state)
        results = scraper.scrape_course_results(driver)
        return _results_by_code(results)
    finally:
        try:
            scraper.close()
        except Exception:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
        shutil.rmtree(profile_dir, ignore_errors=True)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _send_error_once(notifier, state, message, state_path):
    if state.get("error_warning_sent"):
        return
    if notifier.notify_error(message):
        state["error_warning_sent"] = True
        _save_monitor_state(state_path, state)


def main():
    parser = argparse.ArgumentParser(description="Run one VStudy monitor cycle on GitHub Actions")
    parser.add_argument("--auth-state", required=True, help="Encrypted VStudy auth-state file")
    parser.add_argument(
        "--monitor-state",
        default="vstudy-monitor-state.enc",
        help="Encrypted persistent monitor state",
    )
    args = parser.parse_args()

    notifier = TelegramNotifier()
    state = _load_monitor_state(args.monitor_state)

    try:
        current = _run_scraper(args.auth_state)
    except AuthenticationRequiredError as exc:
        print(f"[AUTH REQUIRED] {exc}")
        if state is None:
            state = {
                "version": STATE_VERSION,
                "results": {},
                "error_warning_sent": False,
            }
        if not state.get("auth_warning_sent"):
            if notifier.notify_error(
                "VStudy authentication is no longer valid. Refresh the GitHub authentication state."
            ):
                state["auth_warning_sent"] = True
                _save_monitor_state(args.monitor_state, state)
        return 0
    except Exception as exc:
        print(f"[WARN] VStudy monitor cycle failed: {exc}")
        if state is None:
            state = {
                "version": STATE_VERSION,
                "results": {},
                "error_warning_sent": False,
            }
        _send_error_once(
            notifier,
            state,
            "VStudy monitor failed while checking the portal. The workflow will retry automatically.",
            args.monitor_state,
        )
        return 0

    if state is None:
        baseline = {
            "version": STATE_VERSION,
            "results": current,
            "auth_warning_sent": False,
            "error_warning_sent": False,
        }
        _save_monitor_state(args.monitor_state, baseline)
        print(f"[BASELINE] Saved {len(current)} course result(s) without sending notifications")
        return 0

    previous = state.get("results", {})
    events = []

    for code, result in sorted(current.items()):
        old = previous.get(code)
        if old is None:
            events.append(("NEW", code, result))
        elif _changed(old, result):
            events.append(("CHANGED", code, result))

    next_results = dict(previous)
    notification_failures = []

    for event_type, code, result in events:
        print(f"[EVENT] {event_type} {code}")
        sent = notifier.send_notification(
            result.get("course_code", ""),
            result.get("course_name", ""),
            result.get("grade", ""),
            result.get("status", ""),
            "",
            result.get("course_type", ""),
            result.get("course_gpa", ""),
        )
        if sent:
            next_results[code] = result
        else:
            notification_failures.append(code)

    for code, result in current.items():
        if code not in notification_failures:
            next_results[code] = result

    changed_state = (
        next_results != previous
        or state.get("auth_warning_sent")
        or state.get("error_warning_sent")
    )

    state["version"] = STATE_VERSION
    state["results"] = next_results
    state["auth_warning_sent"] = False
    state["error_warning_sent"] = False

    if changed_state:
        _save_monitor_state(args.monitor_state, state)

    if not events:
        print("[OK] No new or changed course results")
    elif notification_failures:
        print(f"[WARN] Notification failed for: {', '.join(notification_failures)}")
    else:
        print(f"[ALERT] {len(events)} result change(s) notified")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
