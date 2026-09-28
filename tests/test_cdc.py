import random
import statistics
import tempfile
import unittest

from deltasync import ChunkStore, FastCDC


def rand_bytes(seed, n):
    return random.Random(seed).randbytes(n)


class FastCDCTests(unittest.TestCase):
    def test_chunks_tile_the_input(self):
        data = rand_bytes(1, 300_000)
        chunks = list(FastCDC(512, 2048, 8192).chunks(data))
        self.assertEqual(chunks[0].offset, 0)
        for a, b in zip(chunks, chunks[1:]):
            self.assertEqual(a.end, b.offset)
        self.assertEqual(chunks[-1].end, len(data))

    def test_size_bounds_respected(self):
        cdc = FastCDC(1024, 4096, 16384)
        data = rand_bytes(2, 500_000)
        sizes = [c.length for c in cdc.chunks(data)]
        self.assertTrue(all(s <= 16384 for s in sizes))
        self.assertTrue(all(s >= 1024 for s in sizes[:-1]))

    def test_mean_is_near_average(self):
        cdc = FastCDC(1024, 4096, 32768)
        sizes = [c.length for c in cdc.chunks(rand_bytes(3, 2_000_000))]
        mean = statistics.mean(sizes)
        self.assertTrue(0.7 * 4096 < mean < 1.5 * 4096, mean)

    def test_normalization_tightens_distribution(self):
        data = rand_bytes(4, 2_000_000)
        spread = {}
        for level in (0, 2):
            sizes = [c.length for c in FastCDC(1024, 4096, 32768, level).chunks(data)]
            spread[level] = statistics.pstdev(sizes)
        self.assertLess(spread[2], spread[0])

    def test_deterministic(self):
        data = rand_bytes(5, 100_000)
        a = [c.digest for c in FastCDC().chunks(data)]
        b = [c.digest for c in FastCDC().chunks(data)]
        self.assertEqual(a, b)

    def test_boundaries_resync_after_insertion(self):
        data = rand_bytes(6, 400_000)
        cdc = FastCDC(512, 2048, 16384)
        before = {c.digest for c in cdc.chunks(data)}
        edited = data[:1000] + b"inserted bytes" + data[1000:]
        after = [c.digest for c in cdc.chunks(edited)]
        shared = sum(d in before for d in after) / len(after)
        self.assertGreater(shared, 0.95)

    def test_small_and_empty_inputs(self):
        cdc = FastCDC(2048, 8192, 65536)
        self.assertEqual(list(cdc.chunks(b"")), [])
        (only,) = list(cdc.chunks(b"tiny"))
        self.assertEqual((only.offset, only.length), (0, 4))

    def test_max_size_forced_on_uniform_data(self):
        cdc = FastCDC(256, 1024, 4096)
        sizes = [c.length for c in cdc.chunks(b"\x00" * 50_000)]
        self.assertEqual(sum(sizes), 50_000)
        self.assertTrue(all(s <= 4096 for s in sizes))

    def test_parameter_validation(self):
        for args in ((0, 64, 128), (100, 50, 200), (64, 100, 200), (16, 32, 64)):
            with self.assertRaises(ValueError):
                FastCDC(*args)
        with self.assertRaises(ValueError):
            FastCDC(64, 128, 256, normalization=4)


class ChunkStoreTests(unittest.TestCase):
    def test_roundtrip_and_dedup(self):
        store = ChunkStore(FastCDC(512, 2048, 8192))
        v1 = rand_bytes(7, 200_000)
        v2 = v1[:50_000] + b"edit" + v1[50_000:]
        m1 = store.add(v1)
        stored_after_v1 = store.stored_bytes
        m2 = store.add(v2)
        self.assertEqual(store.get(m1), v1)
        self.assertEqual(store.get(m2), v2)
        self.assertLess(store.stored_bytes - stored_after_v1, 20_000)
        self.assertGreater(store.dedup_ratio, 1.8)

    def test_disk_store_persists_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as root:
            data = rand_bytes(8, 50_000)
            m = ChunkStore(FastCDC(512, 2048, 8192), root=root).add(data)
            reopened = ChunkStore(FastCDC(512, 2048, 8192), root=root)
            self.assertEqual(reopened.get(m), data)
            path = reopened._path(m[0])
            raw = bytearray(path.read_bytes())
            raw[0] ^= 0xFF
            path.write_bytes(bytes(raw))
            with self.assertRaises(IOError):
                reopened.get(m)


if __name__ == "__main__":
    unittest.main()
