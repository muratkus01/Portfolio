# CLAUDE.md — AI in Renewable Energy Portfolio

This folder contains Murat's open-source **AI in Renewable Energy Portfolio**. Three active
projects live in `projects/`; three earlier ones were archived on 2026-10-09 and live in
`archive/` (see `archive/README.md`).

## 🤝 Continuity Protocol

1. Read `../AGENTS.md` (shared dual-assistant constitution: co-working mandate, tone bans, NDA boundaries).
2. Read `../HANDOFF.md` (master index).
3. Read `HANDOFF.md` in this folder for active state and recent decisions.
4. Shared skills live in `../.agents/skills/` (shared with Claude Code via `../.claude/skills` directory junction).

## 🚀 Portfolio Projects & Test Suite

The three active projects, all structured packages with full test suites:
- `projects/01-prosumer-pv-bess-mpc-rl/` (`prosumer`, 187 tests)
- `projects/02-utility-hybrid-plant-dispatch/` (`hybrid`, 180 tests)
- `projects/03-probabilistic-forecast-to-bid/` (`f2b`, 61 tests)

Archived, still built and still tested, but not shown in `showcase.html`:
- `archive/02-pumped-storage-rl-multimarket/` (`psw`, 27 tests)
- `archive/03-smart-ev-charging-14a/` (`evc`, 20 tests)
- `archive/04-energy-sharing-rec/` (`rec`, 41 tests)

Run everything, active and archived (516 tests, 2026-11-01: without the gitignored data, 476 passed and 40 skipped with LightGBM installed; CI does not install LightGBM and skips one more):
```bash
pytest -q
```

Check the showcase page after editing it:
```bash
python scripts/verify_html_integrity.py
```

Before ending a session, update this folder's `HANDOFF.md` and append a log entry.
