import random
import unittest

from deltasync.rolling import RollingChecksum, weak_checksum


class RollingChecksumTests(unittest.TestCase):
    def test_roll_matches_recompute_everywhere(self):
        rng = random.Random(1)
        data = bytes(rng.getrandbits(8) for _ in range(3000))
        for w in (1, 7, 64, 700):
            rc = RollingChecksum(data[:w])
            for i in range(1, len(data) - w + 1):
                got = rc.roll(data[i - 1], data[i + w - 1])
                self.assertEqual(got, weak_checksum(data[i:i + w]), (w, i))

    def test_rollout_shrinks_window(self):
        data = bytes(range(200)) * 3
        rc = RollingChecksum(data[-50:])
        for k in range(1, 50):
            got = rc.rollout(data[-50 + k - 1])
            self.assertEqual(got, weak_checksum(data[-50 + k:]))

    def test_all_0xff_does_not_overflow_16_bits(self):
        data = b"\xff" * 100_000
        c = weak_checksum(data)
        self.assertLess(c, 1 << 32)
        rc = RollingChecksum(data[:4096])
        self.assertEqual(rc.roll(0xFF, 0xFF), weak_checksum(data[1:4097]))

    def test_empty(self):
        self.assertEqual(weak_checksum(b""), 0)
        self.assertEqual(RollingChecksum().digest, 0)

    def test_order_sensitive(self):
        self.assertNotEqual(weak_checksum(b"ab"), weak_checksum(b"ba"))


if __name__ == "__main__":
    unittest.main()
