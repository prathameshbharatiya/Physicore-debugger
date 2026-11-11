"""BitInfo of a non-finite float (DESIGN 6.6.1): only what is guaranteed. float64 NaN/Inf: bits re-encoded from the decoded Python float (a start-up
self-check proves the round trip; on failure `bits_hex=null, bits_basis=not_reported`). float32 +-Inf: fully determined by class and sign. float32 NaN:
sign only; the decoder quiets signalling NaNs, so no payload is claimed. JSON: no bit_info."""
from __future__ import annotations

import math
import struct
from typing import Optional

from .findings import BitInfo

SELF_CHECK_PATTERNS = (0x7FF8000000000000, 0xFFF8000000000000, 0x7FF8000000000001, 0x7FF0000000000001, 0xFFF0000000000001, 0x7FF4000000000000,
                       0x7FF0000000000000, 0xFFF0000000000000)


def _f64_to_bits(x: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def _bits_to_f64(b: int) -> float:
    return struct.unpack(">d", struct.pack(">Q", b))[0]


def self_check() -> bool:
    """Round-trip fixed float64 patterns through the re-encoder."""
    try:
        return all(_f64_to_bits(_bits_to_f64(b)) == b for b in SELF_CHECK_PATTERNS)
    except Exception:    # noqa: BLE001
        return False


def sign_of(x: float) -> int:
    return 1 if math.copysign(1.0, x) < 0 else 0


def bit_info(x: float, width: Optional[int], f64_ok: bool) -> Optional[BitInfo]:
    """`width` is the schema-declared float width (None: unknown/JSON -> no bit info)."""
    if width == 64:
        if f64_ok:
            return BitInfo(width=64, sign=sign_of(x), bits_hex=f"{_f64_to_bits(x):016x}", bits_basis="reencoded_from_decoded_float64")
        return BitInfo(width=64, sign=sign_of(x), bits_hex=None, bits_basis="not_reported")
    if width == 32:
        if math.isinf(x):
            return BitInfo(width=32, sign=sign_of(x), bits_hex="ff800000" if x < 0 else "7f800000", bits_basis="determined_by_class_and_sign")
        return BitInfo(width=32, sign=sign_of(x), bits_hex=None, bits_basis="not_guaranteed")
    return None
