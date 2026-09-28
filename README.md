# deltasync

Two ideas that make syncing and backing up large files cheap, implemented from scratch in pure Python with zero dependencies:

1. **The rsync algorithm.** Send only what changed in a file when the sender has never seen the old version. The receiver ships a compact *signature* of its copy; the sender slides a rolling checksum across the new file and answers with a *delta* of COPY and LITERAL instructions.
2. **FastCDC content defined chunking.** Split files at boundaries chosen by the content itself, so an insertion near the start of a file does not shift every chunk after it. This is what makes deduplicating backup tools (restic, borg, casync) work.

```
$ python -m deltasync demo
basis 1.0 MiB, target 1023.9 KiB, 20 random edits

rsync style delta
  block size       1024 B
  signature        20.0 KiB
  delta            20.1 KiB  (reuse 98.1%)
  total on wire    40.1 KiB  vs 1023.9 KiB full copy (25.5x less)

dedup after a 1 byte insertion at the front
  fixed 8 KiB blocks     0.0% of chunks reused
  FastCDC avg 8 KiB     99.2% of chunks reused
```

That last pair of lines is the whole reason content defined chunking exists.

## Why this is useful

Copying a 2 GB VM image or database dump every night because a few pages changed is wasteful. The two techniques here attack that from different sides:

| | rsync delta | FastCDC store |
|---|---|---|
| Question it answers | "How do I turn *their* old file into my new one?" | "How do I store many versions without storing duplicates?" |
| Needs the old file locally | No, only its signature | No, only chunk hashes |
| Handles insertions/deletions | Yes (rolling window finds shifted blocks) | Yes (boundaries resynchronise) |
| Output | A patch | A manifest of content addressed chunks |

## Running it

Requires Python 3.9 or newer. Nothing to install.

```bash
git clone https://github.com/MohammadHossinzehi/deltasync
cd deltasync

python -m deltasync demo                         # synthetic end to end demo
python -m unittest discover -s tests -t .        # 38 tests, about 1 second
```

Syncing real files, the way rsync splits the work between two machines:

```bash
# on the machine that has the OLD file
python -m deltasync signature old.bin old.sig

# on the machine that has the NEW file (it only needs old.sig)
python -m deltasync delta old.sig new.bin changes.dlt

# back on the first machine
python -m deltasync patch old.bin changes.dlt rebuilt.bin
```

Chunking and deduplication:

```bash
python -m deltasync chunk big.tar --avg 8192     # chunk size histogram
python -m deltasync dedup v1.tar v2.tar v3.tar   # how much a chunk store would save
```

Optionally `pip install .` gives you a `deltasync` command.

### As a library

```python
from deltasync import make_signature, make_delta, apply_delta, ChunkStore

sig = make_signature(old_bytes)            # block size defaults to about sqrt(len)
delta = make_delta(sig, new_bytes)
assert apply_delta(old_bytes, delta) == new_bytes
print(delta.stats())                       # reuse ratio, literal bytes, op counts

store = ChunkStore(root="./chunks")        # or in memory with root=None
manifest = store.add(new_bytes)            # list of BLAKE2b chunk ids
assert store.get(manifest) == new_bytes
```

## How it works

### Rolling checksum (`rolling.py`)

The weak checksum over a window `x[k..l]` is Adler style: `a = sum(x)` and `b = sum((l+1-i) * x[i])`, both mod 2^16. Sliding the window by one byte is O(1):

```
a' = a - x[k] + x[l+1]
b' = b - n*x[k] + a'
```

That turns "try every offset of the new file against every block of the old one" into a single linear pass.

### Delta generation (`delta.py`)

At each offset the sender looks the weak checksum up in a hash table built from the signature. Only on a hit does it compute the strong hash (BLAKE2b truncated to 16 bytes). A confirmed match emits COPY and jumps a whole block ahead; a miss turns one byte into a literal and rolls on.

Details that matter in practice:

* **Run merging.** COPY ops for consecutive blocks collapse into `Copy(start, count)`, so an unchanged file is one instruction.
* **Run preference.** When the basis contains duplicate blocks, the matcher prefers the block that extends the current run. Without this, a file of repeated content produces a scattered op list (there is a test for exactly this case).
* **Short final block.** The last basis block is usually shorter than the block size, so it can only match the shrinking window at the very end of the target. The window shrinks with an O(1) `rollout`.
* **End to end integrity.** The delta carries a BLAKE2b hash of the target. Applying it to the wrong basis file fails loudly instead of silently producing garbage.

### FastCDC (`cdc.py`)

Follows Xia et al., *FastCDC* (USENIX ATC 2016):

* **Gear hash:** `fp = (fp << 1) + GEAR[byte]`, one shift, one add and one lookup per byte. Bit *j* of `fp` depends on the last *j+1* bytes, so masks use the **high** bits to get a wide effective window.
* **Cut point skipping:** no boundary is allowed before `min_size`, so those bytes are not hashed at all.
* **Normalized chunking:** a harder mask (more bits) before the average size and an easier mask after it. This pulls the size distribution toward the average; a test checks that the standard deviation really does drop compared to level 0.

The gear table comes from a fixed seed, so chunk boundaries are stable across runs and machines, which is required for deduplication to work at all.

### Wire format (`wire.py`)

Signatures and deltas serialise to a small binary format with a magic number, a version byte and LEB128 varints. The decoder validates block counts, rejects unknown op tags and trailing bytes, and never trusts lengths it cannot satisfy.

## Design decisions

* **Block size defaults to about sqrt(file size)**, the same heuristic rsync uses. Signature cost grows as n/B while the literal bytes lost around each edit grow as B; sqrt(n) balances the two.
* **BLAKE2b instead of MD4/MD5** for strong hashes: it is in the standard library, faster than SHA 2 and has no known collisions. 16 bytes per block is plenty for files of any realistic size.
* **Two separate algorithms, not one.** Fixed blocks plus a rolling search are ideal when you are diffing against *one* known file. Content defined chunks are ideal when you are deduplicating against *everything you have ever stored*. Keeping both side by side makes the tradeoff visible.
* **Disk store writes are atomic** (`write tmp` then `os.replace`) and every chunk read is verified against its content address.
* **Pure Python on purpose.** The goal is a readable reference. The inner loops are tight enough to handle a few MB per second; a C or Rust port would change the constants, not the design.

## Testing

`tests/` covers:

* the rolling checksum against from scratch recomputation at every offset for several window sizes, including all 0xFF input to catch overflow;
* delta round trips on 40 randomised edit scripts, plus targeted cases: identical files, reordered blocks, front insertions, repeated blocks, empty inputs, short tail blocks, wrong basis detection;
* FastCDC tiling, size bounds, mean chunk size, the effect of normalization, determinism and boundary resynchronisation after an insertion;
* the chunk store on disk, including corruption detection;
* the binary format (varint edge cases, truncated and trailing input);
* the CLI pipeline end to end.

## License

MIT
