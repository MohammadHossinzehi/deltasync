"""Command line interface.

    python -m deltasync signature OLD SIG
    python -m deltasync delta SIG NEW DELTA
    python -m deltasync patch OLD DELTA OUT
    python -m deltasync chunk FILE [--avg 8192]
    python -m deltasync dedup FILE [FILE ...] [--avg 8192]
    python -m deltasync demo
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from pathlib import Path

from . import (ChunkStore, FastCDC, apply_delta, decode_delta, decode_signature,
               encode_delta, encode_signature, make_delta, make_signature)


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def cmd_signature(a) -> int:
    basis = Path(a.old).read_bytes()
    sig = make_signature(basis, a.block_size)
    raw = encode_signature(sig)
    Path(a.sig).write_bytes(raw)
    print(f"{len(sig.blocks)} blocks of {sig.block_size} B -> signature {_human(len(raw))}")
    return 0


def cmd_delta(a) -> int:
    sig = decode_signature(Path(a.sig).read_bytes())
    new = Path(a.new).read_bytes()
    t0 = time.perf_counter()
    d = make_delta(sig, new)
    dt = time.perf_counter() - t0
    raw = encode_delta(d)
    Path(a.delta).write_bytes(raw)
    s = d.stats()
    print(f"target {_human(s['target_size'])}: reused {s['reuse_ratio']:.1%}, "
          f"{s['copy_ops']} copy ops, {_human(s['literal_bytes'])} literal "
          f"-> delta {_human(len(raw))} in {dt:.2f}s")
    return 0


def cmd_patch(a) -> int:
    basis = Path(a.old).read_bytes()
    d = decode_delta(Path(a.delta).read_bytes())
    out = apply_delta(basis, d)
    Path(a.out).write_bytes(out)
    print(f"wrote {_human(len(out))} to {a.out} (hash verified)")
    return 0


def _chunker(a) -> FastCDC:
    return FastCDC(a.avg // 4, a.avg, a.avg * 8)


def cmd_chunk(a) -> int:
    data = Path(a.file).read_bytes()
    sizes = [c.length for c in _chunker(a).chunks(data)]
    if not sizes:
        print("empty file")
        return 0
    mean = sum(sizes) / len(sizes)
    print(f"{len(sizes)} chunks, mean {_human(mean)}, min {_human(min(sizes))}, "
          f"max {_human(max(sizes))}")
    buckets = Counter(min(s * 8 // (a.avg * 2), 15) for s in sizes)
    peak = max(buckets.values())
    for b in range(16):
        lo = b * a.avg * 2 // 8
        bar = "#" * round(40 * buckets.get(b, 0) / peak)
        print(f"  {_human(lo):>9} | {bar}")
    return 0


def cmd_dedup(a) -> int:
    store = ChunkStore(_chunker(a))
    for f in a.files:
        before = store.stored_bytes
        m = store.add(Path(f).read_bytes())
        print(f"{f}: {len(m)} chunks, {_human(store.stored_bytes - before)} new")
    print(f"logical {_human(store.logical_bytes)}, stored {_human(store.stored_bytes)}, "
          f"dedup ratio {store.dedup_ratio:.2f}x")
    return 0


def _mutate(rng: random.Random, data: bytes, edits: int) -> bytes:
    buf = bytearray(data)
    for _ in range(edits):
        pos = rng.randrange(len(buf))
        kind = rng.choice(("insert", "delete", "replace"))
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 64)))
        if kind == "insert":
            buf[pos:pos] = blob
        elif kind == "delete":
            del buf[pos:pos + len(blob)]
        else:
            buf[pos:pos + len(blob)] = blob
    return bytes(buf)


def cmd_demo(a) -> int:
    rng = random.Random(a.seed)
    size = a.size
    words = [bytes(rng.choice(b"abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 9)))
             for _ in range(3000)]
    parts, n = [], 0
    while n < size:
        w = rng.choice(words) + b" "
        parts.append(w)
        n += len(w)
    old = b"".join(parts)[:size]
    new = _mutate(rng, old, a.edits)
    print(f"basis {_human(len(old))}, target {_human(len(new))}, {a.edits} random edits\n")

    sig = make_signature(old)
    sig_raw = encode_signature(sig)
    d = make_delta(sig, new)
    d_raw = encode_delta(d)
    assert apply_delta(old, d) == new
    wire = len(sig_raw) + len(d_raw)
    print("rsync style delta")
    print(f"  block size       {sig.block_size} B")
    print(f"  signature        {_human(len(sig_raw))}")
    print(f"  delta            {_human(len(d_raw))}  (reuse {d.stats()['reuse_ratio']:.1%})")
    print(f"  total on wire    {_human(wire)}  vs {_human(len(new))} full copy "
          f"({len(new) / wire:.1f}x less)\n")

    print("dedup after a 1 byte insertion at the front")
    shifted = b"!" + old
    for name, fixed in (("fixed 8 KiB blocks", True), ("FastCDC avg 8 KiB", False)):
        if fixed:
            blk = lambda b: {b[i:i + 8192] for i in range(0, len(b), 8192)}
            a_set, b_set = blk(old), blk(shifted)
            shared = len(a_set & b_set) / len(b_set)
        else:
            cdc = FastCDC()
            a_set = {c.digest for c in cdc.chunks(old)}
            b_set = {c.digest for c in cdc.chunks(shifted)}
            shared = len(a_set & b_set) / len(b_set)
        print(f"  {name:<20} {shared:6.1%} of chunks reused")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="deltasync", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("signature", help="compute block signature of a basis file")
    s.add_argument("old"); s.add_argument("sig")
    s.add_argument("--block-size", type=int, default=None)
    s.set_defaults(fn=cmd_signature)

    s = sub.add_parser("delta", help="encode NEW as a delta against a signature")
    s.add_argument("sig"); s.add_argument("new"); s.add_argument("delta")
    s.set_defaults(fn=cmd_delta)

    s = sub.add_parser("patch", help="rebuild NEW from OLD and a delta")
    s.add_argument("old"); s.add_argument("delta"); s.add_argument("out")
    s.set_defaults(fn=cmd_patch)

    for name, fn, h in (("chunk", cmd_chunk, "show the FastCDC chunk size histogram"),):
        s = sub.add_parser(name, help=h)
        s.add_argument("file")
        s.add_argument("--avg", type=int, default=8192)
        s.set_defaults(fn=fn)

    s = sub.add_parser("dedup", help="store files in a chunk store and report savings")
    s.add_argument("files", nargs="+")
    s.add_argument("--avg", type=int, default=8192)
    s.set_defaults(fn=cmd_dedup)

    s = sub.add_parser("demo", help="self contained demo on synthetic data")
    s.add_argument("--size", type=int, default=1 << 20)
    s.add_argument("--edits", type=int, default=20)
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(fn=cmd_demo)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (ValueError, OSError) as e:
        print(f"deltasync: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
