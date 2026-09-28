"""Block signatures of a basis file.

The receiver (who owns the old file) splits it into fixed size blocks and
sends, per block, a cheap weak checksum and an expensive strong hash. The
sender only ever hashes strongly when the weak checksum already matches,
so the strong hash is computed rarely.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Dict, List

from .rolling import weak_checksum

DEFAULT_STRONG_LEN = 16  # bytes of BLAKE2b kept per block


def strong_hash(data: bytes, length: int = DEFAULT_STRONG_LEN) -> bytes:
    return hashlib.blake2b(data, digest_size=length).digest()


@dataclass(frozen=True)
class BlockSig:
    index: int
    weak: int
    strong: bytes


@dataclass
class Signature:
    block_size: int
    strong_len: int
    file_size: int
    blocks: List[BlockSig]
    _lookup: Dict[int, List[BlockSig]] = field(default=None, repr=False, compare=False)

    def lookup(self) -> Dict[int, List[BlockSig]]:
        """weak checksum -> candidate blocks. Built lazily, cached."""
        if self._lookup is None:
            table: Dict[int, List[BlockSig]] = {}
            for blk in self.blocks:
                table.setdefault(blk.weak, []).append(blk)
            self._lookup = table
        return self._lookup

    def block_len(self, index: int) -> int:
        """Length of block `index` (the last block may be short)."""
        start = index * self.block_size
        return min(self.block_size, self.file_size - start)


def suggest_block_size(file_size: int, lo: int = 512, hi: int = 1 << 17) -> int:
    """rsync's heuristic: roughly sqrt(file size), rounded to a multiple of 8.

    Small blocks find more matches but cost more signature bytes; sqrt(n)
    balances signature size (n / B entries) against the literal bytes lost
    around each edit (about B per edit).
    """
    if file_size <= 0:
        return lo
    b = int(math.isqrt(file_size))
    b = (b + 7) & ~7
    return max(lo, min(hi, b))


def make_signature(basis: bytes, block_size: int | None = None,
                   strong_len: int = DEFAULT_STRONG_LEN) -> Signature:
    if block_size is None:
        block_size = suggest_block_size(len(basis))
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    if not 1 <= strong_len <= 64:
        raise ValueError("strong_len must be in 1..64")
    blocks = []
    view = memoryview(basis)
    for i, start in enumerate(range(0, len(basis), block_size)):
        chunk = bytes(view[start:start + block_size])
        blocks.append(BlockSig(i, weak_checksum(chunk), strong_hash(chunk, strong_len)))
    return Signature(block_size, strong_len, len(basis), blocks)
