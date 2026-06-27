<!-- odoo-ai-skills PR template. Delete sections that don't apply. -->

## What & why

<!-- One or two lines: what this changes and the reason. -->

## Native capability check (for Odoo customizations)

> Only required when this PR **adds** a field / model / wizard / report / cron /
> automation, or **overrides a core flow method**. Skip for bug-fixes, view
> tweaks, docs, or work inside your own module.

- [ ] Ran `odoo-ai native-check "<requirement>"` (and/or `odoo-ai capabilities <model>`) before writing code
- [ ] Checked existing **fields** before adding a new one (`odoo-ai brief <model>`)
- [ ] Checked existing **wizards / actions / reports** before creating one
- [ ] Checked **settings / feature groups** before hardcoding behavior
- [ ] Checked **automation rules / server / scheduled actions** before a write-hook or cron
- [ ] **Native candidates considered** (with instance evidence): …
- [ ] **Reused:** … &nbsp; · &nbsp; **Rejected + why:** … &nbsp; · &nbsp; **The gap built here:** …

## Ground truth read first

- [ ] Introspected the live instance (`odoo-ai all <model>`) — didn't guess fields / MRO / `super()` / view arch / security
- [ ] Extension lands at the smallest correct hook and at the right MRO layer (`depends` set accordingly)

## Tested

- [ ] Unit tests pass (`python -m unittest discover -s tests` and/or `pytest skills/odoo-introspect/scripts/tests`)
- [ ] For an Odoo customization: test fails before / passes after; checked non-admin, multi-company, batch where relevant
- [ ] Docs impact: [ none | minor | major ] — updated if needed
