"""
Content-addressed snapshot cache for instance facts (local, no Odoo).

Introspection steps re-run the same read-only facts against the same instance
over and over. This caches a step's JSON payload under a key derived from the
DB, the step, its params, and an addons fingerprint (mtime/size of the source
tree). A later run with an unchanged tree hits the cache instead of spawning a
fresh `odoo-bin shell`.

STRATEGY RULE (enforced in data, not just prose): a cached ("warm") payload MAY
accelerate analysis, but MUST NEVER serve as merge-approval evidence — the
deploy gate only accepts COLD runs. `mark_provenance` stamps every payload with
its provenance ("cold"|"warm"|"stale-rejected") and a `merge_eligible` flag that
is True only for a cold run, so a warm/stale payload can be detected and refused
downstream even if it is otherwise byte-identical to a fresh one.

Public API (other tools code against these EXACT signatures):
    addons_fingerprint(paths)                 -> str   (sha256 of tree mtime/size)
    cache_key(db, step, params, fingerprint)  -> str   (sha256 of canonical JSON)
    lookup(key, cache_dir=None, max_age_s=None) -> dict | None
    store(key, payload, meta, cache_dir=None) -> str   (path written)
    mark_provenance(payload, provenance)      -> dict  (copy + payload["_cache"])

Cache dir: ODOO_AI_CACHE_DIR env override, else ~/.cache/odoo-ai/snapshots.
One JSON file per key.

Usage
-----
    python3 snapshot_cache.py stats       # count / total bytes / oldest / newest
    python3 snapshot_cache.py clear        # remove every cached snapshot
    python3 snapshot_cache.py get <key>    # dump one cached record

Output: pure JSON to stdout.
"""
import os
import sys
import json
import hashlib
import datetime
from pathlib import Path

# Source files whose mtime/size define the addons fingerprint.
SOURCE_EXTS = (".py", ".xml", ".csv", ".js")

USAGE = "snapshot_cache.py stats | clear | get <key>"

# A cached payload may accelerate analysis but is NEVER merge-approval evidence.
_MERGE_CAVEAT = ("warm/stale-rejected snapshots accelerate analysis only; the "
                 "deploy gate requires a cold run — merge_eligible is True only "
                 "for provenance == 'cold'.")


# --- Pure helpers (no Odoo, no filesystem state — unit-testable) -------------
def addons_fingerprint(paths):
    """sha256 over the sorted (base, relpath, mtime_ns, size) of every
    *.py/*.xml/*.csv/*.js file under each path. A missing path contributes a
    stable marker record rather than raising, so a fingerprint is always
    obtainable. Touching a file (mtime) or changing its size flips the hash."""
    records = []
    for base in paths:
        b = os.path.abspath(base)
        if os.path.isfile(b):
            files = [b] if b.endswith(SOURCE_EXTS) else []
            roots = [(os.path.dirname(b), [], [os.path.basename(b)])] if files else []
        elif os.path.isdir(b):
            roots = os.walk(b)
        else:
            records.append(["<missing>", base, 0, 0])
            continue
        for root, _dirs, names in roots:
            for name in names:
                if not name.endswith(SOURCE_EXTS):
                    continue
                fpath = os.path.join(root, name)
                try:
                    st = os.stat(fpath)
                except OSError:
                    records.append(["<unstat>", os.path.relpath(fpath, b), 0, 0])
                    continue
                records.append([base, os.path.relpath(fpath, b),
                                st.st_mtime_ns, st.st_size])
    records.sort()
    blob = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_key(db, step, params, fingerprint):
    """sha256 of a canonical (sort_keys) JSON of the identity tuple, so params
    hash the same regardless of dict ordering."""
    canonical = json.dumps(
        {"db": db, "step": step, "params": params, "fingerprint": fingerprint},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mark_provenance(payload, provenance):
    """Return a shallow copy of payload with payload["_cache"] stamped.

    provenance is "cold" (fresh run — created_at), "warm" (cache hit — cached_at)
    or "stale-rejected" (a hit past its max_age, refused — cached_at). Only a cold
    run is merge_eligible; the flag is the data-level enforcement of the rule that
    warm truth never approves a merge. The input payload is not mutated.
    """
    out = dict(payload or {})
    ts_key = "created_at" if provenance == "cold" else "cached_at"
    out["_cache"] = {
        "provenance": provenance,
        ts_key: _now_iso(),
        "merge_eligible": provenance == "cold",
        "_caveat": _MERGE_CAVEAT,
    }
    return out


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


# --- Cache dir + filesystem I/O ----------------------------------------------
def _cache_dir(cache_dir=None):
    """Explicit arg wins, then ODOO_AI_CACHE_DIR, then ~/.cache/odoo-ai/snapshots."""
    if cache_dir:
        return Path(cache_dir)
    env = os.environ.get("ODOO_AI_CACHE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "odoo-ai" / "snapshots"


def store(key, payload, meta, cache_dir=None):
    """Write one JSON record {key, payload, meta, stored_at} for `key`. Returns
    the file path. Written atomically via a temp file + replace."""
    d = _cache_dir(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{key}.json"
    record = {"key": key, "payload": payload, "meta": meta or {},
              "stored_at": _now_iso()}
    tmp = d / f".{key}.tmp"
    tmp.write_text(json.dumps(record, indent=2, default=str))
    os.replace(tmp, path)
    return str(path)


def lookup(key, cache_dir=None, max_age_s=None):
    """Return {"payload": <dict>, "meta": {...}} for `key`, or None if missing,
    unreadable, or (when max_age_s is set) older than max_age_s. `meta` is the
    stored meta enriched with _stored_at / _age_s / _cache_file."""
    path = _cache_dir(cache_dir) / f"{key}.json"
    if not path.exists():
        return None
    try:
        age = _now() - path.stat().st_mtime
    except OSError:
        return None
    if max_age_s is not None and age > max_age_s:
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    meta = dict(record.get("meta") or {})
    meta.update({"_stored_at": record.get("stored_at"),
                 "_age_s": round(age, 3), "_cache_file": str(path)})
    return {"payload": record.get("payload"), "meta": meta}


def _now():
    # Wall clock for cache-age math; isolated so age logic stays one-liner-simple.
    import time
    return time.time()


# --- Local CLI ----------------------------------------------------------------
def _cmd_stats(cache_dir=None):
    d = _cache_dir(cache_dir)
    files = sorted(d.glob("*.json")) if d.exists() else []
    sizes = [(f, f.stat()) for f in files]
    mtimes = [st.st_mtime for _f, st in sizes]
    return {
        "cache_dir": str(d),
        "count": len(files),
        "total_bytes": sum(st.st_size for _f, st in sizes),
        "oldest": _iso(min(mtimes)) if mtimes else None,
        "newest": _iso(max(mtimes)) if mtimes else None,
    }


def _cmd_clear(cache_dir=None):
    d = _cache_dir(cache_dir)
    removed = 0
    if d.exists():
        for f in d.glob("*.json"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return {"cache_dir": str(d), "removed": removed}


def _cmd_get(key, cache_dir=None):
    path = _cache_dir(cache_dir) / f"{key}.json"
    if not path.exists():
        return {"error": f"no cached snapshot for key {key}", "cache_file": str(path)}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return {"error": f"unreadable snapshot: {type(e).__name__}: {e}",
                "cache_file": str(path)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    try:
        if cmd == "stats":
            report = _cmd_stats()
        elif cmd == "clear":
            report = _cmd_clear()
        elif cmd == "get" and len(argv) >= 2:
            report = _cmd_get(argv[1])
        else:
            report = {"error": "unknown or incomplete command", "usage": USAGE}
    except Exception as e:  # never crash a local tool — errors are JSON
        report = {"error": f"{type(e).__name__}: {e}", "usage": USAGE}
    print(json.dumps(report, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
