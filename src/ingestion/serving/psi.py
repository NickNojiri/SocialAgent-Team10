"""Private set intersection (PSI) on P-256: the reference for the phone app's "Match".

Two people learn which items they both have (reels, places) and nothing else about
each other's lists. Each phone hashes its items to curve points and multiplies them by
a secret only it knows. Multiplying is commutative, so H(x)·a·b == H(x)·b·a: after
each side applies its secret to the other's blinded list, shared items line up, and
nothing else can be read back (the elliptic-curve Diffie-Hellman assumption).

Protocol (the server is only a mailbox and sees blinded points):
  1. A sends  A1 = [H(x)·a for x in A's items]           (A's own order)
  2. B sends  A2 = [p·b for p in A1]  (same order)  and  B1 = [H(y)·b for y in B's items]
  3. A sends  B2 = [q·a for q in B1]  (same order)
  A: its item i is shared  iff  A2[i] in set(B2).   B: its item j is shared iff B2[j] in set(A2).
Each side also learns the size of the other's list. The phone app (mobile_app.py) runs
the same math in JavaScript; test_psi.py checks the two agree point for point.
"""
from __future__ import annotations

import hashlib
import re
import secrets

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
DOMAIN = "spotbot-psi-v1"
POINT_HEX = re.compile(r"^0[23][0-9a-f]{64}$")

Point = tuple[int, int]


def _rhs(x: int) -> int:
    return (x * x * x + A * x + B) % P


def _sqrt(v: int) -> int | None:
    y = pow(v, (P + 1) // 4, P)            # P ≡ 3 (mod 4)
    return y if y * y % P == v else None


def hash_to_point(item: str) -> Point:
    """Try-and-increment: the first SHA-256(domain|ctr|item) that is an x on the curve."""
    ctr = 0
    while True:
        x = int.from_bytes(hashlib.sha256(f"{DOMAIN}|{ctr}|{item}".encode("utf-8")).digest(), "big") % P
        y = _sqrt(_rhs(x))
        if y is not None:
            return x, (y if y % 2 == 0 else P - y)
        ctr += 1


def _add(p1: Point | None, p2: Point | None) -> Point | None:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def mul(k: int, pt: Point) -> Point:
    """k·pt, double-and-add (a reference, not constant-time)."""
    out, add = None, pt
    k %= N
    while k:
        if k & 1:
            out = _add(out, add)
        add = _add(add, add)
        k >>= 1
    if out is None:
        raise ValueError("scalar is a multiple of the group order")
    return out


def encode(pt: Point) -> str:
    x, y = pt
    return ("02" if y % 2 == 0 else "03") + format(x, "064x")


def decode(s: str) -> Point:
    """A compressed point, checked to lie on the curve (refuses anything else)."""
    if not POINT_HEX.match(s or ""):
        raise ValueError("not a compressed P-256 point")
    x = int(s[2:], 16)
    if x >= P:
        raise ValueError("x out of range")
    y = _sqrt(_rhs(x))
    if y is None:
        raise ValueError("not on the curve")
    if y % 2 != int(s[:2], 16) % 2:
        y = P - y
    return x, y


def new_secret() -> int:
    return secrets.randbelow(N - 1) + 1


def blind(items: list[str], k: int) -> list[str]:
    return [encode(mul(k, hash_to_point(i))) for i in items]


def reblind(points: list[str], k: int) -> list[str]:
    return [encode(mul(k, decode(p))) for p in points]


def run(items_a: list[str], items_b: list[str]) -> tuple[list[str], list[str]]:
    """Both sides of one match, in order; returns (what A learns, what B learns)."""
    a, b = new_secret(), new_secret()
    a1 = blind(items_a, a)
    a2, b1 = reblind(a1, b), blind(items_b, b)
    b2 = reblind(b1, a)
    sa, sb = set(a2), set(b2)
    return [x for x, p in zip(items_a, a2) if p in sb], [y for y, q in zip(items_b, b2) if q in sa]
