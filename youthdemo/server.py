import json
import mimetypes
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import analysis, db

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _num(v, name):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if f < 0:
        raise ValueError(f"{name} cannot be negative")
    return f


def save_youth(conn, rec):
    try:
        year = int(rec.get("year"))
    except (TypeError, ValueError):
        raise ValueError("year must be a whole number")
    if not 1990 <= year <= 2100:
        raise ValueError("year looks wrong")
    ms, hs = _num(rec.get("ms"), "middle school"), _num(rec.get("hs"), "high school")
    if ms is None and hs is None:
        raise ValueError("enter a middle school or high school count")
    conn.execute("INSERT OR REPLACE INTO youth(year, ms, hs, note) VALUES (?,?,?,?)",
                 (year, ms, hs, (rec.get("note") or "").strip()[:300]))
    conn.commit()


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _geos(self, conn, qs):
        asked = [g for g in qs.get("geos", [""])[0].split(",") if g]
        known = {r["id"] for r in conn.execute("SELECT id FROM geo")}
        return [g for g in asked if g in known]

    def _schools(self, conn):
        cfg = db.load_config()
        zips, extra = set(cfg.get("zctas", {})), set(cfg.get("include_schools", {}))
        districts = cfg.get("school_districts", {})
        out = []
        for r in conn.execute(
                "SELECT s.id, s.name, s.zip, s.leaid, MIN(e.grade) AS lo, MAX(e.grade) AS hi "
                "FROM school s JOIN school_enr e ON e.school_id = s.id "
                "GROUP BY s.id ORDER BY s.leaid, s.name"):
            d = dict(r)
            d["district"] = districts.get(r["leaid"], r["leaid"])
            d["default"] = r["zip"] in zips or r["id"] in extra
            out.append(d)
        return out

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path.startswith("/api/"):
            conn = db.connect(self.db_path)
            try:
                if u.path == "/api/meta":
                    meta = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM meta")}
                    self._json({
                        "geos": [dict(r) for r in conn.execute("SELECT id, kind, name FROM geo ORDER BY id")],
                        "fetched_at": meta.get("fetched_at"),
                        "warnings": json.loads(meta.get("warnings", "[]")),
                        "schools_fetched_at": meta.get("schools_fetched_at"),
                        "has_census": conn.execute("SELECT 1 FROM acs LIMIT 1").fetchone() is not None,
                        "schools": self._schools(conn),
                    })
                elif u.path == "/api/dashboard":
                    geos = self._geos(conn, qs)
                    schools = [x for x in qs.get("schools", [""])[0].split(",") if x]
                    basis = qs.get("basis", ["census"])[0]
                    pop = (analysis.school_basis(conn, schools) if basis == "schools"
                           else analysis.census_basis(conn, geos))
                    self._json({
                        "series": analysis.series(conn, geos),
                        "pipeline": analysis.pipeline(conn, geos),
                        "schools": analysis.school_series(conn, schools),
                        "compare": analysis.compare(conn, pop),
                    })
                elif u.path == "/api/profile":
                    self._json(analysis.profile(conn))
                else:
                    self._json({"error": "not found"}, 404)
            finally:
                conn.close()
            return
        rel = "index.html" if u.path == "/" else u.path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(full)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/youth":
            return self._json({"error": "not found"}, 404)
        try:
            rec = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._json({"error": "bad request"}, 400)
        conn = db.connect(self.db_path)
        try:
            save_youth(conn, rec)
            self._json({"ok": True})
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        finally:
            conn.close()

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/youth":
            return self._json({"error": "not found"}, 404)
        year = urllib.parse.parse_qs(u.query).get("year", [""])[0]
        conn = db.connect(self.db_path)
        try:
            conn.execute("DELETE FROM youth WHERE year=?", (year,))
            conn.commit()
            self._json({"ok": True})
        finally:
            conn.close()


def serve(port=8000, db_path=None, host="127.0.0.1"):
    conn = db.connect(db_path)
    db.sync_geos(conn, db.load_config())
    conn.close()
    Handler.db_path = db_path
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Youth demographics dashboard: http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
