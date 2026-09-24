import json
import os
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken


STATE_VERSION = 1
ALLOWED_COOKIE_DOMAIN = "saveetha.com"
SAFE_COOKIE_FIELDS = {
    "name",
    "value",
    "domain",
    "path",
    "secure",
    "httpOnly",
    "sameSite",
    "expires",
}


def _fernet():
    key = os.getenv("VSTUDY_STATE_KEY", "").strip()
    if not key:
        raise RuntimeError("VSTUDY_STATE_KEY is required")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("VSTUDY_STATE_KEY is not a valid Fernet key") from exc


def _is_allowed_domain(domain):
    normalized = str(domain or "").lower().lstrip(".")
    return normalized == ALLOWED_COOKIE_DOMAIN or normalized.endswith(f".{ALLOWED_COOKIE_DOMAIN}")


def _is_allowed_origin(origin):
    parsed = urlparse(origin)
    return parsed.scheme == "https" and _is_allowed_domain(parsed.hostname)


def _current_origin(driver):
    origin = driver.execute_script("return window.location.origin;")
    if not _is_allowed_origin(origin):
        raise RuntimeError("The browser is not on an HTTPS saveetha.com origin")
    return origin


def _filtered_cookies(driver):
    response = driver.execute_cdp_cmd("Network.getAllCookies", {})
    cookies = []
    for cookie in response.get("cookies", []):
        if not _is_allowed_domain(cookie.get("domain")):
            continue
        cookies.append({key: value for key, value in cookie.items() if key in SAFE_COOKIE_FIELDS})
    return cookies


def export_auth_state(driver, output_path):
    origin = _current_origin(driver)
    storage = driver.execute_script(
        """
        const readStorage = (storage) => Object.fromEntries(
            Object.keys(storage).map((key) => [key, storage.getItem(key)])
        );
        return {
            localStorage: readStorage(window.localStorage),
            sessionStorage: readStorage(window.sessionStorage),
        };
        """
    )
    state = {
        "version": STATE_VERSION,
        "origins": {
            origin: {
                "localStorage": storage.get("localStorage", {}),
                "sessionStorage": storage.get("sessionStorage", {}),
            }
        },
        "cookies": _filtered_cookies(driver),
    }
    encrypted = _fernet().encrypt(json.dumps(state, separators=(",", ":")).encode("utf-8"))
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encrypted)
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    return len(state["cookies"]), len(state["origins"])


def load_auth_state(input_path):
    try:
        plaintext = _fernet().decrypt(Path(input_path).read_bytes())
        state = json.loads(plaintext.decode("utf-8"))
    except InvalidToken as exc:
        raise RuntimeError("Authentication state could not be decrypted with VSTUDY_STATE_KEY") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Authentication state file is unreadable or invalid") from exc

    if state.get("version") != STATE_VERSION:
        raise RuntimeError("Unsupported authentication state version")
    if not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), dict):
        raise RuntimeError("Authentication state has an invalid structure")
    return state


def inject_auth_state(driver, state):
    driver.execute_cdp_cmd("Network.enable", {})
    cookies = []
    for cookie in state["cookies"]:
        if not _is_allowed_domain(cookie.get("domain")):
            raise RuntimeError("Authentication state contains a non-saveetha cookie")
        cookies.append(cookie)
    if cookies:
        driver.execute_cdp_cmd("Network.setCookies", {"cookies": cookies})

    for origin, storage in state["origins"].items():
        if not _is_allowed_origin(origin):
            raise RuntimeError("Authentication state contains a non-saveetha origin")
        driver.get(origin)
        driver.execute_script(
            """
            const applyStorage = (storage, values) => {
                storage.clear();
                Object.entries(values || {}).forEach(([key, value]) => storage.setItem(key, value));
            };
            applyStorage(window.localStorage, arguments[0]);
            applyStorage(window.sessionStorage, arguments[1]);
            """,
            storage.get("localStorage", {}),
            storage.get("sessionStorage", {}),
        )
        driver.refresh()


def describe_state(state):
    return len(state["cookies"]), len(state["origins"])
