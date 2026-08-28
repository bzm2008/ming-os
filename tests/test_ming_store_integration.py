import pathlib
import json
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


class MingStoreDeploymentContracts(unittest.TestCase):
    def test_store_assets_and_desktop_entry_are_deployed(self):
        for name in (
            "ming-store.py",
            "ming-store-core.py",
            "ming-store-control.py",
        ):
            self.assertIn(name, DESKTOP)
            self.assertTrue((ROOT / "assets" / name).is_file(), name)
        self.assertIn("/usr/share/applications/ming-store.desktop", DESKTOP)
        self.assertIn("Exec=/usr/local/bin/ming-store", DESKTOP)
        self.assertIn("Icon=ming-store", DESKTOP)

    def test_store_authorization_accepts_only_action_and_request_id(self):
        authorized = DESKTOP.split(
            "cat > /usr/local/bin/ming-authorized-action", 1
        )[1].split("MINGAUTHORIZE", 2)[1]
        self.assertIn("store)", authorized)
        self.assertIn("/usr/local/sbin/ming-store-control", authorized)
        self.assertRegex(authorized, r"\^\[a-f0-9\]\{32\}\$")
        self.assertNotIn("http://", authorized)
        self.assertNotIn("https://", authorized)
        self.assertNotIn('eval ', authorized)
        self.assertNotIn('sh -c', authorized)

    def test_store_polkit_policy_has_no_remote_or_any_user_fallback(self):
        self.assertIn("org.mingos.store.manage", DESKTOP)
        policy = DESKTOP.split("org.mingos.store.manage", 1)[1]
        policy = policy[: policy.index("</policyconfig>")]
        self.assertIn("<allow_any>no</allow_any>", policy)
        self.assertIn("<allow_inactive>no</allow_inactive>", policy)
        self.assertNotIn("<allow_any>yes</allow_any>", policy)

    def test_local_deb_opens_the_store_instead_of_direct_install(self):
        self.assertIn("MimeType=application/vnd.debian.binary-package;", DESKTOP)
        self.assertIn("Exec=/usr/local/bin/ming-store --local-deb %f", DESKTOP)
        self.assertNotIn("Exec=ming-package-install-gui %f", DESKTOP)

    def test_default_desktop_dock_and_favorites_use_ming_store(self):
        for source in (DESKTOP, FINALIZE, BUILD):
            self.assertIn("ming-store.desktop", source)
        self.assertIn("ming-store.dockitem", DESKTOP)
        self.assertNotIn("spark-store.dockitem", DESKTOP)
        self.assertNotIn("spark-store.desktop", DESKTOP)
        self.assertIn("spark-store.desktop", FINALIZE)
        self.assertIn("Spark/APM residue", BUILD)

    def test_spark_public_provider_config_is_attributed_and_enabled_with_pinned_key(self):
        config_path = ROOT / "assets" / "ming-store-catalog" / "spark-public.json"
        self.assertTrue(config_path.is_file())
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual("ming.store.spark-public.v1", config["schema"])
        self.assertEqual("spark-public", config["provider"])
        self.assertEqual("GPL-3.0-or-later", config["upstream_license"])
        self.assertEqual(
            "9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
            config["key_fingerprint"],
        )
        self.assertTrue(config["installation_enabled"])
        self.assertNotIn("installation_disabled_reason", config)
        self.assertIn("spark-public.json", DESKTOP)
        self.assertIn("spark-public.json", BUILD)
        self.assertIn("spark-archive-keyring.gpg", BUILD)
        attribution = ROOT / "docs" / "ming-store-spark-public.md"
        self.assertTrue(attribution.is_file())
        self.assertIn("GPL-3.0", attribution.read_text(encoding="utf-8"))

    def test_spark_public_key_fingerprint_is_exactly_the_40_hex_digit_identity(self):
        config_path = ROOT / "assets" / "ming-store-catalog" / "spark-public.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertRegex(config["key_fingerprint"], r"^[0-9A-F]{40}$")
        self.assertEqual(
            "9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
            config["key_fingerprint"],
        )
        self.assertIn("9D9AA859F75024B1A1ECE16E0E41D354A29A440C", BUILD)

    def test_build_gate_requires_exact_spark_key_identity_when_install_is_enabled(self):
        config_path = ROOT / "assets" / "ming-store-catalog" / "spark-public.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(config["installation_enabled"])
        self.assertIn("installation_enabled", BUILD)
        self.assertIn("verify_openpgp_keyring", BUILD)
        self.assertIn("49DFC2D391822E0E50AC9D79B94FF2B5A4EBEBFF9939CEA056733FEF01B9BAA4", BUILD)
        self.assertNotIn("dcs-repo.gpg-key.asc", BUILD)


class SparkRetirementContracts(unittest.TestCase):
    def test_source_no_longer_deploys_spark_or_apm_runtime(self):
        combined = "\n".join((APPS, DESKTOP))
        forbidden = (
            "install_app_store",
            "ming-spark-store",
            "ming-spark-package-control",
            "ming-spark-backend-status",
            "ming-spark-aria2c",
            "spark-update-notifier",
            "store.spark-app",
            "aptss",
            "ssinstall",
        )
        for marker in forbidden:
            self.assertNotIn(marker, combined, marker)

    def test_upgrade_cleanup_preserves_apps_and_never_autoremoves(self):
        self.assertIn("remove --no-auto-remove", FINALIZE)
        self.assertIn("spark-store", FINALIZE)
        self.assertIn("apm", FINALIZE)
        cleanup = FINALIZE[FINALIZE.index("remove --no-auto-remove"):]
        self.assertNotRegex(cleanup, r"\bapt(?:-get)?\s+autoremove\b")
        self.assertNotRegex(cleanup, r"\brm\s+(?:-[^\s]+\s+)*/opt/apps\b")
        self.assertNotRegex(cleanup, r"\bfind\s+/opt/apps\b")
        self.assertNotRegex(
            cleanup,
            r"\brm\s+(?:-[^\s]+\s+).*\.local/share/applications\b",
        )

    def test_build_gate_rejects_spark_residue(self):
        for marker in (
            "Spark/APM residue",
            "ming-store-control",
            "ming.store.catalog.v1",
            "org.mingos.store.manage",
        ):
            self.assertIn(marker, BUILD)

    def test_build_gate_reads_both_store_schemas_from_the_core_asset(self):
        self.assertIn(
            '"usr/local/lib/ming-os/ming-store-core.py", "ming.store.catalog.v1"',
            BUILD,
        )
        self.assertIn(
            '"usr/local/lib/ming-os/ming-store-core.py", "ming.store.transaction.v1"',
            BUILD,
        )
        self.assertNotIn(
            '"usr/local/sbin/ming-store-control", "ming.store.transaction.v1"',
            BUILD,
        )


class ManagedAptSourceContracts(unittest.TestCase):
    def test_selector_uses_one_marked_managed_debian_source(self):
        selector = BASE.split(
            "cat > /usr/local/sbin/ming-apt-source-select", 1
        )[1].split("MINGAPTSOURCE", 2)[1]
        self.assertIn("Managed by Ming OS", selector)
        self.assertIn("backup", selector)
        self.assertIn("apt-get update", selector)
        self.assertIn("rollback", selector)
        self.assertIn("InRelease", selector)
        self.assertIn("90-ming-mirror.list", selector)
        self.assertIn("sources.list", selector)
        self.assertIn("deb.debian.org", selector)


if __name__ == "__main__":
    unittest.main()
