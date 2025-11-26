from __future__ import annotations

import sys
import types
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

try:
    from redis.exceptions import ResponseError
except ModuleNotFoundError:  # pragma: no cover - fallback for test environments without redis

    class ResponseError(Exception):
        pass


def _install_redis_stub() -> None:
    redis_asyncio = types.ModuleType("redis.asyncio")
    redis_asyncio.from_url = (  # type: ignore[attr-defined]
        lambda *_, **__: (_ for _ in ()).throw(
            ModuleNotFoundError("redis is not installed in this environment")
        )
    )
    redis_asyncio.Redis = type("Redis", (), {})  # type: ignore[attr-defined]
    redis_asyncio.ResponseError = ResponseError  # type: ignore[attr-defined]

    redis_exceptions = types.ModuleType("redis.exceptions")
    redis_exceptions.ResponseError = ResponseError  # type: ignore[attr-defined]

    redis_module = types.ModuleType("redis")
    redis_module.asyncio = redis_asyncio  # type: ignore[attr-defined]
    redis_module.exceptions = redis_exceptions  # type: ignore[attr-defined]
    redis_module.Redis = redis_asyncio.Redis  # type: ignore[attr-defined]
    redis_module.ResponseError = ResponseError  # type: ignore[attr-defined]

    sys.modules.setdefault("redis", redis_module)
    sys.modules.setdefault("redis.asyncio", redis_asyncio)
    sys.modules.setdefault("redis.exceptions", redis_exceptions)


_install_redis_stub()

PROJECT_ROOT = Path(__file__).resolve().parent
ORCHESTRATOR_ROOT = PROJECT_ROOT / "services" / "orchestrator"
GPU_WORKER_ROOT = PROJECT_ROOT / "services" / "gpu-ffmpeg"
for path in (ORCHESTRATOR_ROOT, GPU_WORKER_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class InMemoryRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = defaultdict(dict)
        self._sorted: dict[str, dict[str, float]] = defaultdict(dict)
        self._sets: dict[str, set[str]] = defaultdict(set)
        self._kv: dict[str, str] = {}
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
        self._groups: dict[str, set[str]] = defaultdict(set)
        self._positions: dict[tuple[str, str], int] = defaultdict(int)
        self._pending: dict[tuple[str, str], list[str]] = defaultdict(list)

    async def xgroup_create(self, stream: str, group: str, *, id: str, mkstream: bool = False):
        if group in self._groups[stream]:
            raise ResponseError("BUSYGROUP")
        self._groups[stream].add(group)
        if mkstream and stream not in self._streams:
            self._streams[stream] = []

    async def hset(self, key: str, *, mapping: dict[str, str]):
        self._hashes[key].update(mapping)

    async def zadd(self, key: str, mapping: dict[str, float]):
        self._sorted[key].update(mapping)

    async def sadd(self, key: str, value: str):
        self._sets[key].add(value)

    async def srem(self, key: str, value: str):
        self._sets[key].discard(value)

    async def set(self, key: str, value: Any):
        self._kv[key] = str(value)

    async def get(self, key: str):
        return self._kv.get(key)

    async def delete(self, key: str):
        self._kv.pop(key, None)
        self._hashes.pop(key, None)
        self._sets.pop(key, None)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def zrevrange(self, key: str, start: int, stop: int):
        items = sorted(self._sorted.get(key, {}).items(), key=lambda item: item[1], reverse=True)
        return [item[0] for item in items[start : stop + 1]]

    async def zrange(self, key: str, start: int, stop: int):
        items = sorted(self._sorted.get(key, {}).items(), key=lambda item: item[1])
        end = None if stop == -1 else stop + 1
        return [item[0] for item in items[start:end]]

    async def zrem(self, key: str, member: str):
        bucket = self._sorted.get(key)
        if bucket and member in bucket:
            bucket.pop(member, None)

    async def xadd(
        self, stream: str, fields: dict[str, str], *, maxlen: int, approximate: bool = True
    ):
        message_id = f"{len(self._streams[stream])}-0"
        self._streams[stream].append((message_id, fields))
        return message_id

    async def incr(self, key: str):
        current = int(self._kv.get(key, "0"))
        current += 1
        self._kv[key] = str(current)
        return current

    async def decr(self, key: str):
        current = int(self._kv.get(key, "0"))
        current -= 1
        self._kv[key] = str(current)
        return current

    async def xreadgroup(
        self, group: str, consumer: str, streams: dict[str, str], *, count: int, block: int
    ):
        stream = next(iter(streams))
        position_key = (stream, group)
        position = self._positions[position_key]
        if position >= len(self._streams[stream]):
            return []
        message = self._streams[stream][position]
        self._positions[position_key] = position + 1
        self._pending[position_key].append(message[0])
        return [(stream, [(message[0], message[1])])]

    async def xack(self, stream: str, group: str, message_id: str):
        position_key = (stream, group)
        pending = self._pending[position_key]
        if message_id in pending:
            pending.remove(message_id)
        return 1

    async def xpending(self, stream: str, group: str):
        return {"pending": len(self._pending.get((stream, group), []))}

    async def xautoclaim(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_time: int,
        start_id: str,
        count: int,
    ):
        return "0-0", []


@pytest.fixture()
def fake_redis() -> InMemoryRedis:
    return InMemoryRedis()
