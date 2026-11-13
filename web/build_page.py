"""Build the standalone web page (web/dist/youth-yardstick.html) from the
downloaded data. Run after `python3 -m youthdemo fetch`."""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from youthdemo import analysis, census, db  # noqa: E402


def main():
    conn = db.connect()
    cfg = db.load_config()
    zips, extra = set(cfg.get("zctas", {})), set(cfg.get("include_schools", {}))
    districts = cfg.get("school_districts", {})
    schools, enr = [], {}
    for r in conn.execute(
            "SELECT s.id, s.name, s.zip, s.leaid, MIN(e.grade) lo, MAX(e.grade) hi FROM school s "
            "JOIN school_enr e ON e.school_id = s.id GROUP BY s.id ORDER BY s.leaid, s.name"):
        lvl = "High" if r["lo"] >= 9 else "Middle" if r["hi"] <= 8 else "Combined"
        schools.append({"id": r["id"], "name": r["name"], "zip": r["zip"], "lvl": lvl,
                        "mi": cfg.get("school_miles", {}).get(r["id"]),
                        "district": districts.get(r["leaid"], r["leaid"]),
                        "def": r["zip"] in zips or r["id"] in extra})
    for r in conn.execute("SELECT school_id, year, grade, n FROM school_enr"):
        enr.setdefault(r["school_id"], {}).setdefault(r["year"], [None] * 7)[r["grade"] - 6] = r["n"]
    for s in schools:
        s["last"] = max(enr[s["id"]])
    geos = [dict(r) for r in conn.execute("SELECT id, kind, name FROM geo ORDER BY id")]
    acs = {}
    for r in conn.execute("SELECT geo_id, year, var, est, moe FROM acs"):
        if r["var"] in census.SERIES_VARS:
            acs.setdefault(r["geo_id"], {}).setdefault(r["year"], {})[r["var"]] = [r["est"], r["moe"]]
    ages = {}
    for r in conn.execute("SELECT geo_id, age, pop FROM census_age WHERE year=2020 AND age<18 ORDER BY age"):
        ages.setdefault(r["geo_id"], []).append(r["pop"])
    data = {
        "schools": schools, "enr": enr, "geos": geos, "acs": acs, "ages": ages,
        "metrics": {k: {"label": v[0], "vars": v[1]} for k, v in analysis.METRICS.items()},
        "profile": analysis.profile(conn),
        "maxYear": max(max(v) for v in enr.values()),
        "area": cfg.get("area_name", "South Forsyth and Johns Creek"),
        "built": datetime.date.today().isoformat(),
    }
    with open(os.path.join(HERE, "page.template.html")) as f:
        html = f.read()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dist", "youth-yardstick.html")
    with open(out, "w") as f:
        f.write(html.replace("/*__DATA__*/null", blob))
    print(out, f"{os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()
