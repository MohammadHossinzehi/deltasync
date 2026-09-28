"""Compact binary encodings for signatures and deltas.

All integers are unsigned LEB128 varints, so small numbers cost one byte.

Signature:
    b"DSIG" version:u8 block_size:v strong_len:u8 file_size:v count:v
    count * ( weak:u32le strong:strong_len bytes )

Delta:
    b"DDLT" version:u8 block_size:v basis_size:v target_size:v
    hash_len:u8 target_hash  op_count:v
    ops:  0x01 start:v count:v          COPY
          0x02 length:v bytes           LITERAL
"""

from __future__ import annotations

import io
import struct

from .delta import Copy, Delta, Literal
from .signature import BlockSig, Signature

VERSION = 1
SIG_MAGIC = b"DSIG"
DELTA_MAGIC = b"DDLT"
OP_COPY = 0x01
OP_LITERAL = 0x02


def write_varint(buf: io.BytesIO, value: int) -> None:
    if value < 0:
        raise ValueError("varints are unsigned")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            buf.write(bytes((byte | 0x80,)))
        else:
            buf.write(bytes((byte,)))
            return


def read_varint(buf: io.BytesIO) -> int:
    shift = 0
    result = 0
    while True:
        b = buf.read(1)
        if not b:
            raise ValueError("truncated varint")
        byte = b[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _read_exact(buf: io.BytesIO, n: int) -> bytes:
    data = buf.read(n)
    if len(data) != n:
        raise ValueError("truncated input")
    return data


def _header(buf: io.BytesIO, magic: bytes) -> None:
    if _read_exact(buf, 4) != magic:
        raise ValueError(f"bad magic, expected {magic!r}")
    version = _read_exact(buf, 1)[0]
    if version != VERSION:
        raise ValueError(f"unsupported version {version}")


def encode_signature(sig: Signature) -> bytes:
    buf = io.BytesIO()
    buf.write(SIG_MAGIC + bytes((VERSION,)))
    write_varint(buf, sig.block_size)
    buf.write(bytes((sig.strong_len,)))
    write_varint(buf, sig.file_size)
    write_varint(buf, len(sig.blocks))
    for blk in sig.blocks:
        buf.write(struct.pack("<I", blk.weak))
        buf.write(blk.strong)
    return buf.getvalue()


def decode_signature(data: bytes) -> Signature:
    buf = io.BytesIO(data)
    _header(buf, SIG_MAGIC)
    block_size = read_varint(buf)
    strong_len = _read_exact(buf, 1)[0]
    file_size = read_varint(buf)
    count = read_varint(buf)
    expected = -(-file_size // block_size) if block_size else 0
    if count != expected:
        raise ValueError(f"signature has {count} blocks, file size implies {expected}")
    blocks = []
    for i in range(count):
        (weak,) = struct.unpack("<I", _read_exact(buf, 4))
        blocks.append(BlockSig(i, weak, _read_exact(buf, strong_len)))
    return Signature(block_size, strong_len, file_size, blocks)


def encode_delta(delta: Delta) -> bytes:
    buf = io.BytesIO()
    buf.write(DELTA_MAGIC + bytes((VERSION,)))
    write_varint(buf, delta.block_size)
    write_varint(buf, delta.basis_size)
    write_varint(buf, delta.target_size)
    buf.write(bytes((len(delta.target_hash),)))
    buf.write(delta.target_hash)
    write_varint(buf, len(delta.ops))
    for op in delta.ops:
        if isinstance(op, Copy):
            buf.write(bytes((OP_COPY,)))
            write_varint(buf, op.start)
            write_varint(buf, op.count)
        else:
            buf.write(bytes((OP_LITERAL,)))
            write_varint(buf, len(op.data))
            buf.write(op.data)
    return buf.getvalue()


def decode_delta(data: bytes) -> Delta:
    buf = io.BytesIO(data)
    _header(buf, DELTA_MAGIC)
    block_size = read_varint(buf)
    basis_size = read_varint(buf)
    target_size = read_varint(buf)
    hash_len = _read_exact(buf, 1)[0]
    target_hash = _read_exact(buf, hash_len)
    count = read_varint(buf)
    ops = []
    for _ in range(count):
        tag = _read_exact(buf, 1)[0]
        if tag == OP_COPY:
            ops.append(Copy(read_varint(buf), read_varint(buf)))
        elif tag == OP_LITERAL:
            ops.append(Literal(_read_exact(buf, read_varint(buf))))
        else:
            raise ValueError(f"unknown op tag 0x{tag:02x}")
    if buf.read(1):
        raise ValueError("trailing bytes after delta")
    return Delta(block_size, basis_size, target_size, ops, target_hash)
