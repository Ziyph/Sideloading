# Ziyph's Sideloading Source

An unofficial AltStore-style source for apps that don't publish one. Nothing is re-hosted: every
download link points at the app's own GitHub release. A GitHub Action checks for new releases
every 6 hours and rebuilds `source.json`.

## Add the source

Source URL:

```
https://raw.githubusercontent.com/Ziyph/Sideloading/main/source.json
```

- LiveContainer: [livecontainer://source?url=https%3A%2F%2Fraw.githubusercontent.com%2FZiyph%2FSideloading%2Fmain%2Fsource.json](livecontainer://source?url=https%3A%2F%2Fraw.githubusercontent.com%2FZiyph%2FSideloading%2Fmain%2Fsource.json)
- SideStore: [sidestore://source?url=https%3A%2F%2Fraw.githubusercontent.com%2FZiyph%2FSideloading%2Fmain%2Fsource.json](sidestore://source?url=https%3A%2F%2Fraw.githubusercontent.com%2FZiyph%2FSideloading%2Fmain%2Fsource.json)

## Apps

| App | Tracks |
|---|---|
| [Witch](https://github.com/Witch-Launcher/Witch_launcher) | `Witch-<version>-ios.ipa` on the `release` tag |

## How it works

Like a Scoop bucket: each file in `apps/` is a manifest. `scripts/update.py` reads them, asks the
GitHub API for the tracked release, and when the matching asset changes it downloads the IPA once
to read its real version, minimum iOS version, entitlements and privacy strings. It writes
`source.json` (and `source-<channel>.json` for any non-stable channel) plus `state.json`, a cache
that stops unchanged IPAs from being downloaded again.

### Add an app

Create `apps/<name>.json`:

```json
{
  "name": "Example",
  "bundleIdentifier": "com.example.app",
  "developerName": "Someone",
  "subtitle": "One line",
  "localizedDescription": "What it is.",
  "iconURL": "https://…/icon.png",
  "tintColor": "#1E88E5",
  "category": "utilities",
  "channel": "stable",
  "github": {
    "repo": "owner/repo",
    "tag": "optional-tag-name",
    "asset": "Example-[0-9.]+\\.ipa"
  }
}
```

- `asset` is a regular expression that must match exactly one asset name.
- Leave out `tag` to follow the repo's latest release.
- Each bundle ID can appear only once per channel. To track a beta build of the same app, use
  `"channel": "beta"`, which writes `source-beta.json`.
- Set `"enabled": false` to pause an app.

### Run it locally

```
python3 scripts/update.py
```

Set `GITHUB_TOKEN` to avoid the API's low rate limit for anonymous requests.

## Notes

- Release tags that get reused for every build (like Witch's `release`) delete the old assets,
  so only the current version is listed.
- GitHub turns off scheduled workflows in public repos after 60 days without repository
  activity. If that happens, re-enable the workflow in the Actions tab.
