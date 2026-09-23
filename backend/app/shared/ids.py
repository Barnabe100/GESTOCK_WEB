"""Identifiants UUIDv7 (RFC 9562) : triables dans le temps, générables hors ligne."""

import os
import time
import uuid

_RAND_A_MASK = (1 << 12) - 1
_RAND_B_MASK = (1 << 62) - 1


def new_id() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits aléatoires
    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= ((rand >> 62) & _RAND_A_MASK) << 64  # rand_a (12 bits)
    value |= 0b10 << 62  # variante RFC 9562
    value |= rand & _RAND_B_MASK  # rand_b (62 bits)
    return uuid.UUID(int=value)
