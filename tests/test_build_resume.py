import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_HELPER = ROOT / "scripts" / "ming_build_state.py"


def load_state_helper():
    spec = importlib.util.spec_from_file_location("ming_build_state", STATE_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {STATE_HELPER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildStateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            STATE_HELPER.is_file(),
            "the resumable build state helper has not been implemented",
        )
        self.state = load_state_helper()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.state_dir = pathlib.Path(self.tempdir.name) / "build-state"
        self.metadata = self.state.make_metadata(
            version="26.4.1",
            source_commit="a" * 40,
            source_tree_sha256="b" * 64,
            modules_sha256="c" * 64,
            profile="release",
            suite="trixie",
            arch="amd64",
            debian_mirror="https://deb.debian.org/debian/",
            security_mirror="https://security.debian.org/debian-security",
            squashfs_compression="xz",
        )
        self.state.initialize(
            self.state_dir,
            self.metadata,
            build_id="2641-rc4-test",
            build_time_utc="2026-08-26T00:00:00Z",
        )

    def test_completed_stage_is_reused_only_for_matching_input(self):
        input_hash = self.metadata["input_hash"]
        self.state.mark_started(
            self.state_dir,
            "debootstrap",
            input_hash=input_hash,
            now="2026-08-26T00:00:01Z",
            pid=100,
        )
        self.state.mark_completed(
            self.state_dir,
            "debootstrap",
            input_hash=input_hash,
            now="2026-08-26T00:00:03Z",
        )

        self.assertTrue(
            self.state.is_complete(self.state_dir, "debootstrap", input_hash)
        )
        self.assertFalse(
            self.state.is_complete(self.state_dir, "debootstrap", "different")
        )

    def test_resume_rejects_source_profile_module_and_mirror_changes(self):
        changed_values = {
            "source_commit": "d" * 40,
            "profile": "fast-test",
            "modules_sha256": "e" * 64,
            "debian_mirror": "https://mirror.invalid/debian/",
        }
        for field, value in changed_values.items():
            with self.subTest(field=field):
                changed = dict(self.metadata)
                changed[field] = value
                changed = self.state.make_metadata(**{
                    key: changed[key]
                    for key in self.state.METADATA_FIELDS
                })
                with self.assertRaises(self.state.StateMismatch) as caught:
                    self.state.validate(self.state_dir, changed)
                self.assertIn(field, caught.exception.changed_fields)

    def test_invalidate_from_removes_the_selected_stage_and_every_successor(self):
        input_hash = self.metadata["input_hash"]
        for stage in self.state.STAGES[:5]:
            self.state.mark_started(self.state_dir, stage, input_hash=input_hash)
            self.state.mark_completed(self.state_dir, stage, input_hash=input_hash)

        removed = self.state.invalidate_from(self.state_dir, "prepare-chroot")

        self.assertEqual(
            ["prepare-chroot", "modules", "initramfs"],
            removed,
        )
        self.assertTrue(
            self.state.is_complete(self.state_dir, "debootstrap", input_hash)
        )
        self.assertFalse(
            self.state.is_complete(self.state_dir, "prepare-chroot", input_hash)
        )

    def test_failure_record_contains_resume_hint_without_secrets(self):
        command = (
            "ROOT_PASS=plain-secret MING_USER_PASS=another-secret "
            "curl https://build:token@example.invalid/pkg.deb"
        )
        self.state.mark_failed(
            self.state_dir,
            "modules",
            input_hash=self.metadata["input_hash"],
            exit_code=42,
            command=command,
            line=123,
            now="2026-08-26T00:01:00Z",
        )

        failure = json.loads(
            (self.state_dir / "last-failure.json").read_text(encoding="utf-8")
        )
        self.assertEqual("modules", failure["stage"])
        self.assertEqual(42, failure["exit_code"])
        self.assertEqual(123, failure["line"])
        self.assertEqual("./resume_build.sh --from modules", failure["resume_hint"])
        self.assertNotIn("plain-secret", failure["command"])
        self.assertNotIn("another-secret", failure["command"])
        self.assertNotIn("build:token", failure["command"])
        self.assertIn("[REDACTED]", failure["command"])

    def test_json_writes_are_atomic_and_leave_no_temporary_files(self):
        self.state.mark_started(
            self.state_dir,
            "host-preflight",
            input_hash=self.metadata["input_hash"],
        )
        self.state.mark_completed(
            self.state_dir,
            "host-preflight",
            input_hash=self.metadata["input_hash"],
        )

        payload = json.loads(
            (self.state_dir / "host-preflight.json").read_text(encoding="utf-8")
        )
        self.assertEqual("completed", payload["status"])
        self.assertFalse(list(self.state_dir.glob("*.tmp")))

    def test_completed_stage_is_invalid_when_recorded_artifact_is_removed(self):
        artifact = pathlib.Path(self.tempdir.name) / "debootstrap.marker"
        artifact.write_text("ready\n", encoding="utf-8")
        input_hash = self.metadata["input_hash"]

        self.state.mark_started(
            self.state_dir,
            "debootstrap",
            input_hash=input_hash,
        )
        self.state.mark_completed(
            self.state_dir,
            "debootstrap",
            input_hash=input_hash,
            artifacts=[artifact],
        )

        self.assertTrue(
            self.state.is_complete(self.state_dir, "debootstrap", input_hash)
        )
        artifact.unlink()
        self.assertFalse(
            self.state.is_complete(self.state_dir, "debootstrap", input_hash)
        )

    def test_artifact_hashing_does_not_read_the_entire_file_at_once(self):
        artifact = pathlib.Path(self.tempdir.name) / "large-artifact.bin"
        artifact.write_bytes(b"Ming OS checkpoint\n")
        input_hash = self.metadata["input_hash"]

        self.state.mark_started(
            self.state_dir,
            "squashfs",
            input_hash=input_hash,
        )
        with mock.patch.object(
            pathlib.Path,
            "read_bytes",
            side_effect=AssertionError("artifact hashing must stream"),
        ):
            self.state.mark_completed(
                self.state_dir,
                "squashfs",
                input_hash=input_hash,
                artifacts=[artifact],
            )


if __name__ == "__main__":
    unittest.main()
