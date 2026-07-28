import ast
import pathlib
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")


def function_source(name):
    tree = ast.parse(SETTINGS)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_source_segment(SETTINGS, node)


def method_source(class_name, name):
    tree = ast.parse(SETTINGS)
    cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == class_name)
    node = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == name)
    return ast.get_source_segment(SETTINGS, node)


class PrivilegeBoundaryContracts(unittest.TestCase):
    def test_generated_sudoers_never_grants_global_passwordless_root(self):
        self.assertNotRegex(BASE, r"NOPASSWD\s*:\s*ALL")
        self.assertIn("ming-account-control", BASE)
        self.assertIn("org.ming.account.control", BASE)
        self.assertIn("auth_admin_keep", BASE)

    def test_automount_uses_lsblk_json_without_eval(self):
        helper = BASE.split("cat > /usr/local/bin/ming-volume-automount << 'VOLUMEAUTOMOUNT'", 1)[1].split(
            "VOLUMEAUTOMOUNT", 1
        )[0]
        self.assertIn("lsblk --json", helper)
        self.assertIn("json.load", helper)
        self.assertNotIn("lsblk -P", helper)
        self.assertNotIn("eval ", helper)
        self.assertIn("read -r -d ''", helper)


class PasswordHelperContracts(unittest.TestCase):
    def test_password_validation_rejects_control_characters_and_oversized_values(self):
        namespace = {
            "MAX_ACCOUNT_PASSWORD_BYTES": 1024,
        }
        exec(textwrap.dedent(function_source("validate_account_password")), namespace)
        validate = namespace["validate_account_password"]

        self.assertEqual((True, ""), validate("normal password"))
        for value in ("line\nbreak", "line\rbreak", "nul\x00byte", "colon:value", "x" * 1025):
            self.assertFalse(validate(value)[0], value)

    def test_settings_uses_fixed_password_helper_and_stdin_not_shell(self):
        namespace = {}
        exec(textwrap.dedent(function_source("account_password_command")), namespace)
        command = namespace["account_password_command"]("user")
        clear = namespace["account_password_command"]("user", clear=True)
        handler = method_source("MingSettings", "on_set_password")

        self.assertEqual(
            ["pkexec", "/usr/local/sbin/ming-account-control", "set-password", "--user", "user"],
            command,
        )
        self.assertEqual(
            ["pkexec", "/usr/local/sbin/ming-account-control", "clear-password", "--user", "user"],
            clear,
        )
        self.assertIn("run_capture_stdin_async", handler)
        self.assertNotIn("bash", handler)
        self.assertNotIn("chpasswd", handler)

    def test_settings_has_no_pkexec_shell_escape_hatch(self):
        self.assertNotIn('"pkexec", "bash", "-c"', SETTINGS)
        self.assertIn("ming-timeshift-restore", SETTINGS)


if __name__ == "__main__":
    unittest.main()
