"""deltasync: rsync style delta encoding and FastCDC content defined chunking.

Public API
----------
Rolling checksum:
    RollingChecksum

Fixed block delta (the rsync algorithm):
    make_signature(basis, block_size) -> Signature
    make_delta(signature, new_data) -> Delta
    apply_delta(basis, delta) -> bytes

Content defined chunking (FastCDC with normalized chunking):
    FastCDC(min_size, avg_size, max_size).chunks(data)
    ChunkStore for deduplicated storage

Binary wire formats:
    encode_signature / decode_signature
    encode_delta / decode_delta
"""

from .rolling import RollingChecksum, weak_checksum
from .signature import Signature, BlockSig, make_signature, suggest_block_size
from .delta import Delta, Copy, Literal, make_delta, apply_delta
from .cdc import FastCDC, Chunk
from .store import ChunkStore
from .wire import encode_signature, decode_signature, encode_delta, decode_delta

__all__ = [
    "RollingChecksum", "weak_checksum",
    "Signature", "BlockSig", "make_signature", "suggest_block_size",
    "Delta", "Copy", "Literal", "make_delta", "apply_delta",
    "FastCDC", "Chunk", "ChunkStore",
    "encode_signature", "decode_signature", "encode_delta", "decode_delta",
]

__version__ = "1.0.0"
