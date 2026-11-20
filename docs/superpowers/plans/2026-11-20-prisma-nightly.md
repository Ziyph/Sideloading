# Prisma Nightly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve Prisma 2's newest GitHub Actions build in the main source in place of Prisma 6.5.5.

**Architecture:** `scripts/mirror.py` copies the newest successful upstream build into the rolling
prerelease `prisma-nightly` in this repo, with the run number as its build number; `update.py`
then reads that release like any other.

**Tech Stack:** Python 3.12 standard library, `gh` CLI, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-20-prisma-nightly-design.md`

**Execution:** native, in this session (the owner asked to build it directly). Owner: Claude.
Status is tracked in the checkboxes below.

## Global Constraints

- Standard library only in Python; `gh` is the one outside tool (preinstalled on runners).
- Only the app's own `Info.plist` `CFBundleVersion` changes; every other IPA entry stays byte-identical.
- Upstream: `Gsjsjzhznsz/Prisma-Minecraft-iOS-Launcher2`, workflow `development.yml`, branch
  `main`, artifact `com.prisma-devs.prisma-ios.ipa`. Mirror release: `Ziyph/Sideloading`, tag
  `prisma-nightly`, prerelease.
- Upstream reads use `ARTIFACTS_TOKEN` when set; release changes use the ambient `gh` auth
  (`GH_TOKEN=GITHUB_TOKEN` in CI).
- Copy is fixed by the spec's Design section.

## Review Focus

- Release exists but holds no asset for the current run (a failed earlier upload): mirror again.
- Create fails after the delete: the error is logged and the run fails; the next run retries.
- Artifact holds zero or several IPAs: error, release untouched.
- `ARTIFACTS_TOKEN` missing in CI: download fails with `gh`'s error, `update.py` still runs.
- The asset name the script builds must match the manifest's `github.asset` pattern (tested).

---

### Task 1: `scripts/mirror.py` with tests

**Files:**
- Create: `scripts/mirror.py`
- Test: `tests/test_mirror.py`

**Interfaces:**
- Consumes: `update.APPS_DIR`, `update.load_json`, `update.log`, `update.inspect_ipa`.
- Produces: `asset_name(ipa_name: str, run_number: int) -> str`,
  `set_build_version(ipa: pathlib.Path, build: str) -> None`,
  `mirrored_run(assets: list[str] | None) -> int | None`,
  `check_ipa(manifest: dict, ipa: pathlib.Path) -> dict`,
  `mirror_app(manifest: dict, dry_run: bool) -> None`, `main() -> int`.

- [x] **Step 1: Write the failing tests** in `tests/test_mirror.py` (`unittest`, fixture IPA built
  in a temp dir with `Payload/Test.app/Info.plist` (XML, `CFBundleVersion` "1.0.0"),
  `Payload/Test.app/PlugIns/Helper.appex/Info.plist` (`CFBundleVersion` "1.0"), a directory
  entry, and an executable `Payload/Test.app/Test` with Unix mode 0o755):
  - `test_set_build_version_changes_only_app_build`: app build becomes "316", short version and
    the extension's "1.0" unchanged, every other entry's bytes and `external_attr` unchanged,
    entry order unchanged.
  - `test_set_build_version_keeps_binary_plist`: a binary app plist stays binary.
  - `test_set_build_version_without_app_plist_raises`: `RuntimeError`.
  - `test_check_ipa_accepts_matching_build`, `test_check_ipa_rejects_name_outside_manifest_pattern`,
    `test_check_ipa_rejects_other_bundle_id` (added after review).
  - `test_mirrored_run_reads_highest_run_number`, `test_mirrored_run_without_build` (added after
    review).
  - `test_asset_name_matches_manifest`: `asset_name("com.prisma-devs.prisma-1.0.0-ios.ipa", 316)`
    equals `"com.prisma-devs.prisma-1.0.0-ios-r316.ipa"` and fully matches
    `apps/prisma.json`'s `github.asset`.
- [x] **Step 2: Run** `python3 -m unittest discover -s tests -v`. Expected: errors importing `mirror`.
- [x] **Step 3: Implement `scripts/mirror.py`** per the spec's steps 1 to 4 and `--dry-run`;
  rewrite the IPA with `zipfile`, keeping each entry's `ZipInfo`.
- [x] **Step 4: Run** the tests. Expected: all pass. Break `set_build_version` once to see a test fail.

### Task 2: Wire it in and switch Prisma over

**Files:**
- Modify: `apps/prisma.json`, `.github/workflows/update.yml`, `source.config.json`,
  `scripts/update.py` (docstring), `README.md`
- Delete: `apps/prisma-beta.json`, `source-beta.json`

- [x] **Step 1:** Point `apps/prisma.json` at the mirror (spec Design), with asset pattern
  `com\.prisma-devs\.prisma-[0-9][0-9.]*-ios-r[0-9]+\.ipa`, the Launcher2 icon and the new copy.
- [x] **Step 2:** Add the mirror step to `update.yml` before the update step (env `GH_TOKEN`,
  `ARTIFACTS_TOKEN`); give the update step `if: ${{ !cancelled() }}`.
- [x] **Step 3:** Copy edits and beta removal per spec.
- [x] **Step 4: Verify** `python3 scripts/mirror.py --dry-run` prints
  `com.prisma-devs.prisma 1.0.0 (<run number>)`, the unit tests pass, and `update.py` on a
  scratch copy still builds the other apps.
