#!/usr/bin/env python3
"""Mirror GitHub Actions builds into releases in this repo.

Some apps only ship builds as workflow artifacts, which need a GitHub login to
download and expire after a while. For each apps/*.json manifest with a "mirror"
block, this copies the newest successful build into the release its "github"
block tracks, so update.py can read it like any other release.

Each copy gets the upstream run number as its CFBundleVersion: these builds all
report the same version, and AltStore only offers an update when the version or
build changes. Nothing else in the IPA is touched.

Needs the gh CLI. Upstream reads use ARTIFACTS_TOKEN when it is set, because the
workflow's own GITHUB_TOKEN can't download another repo's artifacts.
Run with --dry-run to download and patch the newest build without publishing.
"""
from __future__ import annotations

import json
import os
import pathlib
import plistlib
import re
import subprocess
import sys
import tempfile
import zipfile

from update import APPS_DIR, inspect_ipa, load_json, log

INFO_PLIST = re.compile(r"Payload/[^/]+\.app/Info\.plist")


def gh(*args: str, token: str | None = None) -> str:
    env = {**os.environ, "GH_TOKEN": token} if token else None
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {proc.stderr.strip()}")
    return proc.stdout


def asset_name(ipa_name: str, run_number: int) -> str:
    return f"{ipa_name.removesuffix('.ipa')}-r{run_number}.ipa"


def mirrored_run(assets: list[str] | None) -> int | None:
    """Return the highest upstream run number among a release's mirrored assets."""
    runs = [int(m.group(1)) for a in assets or [] if (m := re.search(r"-r([0-9]+)\.ipa$", a))]
    return max(runs, default=None)


def check_ipa(manifest: dict, ipa: pathlib.Path) -> dict:
    """Check that update.py will accept the patched IPA, so a bad build never replaces a good one."""
    meta = inspect_ipa(ipa)
    if meta["bundleIdentifier"] != manifest["bundleIdentifier"]:
        raise RuntimeError(f"bundle ID in IPA is {meta['bundleIdentifier']!r}, "
                           f"manifest says {manifest['bundleIdentifier']!r}")
    if not re.fullmatch(manifest["github"]["asset"], ipa.name):
        raise RuntimeError(f"{ipa.name} doesn't match the manifest's asset pattern")
    return meta


def set_build_version(ipa: pathlib.Path, build: str) -> None:
    """Set CFBundleVersion in the app's own Info.plist, copying every other entry as is."""
    patched = ipa.with_name(ipa.name + ".tmp")
    with zipfile.ZipFile(ipa) as zin:
        info_name = next((n for n in zin.namelist() if INFO_PLIST.fullmatch(n)), None)
        if not info_name:
            raise RuntimeError("Payload/*.app/Info.plist not found in IPA")
        with zipfile.ZipFile(patched, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item)
                if item.filename == info_name:
                    info = plistlib.loads(data)
                    info["CFBundleVersion"] = build
                    fmt = plistlib.FMT_BINARY if data.startswith(b"bplist") else plistlib.FMT_XML
                    data = plistlib.dumps(info, fmt=fmt)
                zout.writestr(item, data)
    patched.replace(ipa)


def mirror_app(manifest: dict, dry_run: bool) -> None:
    src = manifest["mirror"]
    repo, tag = manifest["github"]["repo"], manifest["github"]["tag"]
    token = os.environ.get("ARTIFACTS_TOKEN") or None

    runs = json.loads(gh("run", "list", "-R", src["repo"], "--workflow", src["workflow"],
                         "--branch", src["branch"], "--status", "success", "--limit", "1",
                         "--json", "databaseId,number,headSha", token=token))
    if not runs:
        raise RuntimeError(f"no successful {src['workflow']} runs on {src['branch']}")
    run = runs[0]
    number, sha = run["number"], run["headSha"]

    try:
        assets = [a["name"] for a in json.loads(
            gh("release", "view", tag, "-R", repo, "--json", "assets"))["assets"]]
    except RuntimeError as exc:
        if "release not found" not in str(exc):
            raise
        assets = None
    # A re-run upstream can briefly make an older run the newest successful one.
    current = mirrored_run(assets)
    if current is not None and current >= number:
        log(f"  {manifest['name']}: unchanged (run #{current})")
        return

    log(f"  {manifest['name']}: new build, run #{number} ({sha[:7]}), mirroring…")
    with tempfile.TemporaryDirectory() as tmp:
        gh("run", "download", str(run["databaseId"]), "-R", src["repo"], "-n", src["artifact"],
           "-D", tmp, token=token)
        ipas = list(pathlib.Path(tmp).rglob("*.ipa"))
        if len(ipas) != 1:
            raise RuntimeError(f"expected one IPA in {src['artifact']}, found {len(ipas)}")
        ipa = ipas[0].rename(ipas[0].with_name(asset_name(ipas[0].name, number)))
        set_build_version(ipa, str(number))
        meta = check_ipa(manifest, ipa)

        if dry_run:
            log(f"  dry run: would publish {ipa.name} to {repo}@{tag}: "
                f"{meta['bundleIdentifier']} {meta['version']} ({meta['buildVersion']})")
            return

        title = f"{manifest['name']} nightly #{number} ({sha[:7]})"
        notes = (f"Build #{number} from {src['repo']}, mirrored by this repo's workflow.\n\n"
                 f"Source: https://github.com/{src['repo']}/tree/{sha}\n"
                 f"Workflow run: https://github.com/{src['repo']}/actions/runs/{run['databaseId']}")
        # Recreate the release so it holds one asset and carries this build's date.
        if assets is not None:
            gh("release", "delete", tag, "-R", repo, "--cleanup-tag", "--yes")
        gh("release", "create", tag, str(ipa), "-R", repo, "--prerelease",
           "--title", title, "--notes", notes)
        log(f"  {manifest['name']}: published {ipa.name}")


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    failed = False
    for path in sorted(APPS_DIR.glob("*.json")):
        m = load_json(path)
        if "mirror" not in m or not m.get("enabled", True):
            continue
        try:
            mirror_app(m, dry_run)
        except Exception as exc:
            log(f"  {m['name']}: ERROR {exc}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
