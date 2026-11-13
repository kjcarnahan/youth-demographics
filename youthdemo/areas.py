"""Set up and download data for any area in the U.S. from a list of ZIP codes.

    python3 -m youthdemo area "South Atlanta" --state GA --zips 30310,30315

Each area gets its own folder under data/areas/ with its own database,
config.json and web page.
"""
import io
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
import zipfile

from . import census, db, schools

GAZ_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
           "2020_Gazetteer/2020_Gaz_zcta_national.zip")

STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split(),
    "01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 "
    "35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 72".split()))


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def state_fips(s):
    s = s.strip().upper()
    if s in STATES:
        return STATES[s]
    if s.isdigit() and s.zfill(2) in STATES.values():
        return s.zfill(2)
    raise ValueError(f"Unknown state '{s}'. Use a two-letter code such as GA or CO.")


def parse_zips(text):
    zips = [z.strip() for z in re.split(r"[,\s]+", text) if z.strip()]
    bad = [z for z in zips if not re.fullmatch(r"\d{5}", z)]
    if bad:
        raise ValueError(f"Not five-digit ZIP codes: {', '.join(bad)}")
    return list(dict.fromkeys(zips))


def gazetteer():
    """{zip: (lat, lon)} for every ZIP code tabulation area (Census 2020 gazetteer)."""
    cache = os.path.join(db.ROOT, "data", "cache")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, "zcta-gazetteer-2020.txt")
    if not os.path.exists(path):
        req = urllib.request.Request(GAZ_URL, headers={"User-Agent": "youthdemo/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            zf = zipfile.ZipFile(io.BytesIO(r.read()))
        with open(path, "wb") as f:
            f.write(zf.read(zf.namelist()[0]))
    out = {}
    with open(path, encoding="utf-8") as f:
        header = [h.strip() for h in f.readline().split("\t")]
        gi, la, lo = header.index("GEOID"), header.index("INTPTLAT"), header.index("INTPTLONG")
        for line in f:
            p = [x.strip() for x in line.split("\t")]
            if len(p) > lo:
                out[p[gi]] = (float(p[la]), float(p[lo]))
    return out


def miles(a, b):
    """Great-circle distance in miles between two (lat, lon) points."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def choose_schools(directory, zips, centers, radius):
    """Middle and high schools located in the ZIP codes or within `radius` miles
    of any ZIP code's center. Returns [(school row, miles to nearest center or None)]."""
    zset, picked = set(zips), []
    for r in directory:
        if (r.get("highest_grade_offered") or -1) < 6:
            continue
        near = None
        if r.get("latitude") is not None and r.get("longitude") is not None and centers:
            near = min(miles((r["latitude"], r["longitude"]), c) for c in centers)
        if r.get("zip_location") in zset or (radius and near is not None and near <= radius):
            picked.append((r, near))
    return picked


def pick_over_years(directories, zips, centers, radius):
    """Union of choose_schools over directories listed oldest first, so schools
    that have since closed or been renamed are kept. Newest record wins for the
    name; the smallest distance seen is kept. -> {12-digit id: (row, miles)}"""
    picked = {}
    for rows in directories:
        for r, d in choose_schools(rows, zips, centers, radius):
            key = r["ncessch"].zfill(12)
            old = picked.get(key)
            if old is not None and old[1] is not None and (d is None or old[1] < d):
                d = old[1]
            picked[key] = (r, d)
    return picked


def build_config(name, state, zips, radius, gaz=None, directories=None, first_year=2010):
    fips = state_fips(state)
    gaz = gaz if gaz is not None else gazetteer()
    unknown = [z for z in zips if z not in gaz]
    if unknown:
        print(f"Warning: no Census map area for {', '.join(unknown)} (post office box or "
              "business-only ZIP codes have none); they are skipped.", file=sys.stderr)
    zips = [z for z in zips if z in gaz]
    if not zips:
        raise ValueError("None of those ZIP codes has a Census map area.")
    if directories is None:
        latest, rows = schools.latest_directory(int(fips))
        directories = [schools.fetch_all(f"directory/{y}/?fips={int(fips)}")
                       for y in range(first_year, latest)] + [rows]
        print(f"School directories {first_year}-{latest}: {sum(map(len, directories))} records",
              file=sys.stderr)
    directory = directories[-1]
    picked = pick_over_years(directories, zips, [gaz[z] for z in zips], radius)
    cities = {}
    for r in directory:
        if r.get("zip_location") in zips and r.get("city_location"):
            c = cities.setdefault(r["zip_location"], {})
            c[r["city_location"]] = c.get(r["city_location"], 0) + 1
    label = lambda z: f"{z} {max(cities[z], key=cities[z].get).title()}" if z in cities else f"{z} "
    return {
        "area_name": name,
        "state_fips": fips,
        "first_acs_year": 2011,
        "first_school_year": first_year,
        "zctas": {z: label(z) for z in zips},
        "places": {},
        "counties": {},
        "school_districts": {r["leaid"].zfill(7): r["lea_name"] for r, _ in picked.values()},
        "school_ids": {k: r["school_name"] for k, (r, _) in picked.items()},
        "school_info": {k: {"name": r["school_name"], "zip": r.get("zip_location"),
                            "city": r.get("city_location"), "leaid": r["leaid"].zfill(7)}
                        for k, (r, _) in picked.items()},
        "school_miles": {k: None if d is None else round(d, 1) for k, (_, d) in picked.items()},
        "include_schools": {},
    }


def run(name, state, zips, radius, fetch=True, build=True):
    folder = os.path.join(db.ROOT, "data", "areas", slug(name))
    os.makedirs(folder, exist_ok=True)
    cfg_path = os.path.join(folder, "config.json")
    # Point the rest of the app at this area's folder.
    db.DATA_DIR = folder
    db.DB_PATH = os.path.join(folder, "youthdemo.sqlite")
    db.CONFIG_PATH = cfg_path
    cfg = build_config(name, state, zips, radius)
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print(f"{name}: {len(cfg['zctas'])} ZIP codes, {len(cfg['school_ids'])} schools "
          f"in {len(cfg['school_districts'])} districts", file=sys.stderr)
    if not fetch:
        return cfg
    conn = db.connect()
    schools.refresh(conn, cfg)
    try:
        census.refresh(conn, cfg)
    except census.CensusError as e:
        print(f"Census data not downloaded: {e}", file=sys.stderr)
    if build:
        out = os.path.join(folder, slug(name) + ".html")
        env = dict(os.environ, YOUTHDEMO_DATA=folder, YOUTHDEMO_CONFIG=cfg_path)
        subprocess.run([sys.executable, os.path.join(db.ROOT, "web", "build_page.py"), out],
                       check=True, env=env)
    return cfg
