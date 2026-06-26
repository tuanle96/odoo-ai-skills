---
name: odoo-deploy
description: >-
  Running and deploying Odoo — odoo.conf options, odoo-bin flags, multi-worker
  + reverse-proxy (nginx websockets), Docker/compose, CI test runs, and
  DB+filestore backup/restore. Use whenever writing or debugging odoo.conf
  (addons_path, workers, gevent_port, proxy_mode, limits, dbfilter), choosing
  odoo-bin flags (-c/-d/-i/-u/--stop-after-init/--dev), setting up
  dev/staging/prod, wiring nginx for longpolling/websockets, dockerizing Odoo +
  Postgres, running tests in CI, or backing up/restoring. Verify the config the
  server actually loaded — don't guess which file or flags are in effect.
---

# Odoo deployment

A wrong `odoo.conf` fails quietly: live chat hangs (no websocket route), the wrong DB loads (`dbfilter`), a worker OOM-kills mid-request, or `-u` silently reverts prod data. The defaults are fine for `--dev`, never for production.

**The rule: deploy multi-worker behind a proxy with explicit limits, and verify the config the server actually loaded — don't assume a file or flag is in effect.**

Targets Odoo 17/18. Cross-version option renames: `skills/odoo-introspect/references/version-matrix.md`.

## Task → command/flag

| Task | Command / flag |
|---|---|
| Run with a config file | `odoo-bin -c /etc/odoo/odoo.conf` |
| Install a module | `odoo-bin -d DB -i my_module --stop-after-init` |
| Update a module (runs migrations) | `odoo-bin -d DB -u my_module --stop-after-init` |
| Update **all** modules | `odoo-bin -d DB -u all --stop-after-init` |
| Dev mode: auto-reload + readable assets | `odoo-bin -d DB --dev=all` (or `reload,qweb,xml`) |
| One-off shell (introspection) | `odoo-bin shell -d DB --no-http` |
| Run tests then exit (CI) | `odoo-bin -d DB -i my_module --test-enable --test-tags '/my_module' --stop-after-init` |
| Don't auto-create/list DBs | `--no-database-list` + `list_db = False` |

`--stop-after-init` makes install/update/test runs **exit with a status code** instead of serving — essential for CI and scripted upgrades.

## odoo.conf — the options that matter

```ini
[options]
addons_path = /opt/odoo/addons,/opt/odoo/custom    ; comma list; order = module search order
data_dir    = /var/lib/odoo                         ; holds filestore/ and sessions/

; --- database ---
db_host = 127.0.0.1
db_port = 5432
db_user = odoo
db_password = ****
dbfilter = ^%d$          ; restrict which DBs are exposed (regex; %d=subdomain, %h=host)
list_db  = False         ; hide the DB manager in production
admin_passwd = ****      ; master password for DB-management endpoints — set & protect it

; --- workers / limits (production) ---
workers = 5              ; multi-process mode; rule of thumb (#CPU * 2) + 1; 0 = threaded/dev only
max_cron_threads = 1
limit_time_cpu  = 60     ; seconds of CPU per request before SIGINT
limit_time_real = 120    ; wall-clock per request before kill (must be > limit_time_cpu)
limit_memory_soft = ...  ; bytes; worker recycled after current request when exceeded
limit_memory_hard = ...  ; bytes; hard cap, request killed
limit_request = 8192     ; requests a worker serves before recycling

; --- behind a proxy ---
proxy_mode = True        ; trust X-Forwarded-* (only with a real proxy in front!)
gevent_port = 8072       ; websocket/longpolling worker port (longpolling_port = deprecated alias)
```

- `workers = 0` runs the threaded server — fine for dev, **never** prod (no websocket worker, no memory recycling).
- `workers > 0` starts a dedicated gevent worker on `gevent_port` for `/websocket/` (live chat, bus, mail).

## Reverse proxy — websockets are the trap

Multi-worker Odoo needs the proxy to route normal traffic to `8069` **and `/websocket/` to the gevent port `8072`** — miss the second route and live chat / longpolling / bus hang with no page error.

```nginx
location /websocket/ {                 # the route everyone forgets
    proxy_pass http://127.0.0.1:8072;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
location / { proxy_pass http://127.0.0.1:8069; }   # + Host / X-Forwarded-* headers
```

Pair with `proxy_mode = True` so Odoo reads the forwarded scheme/host/IP. Full TLS vhost + compose in `references/deployment.md`.

## Environments & CI

| Env | Shape |
|---|---|
| dev | `workers=0`, `--dev=all`, demo data OK, SQLite-free (Postgres always) |
| staging | prod-like config on a **restored copy of prod** — where you rehearse `-u` and migrations |
| prod | multi-worker, `list_db=False`, proxy, backups, no demo data |

CI: install/upgrade + run tests, exit code gates the pipeline (see `odoo-testing` for tags and the `-i` clean / `-u` data matrix):

```bash
odoo-bin -d ci_$BUILD -i my_module --test-enable --test-tags '/my_module' \
         --stop-after-init --without-demo=False ; echo "exit=$?"
```

## Backup / restore — DB **and** filestore

Attachments live in `data_dir/filestore/<db>`, not in Postgres. A `pg_dump` alone loses every uploaded file.

```bash
pg_dump -Fc -U odoo mydb > mydb.dump                 # database
tar czf mydb_fs.tgz -C /var/lib/odoo/filestore mydb  # filestore
# restore: createdb + pg_restore, then untar the filestore back under data_dir/filestore/<db>
```

The web `/web/database/backup` (needs `admin_passwd`) bundles both into one zip — convenient, but memory-heavy on large DBs; prefer `pg_dump` + filestore tar for big instances.

## Gotchas that fail silently

- **`workers > 0` but no `/websocket/` proxy route** → live chat/bus hang with no error in the page.
- **`limit_time_real` ≤ `limit_time_cpu`** → requests killed before the CPU budget matters.
- **`proxy_mode = True` with no proxy** → clients spoof their IP/host.
- **`list_db = True` + reachable `admin_passwd` default** → anyone can drop/dump databases.
- **Restored DB without its filestore** → records exist, every attachment/image 404s.
- **`-u` on prod without rehearsing on staging** → an unguarded migration reshapes live data irreversibly (see `odoo-migration`).
- **Custom addon not on `addons_path`** → `-i`/`-u` reports "module not found" though the folder is right there.

## References & scripts

- `references/deployment.md` — annotated full `odoo.conf`, complete nginx vhost, `docker-compose.yml` (odoo + postgres), `--dev` sub-options, CI script, and the backup/restore runbook.
- Running tests / exit codes / tags: `odoo-testing`. What `-u` will touch before you deploy it: `odoo-migration`, `odoo-data`.
- Per-version config option renames (e.g. `longpolling_port` → `gevent_port`): `skills/odoo-introspect/references/version-matrix.md`.
