# Prisma nightly builds

## Intent

Serve the newest Prisma 2 test builds from its GitHub Actions in the main source, in place of
stable Prisma 6.5.5, so the source always offers Prisma's latest features. The separate beta
source goes away.

## Goals, non-goals, anti-goals

- Goal: the main source's Prisma entry offers the newest successful `main` build of
  `Gsjsjzhznsz/Prisma-Minecraft-iOS-Launcher2` within about 6 hours of it landing.
- Goal: AltStore and SideStore treat each new build as an update.
- Non-goals: installing next to stable Prisma, keeping 6.5.5 or the v1.0 beta, mirroring other
  apps, keeping old builds.
- Anti-goal: changing anything in the app other than its build number.

## Constraints (checked)

- Artifacts need a GitHub login to download (anonymous request: 401), the download link expires
  after about a minute, they are zipped, and they expire after 90 days. So builds must be
  re-hosted.
- Every build reports version 1.0.0, build 1.0.0. AltStore offers an update only when the
  version or build differs from the installed app (`InstalledApp.supportedUpdatesFetchRequest`)
  and rejects a download whose version or build differs from its listing
  (`VerifyAppOperation.verifyDownloadedVersion`). So each copy gets a unique build number.
- The JIT helper extension already reports build 1.0 next to the app's 1.0.0, so only the app's
  own `Info.plist` needs the new build number.
- Same bundle ID as stable Prisma (`com.prisma-devs.prisma`), so one Prisma entry per source.
- The workflow's built-in `GITHUB_TOKEN` can't download another repo's artifacts (per the
  `actions/download-artifact` docs), so downloads use a read-only token the owner stores as the
  `ARTIFACTS_TOKEN` secret. Release changes in this repo keep using `GITHUB_TOKEN`.

## Approaches

1. **Mirror step before `update.py` (picked).** A new `scripts/mirror.py` copies the newest build
   into a release in this repo, and `update.py` reads that release like any other. `update.py`
   stays as it is.
2. Teach `update.py` to read artifacts itself. One script fewer, but it would also have to upload
   files, mixing publishing into a script that only reads releases.
3. An inline shell step in `update.yml`. Least code, but untestable and Prisma-specific YAML.

## Design

- `apps/prisma.json` points `github` at this repo's rolling prerelease `prisma-nightly`, and a new
  `mirror` block names the upstream build:
  `{"repo": "Gsjsjzhznsz/Prisma-Minecraft-iOS-Launcher2", "workflow": "development.yml",
  "branch": "main", "artifact": "com.prisma-devs.prisma-ios.ipa"}`.
- `scripts/mirror.py` (standard library plus the `gh` CLI, which GitHub's runners already have),
  for each manifest with a `mirror` block:
  1. Find the newest successful run of the workflow on the branch.
  2. Stop if the release already holds that run's build or a newer one (an upstream re-run can
     briefly make an older run the newest successful one).
  3. Download the artifact, take the IPA out, and set the app's `CFBundleVersion` to the run
     number (316 at the time of writing). Stop before touching the release unless the IPA has
     the manifest's bundle ID and its name matches the manifest's asset pattern, so a changed
     upstream build never replaces a working one.
  4. Delete the release and its tag, then create it again as a prerelease holding the IPA as
     `<artifact IPA name without .ipa>-r<run>.ipa`, e.g.
     `com.prisma-devs.prisma-1.0.0-ios-r316.ipa`. Recreating it, as Prisma's own nightly step
     does, keeps one asset per release and gives each build a fresh release date, which
     `update.py` uses as the version date. The title is `Prisma nightly #<run> (<short sha>)`,
     which `update.py` shows as the version name; the notes link to the upstream commit and run
     (the source pointer AGPL asks for).
- `mirror.py --dry-run` does steps 1 to 3 and prints what it would publish, without changing the
  release.
- `update.yml` runs `mirror.py` before `update.py`; `update.py` still runs if mirroring fails, so
  the other apps keep updating.
- Remove `apps/prisma-beta.json`, `source-beta.json` and the README's beta links. The README's
  Prisma link points at the Prisma 2 repo.
- Copy:
  - Source description: "My AltStore-style source for apps without one. Apps download straight
    from their own GitHub releases, except Prisma, whose nightly builds are mirrored here. Updated
    automatically via GitHub Actions."
  - Prisma description: "Prisma is a fork of Air with simpler JIT setup on iOS 26 and in-app
    language switching. These are nightly test builds, so expect bugs. Not available in mainland
    China."
  - `update.py`'s docstring drops "Nothing is re-hosted".
- Known gap: when a new build replaces the old asset, the published source points at a deleted
  file until the bot commits the new `source.json` and GitHub's 5-minute raw cache clears. If the
  upload fails or the job stops between deleting and creating the release, that lasts until the
  next run (up to 6 hours, or a manual run of the workflow).

## Checks

- Unit test (standard library `unittest`) for the build-number patch on a small generated IPA.
- Local dry run of `mirror.py` against the real upstream that stops before uploading, then
  inspect the patched IPA with `update.py`'s `inspect_ipa`.
- After pushing: the workflow succeeds, and `source.json` lists Prisma 1.0.0 with build equal to
  the upstream run number and a working download link.

## Left to the builder

Script structure, `gh` versus REST calls for individual steps, and error messages.
