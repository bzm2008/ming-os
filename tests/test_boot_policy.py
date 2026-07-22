import importlib.util
import pathlib
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY = ROOT / "assets" / "ming-boot-policy.py"
BASE = ROOT / "modules" / "01_base.sh"


def load_policy():
    spec = importlib.util.spec_from_file_location("ming_boot_policy_test", POLICY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootPolicyContracts(unittest.TestCase):
    def test_installed_kernel_entries_are_quiet_identified_and_traceable(self):
        source = BASE.read_text(encoding="utf-8")
        grub = source.split(
            "cat > \"${target}/etc/grub.d/09_ming_os\" <<'TARGETGRUBENTRY'\n", 1
        )[1].split("\nTARGETGRUBENTRY\n", 1)[0]
        self.assertIn("--id 'ming-normal'", grub)
        self.assertIn("ming.entry=ming-normal", grub)
        for marker in (
            "quiet splash",
            "loglevel=0",
            "systemd.show_status=false",
            "rd.systemd.show_status=false",
            "rd.udev.log_level=0",
        ):
            self.assertIn(marker, grub)

    def test_first_boot_menu_and_health_confirmed_hidden_policy_are_deployed(self):
        source = BASE.read_text(encoding="utf-8")
        unit = (ROOT / "assets/systemd/ming-boot-policy.service").read_text(encoding="utf-8")
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        for marker in (
            "GRUB_DEFAULT=saved",
            "GRUB_TIMEOUT_STYLE=menu",
            "GRUB_TIMEOUT=8",
            "ming-boot-policy",
            "ming-boot-policy.service",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("ConditionPathExists", unit)
        self.assertIn("confirm --wait 90", unit)
        self.assertIn("graphical.target.wants/ming-boot-policy.service", build)

    def test_session_receipt_is_bound_to_the_current_kernel_boot(self):
        desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn('"boot_id"', desktop)
        self.assertIn("/proc/sys/kernel/random/boot_id", desktop)
        self.assertIn("E_DESKTOP_STALE", policy)

    def test_boot_policy_only_hides_menu_after_healthy_desktop_receipt(self):
        self.assertTrue(POLICY.is_file())
        policy = load_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            status = policy.status(root=root, cmdline="ming.entry=ming-normal")
            self.assertTrue(status["first_boot"])
            self.assertEqual("menu", status["menu_mode"])
            with self.assertRaises(policy.BootPolicyError):
                policy.confirm(root=root, cmdline="ming.entry=ming-normal")

    def test_boot_policy_status_reports_saved_next_and_recordfail(self):
        self.assertTrue(POLICY.is_file())
        policy = load_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "boot/grub").mkdir(parents=True)
            (root / "boot/grub/grubenv").write_text(
                "saved_entry=ming-normal\nnext_entry=ming-slot-a\nrecordfail=1\n",
                encoding="utf-8",
            )
            value = policy.status(
                root=root,
                cmdline="ming.entry=ming-normal",
                grubenv_reader=lambda _path: {
                    "saved_entry": "ming-normal",
                    "next_entry": "ming-slot-a",
                    "recordfail": "1",
                },
            )
            self.assertEqual("ming-normal", value["saved_entry"])
            self.assertEqual("ming-slot-a", value["next_entry"])
            self.assertTrue(value["recordfail"])

    def test_healthy_confirmation_remembers_entry_without_touching_ota_next_entry(self):
        policy = load_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "proc/sys/kernel/random").mkdir(parents=True)
            (root / "proc/sys/kernel/random/boot_id").write_text("boot-123\n", encoding="utf-8")
            receipt = root / "home/user/.cache/ming-os/session-startup.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                '{"healthy":true,"phase":"startup","boot_id":"boot-123"}\n',
                encoding="utf-8",
            )
            grubenv = root / "boot/grub/grubenv"
            grubenv.parent.mkdir(parents=True)
            grubenv.write_text("placeholder\n", encoding="utf-8")
            values = {
                "saved_entry": "ming-legacy",
                "next_entry": "ming-slot-a",
                "recordfail": "0",
            }
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                if "grub-set-default" in command:
                    values["saved_entry"] = command[-1]
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")

            result = policy.confirm(
                root=root,
                cmdline="quiet ming.entry=ming-normal",
                grubenv_reader=lambda _path: dict(values),
                runner=runner,
            )

            self.assertEqual("ming-normal", result["saved_entry"])
            self.assertEqual("ming-slot-a", result["next_entry"])
            self.assertEqual("hidden", result["menu_mode"])
            self.assertEqual(1, sum("update-grub" in command for command in commands))

            commands.clear()
            policy.confirm(
                root=root,
                cmdline="quiet ming.entry=ming-normal",
                grubenv_reader=lambda _path: dict(values),
                runner=runner,
            )
            self.assertEqual([], commands)


if __name__ == "__main__":
    unittest.main()
