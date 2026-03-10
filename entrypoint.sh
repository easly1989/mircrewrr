#!/bin/bash
set -e

# Pulizia lock file stale (container riciclato)
rm -f /tmp/.X99-lock

# Avvio Xvfb con 24-bit color (standard, più compatibile)
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX &
XVFB_PID=$!

# Attendi che Xvfb sia effettivamente pronto (max 10s)
for i in $(seq 1 20); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "Xvfb started successfully (PID $XVFB_PID)"
        break
    fi
    if ! kill -0 $XVFB_PID 2>/dev/null; then
        echo "ERROR: Xvfb process died" >&2
        exit 1
    fi
    sleep 0.5
done

if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "ERROR: Xvfb failed to start after 10s" >&2
    exit 1
fi

# Kill zombie Chrome da container precedente
pkill -9 -f chromium 2>/dev/null || true
pkill -9 -f chrome 2>/dev/null || true

# Log versioni per diagnostica
echo "Chrome: $(chromium --version 2>/dev/null || echo 'NOT FOUND')"
echo "Chromedriver: $(chromedriver --version 2>/dev/null || echo 'NOT FOUND')"
echo "/dev/shm: $(df -h /dev/shm 2>/dev/null | tail -1)"

exec python src/proxy_server.py
