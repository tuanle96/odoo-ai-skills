# Third-party notices

This skill deliberately builds on existing work ("don't reinvent the wheel").

## Vendored (copied into this repo) — MIT licensed
| Origin | Path here | License |
|---|---|---|
| letzdoo/claude-marketplace — `plugins/odoo-development` skill files (18→19 knowledge, data-migration patterns, OWL 18→19) | `references/vendor/letzdoo-odoo-development/` | MIT (LICENSE copy included) |
| ahmed-lakosha/odoo-plugins (TAQAT Techno) — `odoo-upgrade` plugin: precheck/validate/transform scripts, 18→19 pattern reference, error catalog | `references/vendor/taqat-odoo-upgrade/` | MIT (LICENSE copy included) |

Vendored files are content-unmodified (TAQAT SKILL.md renamed to VENDORED_SKILL.md so skill loaders do not register it twice); provenance = directory name + LICENSE copy.

## Invoked as external tools only — NOT vendored (copyleft)
| Tool | License | Why external-only |
|---|---|---|
| OCA/odoo-module-migrator (`odoo-module-migrate` CLI) | AGPL-3.0 | vendoring would AGPL this repo |
| OCA/OpenUpgrade + openupgradelib | AGPL-3.0 | same |
| odoo/odoo `odoo-bin upgrade_code` | LGPL-3.0 | part of user's Odoo checkout |

## Library dependencies referenced by generated migration scripts
| Library | License |
|---|---|
| odoo/upgrade-util (`odoo.upgrade.util`) | LGPL-3.0 |
| odoo-ps/custom-util | see upstream |

Own code in `scripts/` (gen_manifest.py, upgrade_brief.py, upgrade_verify.py,
run_pipeline.sh), `templates/`, and `SKILL.md`: MIT (see LICENSE).
