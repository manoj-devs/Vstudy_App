# VStudy Course Monitor

This project monitors the Saveetha VStudy portal, collects the course table, detects new course results by course code, stores the history in SQLite, and sends Telegram notifications for new results.

## Features

- Persistent Chromium profile for Google/VStudy authentication
- VStudy profile page and **View Details** flow
- Student Progress filtering for **All 17** courses
- Course extraction and deduplication by course code
- SQLite result and notification history
- Telegram notifications for newly detected results
- Runs continuously on Railway with a persistent /data volume
- Automatic retry/recovery for transient VStudy navigation and Selenium failures
- Chromium startup hardening for persistent-profile restarts
- Container heartbeat watchdog so a stuck monitor can exit and be restarted
- Tini init process to reap Chromium child processes and prevent zombie-process buildup

## Monitoring flow

```text
Railway service
    │
    ├── persistent /data volume
    │      ├── vstudy_chrome_profile/
    │      ├── results.db
    │      └── monitor.heartbeat
    │
    └── monitor.py
           │
           ├── Selenium + Chromium
           │      ├── Open VStudy
           │      ├── Open profile
           │      ├── View Details
           │      └── Select All 17
           │
           ├── SQLite comparison
           │
           └── Telegram notification
```

The monitor checks VStudy every **900 seconds (15 minutes)** in the current Railway deployment.

## Reliability and recovery

The scraper is designed to recover from temporary browser or portal problems without losing the authenticated profile.

### Browser startup hardening

The scraper checks the persistent profile before launching Chromium:

- Detects active Chromium processes using the same profile
- Removes stale Chromium singleton locks when safe
- Verifies the profile directory is writable
- Verifies Local State and Preferences are readable
- Uses a dedicated runtime/cache directory under /tmp/chrome
- Uses headless Chromium with container-safe startup flags

### Automatic scrape retries

A scrape can fail because VStudy temporarily loads the wrong dashboard page or Chromium has a transient WebDriver/browser failure.

The scraper now performs up to **3 fresh browser attempts**. Between failed attempts it:

1. Closes the failed browser session
2. Waits 5 seconds
3. Starts a new browser session
4. Repeats the VStudy navigation and scrape

The monitor only sends a Telegram scraping-error notification after the retry attempts are exhausted.

### Container process management

The Railway container runs through **Tini**:

```text
PID 1: tini
   └── python monitor.py
        └── ChromeDriver / Chromium
```

Tini reaps exited Chromium child processes so zombie processes do not accumulate and exhaust Railway's process limit.

### Heartbeat watchdog

monitor.py writes /data/monitor.heartbeat continuously. If the heartbeat becomes stale beyond the configured timeout, the monitor exits so the container platform can restart it.

## Required environment variables

Create a .env file for local use, or configure these as Railway environment variables:

```text
VSTUDY_URL=https://vstudy.saveetha.com/
VSTUDY_PROFILE_DIR=./vstudy_chrome_profile
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
DATABASE_NAME=./results.db
```

The Railway deployment additionally uses persistent container paths such as:

```text
VSTUDY_PROFILE_DIR=/data/vstudy_chrome_profile
DATABASE_NAME=/data/results.db
HEARTBEAT_FILE=/data/monitor.heartbeat
CHROME_RUNTIME_DIR=/tmp/chrome
```

Never commit real Telegram tokens, chat IDs, Google credentials, or other secrets.

## Project structure

```text
.
├── config.py
├── monitor.py
├── monitor_selenium.py
├── vstudy_scraper.py
├── results_db.py
├── telegram_notifier.py
├── test_vstudy_login.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── README.md
└── .github/
    └── workflows/
```

## How it works

1. Open the VStudy portal using the persistent Chromium profile.
2. Navigate to the student profile page.
3. Find and click **View Details**.
4. Select the **All 17** filter in Student Progress.
5. Parse the course table and collect unique course codes.
6. Compare the current results with SQLite history.
7. Save new results.
8. Send Telegram notifications only for newly detected course results.
9. Write heartbeat data and wait 15 minutes before the next check.

On the first successful production run, the existing VStudy state is saved as a baseline without sending notifications for old results.

## Run locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the monitor:

```bash
python monitor.py
```

Run a one-time scraper check:

```bash
python test_vstudy_login.py
```

A successful one-time check prints the number of courses found and exits normally.

## Railway deployment

The production monitor currently runs as an always-on Railway service.

### Persistent data

The Railway service uses the vstudy-data persistent volume mounted at:

```text
/data
```

This volume stores:

```text
/data/vstudy_chrome_profile/
/data/results.db
/data/monitor.heartbeat
```

The authenticated Chromium profile and SQLite history therefore survive normal redeployments and container restarts.

**Do not delete or recreate the /data volume** unless you intentionally want to remove the saved authentication profile and monitoring history.

### Current production configuration

Typical production values include:

```text
HEADLESS=true
UNATTENDED=true
RUN_FOREVER=true
CHECK_INTERVAL=900
VSTUDY_PROFILE_DIR=/data/vstudy_chrome_profile
DATABASE_NAME=/data/results.db
HEARTBEAT_FILE=/data/monitor.heartbeat
CHROME_RUNTIME_DIR=/tmp/chrome
```

Telegram credentials are configured as Railway secrets.

### Production architecture

```text
Laptop OFF
   ↓
Railway
   ↓
Persistent Chromium profile
   ↓
VStudy every 15 minutes
   ↓
SQLite comparison
   ↓
Telegram alert for new results
```

This keeps the monitor running even when the development laptop is powered off.

## Self-hosted backup

The repository also keeps manual GitHub Actions workflows for self-hosted testing/backup scenarios.

These workflows are not the primary production runtime. Railway is the current production monitor.

## Notes

- The project intentionally keeps a persistent browser profile because VStudy is accessed through an authenticated web session.
- Telegram notifications are optional and require a valid bot token and chat ID.
- SQLite preserves result and notification history and deduplicates by course code.
- The monitor is designed to recover from transient VStudy and Chromium failures, but no browser automation can guarantee zero failures forever if the upstream portal or authentication requirements change.
- Do not run multiple Selenium processes against the same persistent Chromium profile at the same time.
- Do not store passwords, Telegram tokens, Google session data, or CAPTCHA workarounds in source control.
