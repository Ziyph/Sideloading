import json
import pathlib
import plistlib
import re
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mirror  # noqa: E402

APP_PLIST = "Payload/Test.app/Info.plist"
APPEX_PLIST = "Payload/Test.app/PlugIns/Helper.appex/Info.plist"
BINARY = "Payload/Test.app/Test"


def make_ipa(path: pathlib.Path, app_plist_fmt=plistlib.FMT_XML, with_app_plist=True) -> None:
    app = {"CFBundleIdentifier": "com.example.test", "CFBundleShortVersionString": "1.0.0",
           "CFBundleVersion": "1.0.0", "CFBundleExecutable": "Test"}
    appex = {"CFBundleIdentifier": "com.example.test.Helper", "CFBundleVersion": "1.0"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("Payload/Test.app/"), b"")
        if with_app_plist:
            z.writestr(APP_PLIST, plistlib.dumps(app, fmt=app_plist_fmt))
        z.writestr(APPEX_PLIST, plistlib.dumps(appex))
        exe = zipfile.ZipInfo(BINARY)
        exe.external_attr = 0o100755 << 16
        exe.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(exe, b"\xcf\xfa\xed\xfe" + bytes(range(256)) * 64)


class SetBuildVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ipa = pathlib.Path(self.tmp.name) / "app.ipa"

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_build_version_changes_only_app_build(self):
        make_ipa(self.ipa)
        with zipfile.ZipFile(self.ipa) as z:
            before = {i.filename: (z.read(i), i.external_attr) for i in z.infolist()}
            order = z.namelist()

        mirror.set_build_version(self.ipa, "316")

        with zipfile.ZipFile(self.ipa) as z:
            self.assertEqual(z.namelist(), order)
            app = plistlib.loads(z.read(APP_PLIST))
            self.assertEqual(app["CFBundleVersion"], "316")
            self.assertEqual(app["CFBundleShortVersionString"], "1.0.0")
            self.assertEqual(plistlib.loads(z.read(APPEX_PLIST))["CFBundleVersion"], "1.0")
            for info in z.infolist():
                if info.filename != APP_PLIST:
                    self.assertEqual((z.read(info), info.external_attr), before[info.filename],
                                     info.filename)

    def test_set_build_version_keeps_binary_plist(self):
        make_ipa(self.ipa, app_plist_fmt=plistlib.FMT_BINARY)
        mirror.set_build_version(self.ipa, "316")
        with zipfile.ZipFile(self.ipa) as z:
            self.assertTrue(z.read(APP_PLIST).startswith(b"bplist"))

    def test_set_build_version_without_app_plist_raises(self):
        make_ipa(self.ipa, with_app_plist=False)
        with self.assertRaises(RuntimeError):
            mirror.set_build_version(self.ipa, "316")


class CheckIpaTest(unittest.TestCase):
    MANIFEST = {"bundleIdentifier": "com.example.test",
                "github": {"asset": r"Test-[0-9][0-9.]*-r[0-9]+\.ipa"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_ipa_accepts_matching_build(self):
        ipa = self.dir / "Test-1.0.0-r316.ipa"
        make_ipa(ipa)
        self.assertEqual(mirror.check_ipa(self.MANIFEST, ipa)["bundleIdentifier"], "com.example.test")

    def test_check_ipa_rejects_name_outside_manifest_pattern(self):
        ipa = self.dir / "Test-7.0-beta1-r316.ipa"
        make_ipa(ipa)
        with self.assertRaises(RuntimeError):
            mirror.check_ipa(self.MANIFEST, ipa)

    def test_check_ipa_rejects_other_bundle_id(self):
        ipa = self.dir / "Test-1.0.0-r316.ipa"
        make_ipa(ipa)
        with self.assertRaises(RuntimeError):
            mirror.check_ipa({**self.MANIFEST, "bundleIdentifier": "com.example.other"}, ipa)


class MirroredRunTest(unittest.TestCase):
    def test_mirrored_run_reads_highest_run_number(self):
        self.assertEqual(mirror.mirrored_run(["a-ios-r316.ipa", "a-ios-r317.ipa", "notes.txt"]), 317)

    def test_mirrored_run_without_build(self):
        self.assertIsNone(mirror.mirrored_run([]))
        self.assertIsNone(mirror.mirrored_run(None))


class AssetNameTest(unittest.TestCase):
    def test_asset_name_matches_manifest(self):
        name = mirror.asset_name("com.prisma-devs.prisma-1.0.0-ios.ipa", 316)
        self.assertEqual(name, "com.prisma-devs.prisma-1.0.0-ios-r316.ipa")
        manifest = json.loads((ROOT / "apps" / "prisma.json").read_text(encoding="utf-8"))
        self.assertTrue(re.fullmatch(manifest["github"]["asset"], name))


if __name__ == "__main__":
    unittest.main()
