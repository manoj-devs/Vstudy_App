# VStudy Course Monitor

This project monitors the Saveetha VStudy portal, collects the course table, detects new course results by course code, stores the history in SQLite, and sends Telegram notifications for new results.

## Features

- Persistent Chrome profile for manual Google sign-in once
- VStudy profile page and View Details flow
- Student Progress filtering for All 17 courses
- Course extraction and deduplication by course code
- SQLite result and notification history
- Telegram notifications for newly detected courses

## Required environment variables

Create a `.env` file with:

```bash
VSTUDY_URL=https://vstudy.saveetha.com/
VSTUDY_PROFILE_DIR=./vstudy_chrome_profile
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
DATABASE_NAME=./results.db
```

## Project structure

```text
.
├── config.py
├── monitor.py
├── monitor_selenium.py
├── vstudy_scraper.py
├── results_db.py
├── telegram_notifier.py
├── requirements.txt
├── .env.example
├── README.md
├── results.db
├── vstudy_chrome_profile/
└── test_vstudy_login.py
```

## How it works

1. Open the VStudy portal using the configured persistent Chrome profile.
2. Navigate to the student profile page.
3. Click View Details.
4. Select the All 17 filter in Student Progress.
5. Parse the course table and collect unique course codes.
6. Store the results in SQLite and notify only when a new course code is seen.

## Run locally

```bash
pip install -r requirements.txt
python monitor.py
```

## Deployment

This repository is a background Selenium worker, not a web frontend. It needs a running Chromium process, a persistent authenticated Chrome profile, and persistent SQLite storage. Netlify cannot run the included Docker image or a long-running Python process, and its serverless functions do not provide the persistent browser profile this scraper requires.

Netlify can be used later for a frontend or a small serverless trigger, but the monitor itself must run on a host that supports containers or a scheduled worker with persistent storage. Do not deploy this worker to Netlify as a static site; it would not perform checks.

For a compatible container host, build the included `Dockerfile` and provide a persistent volume mounted at `/data`. Set:

```text
HEADLESS=true
UNATTENDED=true
RUN_FOREVER=true
VSTUDY_PROFILE_DIR=/data/vstudy_chrome_profile
DATABASE_NAME=/data/results.db
CHECK_INTERVAL=300
SAVE_DEBUG_ARTIFACTS=false
VSTUDY_URL=https://vstudy.saveetha.com/
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Before unattended deployment, copy an already authenticated Chrome profile into the persistent volume at `/data/vstudy_chrome_profile`. If the Google session expires, the profile must be re-authenticated and the volume updated. Without that profile, the service fails clearly instead of waiting for a human prompt.

When re-authentication is needed, the monitor prints `[AUTH REQUIRED]` in the worker logs and sends one Telegram warning. It keeps retrying at `CHECK_INTERVAL` and sends no duplicate warning until authentication works again. This lets you know exactly why no new results are being collected.

The warning state is stored in the SQLite `monitor_state` table, so a worker restart does not cause repeated alerts. A successful scrape resets the state and allows a new warning if authentication expires later. Set `SAVE_DEBUG_ARTIFACTS=true` only when troubleshooting; it is disabled by default for live deployments.

There is no reliable browser-only way to guarantee zero human interaction forever with Google login. A truly zero-interaction design would require VStudy to provide an official API or a long-lived service credential. Do not deploy Google passwords or CAPTCHA workarounds.

For a one-time local check, use `python test_vstudy_login.py`. A successful check prints `Found ... course(s)` and exits with code 0; an authentication or scraping failure exits with code 1.

## GitHub Actions deployment

GitHub-hosted runners are temporary and cannot retain the authenticated Chrome profile between scheduled runs. Use a self-hosted Windows runner that stays online. The included workflow runs one check every 15 minutes and can also be started manually from the Actions tab.

1. Push this repository to GitHub.
2. In the repository, open **Settings > Actions > Runners > New self-hosted runner**, choose **Windows x64**, and follow GitHub's commands on the machine that will stay online. Add the labels `self-hosted`, `Windows`, and `X64` if GitHub does not add them automatically.
3. Install Chrome on that runner and create these directories:

```text
C:\vstudy-data\vstudy_chrome_profile
C:\vstudy-data\chrome-runtime
```

4. On the runner machine, authenticate the profile once with a visible browser:

```powershell
$env:VSTUDY_PROFILE_DIR = 'C:\vstudy-data\vstudy_chrome_profile'
$env:CHROME_RUNTIME_DIR = 'C:\vstudy-data\chrome-runtime'
$env:HEADLESS = 'false'
$env:UNATTENDED = 'false'
python test_vstudy_login.py
```

Complete the Google login when prompted. The test must report `Found 17 course(s)`.

5. In **Settings > Secrets and variables > Actions**, add `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` as repository secrets.
6. Open **Actions > VStudy monitor > Run workflow** for the first check. Later checks run every 15 minutes while the self-hosted runner is online.

The workflow stores the database and Chrome profile outside the checkout at `C:\vstudy-data`, so repository cleanup does not delete the monitor history or login session. If the runner is offline, scheduled checks wait until a runner is available; GitHub Actions does not provide monitoring while the machine is powered off.

## Notes

- The project intentionally keeps the persistent browser profile for authenticated VStudy access.
- Telegram notifications remain optional and are enabled only when the token and chat ID are configured.
- The SQLite database preserves the notification history and deduplicates by course code.
