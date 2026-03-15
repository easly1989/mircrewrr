"""Custom log handler per buffering e streaming SSE."""

import logging
import threading
from collections import deque
from datetime import datetime
from queue import Queue, Empty


class AdminLogHandler(logging.Handler):
    """Cattura log in un buffer circolare e notifica subscriber SSE."""

    def __init__(self, max_entries=500):
        super().__init__()
        self.buffer = deque(maxlen=max_entries)
        self.subscribers: list[Queue] = []
        self._lock = threading.Lock()

    def emit(self, record):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }
        self.buffer.append(entry)

        # Notifica tutti i subscriber SSE
        with self._lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.put_nowait(entry)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.subscribers.remove(q)

    def get_recent(self, count=100) -> list:
        """Ritorna gli ultimi N log entries."""
        items = list(self.buffer)
        return items[-count:]

    def subscribe(self) -> Queue:
        """Crea una nuova subscription SSE."""
        q: Queue = Queue(maxsize=100)
        with self._lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: Queue):
        """Rimuove una subscription SSE."""
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)


# Singleton globale
log_handler = AdminLogHandler()
log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
