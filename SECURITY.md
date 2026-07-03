# Security Policy

## Scope

This project is a documentation/skills suite plus a set of **introspection
scripts** that run inside `odoo-bin shell` and print JSON. The scripts read your
running Odoo instance; they do not open network listeners or persist data by
default. The most important security consideration is therefore **what ends up
in the JSON they emit** and where that JSON goes.

## Handling introspection output

Introspection output can contain sensitive material:

- **The runtime state capture (`state_capture.py`)** captures runtime args,
  locals, and `self` field values.
- **The model brief (`model_brief.py`) with `SOURCE=1`** includes full method
  source bodies.
- Field values, domains, server-action code, and record-rule domains can carry
  secrets, tokens, customer PII, or proprietary business logic.

Guidance:

- **Redaction is on by default for `state`.** Locals/keys/fields whose name
  matches a sensitive-key set (`password`, `token`, `secret`, `api_key`,
  `authorization`, `session`, …) are emitted as `<redacted>`. Extend it with
  `--redact-extra a,b` (or `REDACT_EXTRA`); disable only on a trusted box with
  `--no-redact` (`NO_REDACT=1`).
- **Redaction is key-name based.** It will *not* catch a secret stored under a
  benign name, and it does **not** redact `--fields` *values* or `SOURCE=1`
  source bodies. Treat those as sensitive.
- **Do not paste raw `state` / source JSON into an external LLM, a public issue,
  or a shared channel unless you have reviewed and redacted it.**
- Prefer running introspection against a **dev/staging** database.
- `COMMIT=1` on the runtime layers really persists changes — use it only on a
  throwaway/dev database.

## Supported versions

Security-relevant fixes are applied to the latest released version and `main`.
The skills target Odoo 17/18 through 19; issues specific to older Odoo versions
may not be fixed but will be documented.

## Reporting a vulnerability

If you find a vulnerability — for example a way the scripts leak data that
should be redacted, or a command-injection vector in the CLI — please report it
**privately** rather than opening a public issue:

- Use GitHub's **"Report a vulnerability"** (Security advisories) on the
  repository, or
- email the maintainer listed in `.claude-plugin/plugin.json`.

Please include the Odoo version, the exact command, and a minimal reproduction
(with any sensitive values redacted). We aim to acknowledge reports within a few
days and will credit reporters who want it once a fix ships.

## Good practice when using this suite with an agent

- Keep the JSON the agent reads inside your environment; it's working context,
  not something to ship around.
- Review any data the agent is about to send outside your boundary.
- Pin dependencies and review third-party addons before adding them as
  `depends` — the suite reports which addons in a chain are `local`/third-party
  precisely so you can scrutinize them.
