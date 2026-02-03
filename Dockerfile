FROM python:3.11-slim

LABEL maintainer="MIRCrew Indexer Debug Container"
LABEL description="Container per debugging dell'indexer MIRCrew per Prowlarr"

# Installazione dipendenze di sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    chromium \
    chromium-driver \
    firefox-esr \
    dnsutils \
    iputils-ping \
    net-tools \
    tcpdump \
    vim \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Directory di lavoro
WORKDIR /app

# Copia requirements e installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia codice sorgente
COPY src/ ./src/
COPY config/ ./config/

# Variabili d'ambiente di default
ENV PYTHONUNBUFFERED=1
ENV MIRCREW_USERNAME=""
ENV MIRCREW_PASSWORD=""
ENV MIRCREW_BASE_URL="https://mircrew-releases.org"

# Volume per dati persistenti (cookies, cache, logs)
VOLUME ["/app/data"]

# Comando di default
CMD ["python", "src/debug_indexer.py"]
