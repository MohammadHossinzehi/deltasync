"""Delta generation and application (the sender and receiver halves of rsync).

The sender slides a window of `block_size` bytes over the new file one byte
at a time, updating the rolling checksum in O(1). At each offset:

  1. Look the weak checksum up in the signature's hash table.
  2. If there are candidates, confirm with the strong hash.
  3. On a confirmed match emit COPY(block) and jump a whole block ahead.
     Otherwise the leftmost byte of the window becomes a LITERAL byte and
     the window rolls forward by one.

Adjacent COPY ops that reference consecutive basis blocks are merged into a
single run, so an unchanged 1 GB file becomes one instruction instead of
thousands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

from .rolling import RollingChecksum
from .signature import Signature, strong_hash


@dataclass(frozen=True)
class Copy:
    """Copy `count` consecutive basis blocks starting at block `start`."""
    start: int
    count: int = 1


@dataclass(frozen=True)
class Literal:
    data: bytes


Op = Union[Copy, Literal]


@dataclass
class Delta:
    block_size: int
    basis_size: int
    target_size: int
    ops: List[Op]
    target_hash: bytes = b""  # BLAKE2b-128 of the target, checked on apply

    @property
    def literal_bytes(self) -> int:
        return sum(len(op.data) for op in self.ops if isinstance(op, Literal))

    @property
    def copied_bytes(self) -> int:
        return self.target_size - self.literal_bytes

    def stats(self) -> dict:
        copies = sum(1 for op in self.ops if isinstance(op, Copy))
        return {
            "target_size": self.target_size,
            "copied_bytes": self.copied_bytes,
            "literal_bytes": self.literal_bytes,
            "copy_ops": copies,
            "literal_ops": len(self.ops) - copies,
            "reuse_ratio": (self.copied_bytes / self.target_size) if self.target_size else 1.0,
        }


class _OpBuilder:
    """Accumulates ops, merging adjacent literals and consecutive copies."""

    def __init__(self):
        self.ops: List[Op] = []
        self._lit = bytearray()

    def literal(self, data) -> None:
        self._lit += data

    def copy(self, block: int) -> None:
        self._flush()
        last = self.ops[-1] if self.ops else None
        if isinstance(last, Copy) and last.start + last.count == block:
            self.ops[-1] = Copy(last.start, last.count + 1)
        else:
            self.ops.append(Copy(block, 1))

    def _flush(self) -> None:
        if self._lit:
            self.ops.append(Literal(bytes(self._lit)))
            self._lit = bytearray()

    def finish(self) -> List[Op]:
        self._flush()
        return self.ops


def _next_expected(builder: _OpBuilder) -> Optional[int]:
    last = builder.ops[-1] if builder.ops and not builder._lit else None
    if isinstance(last, Copy):
        return last.start + last.count
    return None


def make_delta(sig: Signature, new: bytes) -> Delta:
    B = sig.block_size
    n = len(new)
    out = _OpBuilder()
    table = sig.lookup()

    if not sig.blocks or n == 0:
        if n:
            out.literal(new)
        return Delta(B, sig.file_size, n, out.finish(), strong_hash(new, 16))

    # Length of the (possibly short) final basis block; a window shorter
    # than B can only ever match that block.
    tail_len = sig.block_len(len(sig.blocks) - 1)

    i = 0
    win_end = min(B, n)
    rc = RollingChecksum(new[0:win_end])
    lit_start = 0  # literal bytes are new[lit_start:i]

    while i < n:
        wlen = win_end - i
        match = None
        candidates = table.get(rc.digest)
        if candidates and (wlen == B or wlen == tail_len):
            strong = strong_hash(new[i:win_end], sig.strong_len)
            # Prefer the block that extends the current COPY run, so runs
            # stay long even when the basis contains repeated blocks.
            expected = _next_expected(out) if lit_start == i else None
            for blk in candidates:
                if blk.strong == strong and sig.block_len(blk.index) == wlen:
                    if match is None:
                        match = blk
                    if blk.index == expected:
                        match = blk
                        break
        if match is not None:
            if lit_start < i:
                out.literal(new[lit_start:i])
            out.copy(match.index)
            i = win_end
            lit_start = i
            win_end = min(i + B, n)
            if i < n:
                rc = RollingChecksum(new[i:win_end])
            continue

        # No match: slide one byte.
        out_byte = new[i]
        if win_end < n:
            rc.roll(out_byte, new[win_end])
            win_end += 1
        else:
            rc.rollout(out_byte)
        i += 1

    if lit_start < n:
        out.literal(new[lit_start:n])
    return Delta(B, sig.file_size, n, out.finish(), strong_hash(new, 16))


def apply_delta(basis: bytes, delta: Delta) -> bytes:
    if len(basis) != delta.basis_size:
        raise ValueError(
            f"basis is {len(basis)} bytes but delta was made against {delta.basis_size}")
    B = delta.block_size
    out = bytearray()
    view = memoryview(basis)
    for op in delta.ops:
        if isinstance(op, Copy):
            start = op.start * B
            end = min((op.start + op.count) * B, len(basis))
            if start >= len(basis) or op.count <= 0:
                raise ValueError(f"copy op out of range: {op}")
            out += view[start:end]
        else:
            out += op.data
    if len(out) != delta.target_size:
        raise ValueError(
            f"reconstructed {len(out)} bytes, delta says {delta.target_size}")
    result = bytes(out)
    if delta.target_hash and strong_hash(result, len(delta.target_hash)) != delta.target_hash:
        raise ValueError("target hash mismatch: basis differs from the one the signature was made from")
    return result
