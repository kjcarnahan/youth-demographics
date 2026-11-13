"""Tests run offline. Census responses here are made-up numbers in the real
API's response shape; they are test fixtures, not data."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from youthdemo import analysis, areas, census, db, server  # noqa: E402


def conn_with(acs=(), ages=(), youth=()):
    c = db.connect(":memory:")
    db.sync_geos(c, {"zctas": {"30041": "A", "30097": "B"}, "places": {}, "counties": {"117": "F"}})
    c.executemany("INSERT INTO acs VALUES (?,?,?,?,?)", acs)
    c.executemany("INSERT INTO census_age VALUES (?,?,?,?)", ages)
    c.executemany("INSERT INTO youth VALUES (?,?,?,?)", youth)
    return c


def teen_rows(geo, year, ms, hs, moe=30):
    return [(geo, year, "B09001_008", ms, moe), (geo, year, "B09001_009", hs, moe)]


class Parse(unittest.TestCase):
    def test_parse_and_sentinels(self):
        rows = [["NAME", "B01001_001E", "B01001_001M", "zip code tabulation area"],
                ["ZCTA5 30041", "70000", "-555555555", "30041"]]
        rec = census.parse_rows(rows, "zcta")
        self.assertEqual(census._num(rec["zcta:30041"]["B01001_001E"]), 70000)
        self.assertIsNone(census._num(rec["zcta:30041"]["B01001_001M"]))
        self.assertIsNone(census._num("-666666666"))

    def test_fetch_acs_falls_back_per_table_and_zcta_state(self):
        calls = []

        def fake(path, params, use_cache=True):
            calls.append(params)
            if "in" not in params:
                raise census.CensusError("needs state")
            cols = params["get"].split(",")
            if any(c.startswith("B14001") for c in cols) and any(c.startswith("B01001") for c in cols):
                raise census.CensusError("HTTP 400")
            if any(c.startswith("B14001") for c in cols):
                raise census.CensusError("HTTP 400")
            return [cols + ["zip code tabulation area"], ["x"] + ["5"] * (len(cols) - 1) + ["30041"]]

        orig, census.fetch_json = census.fetch_json, fake
        try:
            data, warns = census.fetch_acs(2015, "zcta", ["30041"], "13",
                                           {"B01001_001": True, "B14001_006": False})
        finally:
            census.fetch_json = orig
        self.assertEqual(data["zcta:30041"]["B01001_001"], (5.0, 5.0))
        self.assertNotIn("B14001_006", data["zcta:30041"])
        self.assertEqual(len(warns), 1)

    def test_single_age(self):
        header = ["PCT12_%03dN" % i for i in range(1, 210)] + ["county"]
        row = [str(i) for i in range(1, 210)] + ["117"]
        orig, census.fetch_json = census.fetch_json, lambda *a, **k: [header, row]
        try:
            out = census.fetch_single_age_2020("county", ["117"], "13")
        finally:
            census.fetch_json = orig
        # age 0: male col 3 + female col 107; age 12: 15 + 119
        self.assertEqual(out["county:117"][0], 110)
        self.assertEqual(out["county:117"][12], 134)
        self.assertEqual(len(out["county:117"]), 100)


class Analysis(unittest.TestCase):
    def test_series_sums_and_moe(self):
        c = conn_with(teen_rows("zcta:30041", 2020, 3000, 2000, 30) + teen_rows("zcta:30097", 2020, 1000, 500, 40))
        s = analysis.series(c, ["zcta:30041", "zcta:30097"])
        v = s["metrics"]["age_12_17"]["values"][0]
        self.assertEqual(v["est"], 6500)
        self.assertEqual(v["moe"], round((30**2 * 2 + 40**2 * 2) ** 0.5))
        self.assertIsNone(s["metrics"]["total_pop"]["values"][0])

    def test_missing_geo_gives_none(self):
        c = conn_with(teen_rows("zcta:30041", 2020, 3000, 2000))
        s = analysis.series(c, ["zcta:30041", "zcta:30097"])
        self.assertIsNone(s["metrics"]["age_12_17"]["values"][0])

    def test_compare_decomposition(self):
        acs = teen_rows("zcta:30041", 2019, 500, 500) + teen_rows("zcta:30041", 2021, 600, 600)
        c = conn_with(acs, youth=[(2019, 40, 60, ""), (2021, 50, 60, ""), (2023, 60, 72, "")])
        rows = analysis.compare(c, analysis.census_basis(c, ["zcta:30041"]))["rows"]
        self.assertEqual(rows[0]["rate"], 100.0)
        # Population +20%: kept pace = 120. Actual 110 -> real change -10.
        self.assertAlmostEqual(rows[1]["expected"], 120)
        self.assertAlmostEqual(rows[1]["demographic_change"], 20)
        self.assertAlmostEqual(rows[1]["real_change"], -10)
        # 2023 has no vintage: carries the 2021 population.
        self.assertEqual(rows[2]["pop_year"], 2021)
        self.assertAlmostEqual(rows[2]["real_change"], 12)
        self.assertAlmostEqual(rows[2]["ms_rate"], 100.0)

    def test_school_basis(self):
        c = conn_with(youth=[(2020, 10, 10, ""), (2021, 10, 10, "")])
        c.executemany("INSERT INTO school_enr VALUES (?,?,?,?)",
                      [("a", 2020, 7, 100), ("a", 2020, 10, 100), ("b", 2020, 7, 50),
                       ("a", 2021, 7, 125), ("a", 2021, 10, 125)])
        self.assertEqual(analysis.school_basis(c, ["a", "b"]), {2020: (150, 100), 2021: (125, 125)})
        rows = analysis.compare(c, analysis.school_basis(c, ["a"]))["rows"]
        self.assertAlmostEqual(rows[1]["expected"], 25)
        self.assertAlmostEqual(rows[1]["real_change"], -5)

    def test_pipeline(self):
        ages = [("county:117", 2020, a, 100 + a) for a in range(100)]
        c = conn_with(ages=ages)
        p = analysis.pipeline(c, ["county:117"])
        self.assertEqual(p[0], {"year": 2020, "age_12_14": 112 + 113 + 114, "age_15_17": 115 + 116 + 117})
        self.assertEqual(p[-1]["year"], 2032)
        self.assertEqual(p[-1]["age_12_14"], 100 + 101 + 102)
        self.assertIsNone(analysis.pipeline(c, ["county:117", "zcta:30041"]))

    def test_youth_validation(self):
        c = conn_with()
        with self.assertRaises(ValueError):
            server.save_youth(c, {"year": "2024"})
        with self.assertRaises(ValueError):
            server.save_youth(c, {"year": "2024", "ms": "-3"})
        server.save_youth(c, {"year": "2024", "ms": "12", "hs": ""})
        self.assertEqual(analysis.youth_rows(c)[0]["ms"], 12)


class Areas(unittest.TestCase):
    def test_inputs(self):
        self.assertEqual(areas.state_fips("co"), "08")
        self.assertEqual(areas.state_fips("13"), "13")
        with self.assertRaises(ValueError):
            areas.state_fips("ZZ")
        self.assertEqual(areas.parse_zips("30315, 30310 30315"), ["30315", "30310"])
        with self.assertRaises(ValueError):
            areas.parse_zips("3031")
        self.assertEqual(areas.slug("East Denver!"), "east-denver")

    def test_distance(self):
        # Atlanta to Denver is about 1,200 miles.
        self.assertAlmostEqual(areas.miles((33.75, -84.39), (39.74, -104.99)), 1210, delta=30)
        self.assertEqual(areas.miles((33, -84), (33, -84)), 0)

    def test_choose_schools(self):
        def sch(i, zip_, lat, lon, hi=8):
            return {"ncessch": i, "zip_location": zip_, "latitude": lat, "longitude": lon,
                    "highest_grade_offered": hi, "leaid": "L", "lea_name": "N", "school_name": i}
        d = [sch("in", "30315", 33.70, -84.38), sch("near", "30000", 33.72, -84.40),
             sch("far", "30000", 34.50, -84.40), sch("elem", "30315", 33.7, -84.38, hi=5),
             sch("nocoord", "30000", None, None)]
        got = {r["ncessch"]: m for r, m in areas.choose_schools(d, ["30315"], [(33.70, -84.38)], 3)}
        self.assertEqual(set(got), {"in", "near"})
        self.assertEqual(set(r["ncessch"] for r, _ in areas.choose_schools(d, ["30315"], [(33.70, -84.38)], 0)), {"in"})

    def test_closed_schools_are_kept(self):
        def sch(i, name, zip_="80220", leaid="803360"):
            return {"ncessch": i, "zip_location": zip_, "latitude": 39.7, "longitude": -104.9,
                    "highest_grade_offered": 8, "leaid": leaid, "lea_name": "DPS", "school_name": name}
        old = [sch("080336000001", "Closed Middle"), sch("80336000002", "Renamed Old")]
        new = [sch("080336000002", "Renamed New", leaid="0803360")]
        got = areas.pick_over_years([old, new], ["80220"], [(39.73, -104.92)], 3)
        self.assertEqual(sorted(got), ["080336000001", "080336000002"])
        self.assertEqual(got["080336000002"][0]["school_name"], "Renamed New")

    def test_build_config(self):
        d = [{"ncessch": "080336000001", "zip_location": "80220", "latitude": 39.7, "longitude": -104.9,
              "highest_grade_offered": 12, "leaid": "0803360", "lea_name": "DPS",
              "school_name": "A High", "city_location": "Denver"}]
        cfg = areas.build_config("East Denver", "CO", ["80220", "99999"],
                                 3, gaz={"80220": (39.73, -104.92)}, directories=[d])
        self.assertEqual(cfg["zctas"], {"80220": "80220 Denver"})
        self.assertEqual(cfg["state_fips"], "08")
        self.assertEqual(cfg["school_ids"], {"080336000001": "A High"})


if __name__ == "__main__":
    unittest.main()
