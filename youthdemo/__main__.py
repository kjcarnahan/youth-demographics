import argparse
import csv
import sys

from . import analysis, census, db, schools, server


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m youthdemo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="download Census data for the areas in config.json")
    s = sub.add_parser("serve", help="open the dashboard")
    s.add_argument("--port", type=int, default=8000)
    i = sub.add_parser("import-youth", help="load youth-group counts from a CSV "
                       "(columns: year, middle_school, high_school, note)")
    i.add_argument("csv")
    sub.add_parser("export-youth", help="print youth-group counts and comparison as CSV")
    a = p.parse_args(argv)

    if a.cmd == "serve":
        return server.serve(a.port)
    conn = db.connect()
    cfg = db.load_config()
    if a.cmd == "fetch":
        failed = False
        try:
            schools.refresh(conn, cfg)
        except schools.SchoolDataError as e:
            failed = True
            print(f"School enrollment not downloaded: {e}", file=sys.stderr)
        try:
            census.refresh(conn, cfg)
        except census.CensusError as e:
            failed = True
            print(f"Census data not downloaded: {e}", file=sys.stderr)
        print("Finished with problems (see above)." if failed else "Done.",
              "Run: python3 -m youthdemo serve")
    elif a.cmd == "import-youth":
        n = 0
        with open(a.csv, newline="") as f:
            for row in csv.DictReader(f):
                server.save_youth(conn, {"year": row.get("year"), "ms": row.get("middle_school"),
                                         "hs": row.get("high_school"), "note": row.get("note")})
                n += 1
        print(f"Imported {n} rows")
    elif a.cmd == "export-youth":
        db.sync_geos(conn, cfg)
        geos = [f"zcta:{z}" for z in cfg.get("zctas", {})]
        rows = analysis.compare(conn, analysis.census_basis(conn, geos))["rows"]
        if rows:
            w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
