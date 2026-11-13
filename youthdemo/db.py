import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("YOUTHDEMO_DATA", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA_DIR, "youthdemo.sqlite")
CONFIG_PATH = os.environ.get("YOUTHDEMO_CONFIG", os.path.join(ROOT, "config.json"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS geo (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS acs (
  geo_id TEXT NOT NULL, year INTEGER NOT NULL, var TEXT NOT NULL,
  est REAL, moe REAL, PRIMARY KEY (geo_id, year, var));
CREATE TABLE IF NOT EXISTS census_age (
  geo_id TEXT NOT NULL, year INTEGER NOT NULL, age INTEGER NOT NULL,
  pop REAL NOT NULL, PRIMARY KEY (geo_id, year, age));
CREATE TABLE IF NOT EXISTS school (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, zip TEXT, city TEXT, leaid TEXT);
CREATE TABLE IF NOT EXISTS school_enr (
  school_id TEXT NOT NULL, year INTEGER NOT NULL, grade INTEGER NOT NULL,
  n REAL NOT NULL, PRIMARY KEY (school_id, year, grade));
CREATE TABLE IF NOT EXISTS youth (
  year INTEGER PRIMARY KEY, ms REAL, hs REAL, note TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def load_config(path=None):
    with open(path or CONFIG_PATH) as f:
        return json.load(f)


def connect(path=None):
    path = path or DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def sync_geos(conn, cfg):
    """Make the geo table match config.json."""
    rows = []
    for code, name in cfg.get("zctas", {}).items():
        rows.append((f"zcta:{code}", "zcta", name))
    for code, name in cfg.get("places", {}).items():
        rows.append((f"place:{code}", "place", name))
    for code, name in cfg.get("counties", {}).items():
        rows.append((f"county:{code}", "county", name))
    conn.executemany(
        "INSERT INTO geo(id, kind, name) VALUES (?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, name=excluded.name", rows)
    conn.commit()
    return rows
