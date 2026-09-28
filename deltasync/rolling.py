"""Adler style rolling checksum, as used by rsync for its weak block hash.

For a window x[k..l] (inclusive) the two halves are

    a = sum(x[i])                          mod M
    b = sum((l - i + 1) * x[i])            mod M

and the packed checksum is  a | (b << 16).  The useful property is that
sliding the window one byte to the right is O(1):

    a' = a - x[k] + x[l+1]
    b' = b - n * x[k] + a'

where n is the window length. That turns "check every offset of the new
file against every block of the old one" from O(n * block) into O(n).
"""

M = 1 << 16
MASK = M - 1


def weak_checksum(data: bytes) -> int:
    """Checksum of a whole buffer, computed from scratch."""
    a = 0
    b = 0
    n = len(data)
    for i, x in enumerate(data):
        a += x
        b += (n - i) * x
    return (a & MASK) | ((b & MASK) << 16)


class RollingChecksum:
    """A checksum over a fixed length window that can slide one byte at a time."""

    __slots__ = ("a", "b", "n")

    def __init__(self, window: bytes = b""):
        self.n = len(window)
        a = 0
        b = 0
        n = self.n
        for i, x in enumerate(window):
            a += x
            b += (n - i) * x
        self.a = a & MASK
        self.b = b & MASK

    @property
    def digest(self) -> int:
        return self.a | (self.b << 16)

    def roll(self, out_byte: int, in_byte: int) -> int:
        """Drop out_byte from the left of the window, append in_byte on the right."""
        a = (self.a - out_byte + in_byte) & MASK
        self.b = (self.b - self.n * out_byte + a) & MASK
        self.a = a
        return self.a | (self.b << 16)

    def rollout(self, out_byte: int) -> int:
        """Shrink the window from the left (used near end of file)."""
        self.a = (self.a - out_byte) & MASK
        self.b = (self.b - self.n * out_byte) & MASK
        self.n -= 1
        return self.a | (self.b << 16)
