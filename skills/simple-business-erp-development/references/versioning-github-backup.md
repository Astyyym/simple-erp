# Versioning, GitHub backup, and Releases for small ERP apps

Use for local Flask/SQLite ERP projects packaged as Windows desktop apps.

## What belongs in GitHub

Commit source and repeatable build assets:

- `app/`, `tests/`, `docs/` (`docs/需求/`, `docs/复盘/`, ops notes + `docs/README.md`)
- `开发短计划/` (execution plans; not a substitute for long-lived requirements)
- `requirements.txt`, `config.json` template
- build scripts and PyInstaller `.spec`
- `README.md`, `CHANGELOG.md`, `VERSION`

Never commit live business/runtime artifacts:

- `data/`, `logs/`, `temp_pdf/`, `backups/`, `imports/`
- `dist/`, `build/`, `.venv/`, `.venv-win/`
- `*.db`, `*.db-wal`, `*.db-shm`
- root `temp_*.py` / `temp_*.ps1` probes

Verify with:

```bash
git status --ignored --short
git check-ignore -v data/erp.db dist/<app>/<app>.exe logs/app.log
```

## Version display pattern

1. Add `VERSION`, e.g. `v0.4.0`.
2. Add `CHANGELOG.md` with the same version.
3. In Flask `create_app()`, read `VERSION` from the runtime root or PyInstaller bundled root.
4. Inject `app_version` with a context processor.
5. Display it in the sidebar/footer and include it in `/health`.
6. Include `VERSION` in the PyInstaller `datas` list.

## Source release workflow (code + local package)

When 用户 says push + local EXE/ZIP + Release together, run the **full train** (do not stop at `git push`).

1. Edit source; update docs if product behaviour changed.
2. Full `pytest` (Windows: `PYTHONPATH=app .venv-win/Scripts/python.exe -m pytest -q`).
3. Bump `VERSION` / `CHANGELOG.md` / `README.md` (product-facing strings must match code).
4. `git add` product files; run **`git diff --cached --check`** before commit (trailing whitespace aborts).
   - Inspect the index before staging. If unrelated user-staged changes already exist, isolate the release in a temporary index initialized from `HEAD`, or snapshot and restore the unrelated staged state exactly; never let a release commit sweep in pre-existing staged work.
5. Commit and push; verify `HEAD == origin/main`.
6. Before PyInstaller `--clean`, inspect ignored `dist/` and `build/` for live executables, databases, or other user files. If their contents or locks are uncertain, build to unique ASCII scratch paths via `--distpath` and `--workpath`, then package that fresh output without overwriting the existing folders.
7. Free ports 5000/5001 and locks on `dist/简单ERP`; rebuild with `.venv-win/Scripts/python.exe -m PyInstaller 简单ERP.spec --clean --noconfirm` (avoid BAT `pause` for agents).
8. Smoke EXE from ASCII temp + isolated `ERP_DATA_ROOT` (see `windows-chinese-path-exe-smoke.md`). Expect new version + current auth marker.
9. Python `zipfile` → `dist/simple-erp-windows-vX.Y.Z.zip` (ASCII). Optional Chinese zip on disk only — do not upload Chinese basenames.

`git push` alone does **not** put installers on GitHub Releases.

## GitHub Releases installers

When the user asks whether Releases has a package, **query** — do not infer from `main` or local `dist/`:

```bash
gh release list --limit 5
gh api repos/<owner>/<repo>/releases/latest --jq '{tag:.tag_name,assets:[.assets[].name]}'
```

Publish (example):

```bash
gh release create v0.8.0 \
  dist/simple-erp-windows-v0.8.0.zip \
  --title "简单ERP v0.8.0 Windows 桌面版" \
  --notes "..."
```

### Pitfalls (2026-07-12 / 2026-07-14 / 2026-07-26)

- Chinese zip basenames can upload as mangled assets → use `simple-erp-windows-vX.Y.Z.zip` only.
- Tag without assets leaves Latest stale — user will say “releases 没装包”.
- After upload, re-check `gh release view` / `gh api …/releases/latest`.
- Network EOF mid-create is not success/failure proof — re-query same tag.
- Product gate removal (permanent no-login) → **minor** bump (`v0.7.4 → v0.8.0`).
- Multi-issue train then “打包发版” → one minor, not three patches.
- Release notes always: backup data → overwrite program only → never overwrite live `erp.db`.
- Do not commit root `findings.md` / `progress.md` / `task_plan.md` unless asked.

## Upgrade note for operators

Replacing program files must **preserve** the shop’s `data/erp.db`. Always say backup `data/` first.

Full green-folder vs WeChat-like explanation: `references/green-folder-upgrade-and-data.md`.

When user asks “升级会不会替换数据库 / 要不要卸载”:

- Not an MSI → **no uninstall**
- DB lives next to exe → only local overwrite of `data\` hurts it
- Safe path: backup → close app → replace exe/`_internal` only
- Whole-folder paste is the main footgun, not `gh release create`

Release notes must include that one-line data warning every time installers ship.
