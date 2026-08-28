import hashlib
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
CATALOG = ROOT / "assets" / "ming-store-catalog" / "spark-public.json"
TRUSTED_KEYS = ROOT / "assets" / "trusted-keys"


class TrustedKeyAssetContracts(unittest.TestCase):
    CASES = (
        (
            "spark-store.asc",
            "EC9613DCC9501D1E1C2E38E783B69C69F9158A4D26B876285D0A09FB6DC218CD",
            "49DFC2D391822E0E50AC9D79B94FF2B5A4EBEBFF9939CEA056733FEF01B9BAA4",
            "9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
            "/etc/ming-os/store/spark-archive-keyring.gpg",
        ),
        (
            "waydroid.asc",
            "BB31BE14F881A2C96E4AC036F929B60DB91A6D3B59F2025F770EFF9CBD8619B0",
            "71FE05D735C812E15FE229BF10106B02B62561BE8AA5280D63A58E25A5C0C5E2",
            "7CE0331F71E0A238BB1002D70E406D181DCEE19C",
            "/usr/share/keyrings/ming-waydroid.gpg",
        ),
    )

    def test_reviewable_ascii_key_assets_have_exact_pinned_hashes(self):
        for name, ascii_sha256, _binary_sha256, _fingerprint, _destination in self.CASES:
            with self.subTest(name=name):
                path = TRUSTED_KEYS / name
                self.assertTrue(path.is_file(), name)
                self.assertFalse(path.is_symlink(), name)
                payload = path.read_bytes()
                self.assertTrue(payload.startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----"))
                self.assertEqual(ascii_sha256, hashlib.sha256(payload).hexdigest().upper())

    def test_desktop_module_deploys_only_verified_dearmored_keyrings(self):
        self.assertIn('trusted_key_dir="${asset_dir}/trusted-keys"', DESKTOP)
        self.assertIn("deploy_trusted_openpgp_key()", DESKTOP)
        self.assertIn("gpg_dearmor=(", DESKTOP)
        self.assertIn('"${gpg_dearmor[@]}"', DESKTOP)
        self.assertIn("--with-colons", DESKTOP)
        self.assertIn("--fingerprint", DESKTOP)
        for name, ascii_sha256, binary_sha256, fingerprint, destination in self.CASES:
            with self.subTest(name=name):
                for marker in (name, ascii_sha256, binary_sha256, fingerprint, destination):
                    self.assertIn(marker, DESKTOP)

    def test_spark_install_is_enabled_only_with_the_provisioned_keyring(self):
        config = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertTrue(config["installation_enabled"])
        self.assertNotIn("installation_disabled_reason", config)
        self.assertEqual(
            "/etc/ming-os/store/spark-archive-keyring.gpg",
            config["keyring"],
        )
        self.assertEqual(self.CASES[0][3], config["key_fingerprint"])

    def test_rootfs_gate_checks_keyring_bytes_fingerprints_and_config(self):
        self.assertIn("verify_openpgp_keyring", BUILD)
        self.assertIn("hashlib.sha256", BUILD)
        self.assertIn("subprocess.run", BUILD)
        self.assertIn('"--with-colons"', BUILD)
        self.assertIn('"--fingerprint"', BUILD)
        for _name, _ascii_sha256, binary_sha256, fingerprint, destination in self.CASES:
            with self.subTest(destination=destination):
                self.assertIn(destination.lstrip("/"), BUILD)
                self.assertIn(binary_sha256, BUILD)
                self.assertIn(fingerprint, BUILD)
        self.assertIn("Spark installation cannot be enabled without a valid trusted archive keyring", BUILD)

    def test_fingerprint_checks_bind_to_primary_pub_record(self):
        """Do not depend on gpg emitting a primary fpr before subkey fprs."""
        self.assertIn("primary_fingerprint", DESKTOP)
        self.assertIn("$1 == \"pub\"", DESKTOP)
        self.assertIn("primary_fingerprints", BUILD)
        self.assertIn('previous_type == "pub"', BUILD)


if __name__ == "__main__":
    unittest.main()
