import time
from dataclasses import dataclass

from proto import Status


@dataclass(slots=True)
class Entry:
    value: bytes
    expires_at: float | None


class Storage:
    def __init__(self):
        self._data: dict[bytes, Entry] = {}

    def set(self, key: bytes, value: bytes, ttl: int) -> Status:
        expires_at = None

        if ttl > 0:
            expires_at = time.monotonic() + ttl

        self._data[key] = Entry(value=value, expires_at=expires_at)

        return Status.OK

    def get(self, key: bytes) -> tuple[Status, bytes]:
        entry = self._data.get(key)

        if entry is None:
            return Status.NOT_FOUND, b""

        if self._is_expired(entry):
            del self._data[key]
            return Status.EXPIRED, b""

        return Status.OK, entry.value

    def delete(self, key: bytes) -> Status:
        self._data.pop(key, None)
        return Status.OK

    @staticmethod
    def _is_expired(entry: Entry) -> bool:
        if entry.expires_at is None:
            return False
        return time.monotonic() >= entry.expires_at
