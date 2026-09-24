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

## Cloud deployment

Use an always-on Ubuntu 24.04 VPS with Docker Compose. This is a better fit than a scheduled or serverless host because Selenium needs Chromium, the authenticated Chrome profile must persist, and the worker must run continuously. The VPS runs the monitor 24/7; GitHub Actions only uploads new code and restarts the service.

The Compose service mounts `/opt/vstudy-app/data` into the container as `/data`. That directory contains both `vstudy_chrome_profile/` and `results.db`, so authentication and notification history survive container rebuilds and VM reboots. `restart: unless-stopped` restarts a crashed container. A single in-process watchdog exits the monitor when its heartbeat is stale, so Docker also restarts hung Selenium sessions without creating a second monitor process.

### 1. Create the VPS

Create an Ubuntu 24.04 server with at least 2 vCPU, 4 GB RAM, and 20 GB of disk. Allow SSH (TCP 22) from your IP and keep all other inbound ports closed. SSH to the server and install Docker:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl rsync
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
exit
```

Reconnect, then create the deployment directories:

```bash
sudo mkdir -p /opt/vstudy-app/data/vstudy_chrome_profile
sudo chown -R "$USER":"$USER" /opt/vstudy-app
```

### 2. Configure the server environment

After the first GitHub Actions deployment uploads the repository, create `/opt/vstudy-app/.env` on the VPS. Never commit this file:

```bash
cd /opt/vstudy-app
cp .env.example .env
nano .env
```

Set these values, including the real Telegram values only on the server:

```text
VSTUDY_URL=https://vstudy.saveetha.com/
TELEGRAM_TOKEN=replace_on_server_only
TELEGRAM_CHAT_ID=replace_on_server_only
VSTUDY_DATA_DIR=/opt/vstudy-app/data
HEADLESS=true
UNATTENDED=true
RUN_FOREVER=true
CHECK_INTERVAL=300
HEARTBEAT_TIMEOUT=900
SAVE_DEBUG_ARTIFACTS=false
```

The Compose file supplies the container paths for `VSTUDY_PROFILE_DIR`, `DATABASE_NAME`, and `HEARTBEAT_FILE`. Do not put those secrets in GitHub Actions or source control.

### 3. Authenticate directly on the VPS

Do not copy a Windows Chrome profile. Windows browser cookies can be encrypted with Windows-specific keys and may not work in Linux Chromium. Instead, create the authenticated profile directly in the VPS container using the opt-in graphical service.

First, stop the monitor so Chromium is the only process using the profile:

```bash
cd /opt/vstudy-app
docker compose stop vstudy-monitor
```

Create a VNC password file in the persistent data directory. The command prompts for the password without putting it in shell history:

```bash
docker compose -f docker-compose.yml -f docker-compose.auth.yml --profile auth \
	run --rm --entrypoint x11vnc vstudy-auth -storepasswd /data/vnc.passwd
chmod 600 data/vnc.passwd
```

Start the temporary Chromium and VNC service:

```bash
docker compose -f docker-compose.yml -f docker-compose.auth.yml --profile auth \
	up -d vstudy-auth
docker compose -f docker-compose.yml -f docker-compose.auth.yml --profile auth \
	logs --tail=50 vstudy-auth
```

The VNC server is bound to VPS localhost only. It is not publicly reachable. From Windows, open a second PowerShell terminal and keep this SSH tunnel running:

```powershell
ssh -N -L 5901:127.0.0.1:5901 YOUR_USER@YOUR_SERVER
```

Connect a VNC client such as TigerVNC or RealVNC to `127.0.0.1:5901` and enter the VNC password. In the Chromium window, complete the Google/VStudy login manually. Confirm that the browser reaches the VStudy dashboard, then open the profile page and confirm that **View Details** is visible. This browser is using `/data/vstudy_chrome_profile`, so the authenticated profile is created directly on Linux in the persistent volume.

After the login succeeds, close the VNC client and stop/remove the temporary graphical service before starting the monitor:

```bash
cd /opt/vstudy-app
docker compose -f docker-compose.yml -f docker-compose.auth.yml --profile auth \
	stop vstudy-auth
docker compose -f docker-compose.yml -f docker-compose.auth.yml --profile auth \
	rm -f vstudy-auth
docker compose up -d vstudy-monitor
```

The graphical service is disabled unless the `auth` profile is explicitly selected. The monitor uses the same profile headlessly with Selenium after the graphical service is stopped. If Google authentication expires later, repeat this VPS-side procedure; do not run the auth service and monitor at the same time.

### 4. Configure GitHub Actions deployment

Generate a dedicated deploy key on your local machine:

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\vstudy_deploy" -C "vstudy-github-actions"
```

Append `vstudy_deploy.pub` to `/home/YOUR_USER/.ssh/authorized_keys` on the VPS. In the repository's **Settings > Secrets and variables > Actions**, add:

```text
VPS_HOST              VPS public IP or DNS name
VPS_USER              Linux deployment username
VPS_SSH_PRIVATE_KEY  complete contents of vstudy_deploy
VPS_KNOWN_HOSTS      output of ssh-keyscan -H VPS_HOST
```

The workflow in `.github/workflows/vstudy-monitor.yml` runs on GitHub-hosted Ubuntu, uploads the application while preserving `.env` and `data/`, and runs `docker compose up -d --build`. It no longer requires a Windows self-hosted runner and does not use Telegram secrets in the workflow.

Trigger the workflow once from **Actions > VStudy monitor deployment > Run workflow**. The VPS directory must exist before this first upload.

### 5. Start and verify the service

After `.env` and the VPS-created profile are installed, start the service on the VPS:

```bash
cd /opt/vstudy-app
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 -f vstudy-monitor
```

The health status should become `healthy` after the first monitor cycle. The container restarts if Python exits. If Selenium hangs, the in-process watchdog sees that `/data/monitor.heartbeat` is older than `HEARTBEAT_TIMEOUT` seconds and exits Python; Docker then restarts the same container. Useful checks are:

```bash
docker compose exec vstudy-monitor sh -c 'stat /data/monitor.heartbeat && test -f /data/results.db'
docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q vstudy-monitor)"
docker compose restart
docker compose down
```

Use `docker compose stop` for maintenance. Do not delete `/opt/vstudy-app/data`; it holds the authenticated profile and database.

There is no reliable browser-only way to guarantee zero human interaction forever with Google login. A truly zero-interaction design would require VStudy to provide an official API or a long-lived service credential. Do not deploy Google passwords or CAPTCHA workarounds.

For a one-time local check, use `python test_vstudy_login.py`. A successful check prints `Found ... course(s)` and exits with code 0; an authentication or scraping failure exits with code 1.

## Notes

- The project intentionally keeps the persistent browser profile for authenticated VStudy access.
- Telegram notifications remain optional and are enabled only when the token and chat ID are configured.
- The SQLite database preserves the notification history and deduplicates by course code.
