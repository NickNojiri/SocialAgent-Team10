"""Private set intersection (serving/psi.py) and match rooms (serving/match.py)."""
import pytest
from fastapi.testclient import TestClient

from src.ingestion.serving import admin, psi
from src.ingestion.serving.match import MatchError, MatchRooms
from src.ingestion.serving.tenant_auth import ENV_VAR, SCOPE_GUEST, mint_token

G = "424242424242424242"


def test_points_are_on_the_curve_and_round_trip():
    pt = psi.hash_to_point("reel:ABC123")
    assert psi._rhs(pt[0]) == pt[1] * pt[1] % psi.P
    assert psi.decode(psi.encode(pt)) == pt
    assert psi.hash_to_point("reel:ABC123") == pt and psi.hash_to_point("reel:ABC124") != pt


def test_blinding_commutes():
    a, b = psi.new_secret(), psi.new_secret()
    h = psi.hash_to_point("place:tim ho wan|irvine")
    assert psi.mul(b, psi.mul(a, h)) == psi.mul(a, psi.mul(b, h))


def test_each_side_learns_only_the_overlap():
    mine = ["reel:A1", "reel:B2", "place:chef tian|irvine", "reel:SECRET"]
    yours = ["reel:B2", "place:chef tian|irvine", "reel:OTHER", "reel:ZZ"]
    a_learns, b_learns = psi.run(mine, yours)
    assert a_learns == ["reel:B2", "place:chef tian|irvine"]
    assert b_learns == ["reel:B2", "place:chef tian|irvine"]


def test_blinded_points_reveal_nothing_simple():
    a = psi.new_secret()
    blinded = psi.blind(["reel:B2"], a)[0]
    assert blinded != psi.encode(psi.hash_to_point("reel:B2"))           # not the plain hash
    assert psi.blind(["reel:B2"], psi.new_secret())[0] != blinded        # a fresh secret each match


@pytest.mark.parametrize("bad", ["", "04" + "1" * 64, "02" + "g" * 64, "02" + "f" * 64, "02" + "0" * 63])
def test_decode_refuses_anything_but_a_curve_point(bad):
    with pytest.raises(ValueError):
        psi.decode(bad)


def test_rooms_walk_the_three_steps_once_each():
    rooms = MatchRooms()
    r = rooms.create(G, "Nick")
    a, b = psi.new_secret(), psi.new_secret()
    a1 = psi.blind(["reel:X", "reel:Y"], a)
    with pytest.raises(MatchError):
        rooms.step_a(G, r["room"], "wrong-key", a1)
    rooms.step_a(G, r["room"], r["key"], a1)
    with pytest.raises(MatchError):
        rooms.step_b(G, r["room"], psi.reblind(a1, b)[:1], psi.blind(["reel:Y"], b))   # A2 must match A1
    joined = rooms.step_b(G, r["room"], psi.reblind(a1, b), psi.blind(["reel:Y"], b), "Maya")
    assert joined["stage"] == "joined" and joined["name_b"] == "Maya"
    with pytest.raises(MatchError):
        rooms.step_b(G, r["room"], psi.reblind(a1, b), psi.blind(["reel:Y"], b))         # one friend per room
    done = rooms.finish(G, r["room"], r["key"], psi.reblind(joined["b1"], a))
    assert done["stage"] == "done"
    with pytest.raises(MatchError) as e:
        rooms.get("another-guild", r["room"])
    assert e.value.status == 404
    assert rooms.forget_guild(G) == 1


def test_rooms_refuse_non_points_and_oversized_lists():
    rooms = MatchRooms()
    r = rooms.create(G)
    with pytest.raises(MatchError):
        rooms.step_a(G, r["room"], r["key"], ["not a point"])
    with pytest.raises(MatchError):
        rooms.step_a(G, r["room"], r["key"], [])


def test_a_whole_match_through_the_api(monkeypatch):
    """Two phones, the real endpoints: each learns the shared items and nothing else,
    and the server holds only blinded points."""
    monkeypatch.setenv(ENV_VAR, "1" * 64)
    monkeypatch.setattr(admin, "_matches", MatchRooms())
    c = TestClient(admin.app)
    hdr = {"X-Tenant-Token": mint_token(G, scope=SCOPE_GUEST)}
    nick = ["reel:DfKOJ5Qx9jN", "reel:TIMHOWAN1", "place:chef tian|irvine ca", "reel:PRIVATE9"]
    maya = ["reel:TIMHOWAN1", "place:chef tian|irvine ca", "reel:MAYAONLY"]
    a, b = psi.new_secret(), psi.new_secret()
    room = c.post("/api/match", headers=hdr, json={"guild_id": G, "name": "Nick"}).json()
    a1 = psi.blind(nick, a)
    assert c.post(f"/api/match/{room['room']}/a", headers=hdr, json={"guild_id": G, "key": room["key"], "a1": a1}).status_code == 200
    seen = c.get(f"/api/match/{room['room']}?guild_id={G}", headers=hdr).json()
    j = c.post(f"/api/match/{room['room']}/b", headers=hdr,
               json={"guild_id": G, "name": "Maya", "a2": psi.reblind(seen["a1"], b), "b1": psi.blind(maya, b)}).json()
    c.post(f"/api/match/{room['room']}/finish", headers=hdr,
           json={"guild_id": G, "key": room["key"], "b2": psi.reblind(j["b1"], a)})
    final = c.get(f"/api/match/{room['room']}?guild_id={G}", headers=hdr).json()
    sa, sb = set(final["a2"]), set(final["b2"])
    assert [x for x, p in zip(nick, final["a2"]) if p in sb] == ["reel:TIMHOWAN1", "place:chef tian|irvine ca"]
    assert [y for y, q in zip(maya, final["b2"]) if q in sa] == ["reel:TIMHOWAN1", "place:chef tian|irvine ca"]
    assert "TIMHOWAN" not in str(final) and "chef" not in str(final)                # only points on the server
