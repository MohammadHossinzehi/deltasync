"""FastCDC content defined chunking with normalized chunking.

Fixed size blocks have a fatal weakness for deduplication: insert one byte
at the front of a file and every block boundary shifts, so nothing matches.
Content defined chunking places boundaries where the *content* says so,
using a rolling hash over the last few dozen bytes. An insertion only
disturbs the boundaries near it; everything downstream re-synchronises.

This follows Xia et al., "FastCDC: a Fast and Efficient Content-Defined
Chunking Approach for Data Deduplication" (USENIX ATC 2016):

  * Gear hash: fp = (fp << 1) + GEAR[byte]. One shift, one add, one table
    lookup per byte. Bit j of fp depends on the last j+1 bytes, so the
    mask is built from the high bits to get a ~48 byte effective window.
  * Cut point skipping: no boundary can occur before min_size, so those
    bytes are not hashed at all.
  * Normalized chunking: a harder mask (more bits) before the average size
    and an easier mask (fewer bits) after it. This squeezes the chunk size
    distribution toward the average, cutting both tiny chunks (metadata
    overhead) and huge ones (poor dedup).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Iterator, List

U64 = (1 << 64) - 1


def _gear_table(seed: int = 0x5EED_CDC) -> List[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(64) for _ in range(256)]


GEAR = _gear_table()


def _high_mask(bits: int) -> int:
    """`bits` one bits placed at the top of a 64 bit word."""
    if bits <= 0:
        return 0
    return ((1 << bits) - 1) << (64 - bits)


@dataclass(frozen=True)
class Chunk:
    offset: int
    length: int
    digest: str  # BLAKE2b-256 hex, used as the content address

    @property
    def end(self) -> int:
        return self.offset + self.length


class FastCDC:
    def __init__(self, min_size: int = 2048, avg_size: int = 8192,
                 max_size: int = 65536, normalization: int = 2):
        if not (0 < min_size <= avg_size <= max_size):
            raise ValueError("need 0 < min_size <= avg_size <= max_size")
        if avg_size < 64 or avg_size & (avg_size - 1):
            raise ValueError("avg_size must be a power of two and at least 64")
        if not 0 <= normalization <= 3:
            raise ValueError("normalization level must be 0..3")
        self.min_size = min_size
        self.avg_size = avg_size
        self.max_size = max_size
        bits = avg_size.bit_length() - 1
        self.mask_s = _high_mask(bits + normalization)  # harder, below avg
        self.mask_l = _high_mask(bits - normalization)  # easier, above avg

    def cut_point(self, data, start: int) -> int:
        """Return the end offset of the chunk that begins at `start`."""
        remaining = len(data) - start
        if remaining <= self.min_size:
            return len(data)
        end = start + min(remaining, self.max_size)
        normal = start + min(remaining, self.avg_size)
        gear = GEAR
        fp = 0
        i = start + self.min_size
        mask = self.mask_s
        while i < normal:
            fp = ((fp << 1) + gear[data[i]]) & U64
            if not fp & mask:
                return i + 1
            i += 1
        mask = self.mask_l
        while i < end:
            fp = ((fp << 1) + gear[data[i]]) & U64
            if not fp & mask:
                return i + 1
            i += 1
        return end

    def boundaries(self, data) -> Iterator[int]:
        """Yield chunk end offsets."""
        pos = 0
        n = len(data)
        while pos < n:
            pos = self.cut_point(data, pos)
            yield pos

    def chunks(self, data: bytes) -> Iterator[Chunk]:
        view = memoryview(data)
        start = 0
        for end in self.boundaries(data):
            digest = hashlib.blake2b(view[start:end], digest_size=32).hexdigest()
            yield Chunk(start, end - start, digest)
            start = end
