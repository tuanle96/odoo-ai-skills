---
name: odoo-worktree
description: >-
  Isolated feature development for a LIVE Odoo dev environment using git
  worktree — build/test on the running instance while committing to a clean
  branch cut from production, without touching prod or the active dev branch.
  Use whenever a feature must not land on the current dev/uat branch yet
  (pending approval/training), when the Odoo container mounts the main working
  tree (so you can't just switch branches), when syncing changes between a
  worktree branch and the live working copy, or when packaging ORM-created
  records into an addon that must install identically on a clean DB
  (post_init_hook adopt-vs-create + clean-install test ritual).
---

# Odoo + git worktree — develop hot, commit cold

The core tension: the **dev instance mounts the main working tree** (docker
`addons_path` → the repo checkout), so you cannot switch branches without
breaking the running registry — but the feature must stay off both
`production` and the active dev branch (`uat`) until it is approved.

**The pattern: the main tree stays on the dev branch and RUNS the code as
uncommitted files; a git worktree holds a clean feature branch cut from
production and RECEIVES the same changes as commits.**

```
repo/                          ← main worktree, branch uat (env mounts this)
│   my_addon/        (untracked — live code the container executes)
│   other_addon/x.py (modified — your surgical fix)
└── .worktrees/feature-x/      ← second worktree, branch feature/x ← production
        my_addon/    (committed)
```

## Setup

```bash
git fetch origin production
git worktree add .worktrees/feature-x -b feature/x origin/production
```

- `.worktrees/` inside the repo is fine: Odoo's `addons_path` does **not**
  scan recursively, so the copies do not shadow the live addons
  (`odoo-ai preflight <module>` confirms: 0 shadow warnings).
- **Submodule gotcha:** `git worktree add` does NOT checkout submodules. If
  `addons_path` lists a submodule dir (often *first*), a container pointed at
  the worktree will half-load: run
  `git -C .worktrees/feature-x submodule update --init <submodule>`.
- Removing a worktree later (`git worktree remove`) does not delete the
  branch or its commits — the worktree is just a seat.

## Sync discipline — the one rule that prevents disasters

The two trees sit on **different base branches** (`uat` ≠ `production`).
Never sync whole shared directories or merge/checkout across — you would
overwrite one branch's version of shared files with the other's.
**Sync only the explicit paths owned by this workstream.**

Main tree → worktree (the normal loop — you edited where the env runs):

```bash
rsync -a --exclude __pycache__ --delete my_addon other_new_addon .worktrees/feature-x/
cp shared_addon/models/one_file.py .worktrees/feature-x/shared_addon/models/  # surgical file only
cd .worktrees/feature-x && git add -A && git commit && git push
```

Worktree → main tree (pull branch state into the live env):

```bash
git restore --source=feature/x --worktree -- my_addon other_new_addon shared_addon/models/one_file.py
# then restart the Odoo container (Python) or -u (XML/data)
```

For a change inside a **shared, diverged addon**: verify the file is identical
on both bases first (`git diff origin/production HEAD -- path`); if it is,
a plain `git diff > patch` + `git apply` in the worktree is exact.

## Packaging ORM-created records so the branch is self-contained

Building on the live DB via `odoo shell` is the fast loop, but the branch
must reproduce everything. Put the same logic in an **idempotent
`post_init_hook`** (find-or-create by code/name, never by id):

- on the dev DB the hook **adopts** the records you already created;
- on a clean DB it **creates** them.

Dynamic per-DB artifacts (e.g. analytic `x_plan<id>_id` fields, report ids in
client-action contexts) must be resolved **by name at hook runtime**, never
hard-coded from the dev DB.

## The clean-install ritual (non-negotiable)

`-u` does **not** re-run `post_init_hook`, so dev never exercises the
create-path. Every hook edit gets:

```bash
psql: CREATE DATABASE test_db TEMPLATE <old_prod_clone>;   # a dump that predates your records
odoo -d test_db -i my_addon --stop-after-init              # grep -c "Traceback|ParseError" → must be 0
# assert counts / render reports on test_db, then DROP DATABASE
```

This catches the class of bug that only exists on fresh installs — e.g. a
scripted edit that anchored on a non-unique string and injected code into the
wrong function runs fine on dev (hook never re-fires) and explodes on
`-i`. Script-edits into hooks must anchor on unique strings; verify with
`ast.parse` + `odoo-ai validate` + this ritual.

## Recovery & safety notes

- The live env depends on **uncommitted files** — a careless `git clean -fd`
  on the main tree kills the running registry's code. After the first
  worktree commit+push this is recoverable in seconds via
  `git restore --source=feature/x --worktree -- <paths>`.
- Keep the working-tree copies in place until go-live; do not commit them to
  the dev branch "for safety" — that defeats the isolation.
- Deploy later = merge `feature/x` → `production` and fresh `-i` (records
  come from the hook; no migrations for never-deployed addons).

## Checklist

1. `git worktree add .worktrees/<x> -b feature/<x> origin/production`
2. Submodules in the worktree if `addons_path` needs them.
3. Develop in the **main tree**; test on the live instance.
4. `rsync` only workstream paths → commit in worktree → push.
5. Hook logic idempotent; dynamic ids resolved by name.
6. Clean-install test after **every** hook/data change.
7. Before claiming "all pushed": `diff -rq` main-vs-worktree paths,
   `git status --porcelain`, `git log origin/<branch>..HEAD` — all empty.
