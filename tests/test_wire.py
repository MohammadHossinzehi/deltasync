import io
import random
import unittest

from deltasync import (apply_delta, decode_delta, decode_signature, encode_delta,
                       encode_signature, make_delta, make_signature)
from deltasync.wire import read_varint, write_varint


class WireTests(unittest.TestCase):
    def test_varint_roundtrip(self):
        for v in (0, 1, 127, 128, 300, 2**32, 2**63 - 1):
            buf = io.BytesIO()
            write_varint(buf, v)
            buf.seek(0)
            self.assertEqual(read_varint(buf), v)
        buf = io.BytesIO()
        write_varint(buf, 127)
        self.assertEqual(len(buf.getvalue()), 1)

    def test_signature_roundtrip(self):
        data = random.Random(1).randbytes(10_001)
        sig = make_signature(data, 1000, strong_len=8)
        back = decode_signature(encode_signature(sig))
        self.assertEqual(back.blocks, sig.blocks)
        self.assertEqual((back.block_size, back.strong_len, back.file_size), (1000, 8, 10_001))

    def test_delta_roundtrip_and_apply(self):
        rng = random.Random(2)
        old = rng.randbytes(20_000)
        new = old[:5000] + rng.randbytes(300) + old[5000:]
        d = make_delta(make_signature(old, 512), new)
        back = decode_delta(encode_delta(d))
        self.assertEqual(back, d)
        self.assertEqual(apply_delta(old, back), new)

    def test_rejects_garbage(self):
        d = encode_delta(make_delta(make_signature(b"abc" * 100, 64), b"abc" * 101))
        with self.assertRaises(ValueError):
            decode_delta(b"XXXX" + d[4:])
        with self.assertRaises(ValueError):
            decode_delta(d[:-1])
        with self.assertRaises(ValueError):
            decode_delta(d + b"\x00")
        s = encode_signature(make_signature(b"x" * 1000, 100))
        with self.assertRaises(ValueError):
            decode_signature(s[:-3])


if __name__ == "__main__":
    unittest.main()
