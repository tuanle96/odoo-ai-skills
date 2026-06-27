# Deployment — reference

Targets Odoo 17/18, through Odoo 19 (current LTS). Per-version option renames: `skills/odoo-introspect/references/version-matrix.md`.

## Full annotated odoo.conf (production)

```ini
[options]
; --- paths ---
addons_path = /opt/odoo/odoo/addons,/opt/odoo/enterprise,/opt/odoo/custom
data_dir    = /var/lib/odoo            ; filestore/ + sessions/ live here

; --- database ---
db_host = 127.0.0.1
db_port = 5432
db_user = odoo
db_password = CHANGE_ME
db_maxconn = 64                        ; connections per worker pool
dbfilter = ^%d$                        ; %d = subdomain, %h = full host
list_db = False                        ; hide DB manager
admin_passwd = CHANGE_ME_TOO           ; master pw for /web/database/* endpoints

; --- http ---
http_port = 8069
gevent_port = 8072                     ; websocket/longpolling worker (was longpolling_port)
proxy_mode = True                      ; trust X-Forwarded-* from the proxy

; --- workers & limits ---
workers = 5                            ; (#CPU * 2) + 1 as a starting point
max_cron_threads = 1
limit_time_cpu = 60                    ; CPU seconds / request
limit_time_real = 120                  ; wall-clock seconds / request (> limit_time_cpu)
limit_time_real_cron = 300             ; longer budget for cron jobs
limit_memory_soft = 2147483648         ; bytes; recycle worker after request when over
limit_memory_hard = 2684354560         ; bytes; hard kill
limit_request = 8192                   ; requests before a worker recycles

; --- logging ---
logfile = /var/log/odoo/odoo.log
log_level = info
```

Generate a starting file from a running binary: `odoo-bin -c odoo.conf --save --stop-after-init` writes the effective config (every default made explicit) — a good way to *see* what the server thinks the values are rather than guessing.

## odoo-bin flags worth knowing

| Flag | Effect |
|---|---|
| `-c FILE` | load this config (CLI flags still override it) |
| `-d DB` | database name |
| `-i mod[,mod]` / `-i all` | install module(s) |
| `-u mod[,mod]` / `-u all` | update module(s) — runs migrations |
| `--stop-after-init` | exit after init/update/test (returns exit code; no serving) |
| `--without-demo=all` | install without demo data (prod/CI) |
| `--dev=all` | reload + qweb + xml + werkzeug debugger (dev only) |
| `--dev=reload,qweb,xml` | pick sub-options: py auto-reload, in-DB qweb from file, view xml from file |
| `--log-level=debug_sql` | log every SQL query |
| `--test-enable` / `--test-tags` | enable tests / select which (see odoo-testing) |
| `shell --no-http` | interactive ORM shell (used by odoo-introspect scripts) |

## nginx vhost (TLS + websocket)

```nginx
upstream odoo     { server 127.0.0.1:8069; }
upstream odoo_chat { server 127.0.0.1:8072; }

server {
    listen 443 ssl http2;
    server_name erp.example.com;
    ssl_certificate     /etc/letsencrypt/live/erp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/erp.example.com/privkey.pem;

    client_max_body_size 200m;          # large attachments / imports
    proxy_read_timeout 720s;            # match limit_time_real headroom

    # websocket / longpolling — MUST route to the gevent worker
    location /websocket/ {
        proxy_pass http://odoo_chat;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://odoo;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
    }

    # cache static assets aggressively
    location ~* /web/static/ {
        proxy_pass http://odoo;
        proxy_cache_valid 200 60m;
        expires 864000;
    }
}
```

Set `proxy_mode = True` in odoo.conf so Odoo honors `X-Forwarded-Proto` (else it builds `http://` URLs behind your TLS).

## docker-compose.yml (odoo + postgres)

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: postgres
      POSTGRES_USER: odoo
      POSTGRES_PASSWORD: odoo
    volumes:
      - pgdata:/var/lib/postgresql/data
  odoo:
    image: odoo:18
    depends_on: [db]
    ports:
      - "8069:8069"
      - "8072:8072"           # gevent/websocket
    environment:
      HOST: db                # Odoo image reads HOST/USER/PASSWORD for db_*
      USER: odoo
      PASSWORD: odoo
    volumes:
      - odoo-data:/var/lib/odoo            # filestore persists here
      - ./config:/etc/odoo                 # odoo.conf
      - ./addons:/mnt/extra-addons         # custom modules (add to addons_path)
volumes:
  pgdata:
  odoo-data:
```

The official `odoo` image reads `HOST`/`USER`/`PASSWORD` env vars for the DB connection and loads `/etc/odoo/odoo.conf`. Mount custom addons and add `/mnt/extra-addons` to `addons_path`.

## CI test run

```bash
#!/usr/bin/env bash
set -euo pipefail
DB="ci_${GITHUB_RUN_ID:-local}"
createdb -U odoo "$DB"
odoo-bin -d "$DB" \
  -i my_module \
  --test-enable --test-tags '/my_module' \
  --without-demo=False \
  --stop-after-init --log-level=test
# exit code is non-zero if any test fails → pipeline fails
```

`--test-tags` format: `[-][tag][/module][:Class][.method]` — e.g. `/my_module`, `:TestSaleFlow`, `.test_confirm`, `-at_install`, `+post_install`. See `odoo-testing` for `at_install` vs `post_install` and the `-i` clean / `-u` data matrix.

## Backup / restore runbook

```bash
# --- BACKUP (DB + filestore — both required) ---
pg_dump -Fc -h "$DB_HOST" -U odoo "$DB" > "${DB}.dump"
tar czf "${DB}_filestore.tgz" -C "$DATA_DIR/filestore" "$DB"

# --- RESTORE ---
createdb -h "$DB_HOST" -U odoo "$DB_NEW"
pg_restore -h "$DB_HOST" -U odoo -d "$DB_NEW" "${DB}.dump"
mkdir -p "$DATA_DIR/filestore/$DB_NEW"
tar xzf "${DB}_filestore.tgz" -C "$DATA_DIR/filestore" --strip-components=1 \
    -C "$DATA_DIR/filestore/$DB_NEW"
# then: neutralize before using a prod copy as staging (see below)
```

- **Neutralize a prod restore for staging** so a copy can't email real customers or charge cards: `odoo-bin neutralize -d <db>` (v16+) runs each module's `data/neutralize.sql`, disabling outgoing mail, crons, payment providers, and mass mailing. If neutralizing by hand, at minimum deactivate `ir.cron`, point `ir.mail_server` at a catch-all, and blank external webhook URLs.
- The web backup endpoint (`/web/database/backup`, needs `admin_passwd`) zips DB+filestore together — fine for small DBs; use the CLI pair above for large ones to avoid memory spikes.
- Test restores regularly — an unrestorable backup is no backup.
