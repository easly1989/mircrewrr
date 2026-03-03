FROM python:3.11-slim

LABEL maintainer="MIRCrew Proxy"
LABEL description="Torznab proxy per MIRCrew-releases.org"
LABEL org.opencontainers.image.source="https://github.com/easly1989/mircrewrr"

# Installazione dipendenze di sistema
# - curl: per health check
# - libxml2-dev, libxslt-dev: per compilare lxml
# - gcc: compilatore per lxml
# - chromium + deps: per undetected-chromedriver (browser-based login)
# - xvfb: virtual display for non-headless mode (Cloudflare/Turnstile bypass)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libxml2-dev \
    libxslt-dev \
    chromium \
    chromium-driver \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome/Chromium path for undetected-chromedriver
ENV CHROME_PATH=/usr/bin/chromium
# Xvfb virtual display (Chrome runs non-headless to bypass Cloudflare)
ENV DISPLAY=:99

# Directory di lavoro
WORKDIR /app

# Copia requirements e installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia codice sorgente
COPY src/ ./src/

# Variabili d'ambiente di default
ENV PYTHONUNBUFFERED=1
ENV MIRCREW_USERNAME=""
ENV MIRCREW_PASSWORD=""
ENV MIRCREW_API_KEY=""
ENV MIRCREW_URL="https://mircrew-releases.org"
ENV PROXY_HOST="0.0.0.0"
ENV PROXY_PORT="9696"
ENV DATA_DIR="/app/data"
ENV LOG_LEVEL="INFO"

# Volume per dati persistenti (cookies, cache thanks)
VOLUME ["/app/data"]

# Porta esposta (default, sovrascrivibile con PROXY_PORT)
EXPOSE ${PROXY_PORT}

# Health check - usa $PROXY_PORT per supportare porte custom
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PROXY_PORT}/health || exit 1

# Comando di default - start Xvfb for virtual display then run proxy
CMD Xvfb :99 -screen 0 1920x1080x24 & sleep 2 && python src/proxy_server.py
