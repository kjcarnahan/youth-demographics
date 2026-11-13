"""Public-school enrollment by grade from the federal Common Core of Data,
served by the Urban Institute Education Data API (no key needed).

Year means the fall of the school year: 2024 is 2024-25.
"""
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

from . import db

BASE = "https://educationdata.urban.org/api/v1/schools/ccd"
GRADES = range(6, 13)


class SchoolDataError(Exception):
    pass


def fetch_all(path, use_cache=True):
    """GET every page of a query; returns the combined result rows."""
    url = f"{BASE}/{path}"
    d = os.path.join(db.DATA_DIR, "cache")
    os.makedirs(d, exist_ok=True)
    cp = os.path.join(d, "edu-" + hashlib.sha1(url.encode()).hexdigest() + ".json")
    if use_cache and os.path.exists(cp):
        with open(cp) as f:
            return json.load(f)
    rows, nxt = [], url
    while nxt:
        try:
            req = urllib.request.Request(nxt, headers={"User-Agent": "youthdemo/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                page = json.load(resp)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SchoolDataError(f"Cannot read {nxt}: {e}") from e
        rows += page.get("results", [])
        nxt = page.get("next")
    if rows:  # an empty answer may just mean "not published yet"; do not cache it
        with open(cp, "w") as f:
            json.dump(rows, f)
    return rows


def latest_directory(state):
    """-> (year, rows) for the newest school directory published for a state."""
    for y in range(datetime.date.today().year, 2014, -1):
        rows = fetch_all(f"directory/{y}/?fips={state}")
        if rows:
            return y, rows
    raise SchoolDataError("No school directory found")


def refresh(conn, cfg, log=lambda m: print(m, file=sys.stderr)):
    districts = cfg.get("school_districts", {})
    if not districts:
        return
    latest, directory = latest_directory(int(cfg["state_fips"]))
    log(f"Latest school directory: {latest}-{(latest + 1) % 100:02d}")
    names = {}
    if cfg.get("school_info"):
        # An area set up from ZIP codes lists every school seen in any year.
        for sid, m in cfg["school_info"].items():
            names[sid] = m
            conn.execute("INSERT OR REPLACE INTO school VALUES (?,?,?,?,?)",
                         (sid, m["name"], m.get("zip"), m.get("city"), m["leaid"]))
    else:
        for r in directory:
            if r.get("leaid") in districts and (r.get("highest_grade_offered") or -1) >= 6:
                names[r["ncessch"]] = r
                conn.execute("INSERT OR REPLACE INTO school VALUES (?,?,?,?,?)",
                             (r["ncessch"], r["school_name"], r.get("zip_location"),
                              r.get("city_location"), r["leaid"]))
    first = cfg.get("first_school_year", 2010)
    for year in range(first, latest + 1):
        n = 0
        for leaid in districts:
            for grade in GRADES:
                q = f"enrollment/{year}/grade-{grade}/?leaid="
                found = fetch_all(q + leaid)
                if not found and leaid.startswith("0"):
                    # Older years store district ids without the leading zero.
                    found = fetch_all(q + leaid.lstrip("0"))
                for r in found:
                    e = r.get("enrollment")
                    if str(r.get("ncessch")).zfill(12) in names and r.get("race") == 99 and r.get("sex") == 99 \
                            and e is not None and e >= 0:
                        conn.execute("INSERT OR REPLACE INTO school_enr VALUES (?,?,?,?)",
                                     (str(r["ncessch"]).zfill(12), year, grade, e))
                        n += 1
        conn.commit()
        log(f"School enrollment {year}-{(year + 1) % 100:02d}: {n} school-grade counts")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schools_fetched_at', ?)",
                 (datetime.datetime.now().isoformat(timespec="minutes"),))
    conn.commit()
