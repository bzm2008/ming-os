import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
RESUME = (ROOT / "resume_build.sh").read_text(encoding="utf-8")


class BuildScriptContractTests(unittest.TestCase):
    @staticmethod
    def _shell_function(name, next_name):
        start = BUILD.index(f"{name}() {{")
        end = BUILD.index(f"{next_name}() {{", start)
        return BUILD[start:end]

    def _bash_executable(self):
        bash = None
        if os.name == "nt":
            candidate = pathlib.Path(
                os.environ.get("ProgramFiles", r"C:\\Program Files")
            ) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                bash = str(candidate)
        if bash is None:
            bash = shutil.which("bash") or shutil.which("bash.exe")
        self.assertIsNotNone(bash, "these shell contract tests require Bash")
        return bash

    def _run_bash(self, script, *, cwd):
        return subprocess.run(
            [self._bash_executable(), "-s"],
            cwd=str(cwd),
            check=False,
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _bash_path(self, path):
        result = self._run_bash("pwd -P", cwd=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _make_directory_symlink(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.fail(f"shell regression test requires a directory symlink: {error}")

    def _clean_chroot_appstream_payload(self):
        clean = BUILD.split("clean_chroot() {", 1)[1].split(
            "# ======================== 生成 initramfs ========================", 1
        )[0]
        match = re.search(
            r'''chroot_exec bash -c '\n(?P<payload>.*?)\n    '\n    chroot_exec bash -c "rm -rf /var/lib/apt/lists/\*"''',
            clean,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not extract the AppStream cleanup payload")
        return match.group("payload")

    def _source_symlink_guard(self):
        """Extract the guard with its path-component helper for real execution."""
        return "\n".join(
            (
                self._shell_function(
                    "assert_path_has_no_symlink_components",
                    "assert_source_tree_has_no_symlinks",
                ),
                self._shell_function(
                    "assert_source_tree_has_no_symlinks", "build_inputs_sha256"
                ),
            )
        )

    def _retired_residue_namespace(self, root):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        python_gate = validator.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        path_helpers = python_gate[
            python_gate.index("def _rootfs_path"):python_gate.index("def require_file")
        ]
        residue_gate = python_gate[
            python_gate.index("RETIRED_PACKAGE_BASES"):python_gate.index(
                "\nfor private_key in ["
            )
        ]
        namespace = {
            "Path": pathlib.Path,
            "errors": [],
            "os": os,
            "re": re,
            "root": root,
            "stat": __import__("stat"),
        }
        exec(path_helpers + residue_gate, namespace)
        return namespace

    def test_main_build_exposes_staged_resume_interface(self):
        for marker in (
            "host-preflight",
            "debootstrap",
            "prepare-chroot",
            "modules",
            "initramfs",
            "clean-rootfs",
            "squashfs",
            "boot-assets",
            "iso",
            "publish-artifacts",
            "--fresh",
            "--resume",
            "--from",
            "--profile",
            "MING_BUILD_PROFILE",
            "MING_BUILD_FROM",
            "scripts/ming_build_state.py",
            "last-failure.json",
        ):
            self.assertIn(marker, BUILD)

    def test_release_and_fast_profiles_have_explicit_compression_contracts(self):
        for marker in (
            'PROFILE_RELEASE="release"',
            'PROFILE_FAST_TEST="fast-test"',
            'PROFILE_LEGACY_LOWRAM="legacy-lowram"',
            'PROFILE_COMPAT_HWE="compat-hwe"',
            'release:xz',
            'fast-test:zstd',
            'legacy-lowram:zstd',
            'compat-hwe:xz',
            "compression",
            "profile",
        ):
            self.assertIn(marker, BUILD)

    def test_resume_never_sources_or_reimplements_the_main_script(self):
        self.assertNotIn('eval "$(grep', RESUME)
        self.assertNotIn("local modules=(", RESUME)
        self.assertIn('exec "${SCRIPT_DIR}/build_onion_os.sh" --resume', RESUME)
        self.assertIn('MING_BUILD_FROM', RESUME)

    def test_chroot_reuse_is_state_checked_and_fresh_is_explicit(self):
        for marker in (
            "MING_REUSE_CHROOT",
            "MING_BUILD_FRESH",
            "state_is_complete",
            "input_hash",
            "invalidate_from",
            "run_debootstrap",
            "--fresh",
        ):
            self.assertIn(marker, BUILD)
        self.assertNotIn(
            'if [[ "${MING_REUSE_CHROOT}" == "1" && -f "${CHROOT_DIR}/etc/ming-version" ]]',
            BUILD,
        )

    def test_apt_cache_and_chroot_cache_are_explicitly_reusable(self):
        for marker in (
            "APT_ARCHIVES_CACHE",
            "CHROOT_CACHE_DIR",
            "bind",
            "apt-archives",
            "cache-manifest.json",
            "cache invalid",
            "Acquire::Retries=5",
        ):
            self.assertIn(marker, BUILD)

    def test_profile_checkpoints_are_isolated(self):
        self.assertIn("BUILD_STATE_ROOT", BUILD)
        self.assertIn("${BUILD_STATE_ROOT}/${MING_BUILD_PROFILE}", BUILD)

    def test_apt_cache_manifest_is_written_atomically(self):
        self.assertIn("cache-manifest.json.partial", BUILD)

    def test_build_inputs_ignore_generated_python_cache_files(self):
        """Only source assets participate in the reproducible input hash."""
        inputs = BUILD.split("build_inputs_sha256() {", 1)[1].split(
            "file_sha256_or_missing() {", 1
        )[0]
        self.assertIn("-name __pycache__ -o -name .pytest_cache", inputs)
        self.assertIn("-prune -o", inputs)
        self.assertIn("! -name '*.pyc'", inputs)
        self.assertIn("! -name '*.pyo'", inputs)

    def test_asset_copy_excludes_generated_python_cache_files(self):
        prepare = BUILD.split("prepare_chroot_scripts() {", 1)[1].split(
            "mount_chroot() {", 1
        )[0]
        self.assertIn("rsync", prepare)
        self.assertIn("--exclude='__pycache__/'", prepare)
        self.assertIn("--exclude='*.pyc'", prepare)

    def test_build_rejects_symlinked_source_inputs_before_rsync(self):
        """A source link must not smuggle host files into the rootfs."""
        prepare = BUILD.split("prepare_chroot_scripts() {", 1)[1].split(
            "mount_chroot() {", 1
        )[0]
        self.assertIn("assert_source_tree_has_no_symlinks", BUILD)
        self.assertIn("find -P", BUILD)
        self.assertIn("-type l", BUILD)
        self.assertLess(
            prepare.index("assert_source_tree_has_no_symlinks"),
            prepare.index("rsync"),
        )

    def test_source_symlink_guard_has_physical_boundary_and_propagates_find_errors(self):
        """A failed or parent-symlinked scan must stop the build before copying."""
        guard = BUILD.split("assert_source_tree_has_no_symlinks() {", 1)[1].split(
            "build_inputs_sha256() {", 1
        )[0]
        self.assertIn(
            'readonly SCRIPT_DIR_LEXICAL="$(cd -L -- "$(dirname "${BASH_SOURCE[0]}")" && pwd -L)"',
            BUILD,
        )
        self.assertIn(
            'readonly SCRIPT_DIR="$(cd -P -- "${SCRIPT_DIR_LEXICAL}" && pwd -P)"',
            BUILD,
        )
        self.assertIn(
            'source_root_physical="$(cd -P -- "${source_root}" && pwd -P)"',
            guard,
        )
        self.assertIn(
            '"${script_root_physical}"|"${script_root_physical}"/*',
            guard,
        )
        self.assertIn(
            'if ! link="$(find -P "${source_root}" -type l -print -quit)"; then',
            guard,
        )
        self.assertNotIn("done < <(find -P", guard)

    def test_source_symlink_guard_rejects_a_parent_link_in_the_entry_path(self):
        """The physical path cannot hide a linked parent used to start the build."""
        guard = self._source_symlink_guard()
        with tempfile.TemporaryDirectory(prefix="ming-source-guard-") as temporary:
            root = pathlib.Path(temporary)
            physical_script_dir = root / "physical" / "repo"
            for directory in (
                physical_script_dir / "modules",
                physical_script_dir / "config",
                physical_script_dir / "assets",
                physical_script_dir / "scripts",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            lexical_parent = root / "lexical-parent"
            self._make_directory_symlink(lexical_parent, root / "physical")
            root_path = self._bash_path(root)
            script_dir = f"{root_path}/physical/repo"
            lexical_script_dir = f"{root_path}/lexical-parent/repo"
            script = "\n".join(
                (
                    "set -u -o pipefail",
                    'log_error() { printf "%s\\n" "$*" >&2; }',
                    f"MODULES_DIR={shlex.quote(script_dir + '/modules')}",
                    f"CONFIG_DIR={shlex.quote(script_dir + '/config')}",
                    f"SCRIPT_DIR={shlex.quote(script_dir)}",
                    f"SCRIPT_DIR_LEXICAL={shlex.quote(lexical_script_dir)}",
                    f"BUILD_STATE_HELPER={shlex.quote(script_dir + '/scripts/ming_build_state.py')}",
                    guard,
                    "assert_source_tree_has_no_symlinks",
                )
            )
            result = self._run_bash(script, cwd=root)
        self.assertNotEqual(
            result.returncode,
            0,
            "a build started through a parent symlink must be rejected",
        )

    def test_build_input_hash_rejects_a_failed_source_find(self):
        """A failed input enumeration must not produce a reusable build hash."""
        guard = self._source_symlink_guard()
        input_hash = self._shell_function("build_inputs_sha256", "file_sha256_or_missing")
        with tempfile.TemporaryDirectory(prefix="ming-input-hash-") as temporary:
            root = pathlib.Path(temporary)
            script_root = root / "repo"
            for directory in (
                script_root / "modules",
                script_root / "config",
                script_root / "assets",
                script_root / "scripts",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            for build_file in (
                script_root / "build_onion_os.sh",
                script_root / "resume_build.sh",
                script_root / "scripts" / "ming_build_state.py",
            ):
                build_file.write_text("test input\n", encoding="utf-8")
            root_path = self._bash_path(root)
            script_dir = f"{root_path}/repo"
            script = "\n".join(
                (
                    "set -u -o pipefail",
                    'log_error() { printf "%s\\n" "$*" >&2; }',
                    f"MODULES_DIR={shlex.quote(script_dir + '/modules')}",
                    f"CONFIG_DIR={shlex.quote(script_dir + '/config')}",
                    f"SCRIPT_DIR={shlex.quote(script_dir)}",
                    f"SCRIPT_DIR_LEXICAL={shlex.quote(script_dir)}",
                    f"BUILD_STATE_HELPER={shlex.quote(script_dir + '/scripts/ming_build_state.py')}",
                    "MING_OS_BUILD_SUFFIX=rc4",
                    "ISO_VOLUME_ID=MING_TEST",
                    "MING_SKIP_XIAHAI=0",
                    "MING_BUILD_PROFILE=test",
                    "MING_OTA_RELEASE_PUBLIC_KEY_SOURCE=",
                    guard,
                    input_hash,
                    "source_tree_sha256() { printf source-tree; }",
                    "file_sha256_or_missing() { printf missing; }",
                    "find() {",
                    '    if [[ "${1:-}" == "-P" ]]; then',
                    '        command find "$@"',
                    "        return $?",
                    "    fi",
                    "    return 71",
                    "}",
                    "build_inputs_sha256 >/dev/null",
                )
            )
            result = self._run_bash(script, cwd=root)
        self.assertNotEqual(
            result.returncode,
            0,
            "a failed find must make build_inputs_sha256 fail",
        )

    def test_build_input_hash_cleans_temp_list_when_source_identity_fails(self):
        """A source identity failure must not leak a reusable input-list file."""
        guard = self._source_symlink_guard()
        input_hash = self._shell_function("build_inputs_sha256", "file_sha256_or_missing")
        with tempfile.TemporaryDirectory(prefix="ming-input-hash-failure-") as temporary:
            root = pathlib.Path(temporary)
            script_root = root / "repo"
            for directory in (
                script_root / "modules",
                script_root / "config",
                script_root / "assets",
                script_root / "scripts",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            for build_file in (
                script_root / "build_onion_os.sh",
                script_root / "resume_build.sh",
                script_root / "scripts" / "ming_build_state.py",
            ):
                build_file.write_text("test input\n", encoding="utf-8")
            root_path = self._bash_path(root)
            script_dir = f"{root_path}/repo"
            script = "\n".join(
                (
                    "set -u -o pipefail",
                    f"export TMPDIR={shlex.quote(root_path)}",
                    'log_error() { printf "%s\\n" "$*" >&2; }',
                    f"MODULES_DIR={shlex.quote(script_dir + '/modules')}",
                    f"CONFIG_DIR={shlex.quote(script_dir + '/config')}",
                    f"SCRIPT_DIR={shlex.quote(script_dir)}",
                    f"SCRIPT_DIR_LEXICAL={shlex.quote(script_dir)}",
                    f"BUILD_STATE_HELPER={shlex.quote(script_dir + '/scripts/ming_build_state.py')}",
                    "MING_OS_BUILD_SUFFIX=rc4",
                    "ISO_VOLUME_ID=MING_TEST",
                    "MING_SKIP_XIAHAI=0",
                    "MING_BUILD_PROFILE=test",
                    "MING_OTA_RELEASE_PUBLIC_KEY_SOURCE=",
                    guard,
                    input_hash,
                    "source_tree_sha256() { return 71; }",
                    "file_sha256_or_missing() { printf missing; }",
                    "build_inputs_sha256 >/dev/null",
                )
            )
            result = self._run_bash(script, cwd=root)
            leaked = list(root.glob("ming-build-inputs.*"))
        self.assertNotEqual(
            result.returncode,
            0,
            "a source identity failure must make build_inputs_sha256 fail",
        )
        self.assertEqual(leaked, [], "failed hashing leaked a temporary input list")

    def _run_input_hash_failure(self, failure_mode):
        """Run the real hash function with one injected failing operation."""
        guard_start = BUILD.find("assert_path_has_no_symlink_components() {")
        if guard_start >= 0:
            guard_end = BUILD.index("build_inputs_sha256() {", guard_start)
            guard_functions = BUILD[guard_start:guard_end]
        else:
            # The pre-fix script had no source guard; keep the red test focused
            # on hash error propagation rather than failing during extraction.
            guard_functions = "assert_source_tree_has_no_symlinks() { return 0; }\n"
            guard_end = BUILD.index("build_inputs_sha256() {")
        input_start = guard_end
        input_end = BUILD.index("file_sha256_or_missing() {", input_start)
        source_start = BUILD.index("source_tree_sha256() {")
        source_end = (
            BUILD.index("assert_path_has_no_symlink_components() {", source_start)
            if guard_start >= 0
            else BUILD.index("build_inputs_sha256() {", source_start)
        )
        file_start = input_end
        file_end = BUILD.index("tools_fingerprint() {", file_start)
        shell_functions = "\n".join(
            (
                BUILD[source_start:source_end],
                guard_functions,
                BUILD[input_start:input_end],
                BUILD[file_start:file_end],
            )
        )
        with tempfile.TemporaryDirectory(prefix="ming-input-hash-failure-") as temporary:
            root = pathlib.Path(temporary)
            script_root = root / "repo"
            for directory in ("modules", "config", "assets", "scripts"):
                (script_root / directory).mkdir(parents=True)
            (script_root / "modules" / "input.txt").write_text(
                "input\n", encoding="utf-8"
            )
            for relative in (
                "build_onion_os.sh",
                "resume_build.sh",
                "scripts/ming_build_state.py",
            ):
                (script_root / relative).write_text("build input\n", encoding="utf-8")
            xiahai = root / "xiahai.deb"
            xiahai.write_bytes(b"xiahai")
            (root / "tmp").mkdir()
            root_path = self._bash_path(root)
            script_path = self._bash_path(script_root)
            tmp_path = self._bash_path(root / "tmp")
            xiahai_path = f"{root_path}/xiahai.deb"
            input_path = f"{script_path}/modules/input.txt"
            fail_setup = {
                "source-tree": "source_tree_sha256() { return 71; }",
                "file-hash": (
                    "FAIL_PATH=%s\n"
                    "sha256sum() {\n"
                    "    local target=\"${1:-}\"; [[ \"$target\" == -- ]] && target=\"${2:-}\"\n"
                    "    if [[ \"$target\" == \"$FAIL_PATH\" ]]; then return 72; fi\n"
                    "    command sha256sum \"$@\"\n"
                    "}"
                    % shlex.quote(xiahai_path)
                ),
                "input-hash": (
                    "FAIL_PATH=%s\n"
                    "sha256sum() {\n"
                    "    local target=\"${1:-}\"; [[ \"$target\" == -- ]] && target=\"${2:-}\"\n"
                    "    if [[ \"$target\" == \"$FAIL_PATH\" ]]; then return 73; fi\n"
                    "    command sha256sum \"$@\"\n"
                    "}"
                    % shlex.quote(input_path)
                ),
                "final-hash": (
                    "sha256sum() {\n"
                    "    local target=\"${1:-}\"; [[ \"$target\" == -- ]] && target=\"${2:-}\"\n"
                    "    case \"$target\" in\n"
                        "        *ming-build-input-payload.*) return 74 ;;\n"
                    "        '') return 74 ;;\n"
                    "        *) command sha256sum \"$@\" ;;\n"
                    "    esac\n"
                    "}"
                ),
            }
            script = "\n".join(
                (
                    "set -u -o pipefail",
                    f"export TMPDIR={shlex.quote(tmp_path)}",
                    'log_error() { printf "%s\\n" "$*" >&2; }',
                    f"MODULES_DIR={shlex.quote(script_path + '/modules')}",
                    f"CONFIG_DIR={shlex.quote(script_path + '/config')}",
                    f"SCRIPT_DIR={shlex.quote(script_path)}",
                    f"SCRIPT_DIR_LEXICAL={shlex.quote(script_path)}",
                    f"BUILD_STATE_HELPER={shlex.quote(script_path + '/scripts/ming_build_state.py')}",
                    "MING_OS_BUILD_SUFFIX=rc4",
                    "ISO_VOLUME_ID=MING_TEST",
                    "MING_SKIP_XIAHAI=0",
                    f"MING_XIAHAI_DEB_SOURCE={shlex.quote(xiahai_path)}",
                    "MING_BUILD_PROFILE=test",
                    "MING_OTA_RELEASE_PUBLIC_KEY_SOURCE=",
                    shell_functions,
                    "source_tree_sha256() { printf source-tree; }"
                    if failure_mode != "source-tree" else "",
                    fail_setup[failure_mode],
                    "set +e",
                    "build_inputs_sha256 >\"$TMPDIR/hash.out\"",
                    "rc=$?",
                    "set -e",
                    "remaining=$(find \"$TMPDIR\" -maxdepth 1 -type f -name 'ming-build-input-*' | wc -l)",
                    'printf "rc=%s remaining=%s\\n" "$rc" "$remaining"',
                )
            )
            result = self._run_bash(script, cwd=root)
            output = (root / "tmp" / "hash.out").read_text(
                encoding="utf-8", errors="replace"
            ) if (root / "tmp" / "hash.out").exists() else ""
        self.assertEqual(result.returncode, 0, result.stderr)
        match = re.search(r"rc=(\d+) remaining=(\d+)", result.stdout)
        self.assertIsNotNone(match, result.stdout + result.stderr + output)
        return int(match.group(1)), int(match.group(2)), result

    def test_build_input_hash_propagates_source_tree_failure_and_cleans_temp_files(self):
        rc, remaining, _result = self._run_input_hash_failure("source-tree")
        self.assertNotEqual(rc, 0)
        self.assertEqual(remaining, 0)

    def test_build_input_hash_propagates_each_file_hash_failure_and_cleans_temp_files(self):
        for failure_mode in ("input-hash", "file-hash", "final-hash"):
            with self.subTest(failure_mode=failure_mode):
                rc, remaining, _result = self._run_input_hash_failure(failure_mode)
                self.assertNotEqual(rc, 0)
                self.assertEqual(remaining, 0)

    def test_initialize_build_state_propagates_hash_failures_before_array_build(self):
        """Hash command substitutions must not be hidden by array assignment."""
        initialize = BUILD.split("initialize_build_state() {", 1)[1].split(
            "state_is_complete() {", 1
        )[0]
        for marker in (
            'if ! source_tree_hash="$(source_tree_sha256)"; then',
            'if ! modules_hash="$(build_inputs_sha256)"; then',
            'if ! xiahai_hash="$(file_sha256_or_missing',
            'if ! keyring_hash="$(file_sha256_or_missing',
            'if ! tools_hash="$(tools_fingerprint)"; then',
        ):
            self.assertIn(marker, initialize)
        self.assertNotIn('--modules-sha256 "$(build_inputs_sha256)"', initialize)

    def test_clean_rootfs_signal_trap_exits_after_cleanup(self):
        """HUP/INT/TERM must not resume the cleanup payload after interruption."""
        payload = self._clean_chroot_appstream_payload()
        translated = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
        translated = translated.replace(
            "        apt-cache dumpavail",
            "        appstream_signal_test_hook\n        apt-cache dumpavail",
            1,
        )
        self.assertIn("appstream_signal_test_hook", translated)
        for signal_name, signal_number in (("HUP", 1), ("INT", 2), ("TERM", 15)):
            with self.subTest(signal=signal_name), tempfile.TemporaryDirectory(
                prefix="ming-appstream-signal-"
            ) as temporary:
                root = pathlib.Path(temporary)
                apt_lists = root / "var" / "lib" / "apt" / "lists"
                apt_lists.mkdir(parents=True)
                (apt_lists / "trusted_dep11_Components-amd64.yml").write_text(
                    "Package: trusted-current\n", encoding="utf-8"
                )
                (root / "var" / "cache" / "swcatalog").mkdir(parents=True)
                root_path = self._bash_path(root)
                script = "\n".join(
                    (
                        "set +e",
                        f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                        f"export MING_TEST_SIGNAL={signal_number}",
                        "appstream_signal_test_hook() {",
                        "    appstream_signal_exit \"$MING_TEST_SIGNAL\"",
                        "    return 77",
                        "}",
                        "(",
                        translated,
                        ")",
                        "rc=$?",
                        'printf "rc=%s\\n" "$rc"',
                        'if [ "$rc" -eq 0 ]; then printf completed > "$MING_TEST_ROOT/signal-completed"; fi',
                    )
                )
                result = self._run_bash(script, cwd=root)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f"rc={128 + signal_number}")
                self.assertFalse((root / "signal-completed").exists())
                self.assertFalse(
                    (root / "var" / "lib" / "ming-os" / "appstream-apt-packages.txt").exists()
                )
                self.assertEqual(
                    list((root / "var" / "cache" / "swcatalog").glob(".ming-appstream-yaml.*")),
                    [],
                )
        for marker in (
            'trap "appstream_signal_exit 1" HUP',
            'trap "appstream_signal_exit 2" INT',
            'trap "appstream_signal_exit 15" TERM',
            "128 + signal",
        ):
            self.assertIn(marker, BUILD)

    def test_clean_rootfs_rejects_a_symlinked_swcatalog_parent(self):
        """The cleanup destination must stay inside the target rootfs."""
        payload = self._clean_chroot_appstream_payload()
        with tempfile.TemporaryDirectory(prefix="ming-appstream-parent-link-") as temporary:
            root = pathlib.Path(temporary)
            apt_lists = root / "var" / "lib" / "apt" / "lists"
            apt_lists.mkdir(parents=True)
            (apt_lists / "trusted_dep11_Components-amd64.yml").write_text(
                "Package: trusted-current\n", encoding="utf-8"
            )
            cache = root / "var" / "cache"
            cache.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (cache / "swcatalog").symlink_to(outside, target_is_directory=True)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            (fake_bin / "apt-cache").write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' 'Package: trusted-current'\n",
                encoding="utf-8",
            )
            os.chmod(fake_bin / "apt-cache", 0o755)
            root_path = self._bash_path(root)
            fake_bin_path = self._bash_path(fake_bin)
            translated = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
            script = "\n".join(
                (
                    "set +e",
                    f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                    f"export PATH={shlex.quote(fake_bin_path)}:\"$PATH\"",
                    translated,
                    "printf 'completed\\n'",
                )
            )
            result = self._run_bash(script, cwd=root)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(outside.iterdir()), [])

    def test_clean_rootfs_rejects_a_symlinked_appstream_index_dir(self):
        """The package index must not be written through a reused-rootfs link."""
        payload = self._clean_chroot_appstream_payload()
        with tempfile.TemporaryDirectory(prefix="ming-appstream-index-link-") as temporary:
            root = pathlib.Path(temporary)
            apt_lists = root / "var" / "lib" / "apt" / "lists"
            apt_lists.mkdir(parents=True)
            (apt_lists / "trusted_dep11_Components-amd64.yml").write_text(
                "Package: trusted-current\n", encoding="utf-8"
            )
            outside = root / "outside-index"
            outside.mkdir()
            index_dir = root / "var" / "lib" / "ming-os"
            self._make_directory_symlink(index_dir, outside)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_apt_cache = fake_bin / "apt-cache"
            fake_apt_cache.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" != "dumpavail" ]; then exit 64; fi\n'
                "printf '%s\\n' 'Package: trusted-current'\n",
                encoding="utf-8",
            )
            os.chmod(fake_apt_cache, 0o755)
            root_path = self._bash_path(root)
            fake_bin_path = self._bash_path(fake_bin)
            translated = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
            script = "\n".join(
                (
                    "set +e",
                    f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                    f"export PATH={shlex.quote(fake_bin_path)}:\"$PATH\"",
                    translated,
                    "printf 'completed\\n'",
                )
            )
            result = self._run_bash(script, cwd=root)
            escaped_files = list(outside.rglob("*"))
        self.assertNotEqual(
            result.returncode,
            0,
            "a symlinked AppStream index directory must be rejected",
        )
        self.assertEqual(escaped_files, [], "cleanup wrote through the index directory symlink")

    def test_input_hash_includes_complete_source_tree_identity(self):
        inputs = BUILD.split("build_inputs_sha256() {", 1)[1].split(
            "file_sha256_or_missing() {", 1
        )[0]
        self.assertIn("source_tree_sha256", inputs)
        self.assertIn("assert_source_tree_has_no_symlinks", inputs)

    def test_clean_rootfs_removes_generated_python_cache_files(self):
        """Module-time py_compile must not ship bytecode in the release rootfs."""
        clean = BUILD.split("clean_chroot() {", 1)[1].split(
            "# ======================== 生成 initramfs ========================", 1
        )[0]
        self.assertIn("__pycache__", clean)
        self.assertIn("*.pyc", clean)
        self.assertIn("*.pyo", clean)

    def test_clean_rootfs_indexes_copied_apt_style_dep11_metadata(self):
        """APT DEP-11 filenames must not be excluded from the strict index."""
        clean = BUILD.split("clean_chroot() {", 1)[1].split(
            "# ======================== 生成 initramfs ========================", 1
        )[0]
        index = clean.split("index_dir=/var/lib/ming-os", 1)[1].split(
            'chroot_exec bash -c "rm -rf /var/lib/apt/lists/*"', 1
        )[0]
        self.assertIn('find -P "${metadata_stage}" -maxdepth 1 -type f', index)
        self.assertIn("*.yml.gz", index)
        self.assertNotIn('"${destination}"/Components-', index)
        self.assertIn("apt-cache dumpavail", index)
        self.assertIn("comm -12", index)
        self.assertIn("mktemp", index)

    def test_clean_rootfs_copies_all_apt_dep11_yaml_suffixes(self):
        """Both APT YAML spellings must survive before apt lists are removed."""
        clean = BUILD.split("clean_chroot() {", 1)[1].split(
            "# ======================== 生成 initramfs ========================", 1
        )[0]
        source_patterns = clean.split("for source in", 1)[1].split("; do", 1)[0].split()
        for pattern in (
            "/var/lib/apt/lists/*_dep11_Components-*.yml",
            "/var/lib/apt/lists/*_dep11_Components-*.yml.gz",
            "/var/lib/apt/lists/*_dep11_Components-*.yaml",
            "/var/lib/apt/lists/*_dep11_Components-*.yaml.gz",
        ):
            self.assertIn(pattern, source_patterns)

    def test_clean_rootfs_rebuilds_metadata_without_counting_stale_yaml(self):
        """Only metadata copied during this cleanup may feed the package index."""
        clean = BUILD.split("clean_chroot() {", 1)[1].split(
            "# ======================== 生成 initramfs ========================", 1
        )[0]
        index = clean.split("index_dir=/var/lib/ming-os", 1)[1].split(
            'chroot_exec bash -c "rm -rf /var/lib/apt/lists/*"', 1
        )[0]
        self.assertIn("destination_parent=/var/cache/swcatalog", clean)
        self.assertIn(
            'metadata_stage="$(mktemp -d "${destination_parent}/.ming-appstream-yaml.XXXXXX")"',
            clean,
        )
        self.assertIn('target="${metadata_stage}/$(basename "${source}")"', clean)
        self.assertIn('find -P "${metadata_stage}" -maxdepth 1 -type f', index)
        self.assertNotIn('find -P "${destination}" -maxdepth 1 -type f', index)
        self.assertIn('rm -rf -- "${destination}"', clean)
        self.assertIn('chmod 0755 "${metadata_stage}"', clean)
        self.assertLess(
            clean.index('chmod 0755 "${metadata_stage}"'),
            clean.index('mv -- "${metadata_stage}" "${destination}"'),
        )
        self.assertIn('mv -- "${metadata_stage}" "${destination}"', clean)

    def test_clean_rootfs_ignores_stale_swcatalog_metadata_behind_a_symlink(self):
        """Only DEP-11 records from this APT lists directory may enter the index."""
        payload = self._clean_chroot_appstream_payload()
        with tempfile.TemporaryDirectory(prefix="ming-appstream-") as temporary:
            root = pathlib.Path(temporary)
            apt_lists = root / "var" / "lib" / "apt" / "lists"
            apt_lists.mkdir(parents=True)
            (apt_lists / "trusted_dep11_Components-amd64.yml").write_text(
                "Package: trusted-current\n", encoding="utf-8"
            )
            stale_yaml = root / "var" / "cache" / "swcatalog" / "yaml"
            stale_yaml.mkdir(parents=True)
            (stale_yaml / "stale.yml").write_text(
                "Package: stale-cache-only\n", encoding="utf-8"
            )
            swcatalog = root / "var" / "lib" / "swcatalog"
            swcatalog.mkdir(parents=True)
            self._make_directory_symlink(swcatalog / "yaml", stale_yaml)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_apt_cache = fake_bin / "apt-cache"
            fake_apt_cache.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" != "dumpavail" ]; then exit 64; fi\n'
                "printf '%s\\n' 'Package: trusted-current' '' 'Package: stale-cache-only'\n",
                encoding="utf-8",
            )
            os.chmod(fake_apt_cache, 0o755)
            root_path = self._bash_path(root)
            fake_bin_path = self._bash_path(fake_bin)
            payload = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
            script = "\n".join(
                (
                    "set -u -o pipefail",
                    f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                    f"export PATH={shlex.quote(fake_bin_path)}:\"$PATH\"",
                    payload,
                )
            )
            result = self._run_bash(script, cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            index = root / "var" / "lib" / "ming-os" / "appstream-apt-packages.txt"
            self.assertEqual(index.read_text(encoding="utf-8").splitlines(), ["trusted-current"])

    def test_clean_rootfs_rejects_a_swcatalog_parent_symlink(self):
        """AppStream staging must not write through a cache directory link."""
        payload = self._clean_chroot_appstream_payload()
        with tempfile.TemporaryDirectory(prefix="ming-appstream-parent-") as temporary:
            root = pathlib.Path(temporary)
            apt_lists = root / "var" / "lib" / "apt" / "lists"
            apt_lists.mkdir(parents=True)
            (apt_lists / "trusted_dep11_Components-amd64.yml").write_text(
                "Package: trusted-current\n", encoding="utf-8"
            )
            outside = root / "outside-cache"
            outside.mkdir()
            cache_dir = root / "var" / "cache"
            cache_dir.mkdir(parents=True)
            self._make_directory_symlink(cache_dir / "swcatalog", outside)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_apt_cache = fake_bin / "apt-cache"
            fake_apt_cache.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" != "dumpavail" ]; then exit 64; fi\n'
                "printf '%s\\n' 'Package: trusted-current'\n",
                encoding="utf-8",
            )
            os.chmod(fake_apt_cache, 0o755)
            root_path = self._bash_path(root)
            fake_bin_path = self._bash_path(fake_bin)
            payload = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
            script = "\n".join(
                (
                    "set -eu -o pipefail",
                    f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                    f"export PATH={shlex.quote(fake_bin_path)}:\"$PATH\"",
                    payload,
                )
            )
            result = self._run_bash(script, cwd=root)
            escaped_files = list(outside.rglob("*"))
        self.assertNotEqual(
            result.returncode,
            0,
            "a symlinked swcatalog parent must be rejected",
        )
        self.assertEqual(escaped_files, [], "cleanup wrote through the cache symlink")

    def test_clean_rootfs_rejects_a_ming_os_index_parent_symlink(self):
        """The derived package index must stay inside the target rootfs."""
        payload = self._clean_chroot_appstream_payload()
        with tempfile.TemporaryDirectory(prefix="ming-appstream-index-parent-") as temporary:
            root = pathlib.Path(temporary)
            apt_lists = root / "var" / "lib" / "apt" / "lists"
            apt_lists.mkdir(parents=True)
            (apt_lists / "trusted_dep11_Components-amd64.yml").write_text(
                "Package: trusted-current\n", encoding="utf-8"
            )
            outside = root / "outside-index"
            outside.mkdir()
            ming_lib = root / "var" / "lib"
            self._make_directory_symlink(ming_lib / "ming-os", outside)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_apt_cache = fake_bin / "apt-cache"
            fake_apt_cache.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" != "dumpavail" ]; then exit 64; fi\n'
                "printf '%s\\n' 'Package: trusted-current'\n",
                encoding="utf-8",
            )
            os.chmod(fake_apt_cache, 0o755)
            root_path = self._bash_path(root)
            fake_bin_path = self._bash_path(fake_bin)
            payload = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
            script = "\n".join(
                (
                    "set -eu -o pipefail",
                    f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                    f"export PATH={shlex.quote(fake_bin_path)}:\"$PATH\"",
                    payload,
                )
            )
            result = self._run_bash(script, cwd=root)
            escaped_files = list(outside.rglob("*"))
        self.assertNotEqual(
            result.returncode,
            0,
            "a symlinked /var/lib/ming-os parent must be rejected",
        )
        self.assertEqual(escaped_files, [], "cleanup wrote through the index symlink")

    def test_clean_rootfs_creates_a_missing_swcatalog_parent_inside_rootfs(self):
        """A fresh chroot without AppStream cache directories remains buildable."""
        payload = self._clean_chroot_appstream_payload()
        with tempfile.TemporaryDirectory(prefix="ming-appstream-fresh-") as temporary:
            root = pathlib.Path(temporary)
            apt_lists = root / "var" / "lib" / "apt" / "lists"
            apt_lists.mkdir(parents=True)
            (apt_lists / "trusted_dep11_Components-amd64.yml").write_text(
                "Package: trusted-current\n", encoding="utf-8"
            )
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_apt_cache = fake_bin / "apt-cache"
            fake_apt_cache.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" != "dumpavail" ]; then exit 64; fi\n'
                "printf '%s\\n' 'Package: trusted-current'\n",
                encoding="utf-8",
            )
            os.chmod(fake_apt_cache, 0o755)
            root_path = self._bash_path(root)
            fake_bin_path = self._bash_path(fake_bin)
            payload = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
            script = "\n".join(
                (
                    "set -eu -o pipefail",
                    f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                    f"export PATH={shlex.quote(fake_bin_path)}:\"$PATH\"",
                    payload,
                )
            )
            result = self._run_bash(script, cwd=root)
            index = root / "var" / "lib" / "ming-os" / "appstream-apt-packages.txt"
            destination = root / "var" / "cache" / "swcatalog" / "yaml"
            index_lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else None
            destination_exists = destination.is_dir()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(index_lines, ["trusted-current"])
        self.assertTrue(destination_exists)

    def test_clean_rootfs_signal_cleanup_exits_without_continuing(self):
        """An interrupted AppStream cleanup must stop after removing staging files."""
        payload = self._clean_chroot_appstream_payload()
        # Exercise the signal handler directly without relying on errexit to
        # stop the caller.  The handler itself must terminate the payload.
        payload = payload.replace("set -eu -o pipefail", "set -u -o pipefail", 1)
        marker = 'dep11_packages="$(mktemp'
        self.assertIn(marker, payload)
        payload = payload.replace(
            marker,
            ': > "${MING_TEST_SIGNAL_STARTED}"\n'
            '( sleep 0.1; kill -TERM $$ ) &\n'
            'sleep 1\n'
            'printf continued > "${MING_TEST_CONTINUED}"\n'
            + marker,
            1,
        )
        with tempfile.TemporaryDirectory(prefix="ming-appstream-signal-") as temporary:
            root = pathlib.Path(temporary)
            root_path = self._bash_path(root)
            payload = payload.replace("/var/", "${MING_TEST_ROOT}/var/")
            signal_started = root / "signal-started"
            continued = root / "continued"
            script = "\n".join(
                (
                    "set -u -o pipefail",
                    f"export MING_TEST_ROOT={shlex.quote(root_path)}",
                    f"export MING_TEST_SIGNAL_STARTED={shlex.quote(root_path + '/signal-started')}",
                    f"export MING_TEST_CONTINUED={shlex.quote(root_path + '/continued')}",
                    payload,
                )
            )
            result = self._run_bash(script, cwd=root)
            continued_exists = continued.exists()
        self.assertNotEqual(result.returncode, 0, "SIGTERM was swallowed by cleanup")
        self.assertFalse(continued_exists, "cleanup continued after SIGTERM")

    def test_clean_rootfs_removes_machine_keys_without_broad_public_material_wipe(self):
        """A release image must not clone build-host identities to every install."""
        clean = BUILD.split("clean_chroot() {", 1)[1].split(
            "# ======================== 生成 initramfs ========================", 1
        )[0]
        for marker in (
            "/etc/ssh/ssh_host_*_key",
            "/etc/ssh/ssh_host_*_key.pub",
            "/etc/ssl/private/ssl-cert-snakeoil.key",
            "/usr/share/doc/openvpn/examples/sample-keys/*.key",
        ):
            self.assertIn(marker, clean)
        self.assertNotIn("/etc/ssh/*'", clean)
        self.assertNotIn("/etc/ssh/*\"", clean)
        self.assertNotIn(
            "rm -f /usr/share/doc/openvpn/examples/sample-keys/*", clean
        )
        self.assertNotIn("sample-keys/*.pub", clean)

    def test_rootfs_gate_rejects_build_time_private_key_material(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        for marker in (
            'root.glob("etc/ssh/ssh_host_*_key")',
            '"etc/ssl/private/ssl-cert-snakeoil.key"',
            'root.glob("usr/share/doc/openvpn/examples/sample-keys/*.key")',
            '"build-time private key material"',
        ):
            self.assertIn(marker, validator)
        self.assertNotIn(
            'root.glob("usr/share/doc/openvpn/examples/sample-keys/*")', validator
        )

    def test_successful_build_preserves_checkpoint_artifacts_for_resume(self):
        body = BUILD.split("build_iso() {", 1)[1].split(
            "build_iso_manual() {", 1
        )[0]
        self.assertNotIn('rm -rf "${ISO_DIR}"', body)

    def test_legacy_reuse_flag_cannot_bypass_checkpoint_validation(self):
        body = BUILD.split("stage_debootstrap() {", 1)[1].split(
            "stage_prepare_chroot() {", 1
        )[0]
        self.assertNotIn("MING_REUSE_CHROOT", body)

    def test_iso_is_built_to_a_partial_path_before_publish(self):
        body = BUILD.split("stage_iso() {", 1)[1].split(
            "stage_publish_artifacts() {", 1
        )[0]
        self.assertIn(".partial", body)
        self.assertIn("mv -f", body)

    def test_stage_artifacts_are_passed_without_word_splitting(self):
        self.assertIn("declare -a STAGE_ARTIFACTS", BUILD)
        self.assertIn(
            'state_mark_completed "${stage}" "${STAGE_ARTIFACTS[@]}"',
            BUILD,
        )

    def test_apt_cache_manifest_uses_structured_json_io(self):
        validation = BUILD.split("validate_apt_cache_manifest() {", 1)[1].split(
            "write_apt_cache_manifest() {", 1
        )[0]
        writer = BUILD.split("write_apt_cache_manifest() {", 1)[1].split(
            "write_stage_marker() {", 1
        )[0]
        self.assertIn("json.load", validation)
        self.assertIn("json.dump", writer)

    def test_rerunning_an_intermediate_stage_invalidates_successors(self):
        self.assertIn("invalidate_successors", BUILD)
        run_stage = BUILD.split("run_stage() {", 1)[1].split(
            "# 检查命令是否存在", 1
        )[0]
        self.assertIn("invalidate_successors", run_stage)

    def test_failure_trap_records_stage_and_command_before_exit(self):
        for marker in (
            "CURRENT_STAGE",
            "CURRENT_COMMAND",
            "record_build_failure",
            "BASH_LINENO",
            "PIPESTATUS",
            "resume_hint",
        ):
            self.assertIn(marker, BUILD)

    def test_old_entrypoints_delegate_to_the_rc_builder(self):
        for name in (
            "continue_build.sh",
            "fast_build_iso.sh",
            "final_build_iso.sh",
            "rebuild_iso.sh",
            "generate_final_iso.sh",
        ):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("build_onion_os.sh", source, name)
            self.assertNotIn("grub-mkrescue", source, name)
            self.assertNotIn("ONION_OS_VERSION=\"26.0.0\"", source, name)

    def test_profile_wrappers_cannot_be_overridden_by_later_arguments(self):
        fast = (ROOT / "fast_build_iso.sh").read_text(encoding="utf-8")
        release = (ROOT / "final_build_iso.sh").read_text(encoding="utf-8")
        self.assertIn('"$@" --profile fast-test', fast)
        self.assertIn('"$@" --profile release', release)

    def test_host_apt_dependency_install_has_timeout_and_retry_policy(self):
        install = BUILD.split("install_build_deps() {", 1)[1].split(
            "verify_debootstrap_keyring() {", 1
        )[0]
        for marker in (
            "DEBIAN_FRONTEND=noninteractive",
            "Acquire::Retries=5",
            "Acquire::http::Timeout=15",
            "Acquire::https::Timeout=15",
        ):
            self.assertIn(marker, install)

    def test_host_preflight_installs_file_for_kernel_validation(self):
        self.assertIn("require_cmd file", BUILD)
        install = BUILD.split("install_build_deps() {", 1)[1].split(
            "verify_debootstrap_keyring() {", 1
        )[0]
        self.assertIn("mtools dosfstools file rsync python3-yaml debian-archive-keyring", install)

    def test_host_preflight_installs_yaml_for_calamares_validation(self):
        install = BUILD.split("install_build_deps() {", 1)[1].split(
            "verify_debootstrap_keyring() {", 1
        )[0]
        self.assertIn("python3-yaml", install)
        self.assertIn("import yaml", BUILD)

    def test_apt_cache_manifest_validates_mirror_identity(self):
        validation = BUILD.split("validate_apt_cache_manifest() {", 1)[1].split(
            "write_apt_cache_manifest() {", 1
        )[0]
        self.assertIn('payload.get("mirror")', validation)
        self.assertIn('payload.get("security_mirror")', validation)
        self.assertIn('payload.get("keyring_sha256")', validation)
        self.assertIn('"keyring_sha256": sys.argv[6]', BUILD)

    def test_apt_cache_identity_mismatch_removes_reusable_deb_archives(self):
        validation = BUILD.split("validate_apt_cache_manifest() {", 1)[1].split(
            "write_apt_cache_manifest() {", 1
        )[0]
        self.assertIn('find "${APT_ARCHIVES_CACHE}"', validation)
        self.assertIn("-name '*.deb'", validation)
        self.assertIn("cache identity", validation.lower())

    def test_release_image_contains_ota_minisign_tool_and_public_key_gate(self):
        install = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
        dependencies = install.split("install_ota_dependencies() {", 1)[1].split(
            "deploy_ota_backup_engine() {", 1
        )[0]
        self.assertIn("minisign", dependencies)
        self.assertIn("deploy_ota_release_trust", install)
        self.assertIn("ota-release.minisign.pub", BUILD)
        self.assertIn('"etc/ming-update/ota-release.minisign.pub"', BUILD)

    def test_release_profile_rejects_skipping_xiahai_and_sidecar_records_it(self):
        profile = BUILD.split("configure_build_profile() {", 1)[1].split(
            "acquire_build_lock() {", 1
        )[0]
        self.assertIn("MING_SKIP_XIAHAI", profile)
        self.assertIn("release", profile)
        self.assertIn("release_eligible", BUILD)
        self.assertIn("skip_xiahai", BUILD)

    def test_rootfs_gate_resolves_symlinks_without_escaping_target(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        for marker in (
            "def _rootfs_path(relative_path):",
            "path.lstat()",
            "os.readlink(path)",
            "resolves outside target rootfs",
            "contains an unresolved or looping symlink",
        ):
            self.assertIn(marker, validator)

    def test_rootfs_gate_detects_dangling_forbidden_symlink(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        absent = validator.split("def require_absent(", 1)[1].split(
            "def validate_generated_executable", 1
        )[0]
        self.assertIn("path.lstat()", absent)
        self.assertIn("path.is_symlink()", absent)

    def test_rootfs_gate_checks_retired_package_variants_and_nested_residue(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        self.assertIn("find_retired_residue", validator)
        finder = validator.split("def find_retired_residue", 1)[1].split(
            "for residue in find_retired_residue", 1
        )[0]
        for marker in (
            "var/lib/dpkg/status",
            'state_fields[-1] == "config-files"',
            "os.walk(",
            "followlinks=False",
            "usr/share/applications",
            "usr/lib/systemd/system",
            "usr/share/polkit-1/actions",
        ):
            self.assertIn(marker, finder)
        for marker in ("spark-store", "ace-client", "spark-public.json", "spark-archive-keyring.gpg"):
            self.assertIn(marker, validator)

    def test_rootfs_gate_rejects_all_retired_dpkg_config_file_variants(self):
        """Removed clients with retained dpkg configuration are still image residue."""
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        package_bases = validator.split("RETIRED_PACKAGE_BASES = (", 1)[1].split(
            ")", 1
        )[0]
        for package in (
            "spark-update-notifier",
            "ming-spark-package-control",
            "ming-package-install-gui",
            "ming-spark-backend-status",
            "ming-spark-aria2c",
            "ming-spark-store",
        ):
            self.assertIn(f'"{package}"', package_bases)
        self.assertIn(
            'retained_config = len(state_fields) >= 3 and state_fields[-1] == "config-files"',
            validator,
        )

    def test_rootfs_gate_rejects_retired_packages_with_any_config_files_status(self):
        """The rootfs gate must catch retained configuration after any dpkg action."""
        with tempfile.TemporaryDirectory(prefix="ming-retired-dpkg-") as temporary:
            root = pathlib.Path(temporary)
            status = root / "var" / "lib" / "dpkg" / "status"
            status.parent.mkdir(parents=True)
            status.write_text(
                "Package: spark-store\nStatus: purge ok config-files\n",
                encoding="utf-8",
            )
            namespace = self._retired_residue_namespace(root)
            findings = namespace["find_retired_residue"]()
        self.assertIn(
            "dpkg status: spark-store (purge ok config-files)",
            findings,
        )
        self.assertEqual(namespace["errors"], [])


if __name__ == "__main__":
    unittest.main()
