FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver xvfb x11vnc fluxbox \
    && chromium --version \
    && chromedriver --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p /data/vstudy_chrome_profile /tmp/chrome

ENV HEADLESS=true \
    UNATTENDED=true \
    RUN_FOREVER=true \
    VSTUDY_PROFILE_DIR=/data/vstudy_chrome_profile \
    DATABASE_NAME=/data/results.db \
    HEARTBEAT_FILE=/data/monitor.heartbeat

CMD ["python", "monitor.py"]
