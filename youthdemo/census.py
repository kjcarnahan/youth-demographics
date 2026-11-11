"""Download from the U.S. Census Bureau API (api.census.gov).

Sources used
  * ACS 5-year detailed tables, one call per vintage and geography type.
  * 2020 Decennial Census DHC table PCT12 (population by single year of age).

The API requires a free key (https://api.census.gov/data/key_signup.html);
set it in the CENSUS_API_KEY environment variable.
"""
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import db

BASE = "https://api.census.gov/data"

# Variables pulled for every ACS vintage (time series). True = also pull the
# margin of error.
SERIES_VARS = {
    "B01001_001": True,   # total population
    "B01001_005": True,   # male 10-14
    "B01001_006": True,   # male 15-17
    "B01001_007": True,   # male 18-19
    "B01001_029": True,   # female 10-14
    "B01001_030": True,   # female 15-17
    "B01001_031": True,   # female 18-19
    "B09001_001": True,   # population under 18
    "B09001_007": True,   # 9-11
    "B09001_008": True,   # 12-14
    "B09001_009": True,   # 15-17
    "B14001_006": True,   # enrolled in school, grades 5-8
    "B14001_007": True,   # enrolled in school, grades 9-12
}
# Ages 10-14 and 15-17 by sex for race/ethnicity iterations of B01001:
# H = White alone not Hispanic, B = Black alone, D = Asian alone, I = Hispanic.
for _t in "HBDI":
    for _v in ("005", "006", "020", "021"):
        SERIES_VARS[f"B01001{_t}_{_v}"] = False

# Extra variables pulled for the latest vintage only (community profile).
PROFILE_VARS = [
    "B19013_001",                # median household income
    "B11005_001", "B11005_002",  # households; households with children <18
    "B03002_001", "B03002_003", "B03002_004", "B03002_006", "B03002_012",
    "C16007_002", "C16007_003",  # age 5-17; of those, speak only English
    "B25003_001", "B25003_002",  # occupied homes; owner-occupied
    "B05002_001", "B05002_013",  # population; foreign born
]


class CensusError(Exception):
    pass


class FatalCensusError(CensusError):
    """Retrying or trying another year will not help (no network, no key)."""


KEY_HELP = ("The Census API requires a free key. Get one at "
            "https://api.census.gov/data/key_signup.html, then run:  "
            "export CENSUS_API_KEY=yourkey")


def _cache_path(url):
    d = os.path.join(db.DATA_DIR, "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, hashlib.sha1(url.encode()).hexdigest() + ".json")


def fetch_json(path, params, use_cache=True):
    """GET a Census API table; returns the list-of-rows JSON. Caches on disk."""
    query = urllib.parse.urlencode(params, safe=":,*()")
    url = f"{BASE}/{path}?{query}"
    cp = _cache_path(url)
    if use_cache and os.path.exists(cp):
        with open(cp) as f:
            return json.load(f)
    full = url
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        full += "&key=" + urllib.parse.quote(key)
    try:
        req = urllib.request.Request(full, headers={"User-Agent": "youthdemo/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            final_url = resp.geturl()
    except urllib.error.HTTPError as e:
        raise CensusError(f"HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, OSError) as e:
        raise FatalCensusError(f"Cannot reach api.census.gov ({e})") from e
    if "missing_key" in final_url or "invalid_key" in final_url or "<title>Missing Key" in body \
            or "<title>Invalid Key" in body:
        raise FatalCensusError(("The key in CENSUS_API_KEY was rejected. " if key else "") + KEY_HELP)
    if not body.strip():
        raise CensusError(f"Empty response for {url}")
    try:
        data = json.loads(body)
    except ValueError as e:
        raise CensusError(f"Non-JSON response for {url}: {body[:200]}") from e
    with open(cp, "w") as f:
        json.dump(data, f)
    return data


def _num(s):
    """Census numbers arrive as strings; large negatives are 'not available'."""
    if s is None or s == "":
        return None
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return None if v <= -111111111 else v


def parse_rows(rows, kind):
    """list-of-rows -> {geo_id: {column: raw string}}"""
    if not rows or len(rows) < 2:
        return {}
    header = rows[0]
    geo_col = {"zcta": "zip code tabulation area", "place": "place", "county": "county"}[kind]
    if geo_col not in header:
        raise CensusError(f"Response has no '{geo_col}' column: {header}")
    out = {}
    for r in rows[1:]:
        rec = dict(zip(header, r))
        out[f"{kind}:{rec[geo_col]}"] = rec
    return out


def _geo_clauses(kind, codes, state):
    """Possible for/in clauses, in the order to try them."""
    joined = ",".join(codes)
    if kind == "zcta":
        f = {"for": f"zip code tabulation area:{joined}"}
        # Some vintages nest ZCTAs under state, others do not.
        return [f, {**f, "in": f"state:{state}"}]
    return [{"for": f"{kind}:{joined}", "in": f"state:{state}"}]


def _request_geo(path, get, kind, codes, state):
    last = None
    for clause in _geo_clauses(kind, codes, state):
        try:
            return parse_rows(fetch_json(path, {"get": get, **clause}), kind)
        except FatalCensusError:
            raise
        except CensusError as e:
            last = e
    raise last


def fetch_acs(year, kind, codes, state, var_moe):
    """-> ({geo_id: {var: (est, moe)}}, [warnings]). var_moe: {var: want_moe}"""
    def cols(vs):
        c = []
        for v in vs:
            c.append(v + "E")
            if var_moe[v]:
                c.append(v + "M")
        return c

    def run(vs):
        recs = _request_geo(f"{year}/acs/acs5", ",".join(["NAME"] + cols(vs)), kind, codes, state)
        out = {}
        for gid, rec in recs.items():
            out[gid] = {v: (_num(rec.get(v + "E")), _num(rec.get(v + "M")) if var_moe[v] else None)
                        for v in vs}
        return out

    allv = list(var_moe)
    warnings = []
    # The API allows 50 columns per call.
    chunks, cur = [], []
    for v in allv:
        if len(cols(cur + [v])) > 48:
            chunks.append(cur)
            cur = []
        cur.append(v)
    chunks.append(cur)
    result = {}
    for chunk in chunks:
        try:
            part = run(chunk)
        except FatalCensusError:
            raise
        except CensusError:
            # One missing table fails the whole call: retry table by table.
            part = {}
            tables = {}
            for v in chunk:
                tables.setdefault(v.split("_")[0], []).append(v)
            for t, vs in tables.items():
                try:
                    for gid, d in run(vs).items():
                        part.setdefault(gid, {}).update(d)
                except FatalCensusError:
                    raise
                except CensusError as e:
                    warnings.append(f"{year} {kind} table {t}: {e}")
        for gid, d in part.items():
            result.setdefault(gid, {}).update(d)
    return result, warnings


def fetch_single_age_2020(kind, codes, state):
    """2020 Census DHC PCT12 -> {geo_id: {age: population}} (both sexes, 0-99)."""
    recs = _request_geo("2020/dec/dhc", "group(PCT12)", kind, codes, state)
    out = {}
    for gid, rec in recs.items():
        ages = {}
        for age in range(100):
            m = _num(rec.get(f"PCT12_{3 + age:03d}N"))
            f = _num(rec.get(f"PCT12_{107 + age:03d}N"))
            if m is None or f is None:
                break
            ages[age] = m + f
        if ages:
            out[gid] = ages
    return out


def latest_acs_year(state):
    this = datetime.date.today().year
    for y in range(this, 2014, -1):
        try:
            fetch_json(f"{y}/acs/acs5", {"get": "B01001_001E", "for": f"state:{state}"})
            return y
        except FatalCensusError:
            raise
        except CensusError:
            pass
    raise CensusError("No ACS 5-year vintage found")


def refresh(conn, cfg, log=lambda m: print(m, file=sys.stderr)):
    """Download everything in config.json into the database."""
    state = cfg["state_fips"]
    db.sync_geos(conn, cfg)
    kinds = {"zcta": list(cfg.get("zctas", {})), "place": list(cfg.get("places", {})),
             "county": list(cfg.get("counties", {}))}
    latest = latest_acs_year(state)
    log(f"Latest ACS 5-year vintage: {latest}")
    warnings = []
    for year in range(cfg.get("first_acs_year", 2011), latest + 1):
        var_moe = dict(SERIES_VARS)
        if year == latest:
            var_moe.update({v: False for v in PROFILE_VARS})
        n = 0
        for kind, codes in kinds.items():
            if not codes:
                continue
            data, w = fetch_acs(year, kind, codes, state, var_moe)
            warnings += w
            for gid, vals in data.items():
                for var, (est, moe) in vals.items():
                    conn.execute("INSERT OR REPLACE INTO acs VALUES (?,?,?,?,?)",
                                 (gid, year, var, est, moe))
                    n += 1
        conn.commit()
        log(f"ACS {year - 4}-{year}: {n} values")
    for kind, codes in kinds.items():
        if not codes:
            continue
        try:
            data = fetch_single_age_2020(kind, codes, state)
        except FatalCensusError:
            raise
        except CensusError as e:
            warnings.append(f"2020 Census single-year ages for {kind}: {e}")
            continue
        for gid, ages in data.items():
            for age, pop in ages.items():
                conn.execute("INSERT OR REPLACE INTO census_age VALUES (?,?,?,?)",
                             (gid, 2020, age, pop))
        log(f"2020 Census ages ({kind}): {len(data)} geographies")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('fetched_at', ?)",
                 (datetime.datetime.now().isoformat(timespec="minutes"),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('warnings', ?)", (json.dumps(warnings),))
    conn.commit()
    for w in warnings:
        log("WARNING: " + w)
    return warnings
