"""A content addressed, deduplicating chunk store.

Files are split with FastCDC, each chunk is stored once under its BLAKE2b
digest, and a file is represented by its manifest: the ordered list of
chunk digests. Storing ten slightly different versions of a file costs
roughly one copy plus the changed chunks.

The store is in memory by default; pass `root` to persist chunks on disk
using a git style two character fan out (root/ab/cdef...).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional

from .cdc import FastCDC


class ChunkStore:
    def __init__(self, chunker: Optional[FastCDC] = None, root: Optional[str] = None):
        self.chunker = chunker or FastCDC()
        self.root = Path(root) if root else None
        self._mem: Dict[str, bytes] = {}
        self.logical_bytes = 0
        self.stored_bytes = 0
        self.chunks_seen = 0
        self.chunks_stored = 0
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:]

    def has(self, digest: str) -> bool:
        if self.root:
            return self._path(digest).exists()
        return digest in self._mem

    def _put(self, digest: str, data: bytes) -> None:
        if self.root:
            p = self._path(digest)
            p.parent.mkdir(exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)  # atomic: a crash never leaves a torn chunk
        else:
            self._mem[digest] = data

    def get_chunk(self, digest: str) -> bytes:
        data = self._path(digest).read_bytes() if self.root else self._mem[digest]
        if hashlib.blake2b(data, digest_size=32).hexdigest() != digest:
            raise IOError(f"chunk {digest[:12]} is corrupt")
        return data

    def add(self, data: bytes) -> List[str]:
        """Store `data`, returning its manifest."""
        manifest = []
        view = memoryview(data)
        for ch in self.chunker.chunks(data):
            self.chunks_seen += 1
            if not self.has(ch.digest):
                self._put(ch.digest, bytes(view[ch.offset:ch.end]))
                self.chunks_stored += 1
                self.stored_bytes += ch.length
            manifest.append(ch.digest)
        self.logical_bytes += len(data)
        return manifest

    def get(self, manifest: List[str]) -> bytes:
        return b"".join(self.get_chunk(d) for d in manifest)

    @property
    def dedup_ratio(self) -> float:
        """logical / stored. 1.0 means no savings, 5.0 means 5x smaller."""
        return self.logical_bytes / self.stored_bytes if self.stored_bytes else 1.0
