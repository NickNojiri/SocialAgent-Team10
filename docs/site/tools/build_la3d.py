"""Build the real-Los-Angeles data behind docs/site/spotbot.html's 3D city.

Downloads (cached under data/la3d/, which is never committed):
  - elevation: AWS Terrain Tiles, terrarium PNGs at zoom 12 (USGS / NOAA sources)
  - buildings and roads: OpenStreetMap through the Overpass API (ODbL, credited in the page footer)
  - textures: CC0 materials from ambientCG, 1K JPGs

Writes what the page loads (docs/site/la/):
  la.json        grid metadata and a few named spots (Figueroa St, a Hollywood Blvd storefront)
  height.bin     int16 decimetres, nz rows of nx samples, row 0 at the north edge
  buildings.bin  uint32 count, then per building: uint16 n, uint16 height dm, uint16 min height dm,
                 uint8 kind, uint8 0, n x (int16 x, int16 z) metres
  roads.bin      uint32 count, then per road: uint8 type, int8 layer, uint16 n, n x (int16 x, int16 z)
  tex/*.jpg      facade, roof and ground textures

Local frame: metres, x east, z south, origin at downtown LA (34.0522 N, 118.2437 W).
Usage: python docs/site/tools/build_la3d.py
"""
import io
import json
import math
import re
import struct
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import requests
import truststore
from PIL import Image
from shapely.geometry import LineString, Polygon

truststore.inject_into_ssl()                       # the home network re-signs TLS; trust the OS store

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "data" / "la3d"
OUT = ROOT / "docs" / "site" / "la"
LAT0, LON0 = 34.0522, -118.2437
SOUTH, WEST, NORTH, EAST = 33.96, -118.54, 34.30, -118.10
MX = 111320 * math.cos(math.radians(LAT0))        # metres per degree of longitude here
MZ = 110574                                        # metres per degree of latitude
GRID = 60                                          # metres between height samples
UA = {"User-Agent": "SpotBot-landing-page/1.0 (CSULB capstone; one-off build)"}

# where every building is kept, small ones included; elsewhere only the tall ones
ZONES = {
    "downtown":    (34.030, -118.275, 34.066, -118.230),
    "koreatown":   (34.054, -118.330, 34.070, -118.275),
    "hollywood":   (34.088, -118.345, 34.106, -118.310),
    "santamonica": (34.000, -118.505, 34.030, -118.480),
    "centurycity": (34.050, -118.425, 34.065, -118.405),
    "griffith":    (34.112, -118.310, 34.125, -118.292),
}
ROAD_TYPES = ["motorway", "motorway_link", "trunk", "trunk_link", "primary", "secondary", "tertiary", "residential"]
TEXTURES = {   # asset id -> what we keep from it
    "Facade001": {"glass": ["Color", "NormalGL"]},
    "Facade019A": {"office": ["Color", "NormalGL", "AmbientOcclusion"]},
    "Facade019B": {"office": ["Emission"], "glass": ["Emission"]},
    "Facade018A": {"brick": ["Color", "NormalGL", "AmbientOcclusion"]},
    "Facade018C": {"brick": ["Emission"]},
    "Facade020A": {"grid": ["Color", "NormalGL", "AmbientOcclusion"]},
    "Facade020B": {"grid": ["Emission"]},
    "PaintedPlaster017": {"plaster": ["Color", "NormalGL"]},
    "RoofingTiles013A": {"tiles": ["Color", "NormalGL"]},
    "Concrete034": {"roof": ["Color", "NormalGL"]},
    "Asphalt031": {"asphalt": ["Color", "NormalGL"]},
}


def local(lat, lon):
    return (lon - LON0) * MX, -(lat - LAT0) * MZ


def get(url, path, **kw):
    """Download once into the cache."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, headers=UA, timeout=600, **kw)
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(.2)
    return path


MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]


def overpass(name, query):
    """One Overpass query, cached; busy servers (429/5xx) get a pause and the next mirror."""
    path = CACHE / f"osm_{name}.json"
    if not path.exists():
        for attempt in range(6):
            url = MIRRORS[attempt % len(MIRRORS)]
            print(f"overpass: {name} via {url.split('/')[2]} …", flush=True)
            try:
                r = requests.post(url, data={"data": query}, headers=UA, timeout=900)
                if r.status_code == 200 and r.content.lstrip().startswith(b"{"):
                    path.write_bytes(r.content)
                    break
                print(f"  {r.status_code}, retrying", flush=True)
            except requests.RequestException as e:
                print(f"  {type(e).__name__}, retrying", flush=True)
            time.sleep(20 * (attempt + 1))
        else:
            raise RuntimeError(f"overpass {name}: every attempt failed")
    return json.loads(path.read_text(encoding="utf-8"))["elements"]


def tiled(name, template, n=3):
    """Run a query over an n x n grid of boxes covering the region; {box} in template is replaced."""
    out = []
    for i in range(n):
        for j in range(n):
            s = SOUTH + (NORTH - SOUTH) * i / n
            w = WEST + (EAST - WEST) * j / n
            box = f"{s:.4f},{w:.4f},{s + (NORTH - SOUTH) / n:.4f},{w + (EAST - WEST) / n:.4f}"
            out += overpass(f"{name}_{i}{j}", template.replace("{box}", box))
    return out


# ── elevation ──
def tile_xy(lat, lon, z):
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def build_height():
    z = 12
    x0, y0 = tile_xy(NORTH, WEST, z)
    x1, y1 = tile_xy(SOUTH, EAST, z)
    tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            p = get(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{tx}/{ty}.png", CACHE / "terrarium" / f"{z}_{tx}_{ty}.png")
            a = np.asarray(Image.open(p).convert("RGB"), np.float32)
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    xw, zn = local(NORTH, WEST)
    xe, zs = local(SOUTH, EAST)
    nx, nz = int((xe - xw) / GRID) + 1, int((zs - zn) / GRID) + 1
    xs = xw + np.arange(nx) * GRID
    zs_ = zn + np.arange(nz) * GRID
    X, Z = np.meshgrid(xs, zs_)
    lon = LON0 + X / MX
    lat = LAT0 - Z / MZ
    n = 2 ** z
    px = ((lon + 180) / 360 * n - tx0) * 256 - .5
    py = ((1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n - ty0) * 256 - .5
    ix, iy = np.floor(px).astype(int), np.floor(py).astype(int)
    fx, fy = px - ix, py - iy
    ix = np.clip(ix, 0, mosaic.shape[1] - 2)
    iy = np.clip(iy, 0, mosaic.shape[0] - 2)
    h = (mosaic[iy, ix] * (1 - fx) * (1 - fy) + mosaic[iy, ix + 1] * fx * (1 - fy)
         + mosaic[iy + 1, ix] * (1 - fx) * fy + mosaic[iy + 1, ix + 1] * fx * fy)
    (OUT / "height.bin").write_bytes(np.clip(np.round(h * 10), -32768, 32767).astype("<i2").tobytes())
    print(f"height: {nx} x {nz}, {h.min():.0f} to {h.max():.0f} m")
    return {"nx": nx, "nz": nz, "x0": round(xw, 2), "z0": round(zn, 2), "step": GRID}


# ── buildings ──
NUM = re.compile(r"[-+]?\d*\.?\d+")


def metres(v):
    if not v:
        return None
    m = NUM.search(v)
    if not m:
        return None
    x = float(m.group())
    return x * .3048 if ("'" in v or "ft" in v) else x


def height_of(tags):
    h = metres(tags.get("height"))
    if h is None and tags.get("building:levels"):
        lv = metres(tags["building:levels"])
        h = lv * 3.6 + 1 if lv else None
    if h is None:
        h = 5.5 if tags.get("building") in ("house", "residential", "detached", "garage", "garages", "semidetached_house") else 7
    mh = metres(tags.get("min_height"))
    if mh is None and tags.get("building:min_level"):
        lv = metres(tags["building:min_level"])
        mh = lv * 3.6 if lv else None
    return max(2.5, min(h, 600)), max(0, mh or 0)


def kind_of(tags, h, bid):
    if h >= 60:
        return 0                                   # glass tower
    if h >= 25:
        return 1 if bid % 3 else 2                 # office facades, two looks
    if h >= 11:
        return 3                                   # brick mid-rise
    if tags.get("building") in ("house", "residential", "detached", "semidetached_house", "bungalow"):
        return 5                                   # a house: stucco walls, hipped tile roof
    return 4                                       # low stucco commercial


def rings(el):
    if el["type"] == "way" and "geometry" in el:
        yield el["geometry"]
    elif el["type"] == "relation":
        for m in el.get("members", []):
            if m.get("role") == "outer" and "geometry" in m:
                yield m["geometry"]


def build_buildings():
    q = "[out:json][timeout:900];(" + "".join(
        f'way["building"]({s},{w},{n},{e});relation["building"]({s},{w},{n},{e});' for s, w, n, e in ZONES.values()) + ");out geom;"
    zone = overpass("zones", q)
    tall = tiled("tall", "[out:json][timeout:900];("
                 'way["building"]["height"~"^([2-9][0-9]|[1-9][0-9][0-9])(\\\\.[0-9]*)?( m)?$"]({box});'
                 'way["building"]["building:levels"~"^([6-9]|[1-9][0-9])$"]({box});'
                 'relation["building"]["height"~"^([2-9][0-9]|[1-9][0-9][0-9])(\\\\.[0-9]*)?( m)?$"]({box});'
                 ");out geom;")
    seen, out = set(), []
    for el in zone + tall:
        key = (el["type"], el["id"])
        if key in seen or "tags" not in el:
            continue
        seen.add(key)
        h, mh = height_of(el["tags"])
        k = kind_of(el["tags"], h, el["id"])
        for ring in rings(el):
            pts = [local(p["lat"], p["lon"]) for p in ring]
            if len(pts) < 4:
                continue
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area < 15 or poly.geom_type != "Polygon":
                continue
            poly = poly.simplify(.7, preserve_topology=True)
            c = list(poly.exterior.coords)[:-1]
            if len(c) < 3:
                continue
            out.append((k, h, mh, [(round(x), round(z)) for x, z in c]))
    buf = bytearray(struct.pack("<I", len(out)))
    for k, h, mh, c in out:
        buf += struct.pack("<HHHBB", len(c), min(65535, round(h * 10)), min(65535, round(mh * 10)), k, 0)
        buf += struct.pack(f"<{len(c) * 2}h", *[v for p in c for v in p])
    (OUT / "buildings.bin").write_bytes(bytes(buf))
    print(f"buildings: {len(out)} ({len(buf) / 1e6:.1f} MB)")


# ── roads ──
def build_roads():
    major = tiled("roads_major", '[out:json][timeout:900];way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|secondary|tertiary)$"]({box});out geom;', 2)
    minor = tiled("roads_minor", '[out:json][timeout:900];way["highway"~"^(residential|unclassified|living_street)$"]({box});out tags geom qt;', 3)
    roads, named, seen = [], {}, set()
    for el in major + minor:
        if el["id"] in seen:                       # ways crossing a tile edge come back twice
            continue
        seen.add(el["id"])
        tags = el.get("tags", {})
        if "geometry" not in el or tags.get("tunnel") in ("yes", "building_passage") or tags.get("area") == "yes":
            continue
        hw = tags.get("highway", "residential")
        t = ROAD_TYPES.index(hw) if hw in ROAD_TYPES else (5 if hw == "primary_link" else 7)
        if hw == "primary":
            t = 4
        layer = int(metres(tags.get("layer")) or 0) if tags.get("layer") else 0
        if tags.get("bridge") and tags.get("bridge") != "no" and layer <= 0:
            layer = 1
        pts = [local(p["lat"], p["lon"]) for p in el["geometry"]]
        line = LineString(pts).simplify(1.5 if t < 7 else 3)
        c = [(round(x), round(z)) for x, z in line.coords]
        if len(c) >= 2:
            roads.append((t, max(-4, min(6, layer)), c))
        name = tags.get("name", "")
        if name:
            named.setdefault(name, []).extend(pts)
    buf = bytearray(struct.pack("<I", len(roads)))
    for t, layer, c in roads:
        buf += struct.pack("<BbH", t, layer, len(c)) + struct.pack(f"<{len(c) * 2}h", *[v for p in c for v in p])
    (OUT / "roads.bin").write_bytes(bytes(buf))
    print(f"roads: {len(roads)} ({len(buf) / 1e6:.1f} MB)")
    return named


def street_line(named, pattern, box, samples=10):
    """The centre line of a named street inside a lat/lon box, as evenly spaced points."""
    s, w, n, e = box
    (xw, zn), (xe, zs) = local(n, w), local(s, e)
    pts = np.array([p for name, ps in named.items() if re.search(pattern, name) for p in ps
                    if xw <= p[0] <= xe and zn <= p[1] <= zs])
    if len(pts) < 2:
        return []
    mean = pts.mean(0)
    axis = np.linalg.svd(pts - mean)[2][0]
    t = (pts - mean) @ axis
    edges = np.linspace(t.min(), t.max(), samples + 1)
    line = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = pts[(t >= a) & (t <= b)]
        if len(sel):
            line.append([round(float(v), 1) for v in sel.mean(0)])
    return line


# ── textures ──
def build_textures():
    (OUT / "tex").mkdir(parents=True, exist_ok=True)
    for asset, uses in TEXTURES.items():
        z = get(f"https://ambientcg.com/get?file={asset}_1K-JPG.zip", CACHE / "ambientcg" / f"{asset}_1K-JPG.zip")
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            for name, maps in uses.items():
                for m in maps:
                    hit = [f for f in names if f.endswith(f"_{m}.jpg")]
                    if not hit:
                        print(f"  {asset}: no {m} map, skipped")
                        continue
                    im = Image.open(io.BytesIO(zf.read(hit[0]))).convert("RGB")
                    im.thumbnail((1024, 1024))
                    short = {"Color": "col", "NormalGL": "nrm", "AmbientOcclusion": "ao", "Emission": "emi"}[m]
                    im.save(OUT / "tex" / f"{name}_{short}.jpg", quality=82, optimize=True)
    total = sum(f.stat().st_size for f in (OUT / "tex").glob("*.jpg"))
    print(f"textures: {len(list((OUT / 'tex').glob('*.jpg')))} files, {total / 1e6:.1f} MB")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"origin": [LAT0, LON0], "grid": build_height()}
    build_buildings()
    named = build_roads()
    meta["figueroa"] = street_line(named, r"Figueroa Street", (34.042, -118.268, 34.058, -118.250))
    blvd = street_line(named, r"^Hollywood Boulevard$", (34.098, -118.334, 34.104, -118.322), samples=6)
    meta["hollywoodBlvd"] = blvd
    build_textures()
    (OUT / "la.json").write_text(json.dumps(meta, indent=1), encoding="utf-8", newline="\n")
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"total shipped: {total / 1e6:.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
