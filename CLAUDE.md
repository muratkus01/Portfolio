# CLAUDE.md — AI in Renewable Energy Portfolio

This folder contains Murat's open-source **AI in Renewable Energy Portfolio** (`projects/01` to `06`).

## 🤝 Continuity Protocol

1. Read `../AGENTS.md` (shared dual-assistant constitution: co-working mandate, tone bans, NDA boundaries).
2. Read `../HANDOFF.md` (master index).
3. Read `HANDOFF.md` in this folder for active state and recent decisions.
4. Shared skills live in `../.agents/skills/` (shared with Claude Code via `../.claude/skills` directory junction).

## 🚀 Portfolio Projects & Test Suite

All 6 projects are structured packages in `projects/` with full test suites:
- `projects/01-prosumer-pv-bess-mpc-rl/` (`prosumer`)
- `projects/02-pumped-storage-rl-multimarket/` (`psw`)
- `projects/03-smart-ev-charging-14a/` (`evc`)
- `projects/04-energy-sharing-rec/` (`rec`)
- `projects/05-utility-hybrid-plant-dispatch/` (`hybrid`)
- `projects/06-probabilistic-forecast-to-bid/` (`f2b`)

Run the full portfolio test suite (94 passing unit tests):
```bash
pytest -v
```

Before ending a session, update this folder's `HANDOFF.md` and append a log entry.
