"""Turn stored Census values + youth-group counts into the numbers the UI shows."""
import math

# metric -> (label, [ACS variables summed])
METRICS = {
    "total_pop": ("Total population", ["B01001_001"]),
    "under_18": ("Under 18", ["B09001_001"]),
    "age_9_11": ("Ages 9-11", ["B09001_007"]),
    "age_12_14": ("Ages 12-14", ["B09001_008"]),
    "age_15_17": ("Ages 15-17", ["B09001_009"]),
    "age_12_17": ("Ages 12-17", ["B09001_008", "B09001_009"]),
    "age_18_19": ("Ages 18-19", ["B01001_007", "B01001_031"]),
    "male_10_17": ("Boys 10-17", ["B01001_005", "B01001_006"]),
    "female_10_17": ("Girls 10-17", ["B01001_029", "B01001_030"]),
    "enrolled_5_8": ("Enrolled, grades 5-8", ["B14001_006"]),
    "enrolled_9_12": ("Enrolled, grades 9-12", ["B14001_007"]),
    "white_nh_10_17": ("White, not Hispanic", [f"B01001H_{v}" for v in ("005", "006", "020", "021")]),
    "asian_10_17": ("Asian", [f"B01001D_{v}" for v in ("005", "006", "020", "021")]),
    "hispanic_10_17": ("Hispanic (any race)", [f"B01001I_{v}" for v in ("005", "006", "020", "021")]),
    "black_10_17": ("Black", [f"B01001B_{v}" for v in ("005", "006", "020", "021")]),
}


def _acs(conn, geo_ids):
    """{year: {geo: {var: (est, moe)}}}"""
    q = "SELECT geo_id, year, var, est, moe FROM acs WHERE geo_id IN (%s)" % ",".join("?" * len(geo_ids))
    out = {}
    for r in conn.execute(q, geo_ids):
        out.setdefault(r["year"], {}).setdefault(r["geo_id"], {})[r["var"]] = (r["est"], r["moe"])
    return out


def series(conn, geo_ids):
    """Sum each metric across the chosen geographies, per ACS vintage.

    A value is None when any piece is missing. Margins of error combine as the
    root sum of squares (Census Bureau approximation).
    """
    data = _acs(conn, geo_ids) if geo_ids else {}
    years = sorted(data)
    out = {"years": years, "metrics": {}}
    for name, (label, vars_) in METRICS.items():
        vals = []
        for y in years:
            est, moe2, ok, moe_ok = 0.0, 0.0, True, True
            for g in geo_ids:
                for v in vars_:
                    e, m = data[y].get(g, {}).get(v, (None, None))
                    if e is None:
                        ok = False
                    else:
                        est += e
                    if m is None:
                        moe_ok = False
                    else:
                        moe2 += m * m
            vals.append({"est": est, "moe": round(math.sqrt(moe2)) if moe_ok else None} if ok else None)
        out["metrics"][name] = {"label": label, "values": vals}
    return out


def pipeline(conn, geo_ids, last_year=2032):
    """Age the 2020 Census single-year-of-age counts forward, no migration.

    The children counted in 2020 are the teens of later years: a 6-year-old in
    2020 is 12 in 2026. Usable through 2032, when 2020's newborns turn 12.
    """
    if not geo_ids:
        return None
    q = ("SELECT geo_id, age, pop FROM census_age WHERE year=2020 AND geo_id IN (%s)"
         % ",".join("?" * len(geo_ids)))
    ages, have = {}, set()
    for r in conn.execute(q, geo_ids):
        ages[r["age"]] = ages.get(r["age"], 0) + r["pop"]
        have.add(r["geo_id"])
    if have != set(geo_ids):
        return None
    rows = []
    for y in range(2020, last_year + 1):
        k = y - 2020
        if 12 - k < 0:
            break
        ms = sum(ages.get(a - k, 0) for a in (12, 13, 14))
        hs = sum(ages.get(a - k, 0) for a in (15, 16, 17))
        rows.append({"year": y, "age_12_14": ms, "age_15_17": hs})
    return rows


def youth_rows(conn):
    return [dict(r) for r in conn.execute("SELECT year, ms, hs, note FROM youth ORDER BY year")]


def school_series(conn, school_ids):
    """Enrollment in the chosen schools per school year (fall), grades 6-12."""
    if not school_ids:
        return {"years": [], "ms": [], "hs": [], "grades": {}}
    q = ("SELECT year, grade, SUM(n) AS n FROM school_enr WHERE school_id IN (%s) "
         "GROUP BY year, grade" % ",".join("?" * len(school_ids)))
    by = {}
    for r in conn.execute(q, school_ids):
        by.setdefault(r["year"], {})[r["grade"]] = r["n"]
    years = sorted(by)
    return {
        "years": years,
        "ms": [sum(by[y].get(g, 0) for g in (6, 7, 8)) for y in years],
        "hs": [sum(by[y].get(g, 0) for g in (9, 10, 11, 12)) for y in years],
        "grades": {g: [by[y].get(g) for y in years] for g in range(6, 13)},
    }


def census_basis(conn, geo_ids):
    """{year: (ages 12-14, ages 15-17)} from the ACS."""
    s = series(conn, geo_ids)
    out = {}
    for y, ms, hs in zip(s["years"], s["metrics"]["age_12_14"]["values"],
                         s["metrics"]["age_15_17"]["values"]):
        if ms is not None and hs is not None:
            out[y] = (ms["est"], hs["est"])
    return out


def school_basis(conn, school_ids):
    """{year: (grades 6-8, grades 9-12)} from school enrollment."""
    s = school_series(conn, school_ids)
    return {y: (ms, hs) for y, ms, hs in zip(s["years"], s["ms"], s["hs"]) if ms + hs > 0}


def compare(conn, pop):
    """Join youth-group counts to a teen population and split growth.

    pop is {year: (middle-school-age count, high-school-age count)}.
    For each year after the first (the baseline):
      expected = baseline group size x (teen population now / teen population then)
      demographic change = expected - baseline   (growth the area handed you)
      real change        = actual - expected     (growth in share of local teens)
    """
    pop_years = sorted(pop)
    rows, base = [], None
    for yr in youth_rows(conn):
        ms, hs = yr["ms"], yr["hs"]
        total = (ms or 0) + (hs or 0) if (ms is not None or hs is not None) else None
        row = {"year": yr["year"], "ms": ms, "hs": hs, "total": total, "note": yr["note"],
               "pop_year": None, "pop_total": None, "pop_ms": None, "pop_hs": None,
               "rate": None, "ms_rate": None, "hs_rate": None,
               "expected": None, "demographic_change": None, "real_change": None}
        # Use the matching year; else the most recent earlier one; else the earliest.
        py = None
        if pop_years:
            earlier = [y for y in pop_years if y <= yr["year"]]
            py = earlier[-1] if earlier else pop_years[0]
        if py is not None:
            pms, phs = pop[py]
            ptot = pms + phs
            row.update(pop_year=py, pop_total=ptot, pop_ms=pms, pop_hs=phs)
            if ms is not None and pms:
                row["ms_rate"] = 1000 * ms / pms
            if hs is not None and phs:
                row["hs_rate"] = 1000 * hs / phs
            if total is not None and ptot:
                row["rate"] = 1000 * total / ptot
                if base is None:
                    base = (total, ptot)
                    row.update(expected=total, demographic_change=0.0, real_change=0.0)
                else:
                    exp = base[0] * ptot / base[1]
                    row.update(expected=exp, demographic_change=exp - base[0],
                               real_change=total - exp)
        rows.append(row)
    return {"rows": rows, "latest_pop_year": pop_years[-1] if pop_years else None}


def profile(conn):
    """Latest-vintage community profile, one row per geography."""
    r = conn.execute("SELECT MAX(year) AS y FROM acs WHERE var='B19013_001'").fetchone()
    if not r or r["y"] is None:
        return {"year": None, "rows": []}
    year = r["y"]
    rows = []
    for g in conn.execute("SELECT id, kind, name FROM geo ORDER BY kind DESC, id"):
        v = {x["var"]: x["est"] for x in conn.execute(
            "SELECT var, est FROM acs WHERE geo_id=? AND year=?", (g["id"], year))}
        if not v:
            continue

        def pct(n, d):
            a, b = v.get(n), v.get(d)
            return 100 * a / b if a is not None and b else None

        teens = None
        if v.get("B09001_008") is not None and v.get("B09001_009") is not None:
            teens = v["B09001_008"] + v["B09001_009"]
        pop = v.get("B01001_001")
        eng = pct("B16007_003", "B16007_002")
        rows.append({
            "id": g["id"], "kind": g["kind"], "name": g["name"],
            "population": pop,
            "teens_12_17": teens,
            "teen_share": 100 * teens / pop if teens is not None and pop else None,
            "median_income": v.get("B19013_001"),
            "hh_with_children": pct("B11005_002", "B11005_001"),
            "white_nh": pct("B03002_003", "B03002_001"),
            "black_nh": pct("B03002_004", "B03002_001"),
            "asian_nh": pct("B03002_006", "B03002_001"),
            "hispanic": pct("B03002_012", "B03002_001"),
            "other_language_5_17": 100 - eng if eng is not None else None,
            "foreign_born": pct("B05002_013", "B05002_001"),
            "owner_occupied": pct("B25003_002", "B25003_001"),
        })
    return {"year": year, "rows": rows}
