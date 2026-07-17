import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE = ROOT / "tools" / "ming-release-vault-check.service"
TIMER = ROOT / "tools" / "ming-release-vault-check.timer"
INSTALLER = ROOT / "tools" / "ming-release-vault-install.sh"


class ReleaseVaultSystemdTests(unittest.TestCase):
    def test_monthly_service_is_read_only_and_bounded(self):
        text = SERVICE.read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", text)
        self.assertIn("User=ming-release-vault", text)
        self.assertIn("NoNewPrivileges=yes", text)
        self.assertIn("PrivateTmp=yes", text)
        self.assertIn("ProtectSystem=strict", text)
        self.assertIn("ReadOnlyPaths=/etc/ming-os/release-vault.json", text)
        self.assertIn("TimeoutStartSec=30s", text)
        self.assertIn("verify-nas", text)
        self.assertIn("release-vault-check.jsonl", text)
        self.assertNotIn("gpg --decrypt", text)

    def test_timer_is_monthly_persistent_and_bounded(self):
        text = TIMER.read_text(encoding="utf-8")
        self.assertIn("OnCalendar=monthly", text)
        self.assertIn("Persistent=true", text)
        self.assertIn("RandomizedDelaySec=1h", text)

    def test_installer_is_fail_closed_and_does_not_handle_private_material(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("/etc/ming-os/release-vault.json", text)
        self.assertIn("systemctl daemon-reload", text)
        self.assertIn("systemctl enable", text)
        self.assertIn("MING_RELEASE_VAULT", text)
        self.assertNotIn("gpg --decrypt", text)
        self.assertNotIn("age -d", text)
        self.assertNotIn("ssh-keygen", text)
        self.assertNotIn("systemctl start", text)


if __name__ == "__main__":
    unittest.main()
