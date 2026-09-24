import os
import threading
import time
from pathlib import Path

from config import CHECK_INTERVAL, HEARTBEAT_FILE, HEARTBEAT_TIMEOUT, RUN_FOREVER
from monitor_selenium import VStudyMonitor


def write_heartbeat():
    heartbeat_path = Path(HEARTBEAT_FILE)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(str(time.time()), encoding="ascii")


def stop_if_heartbeat_is_stale():
    check_interval = max(5, min(60, HEARTBEAT_TIMEOUT // 3))
    while True:
        try:
            heartbeat_age = time.time() - Path(HEARTBEAT_FILE).stat().st_mtime
            if heartbeat_age > HEARTBEAT_TIMEOUT:
                print(
                    f"[✗] Heartbeat is stale ({heartbeat_age:.0f}s); exiting for Docker restart",
                    flush=True,
                )
                os._exit(1)
        except FileNotFoundError:
            pass
        time.sleep(check_interval)


if __name__ == "__main__":
    monitor = VStudyMonitor()
    write_heartbeat()
    threading.Thread(target=stop_if_heartbeat_is_stale, daemon=True).start()
    while True:
        write_heartbeat()
        try:
            monitor.run()
        except Exception as exc:
            print(f"[✗] Monitor cycle failed: {exc}")
            if not RUN_FOREVER:
                raise
        write_heartbeat()
        if not RUN_FOREVER:
            break
        print(f"[*] Next check in {CHECK_INTERVAL} seconds")
        time.sleep(CHECK_INTERVAL)
