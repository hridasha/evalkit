"""Remember the judge's answers on disk: running the same evaluation again costs no quota."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

from evalkit.llm.base import LLM, Usage


class CachedLLM:
    def __init__(self, inner: LLM, path: str | Path):
        self.inner = inner
        self.usage: Usage = inner.usage
        self.hits = 0
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, reply TEXT NOT NULL)")
            self.db.commit()

    def _key(self, prompt: str, system: str | None, json_mode: bool) -> str:
        model = getattr(self.inner, "model", type(self.inner).__name__)
        raw = f"{model}\x00{system or ''}\x00{json_mode}\x00{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str:
        key = self._key(prompt, system, json_mode)
        with self.lock:
            row = self.db.execute("SELECT reply FROM cache WHERE key = ?", (key,)).fetchone()
        if row:
            self.hits += 1
            return row[0]
        reply = await self.inner.complete(prompt, system=system, json_mode=json_mode)
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO cache (key, reply) VALUES (?, ?)", (key, reply))
            self.db.commit()
        return reply
