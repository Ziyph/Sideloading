#!/usr/bin/env python3
"""Rebuild the AltStore source files from the manifests in apps/.

Works like a Scoop bucket's checkver/autoupdate: each apps/*.json manifest says
which GitHub release and asset to track. When the asset changes, the IPA is
downloaded once to read its real version, minimum iOS version, entitlements and
privacy strings, and the source JSON is regenerated. Nothing is re-hosted; the
downloadURL is the upstream release asset.

Standard library only, so it runs on a stock GitHub Actions runner.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import plistlib
import re
import shutil
import struct
import sys
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"
CONFIG_FILE = ROOT / "source.config.json"
STATE_FILE = ROOT / "state.json"

USER_AGENT = "altsource-updater (+https://github.com/Ziyph/Sideloading)"
# Entitlements AltStore treats as implicit; its docs say to leave them out.
IMPLICIT_ENTITLEMENTS = {"application-identifier", "com.apple.developer.team-identifier"}
CSMAGIC_EMBEDDED_ENTITLEMENTS = b"\xfa\xde\x71\x71"
APP_FIELDS = ("name", "bundleIdentifier", "developerName", "subtitle",
              "localizedDescription", "iconURL", "tintColor", "category", "screenshots")


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: pathlib.Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json_if_changed(path: pathlib.Path, data) -> bool:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def channel_file(channel: str) -> str:
    return "source.json" if channel == "stable" else f"source-{channel}.json"


# --- GitHub -----------------------------------------------------------------

def github_get(url: str):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_release(gh: dict) -> dict:
    repo = gh["repo"]
    tag = gh.get("tag")
    if tag:
        return github_get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    return github_get(f"https://api.github.com/repos/{repo}/releases/latest")


def pick_asset(release: dict, pattern: str) -> dict:
    rx = re.compile(pattern)
    matches = [a for a in release.get("assets", []) if rx.fullmatch(a["name"])]
    if not matches:
        names = ", ".join(a["name"] for a in release.get("assets", []))
        raise RuntimeError(f"no asset matches {pattern!r} (assets: {names})")
    if len(matches) > 1:
        raise RuntimeError(f"{pattern!r} matches several assets: {[a['name'] for a in matches]}")
    return matches[0]


def download(url: str, dest: pathlib.Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1 << 20)


# --- IPA inspection ---------------------------------------------------------

def read_entitlements(binary: bytes) -> dict:
    """Return the entitlements embedded in the main executable's code signature."""
    i = binary.find(CSMAGIC_EMBEDDED_ENTITLEMENTS)
    if i < 0:
        return {}
    length = struct.unpack(">I", binary[i + 4:i + 8])[0]
    try:
        return plistlib.loads(binary[i + 8:i + length])
    except Exception:
        return {}


def inspect_ipa(path: pathlib.Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        info_name = next((n for n in names
                          if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)), None)
        if not info_name:
            raise RuntimeError("Payload/*.app/Info.plist not found in IPA")
        app_dir = info_name.rsplit("/", 1)[0]
        info = plistlib.loads(z.read(info_name))
        exe = info.get("CFBundleExecutable")
        entitlements = read_entitlements(z.read(f"{app_dir}/{exe}")) if exe else {}
    privacy = {k: v for k, v in info.items() if k.endswith("UsageDescription") and isinstance(v, str)}
    return {
        "bundleIdentifier": info.get("CFBundleIdentifier"),
        "version": str(info.get("CFBundleShortVersionString", "")),
        "buildVersion": str(info.get("CFBundleVersion", "")),
        "minOSVersion": info.get("MinimumOSVersion"),
        "entitlements": sorted(k for k in entitlements if k not in IMPLICIT_ENTITLEMENTS),
        "privacy": dict(sorted(privacy.items())),
    }


# --- Building entries -------------------------------------------------------

def release_notes(release: dict) -> str:
    body = (release.get("body") or "").strip()
    title = (release.get("name") or release.get("tag_name") or "").strip()
    text = body or title
    return text[:1500] + ("…" if len(text) > 1500 else "")


def resolve_app(manifest: dict, cached: dict | None) -> dict:
    """Return {'fingerprint', 'version', 'appPermissions'} for one manifest."""
    gh = manifest["github"]
    release = fetch_release(gh)
    asset = pick_asset(release, gh["asset"])
    fingerprint = f"{asset['id']}:{asset['updated_at']}:{asset['size']}"
    if cached and cached.get("fingerprint") == fingerprint:
        log(f"  {manifest['name']}: unchanged ({asset['name']})")
        return cached

    log(f"  {manifest['name']}: new asset {asset['name']} ({asset['size']} bytes), inspecting…")
    with tempfile.TemporaryDirectory() as tmp:
        ipa = pathlib.Path(tmp) / "app.ipa"
        download(asset["browser_download_url"], ipa)
        size = ipa.stat().st_size
        meta = inspect_ipa(ipa)

    if meta["bundleIdentifier"] != manifest["bundleIdentifier"]:
        raise RuntimeError(f"bundle ID in IPA is {meta['bundleIdentifier']!r}, "
                           f"manifest says {manifest['bundleIdentifier']!r}")
    published = release.get("published_at") or asset.get("updated_at")
    version = {
        "version": meta["version"],
        "buildVersion": meta["buildVersion"],
        "marketingVersion": (release.get("name") or meta["version"]).strip(),
        "date": published,
        "localizedDescription": release_notes(release),
        "downloadURL": asset["browser_download_url"],
        "size": size,
    }
    if meta["minOSVersion"]:
        version["minOSVersion"] = meta["minOSVersion"]
    return {
        "fingerprint": fingerprint,
        "version": version,
        "appPermissions": {"entitlements": meta["entitlements"], "privacy": meta["privacy"]},
    }


def build_app(manifest: dict, resolved: dict) -> dict:
    app = {k: manifest[k] for k in APP_FIELDS if k in manifest}
    v = resolved["version"]
    # The tracked release tags are reused for each new build, so older asset
    # URLs stop working: list only the current version.
    app["versions"] = [v]
    app["appPermissions"] = resolved["appPermissions"]
    # Legacy single-version fields for older AltStore-format clients.
    app["version"] = v["version"]
    app["versionDate"] = v["date"]
    app["versionDescription"] = v.get("localizedDescription", "")
    app["downloadURL"] = v["downloadURL"]
    app["size"] = v["size"]
    return app


def validate(source: dict) -> None:
    required_app = ("name", "bundleIdentifier", "developerName", "localizedDescription", "iconURL")
    required_version = ("version", "buildVersion", "date", "downloadURL", "size")
    ids = set()
    for app in source["apps"]:
        for key in required_app:
            if not app.get(key):
                raise RuntimeError(f"{app.get('name')}: missing {key}")
        if app["bundleIdentifier"] in ids:
            raise RuntimeError(f"duplicate bundleIdentifier {app['bundleIdentifier']} in one source")
        ids.add(app["bundleIdentifier"])
        for v in app["versions"]:
            for key in required_version:
                if v.get(key) in (None, ""):
                    raise RuntimeError(f"{app['name']}: version missing {key}")


def main() -> int:
    config = load_json(CONFIG_FILE)
    state = load_json(STATE_FILE, {})
    manifests = []
    for path in sorted(APPS_DIR.glob("*.json")):
        m = load_json(path)
        m["_file"] = path.name
        if m.get("enabled", True):
            manifests.append(m)

    channels: dict[str, list[dict]] = {}
    failed = False
    new_state = {}
    for m in manifests:
        channel = m.get("channel", "stable")
        key = f"{channel}/{m['bundleIdentifier']}"
        try:
            resolved = resolve_app(m, state.get(key))
        except Exception as exc:  # keep the last good entry if the check fails
            log(f"  {m['name']}: ERROR {exc}")
            failed = True
            resolved = state.get(key)
            if not resolved:
                # No cache yet: carry over the entry already published, if any.
                published = load_json(ROOT / channel_file(channel), {}) or {}
                old = next((a for a in published.get("apps", [])
                            if a.get("bundleIdentifier") == m["bundleIdentifier"]), None)
                if old:
                    channels.setdefault(channel, []).append(old)
                continue
        new_state[key] = resolved
        channels.setdefault(channel, []).append(build_app(m, resolved))

    changed = False
    for channel, apps in channels.items():
        fname = channel_file(channel)
        source = {
            "name": config["name"] + ("" if channel == "stable" else f" ({channel})"),
            "identifier": config["identifier"] + ("" if channel == "stable" else f".{channel}"),
        }
        for k in ("subtitle", "description", "iconURL", "website", "tintColor"):
            if config.get(k):
                source[k] = config[k]
        if config.get("sourceURL"):
            source["sourceURL"] = config["sourceURL"].format(file=fname)
        source["apps"] = apps
        source["news"] = []
        validate(source)
        if write_json_if_changed(ROOT / fname, source):
            log(f"wrote {fname}")
            changed = True
    if write_json_if_changed(STATE_FILE, new_state):
        changed = True
    log("changes written" if changed else "no changes")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
