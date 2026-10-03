"""Synthetic example: CSV import for the bookshelf, as the observed session left it."""
import csv
from datetime import datetime

DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")


def detect(path):
    """Exported lists come as UTF-8 or GBK."""
    raw = open(path, "rb").read()
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def parse_date(d):
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(d.strip(), f).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unknown date format: {d!r}")


def parse(path):
    with open(path, encoding=detect(path), newline="") as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    head, body = rows[0], rows[1:]
    out = []
    for r in body:
        rec = dict(zip(head, r))
        rec["added_at"] = parse_date(rec.get("added_at", ""))
        out.append(rec)
    return out


def dedupe(rows):
    """By ISBN; rows without an ISBN by title + author (the rule the user gave)."""
    seen, out = set(), []
    for r in rows:
        key = r.get("isbn") or (r.get("title"), r.get("author"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def load(path):
    return dedupe(parse(path))
