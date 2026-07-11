"""UUIDv7 generation compatible with Python versions before 3.14."""

import os
import threading
import time
import uuid


_last_timestamp_v7: int | None = None
_last_counter_v7: int | None = None
_uuid7_lock = threading.Lock()


def _new_counter_and_tail() -> tuple[int, int]:
    """Return a random 41-bit counter and 32-bit random tail."""
    random_bits = int.from_bytes(os.urandom(10), "big")
    counter = (random_bits >> 32) & 0x1FF_FFFF_FFFF
    tail = random_bits & 0xFFFF_FFFF
    return counter, tail


def _compat_uuid7() -> uuid.UUID:
    """Generate a monotonic RFC 9562 UUIDv7 using only pre-3.14 APIs."""
    global _last_timestamp_v7, _last_counter_v7

    with _uuid7_lock:
        timestamp_ms = time.time_ns() // 1_000_000

        if _last_timestamp_v7 is None or timestamp_ms > _last_timestamp_v7:
            counter, tail = _new_counter_and_tail()
        else:
            if timestamp_ms < _last_timestamp_v7:
                timestamp_ms = _last_timestamp_v7 + 1
            counter = _last_counter_v7 + 1
            if counter > 0x3FF_FFFF_FFFF:
                timestamp_ms += 1
                counter, tail = _new_counter_and_tail()
            else:
                tail = int.from_bytes(os.urandom(4), "big")

        counter_hi = (counter >> 30) & 0x0FFF
        counter_lo = counter & 0x3FFF_FFFF

        value = (timestamp_ms & 0xFFFF_FFFF_FFFF) << 80
        value |= 0x7 << 76
        value |= counter_hi << 64
        value |= 0b10 << 62
        value |= counter_lo << 32
        value |= tail & 0xFFFF_FFFF

        result = uuid.UUID(int=value)
        _last_timestamp_v7 = timestamp_ms
        _last_counter_v7 = counter
        return result


def uuid7() -> uuid.UUID:
    """Return a UUIDv7 using the native implementation when available."""
    native_uuid7 = getattr(uuid, "uuid7", None)
    if native_uuid7 is not None:
        return native_uuid7()
    return _compat_uuid7()
