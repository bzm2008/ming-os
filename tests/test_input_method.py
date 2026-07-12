import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


def install_fcitx5_source():
    return APPS.split("seed_ming_input_file() {", 1)[1].split(
        "# ======================== 应用商店", 1
    )[0]


def input_control_source():
    return APPS.split(
        "cat > /usr/local/sbin/ming-input-control << 'MINGINPUTCONTROL'", 1
    )[1].split("MINGINPUTCONTROL", 1)[0].lstrip()


class MingInputMethodContractTests(unittest.TestCase):
    def test_fcitx_install_includes_rime_and_seeds_a_non_package_theme(self):
        source = install_fcitx5_source()
        for package in ("fcitx5-rime", "librime-data", "rime-data-luna-pinyin"):
            self.assertIn(package, source)
        self.assertIn("/usr/local/share/fcitx5/themes/Ming-Candidate", source)
        self.assertIn("Theme=Ming-Candidate", source)
        self.assertIn("Font=Noto Sans CJK SC 15", source)
        self.assertIn("MenuFont=Noto Sans CJK SC 16", source)
        self.assertIn("Vertical Candidate List=True", source)
        self.assertIn("DefaultPageSize=7", source)

    def test_profile_keeps_pinyin_default_and_exposes_rime_without_overwriting_users(self):
        source = install_fcitx5_source()
        self.assertIn("DefaultIM=pinyin", source)
        self.assertIn("[Groups/0/Items/2]", source)
        self.assertIn("Name=rime", source)
        self.assertIn('skel_root="/etc/skel"', source)
        self.assertIn("seed_ming_input_file", source)
        self.assertNotIn(".local/share/fcitx5/rime", source)
        self.assertNotIn(".local/share/fcitx5/pinyin", source)

    def test_profile_uses_fcitx5s_real_profile_file_path(self):
        source = install_fcitx5_source()
        self.assertIn('"${skel_root}/.config/fcitx5/profile" <<', source)
        self.assertNotIn(".config/fcitx5/profile/default", source)

    def test_legacy_profile_directories_are_migrated_before_seed_files_are_written(self):
        source = install_fcitx5_source()

        self.assertIn("migrate_legacy_fcitx_profile_path() {", source)
        self.assertIn("fcitx_profile_is_ming() {", source)
        self.assertIn("backup_legacy_fcitx_file() {", source)
        self.assertIn("normalize_fcitx_profile() {", source)
        self.assertIn('local backup_path="${profile_path}.legacy-directory"', source)
        self.assertIn('local backup_path="${path}.ming-legacy-backup"', source)
        self.assertIn('find "${profile_path}" -mindepth 1 -print -quit', source)
        self.assertIn('mv "${profile_path}" "${backup_path}"', source)
        self.assertIn('rmdir "${profile_path}"', source)
        self.assertIn(
            'migrate_legacy_fcitx_profile_path "${skel_root}/.config/fcitx5/profile"', source)
        self.assertIn('migrate_legacy_fcitx_profile_path "${path}"', source)
        self.assertIn('normalize_fcitx_profile "${destination}" "${source}"', source)
        self.assertIn('if [[ ! -e "${destination}" ]]', source)
        self.assertNotIn(".local/share/fcitx5/rime", source)

    def test_factory_user_input_files_are_normalized_without_duplicate_daemons(self):
        source = install_fcitx5_source()

        self.assertIn("normalize_fcitx_classicui() {", source)
        self.assertIn("normalize_fcitx_xinputrc() {", source)
        self.assertIn('normalize_fcitx_classicui "${skel_root}/.config/fcitx5/conf/classicui.conf"', source)
        self.assertIn('normalize_fcitx_xinputrc "${destination}" "${source}"', source)
        self.assertIn("Theme=Ming-Candidate", source)
        self.assertIn("Font=Noto Sans CJK SC 15", source)
        self.assertIn("MenuFont=Noto Sans CJK SC 16", source)
        self.assertIn("Vertical Candidate List=True", source)
        self.assertIn("run_im fcitx5", source)
        self.assertIn("fcitx5 -d --replace", source)

    def test_autostart_is_the_only_daemon_start_path_and_environment_is_idempotent(self):
        source = install_fcitx5_source()
        autostart = source.split("FCITX5AUTO'\n", 1)[1].split("\nFCITX5AUTO", 1)[0]
        xinputrc = source.split("MINGXINPUTRC'\n", 1)[1].split("\nMINGXINPUTRC", 1)[0]
        self.assertIn("Exec=sh -c 'sleep 2; fcitx5 -d --replace'", autostart)
        self.assertNotIn("run_im fcitx5", xinputrc)
        self.assertNotIn("fcitx5 -d --replace", xinputrc)
        self.assertNotIn("cat >> /etc/environment", source)
        self.assertIn("sed -i", source)

    def test_build_gate_accepts_environment_only_xinputrc_and_checks_rime_assets(self):
        self.assertIn(
            'xinputrc = require_file("home/user/.xinputrc", "XMODIFIERS=@im=fcitx")',
            BUILD,
        )
        self.assertIn('"run_im fcitx5" in xinputrc', BUILD)
        self.assertIn('"fcitx5 -d --replace" in xinputrc', BUILD)
        for marker in [
            '"Name=rime"',
            '"usr/local/share/fcitx5/themes/Ming-Candidate/theme.conf"',
            '"usr/local/sbin/ming-input-control"',
            "fcitx5-rime",
            "rime-data-luna-pinyin",
        ]:
            self.assertIn(marker, BUILD)

    def run_control(self, *args, rime_schema=True, rime_addon=True, rime_fails=False):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)

            def write_posix(path, content):
                with path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)

            home = root / "home"
            profile = home / ".config/fcitx5/profile"
            classicui = home / ".config/fcitx5/conf/classicui.conf"
            profile.parent.mkdir(parents=True)
            classicui.parent.mkdir(parents=True)
            write_posix(
                profile,
                "DefaultIM=pinyin\n[Groups/0/Items/1]\nName=pinyin\n"
                "[Groups/0/Items/2]\nName=rime\n",
            )
            write_posix(
                classicui,
                "Theme=Ming-Candidate\nFont=Noto Sans CJK SC 15\n"
                "MenuFont=Noto Sans CJK SC 16\n",
            )
            schema = root / "luna_pinyin.schema.yaml"
            addon = root / "rime.so"
            if rime_schema:
                schema.touch()
            if rime_addon:
                addon.touch()

            state = root / "engine"
            write_posix(state, "pinyin")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            remote = bin_dir / "fcitx5-remote"
            write_posix(
                remote,
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  -s)\n"
                "    if [ \"$2\" = rime ] && [ \"${MING_RIME_FAIL:-0}\" = 1 ]; then exit 1; fi\n"
                "    printf %s \"$2\" > \"$MING_FCITX_STATE\";;\n"
                "  -n) cat \"$MING_FCITX_STATE\";;\n"
                "esac\n",
            )
            remote.chmod(0o755)
            pgrep = bin_dir / "pgrep"
            write_posix(pgrep, "#!/bin/sh\nexit 0\n")
            pgrep.chmod(0o755)
            fcitx5 = bin_dir / "fcitx5"
            write_posix(fcitx5, "#!/bin/sh\nexit 0\n")
            fcitx5.chmod(0o755)
            script = root / "ming-input-control"
            write_posix(script, input_control_source().replace("\r\n", "\n"))
            script.chmod(0o755)
            env = os.environ | {
                "HOME": str(home),
                "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "MING_INPUT_PROFILE": str(profile),
                "MING_INPUT_CLASSICUI": str(classicui),
                "MING_RIME_SCHEMA": str(schema),
                "MING_RIME_ADDON": str(addon),
                "MING_FCITX_STATE": str(state),
                "MING_RIME_FAIL": "1" if rime_fails else "0",
            }
            if os.name == "nt":
                if not shutil.which("wsl.exe"):
                    self.skipTest("Bash runtime is unavailable on this Windows host")

                def wsl_path(path):
                    windows_path = str(path).replace("\\", "/")
                    self.assertRegex(windows_path, r"^[A-Za-z]:/")
                    return "/mnt/%s/%s" % (windows_path[0].lower(), windows_path[3:])

                wsl_env = {
                    key: wsl_path(value)
                    for key, value in env.items()
                    if key
                    in {
                        "HOME",
                        "MING_INPUT_PROFILE",
                        "MING_INPUT_CLASSICUI",
                        "MING_RIME_SCHEMA",
                        "MING_RIME_ADDON",
                        "MING_FCITX_STATE",
                    }
                }
                wsl_env["PATH"] = wsl_path(bin_dir) + ":/usr/bin:/bin"
                wsl_env["MING_RIME_FAIL"] = env["MING_RIME_FAIL"]
                result = subprocess.run(
                    [
                        "wsl.exe",
                        "-d",
                        "Ubuntu",
                        "--",
                        "env",
                        *[f"{key}={value}" for key, value in wsl_env.items()],
                        "bash",
                        wsl_path(script),
                        *args,
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
            else:
                result = subprocess.run(
                    ["bash", str(script), *args],
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
            return result, state.read_text(encoding="utf-8")

    def test_status_json_reports_framework_profile_theme_and_rime_readiness(self):
        result, _ = self.run_control("status", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual("fcitx5", status["framework"]["name"])
        self.assertTrue(status["daemon"]["running"])
        self.assertEqual("pinyin", status["profile"]["default"])
        self.assertTrue(status["profile"]["rime_entry"])
        self.assertTrue(status["addon"]["rime"])
        self.assertEqual("Ming-Candidate", status["theme"])
        self.assertEqual("Noto Sans CJK SC 15/16", status["font"])
        self.assertEqual("pinyin", status["current_engine"])
        self.assertTrue(status["rime"]["available"])

    def test_set_engine_rejects_unknown_engine(self):
        result, engine = self.run_control("set-engine", "ibus")
        self.assertEqual(2, result.returncode)
        self.assertIn("<pinyin|rime>", result.stderr)
        self.assertEqual("pinyin", engine)

    def test_set_engine_falls_back_to_pinyin_when_rime_schema_is_unavailable(self):
        result, engine = self.run_control("set-engine", "rime", rime_schema=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("fell back to pinyin", result.stderr)
        self.assertEqual("pinyin", engine)

    def test_set_engine_falls_back_when_the_running_daemon_rejects_rime(self):
        result, engine = self.run_control("set-engine", "rime", rime_fails=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("fell back to pinyin", result.stderr)
        self.assertEqual("pinyin", engine)

    def test_set_engine_uses_rime_only_after_the_daemon_accepts_it(self):
        result, engine = self.run_control("set-engine", "rime")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("rime", engine)


if __name__ == "__main__":
    unittest.main()
