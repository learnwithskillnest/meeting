"""In-process realtime hub for Server-Sent Events.

Each connected browser tab holds an asyncio.Queue. Events are pushed to every
queue belonging to the recipient user. This works for a single app process;
for horizontal scaling replace `publish` with Redis pub/sub (see docs).
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any


class Hub:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def connect(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues[user_id].add(q)
        return q

    def disconnect(self, user_id: str, q: asyncio.Queue) -> None:
        self._queues[user_id].discard(q)
        if not self._queues[user_id]:
            self._queues.pop(user_id, None)

    def is_online(self, user_id: str) -> bool:
        return bool(self._queues.get(user_id))

    def publish(self, user_id: str, event: str, data: Any) -> None:
        payload = json.dumps(data, default=str)
        for q in list(self._queues.get(user_id, ())):
            try:
                q.put_nowait((event, payload))
            except asyncio.QueueFull:
                pass  # slow client; it will resync via REST on reconnect
            except RuntimeError:
                pass

    def connection_count(self) -> int:
        return sum(len(v) for v in self._queues.values())


hub = Hub()
