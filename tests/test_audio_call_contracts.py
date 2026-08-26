import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ROOT / "modules" / "02_apps.sh"
BASE = ROOT / "modules" / "01_base.sh"


class AudioCallBuildContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apps = APPS.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")

    def test_required_runtime_uses_pipewire_and_has_no_pulseaudio_daemon(self):
        required_block = self.apps[
            self.apps.index("readonly REQUIRED_DESKTOP_RUNTIME_PACKAGES=("):
            self.apps.index(")", self.apps.index("readonly REQUIRED_DESKTOP_RUNTIME_PACKAGES=(")) + 1]
        for package in [
            "pipewire", "pipewire-pulse", "pipewire-alsa", "wireplumber",
            "libspa-0.2-bluetooth", "pulseaudio-utils", "libasound2-plugins",
            "pavucontrol", "dbus-user-session", "dbus-x11", "libpam-systemd",
        ]:
            self.assertIn(package, required_block)
        self.assertNotRegex(required_block, r"(?m)^\s*pulseaudio\s*$")
        self.assertNotIn("pulseaudio-module-bluetooth", required_block)

    def test_build_and_resume_gates_require_the_pipewire_runtime_stack(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        resume = (ROOT / "resume_build.sh").read_text(encoding="utf-8")
        for source in (self.apps, build):
            for package in (
                "pipewire", "pipewire-pulse", "pipewire-alsa", "wireplumber",
                "libspa-0.2-bluetooth",
                "dbus-user-session", "dbus-x11", "libpam-systemd",
            ):
                self.assertIn(package, source)
        self.assertIn('exec "${SCRIPT_DIR}/build_onion_os.sh" --resume', resume)
        self.assertIn("configure_pipewire_audio", self.apps)
        self.assertIn(
            "systemctl --global enable pipewire.socket pipewire-pulse.socket wireplumber.service",
            self.apps,
        )

    def test_audio_runtime_requires_a_systemd_user_bus_for_lightdm_sessions(self):
        block = self.apps[
            self.apps.index("readonly REQUIRED_DESKTOP_RUNTIME_PACKAGES=("):
            self.apps.index(")", self.apps.index("readonly REQUIRED_DESKTOP_RUNTIME_PACKAGES=(")) + 1]
        for package in ("dbus-user-session", "dbus-x11", "libpam-systemd"):
            self.assertIn(package, block)

    def test_wechat_wrapper_repairs_audio_before_launch_without_memory_ceiling(self):
        start = self.apps.index("cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'")
        end = self.apps.index("\nWECHATWRAP", start + len("cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'"))
        wrapper = self.apps[start:end]
        self.assertIn("ming-device-control audio-repair-call", wrapper)
        self.assertNotIn("MemoryMax=", wrapper)
        self.assertNotIn("MemoryHigh=", wrapper)
        self.assertNotIn("nice -n", wrapper)
        self.assertNotIn("ionice", wrapper)

    def test_earlyoom_does_not_prefer_wechat_for_termination(self):
        self.assertNotIn("|wechat|", self.base)
