---
name: odoo-docs
description: >
  Use when: "how does X work in Odoo", "where is Y documented", "Odoo docs for Z",
  "ORM API reference", "what does this decorator do", "field type options",
  or any Odoo developer documentation lookup. Builds a local TF-IDF index once
  from the official odoo/documentation repo; queries it offline, linking canonical
  odoo.com URLs. Always subordinate to live instance introspection.
---

# odoo-docs — Local Developer Doc Index

Offline TF-IDF search over Odoo developer documentation, built once from the
official `odoo/documentation` repository (CC-BY-SA-4.0, © Odoo S.A.).
The index lives at `~/.odoo-ai/docs-index/<version>/index.json` — outside this
repo, never committed.  Every result links to the canonical odoo.com page.

## One-time build

```bash
# Best-effort: sparse-clones odoo/documentation (branch 18.0) via git
odoo-ai docs-build --version 18

# From a local checkout (no network needed):
odoo-ai docs-build --version 18 --src /path/to/odoo/documentation
```

The index is a derived artifact under CC-BY-SA-4.0.  Rebuild whenever you
upgrade Odoo or want fresher docs.  Building takes ~1–2 min on the first run.

## Query

```bash
odoo-ai docs "ORM create method" --version 18
odoo-ai docs "computed field dependencies" --version 18 --top 10
```

Output JSON includes `results[].url` pointing to canonical odoo.com pages and
a `_caveat` reminding you to existence-gate against the live instance:

```json
{
  "query": "...",
  "version": "18",
  "results": [
    {
      "heading": "Fields Reference",
      "rel_path": "content/developer/reference/backend/orm.rst",
      "anchor":   "fields-reference",
      "score":    0.72,
      "preview":  "Fields define the columns of a model ...",
      "url":      "https://www.odoo.com/documentation/18.0/developer/reference/backend/orm.html#fields-reference"
    }
  ],
  "_caveat": "Docs say how the API SHOULD work; introspect the live instance..."
}
```

## Correct workflow: docs → introspect → trust the instance

```
odoo-ai docs "sale.order fields"   →  learn the API design, get the URL
odoo-ai native-check / introspect  →  confirm it exists in THIS instance
```

1. **docs** — answers "how does this API work?" and "where's the reference?"
2. **introspect** (`odoo-ai native-check`, `odoo-ai capabilities`) — answers
   "does this model/field/method exist in the running database?"
3. **Trust the instance** — if introspect says it's absent, the doc answer is
   moot; never claim a feature exists based on docs alone.

> Docs describe how the API _should_ work.  The live instance is what the agent
> can actually use.  Existence-gate every model, field, and method before relying on it.

## License hygiene

| What                        | Where                                      |
|-----------------------------|--------------------------------------------|
| Source                      | `odoo/documentation` (CC-BY-SA-4.0)       |
| Built index                 | `~/.odoo-ai/docs-index/<V>/index.json`    |
| Committed to repo?          | **Never** — derived artifact, stays local  |
| Attribution in results      | `url` field → canonical odoo.com page      |

Do not redistribute the index file; share the canonical URL instead.
