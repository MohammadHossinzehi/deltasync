import random
import unittest

from deltasync import (Copy, Literal, apply_delta, make_delta, make_signature,
                       suggest_block_size)


def rand_bytes(rng, n):
    return bytes(rng.getrandbits(8) for _ in range(n))


def roundtrip(tc, old, new, block_size=None):
    sig = make_signature(old, block_size)
    d = make_delta(sig, new)
    tc.assertEqual(apply_delta(old, d), new)
    return d


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(42)

    def test_identical_file_is_one_copy_op(self):
        old = rand_bytes(self.rng, 10_000)
        d = roundtrip(self, old, old, 256)
        self.assertEqual(d.ops, [Copy(0, 40)])
        self.assertEqual(d.literal_bytes, 0)

    def test_identical_with_short_tail_block(self):
        old = rand_bytes(self.rng, 10_000 + 37)
        d = roundtrip(self, old, old, 256)
        self.assertEqual(d.ops, [Copy(0, 40)])  # 39 full blocks + a 53 byte tail
        self.assertEqual(d.literal_bytes, 0)

    def test_completely_different_is_all_literal(self):
        old = rand_bytes(self.rng, 5000)
        new = rand_bytes(self.rng, 5000)
        d = roundtrip(self, old, new, 128)
        self.assertEqual(d.literal_bytes, len(new))

    def test_insertion_at_front_still_reuses_everything_after(self):
        old = rand_bytes(self.rng, 20_000)
        new = b"HEADER!" + old
        d = roundtrip(self, old, new, 512)
        self.assertEqual(d.literal_bytes, 7)
        self.assertIsInstance(d.ops[0], Literal)

    def test_small_edit_in_middle(self):
        old = rand_bytes(self.rng, 50_000)
        new = old[:25_000] + b"patched" + old[25_010:]
        d = roundtrip(self, old, new, 1000)
        # at most the one damaged block plus the edit itself is literal
        self.assertLessEqual(d.literal_bytes, 1000 + 7)

    def test_block_reordering(self):
        old = rand_bytes(self.rng, 4096)
        blocks = [old[i:i + 512] for i in range(0, 4096, 512)]
        new = b"".join(reversed(blocks))
        d = roundtrip(self, old, new, 512)
        self.assertEqual(d.literal_bytes, 0)
        self.assertEqual([op.start for op in d.ops], list(range(7, -1, -1)))

    def test_repeated_blocks_keep_runs_long(self):
        block = rand_bytes(self.rng, 64)
        old = block * 50
        d = roundtrip(self, old, old, 64)
        self.assertEqual(d.ops, [Copy(0, 50)])

    def test_empty_cases(self):
        roundtrip(self, b"", b"")
        roundtrip(self, b"", b"abc")
        roundtrip(self, b"abc", b"")

    def test_new_shorter_than_one_block(self):
        old = rand_bytes(self.rng, 1000)
        roundtrip(self, old, old[:10], 256)
        roundtrip(self, old, old[-5:], 256)

    def test_short_tail_block_matches_at_end_of_target(self):
        old = rand_bytes(self.rng, 1000)  # tail block is 1000 - 3*256 = 232 bytes
        new = b"prefix" + old[768:]
        d = roundtrip(self, old, new, 256)
        self.assertEqual(d.ops, [Literal(b"prefix"), Copy(3, 1)])

    def test_short_tail_block_cannot_match_mid_file(self):
        # A full window is 256 bytes, so a 232 byte basis block can only be
        # compared against the shrinking window at the very end of the target.
        old = rand_bytes(self.rng, 1000)
        new = old[768:] + b"xyz"
        d = roundtrip(self, old, new, 256)
        self.assertEqual(d.literal_bytes, len(new))

    def test_randomised_edits_roundtrip(self):
        for trial in range(40):
            rng = random.Random(trial)
            old = rand_bytes(rng, rng.randint(0, 6000))
            new = bytearray(old)
            for _ in range(rng.randint(0, 8)):
                pos = rng.randint(0, len(new))
                op = rng.randint(0, 2)
                if op == 0:
                    new[pos:pos] = rand_bytes(rng, rng.randint(1, 300))
                elif op == 1:
                    del new[pos:pos + rng.randint(1, 300)]
                else:
                    new[pos:pos + 5] = rand_bytes(rng, 5)
            roundtrip(self, old, bytes(new), rng.choice([16, 64, 100, 512]))

    def test_wrong_basis_is_detected(self):
        old = rand_bytes(self.rng, 4000)
        new = old[:2000] + b"x" + old[2000:]
        d = make_delta(make_signature(old, 256), new)
        tampered = bytearray(old)
        tampered[10] ^= 1
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            apply_delta(bytes(tampered), d)
        with self.assertRaisesRegex(ValueError, "basis is"):
            apply_delta(old[:-1], d)

    def test_suggest_block_size(self):
        self.assertEqual(suggest_block_size(0), 512)
        self.assertEqual(suggest_block_size(1 << 20), 1024)
        self.assertEqual(suggest_block_size(1 << 40), 1 << 17)
        self.assertEqual(suggest_block_size(10**8) % 8, 0)

    def test_signature_validation(self):
        with self.assertRaises(ValueError):
            make_signature(b"abc", 0)
        with self.assertRaises(ValueError):
            make_signature(b"abc", 4, strong_len=0)


if __name__ == "__main__":
    unittest.main()
