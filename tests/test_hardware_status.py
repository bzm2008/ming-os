import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDWARE_STATUS = ROOT / "assets" / "ming-hardware-status.py"
DESKTOP_MODULE = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


def load_hardware_status():
    spec = importlib.util.spec_from_file_location("ming_hardware_status", HARDWARE_STATUS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, command, timeout=8):
        command = tuple(command)
        self.commands.append(command)
        return self.responses.get(command, (1, "", "not available"))


class GraphicsStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hardware = load_hardware_status()

    def test_kaby_lake_i915_with_h264_and_vp9_is_ready_without_av1(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (
                0,
                "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n"
                "\tKernel driver in use: i915",
                "",
            ),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "root=/dev/sda1 quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (
                0,
                "Driver version: Intel iHD driver\nVAProfileH264High\nVAProfileVP9Profile0",
                "",
            ),
        })
        with tempfile.TemporaryDirectory() as directory:
            render_node = pathlib.Path(directory) / "renderD128"
            render_node.touch()
            status = self.hardware.HardwareStatus(
                runner=runner,
                render_nodes=lambda: [render_node],
                render_access=lambda _path: True,
                xorg_log_reader=lambda: '(II) LoadModule: "modesetting"',
            ).graphics_status()

        self.assertEqual("normal", status["state"])
        self.assertTrue(status["edge_hardware_video"])
        self.assertEqual("available", status["codecs"]["h264"])
        self.assertEqual("available", status["codecs"]["vp9"])
        self.assertEqual("unsupported", status["codecs"]["av1"])
        self.assertIn("HD Graphics 620", status["model"])

    def test_virtual_machine_never_enables_edge_hardware_video(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (0, "00:02.0 VGA compatible controller: VirtualBox Graphics Adapter", ""),
            ("lsmod",): (0, "", ""),
            ("systemd-detect-virt", "--quiet"): (0, "oracle", ""),
            ("cat", "/proc/cmdline"): (0, "root=/dev/sda1", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: llvmpipe", ""),
            ("vainfo", "--display", "drm"): (1, "", "cannot open display"),
        })
        status = self.hardware.HardwareStatus(runner=runner, render_nodes=lambda: []).graphics_status()

        self.assertEqual("attention", status["state"])
        self.assertFalse(status["edge_hardware_video"])
        self.assertIn("虚拟机", status["recommendation"])

    def test_legacy_ming_intel_ddx_is_reported_separately_from_i915_kernel_driver(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (
                0,
                "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n"
                "\tKernel driver in use: i915",
                "",
            ),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (0, "VAProfileH264High", ""),
        })
        with tempfile.TemporaryDirectory() as directory:
            legacy_config = pathlib.Path(directory) / "20-intel.conf"
            legacy_config.write_text(
                "# Managed by Ming OS legacy Intel Xorg setup\n"
                "Section \"Device\"\n    Driver \"intel\"\nEndSection\n",
                encoding="utf-8",
            )
            status = self.hardware.HardwareStatus(
                runner=runner,
                render_nodes=lambda: [pathlib.Path("/dev/dri/renderD128")],
                xorg_config_path=legacy_config,
                render_access=lambda _path: True,
            ).graphics_status()

        self.assertEqual("i915", status["driver"])
        self.assertEqual("legacy-intel-ddx", status["xorg_backend"])
        self.assertTrue(status["legacy_intel_config"])
        self.assertEqual("attention", status["state"])
        self.assertIn("Xorg", status["recommendation"])
        self.assertFalse(status["edge_hardware_video"])

    def test_edge_hardware_video_requires_known_render_access(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (
                0,
                "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n"
                "\tKernel driver in use: i915",
                "",
            ),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (0, "VAProfileH264High\nVAProfileVP9Profile0", ""),
        })
        # A raced/disappeared render node has unknown access, not verified access.
        status = self.hardware.HardwareStatus(
            runner=runner,
            render_nodes=lambda: [pathlib.Path("/tmp/ming-missing-render-node")],
            xorg_log_reader=lambda: '(II) LoadModule: "modesetting"',
        ).graphics_status()
        self.assertIsNone(status["render_access"])
        self.assertFalse(status["edge_hardware_video"])

    def test_edge_hardware_video_requires_modesetting_backend(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (
                0,
                "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n"
                "\tKernel driver in use: i915",
                "",
            ),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (0, "VAProfileH264High\nVAProfileVP9Profile0", ""),
        })
        with tempfile.TemporaryDirectory() as directory:
            render_node = pathlib.Path(directory) / "renderD128"
            render_node.touch()
            status = self.hardware.HardwareStatus(
                runner=runner,
                render_nodes=lambda: [render_node],
                render_access=lambda _path: True,
                xorg_log_reader=lambda: '(II) LoadModule: "intel"',
            ).graphics_status()
        self.assertEqual("legacy-intel-ddx", status["xorg_backend"])
        self.assertFalse(status["edge_hardware_video"])

    def test_edge_hardware_video_requires_both_h264_and_vp9(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (
                0,
                "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n"
                "\tKernel driver in use: i915",
                "",
            ),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (0, "VAProfileH264High", ""),
        })
        with tempfile.TemporaryDirectory() as directory:
            render_node = pathlib.Path(directory) / "renderD128"
            render_node.touch()
            status = self.hardware.HardwareStatus(
                runner=runner,
                render_nodes=lambda: [render_node],
                render_access=lambda _path: True,
                xorg_log_reader=lambda: '(II) LoadModule: "modesetting"',
            ).graphics_status()
        self.assertEqual("available", status["codecs"]["h264"])
        self.assertEqual("unsupported", status["codecs"]["vp9"])
        self.assertFalse(status["edge_hardware_video"])

    def test_render_permission_and_vaapi_error_are_reported_without_claiming_edge_ready(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (
                0,
                "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n"
                "\tKernel driver in use: i915",
                "",
            ),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (1, "", "libva error: permission denied"),
        })
        with tempfile.TemporaryDirectory() as directory:
            render_node = pathlib.Path(directory) / "renderD128"
            render_node.touch()
            status = self.hardware.HardwareStatus(
                runner=runner,
                render_nodes=lambda: [render_node],
                render_access=lambda _path: False,
            ).graphics_status()

        self.assertFalse(status["render_access"])
        self.assertFalse(status["vaapi"])
        self.assertEqual("libva error: permission denied", status["vaapi_error"])
        self.assertFalse(status["edge_hardware_video"])
        self.assertEqual("attention", status["state"])

    def test_xorg_backend_requires_log_evidence_and_accepts_injected_log_reader(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (
                0,
                "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n"
                "\tKernel driver in use: i915",
                "",
            ),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (0, "VAProfileH264High", ""),
        })
        unknown = self.hardware.HardwareStatus(
            runner=runner,
            render_nodes=lambda: [pathlib.Path("/dev/dri/renderD128")],
            render_access=lambda _path: True,
            xorg_log_reader=lambda: "",
        ).graphics_status()
        modesetting = self.hardware.HardwareStatus(
            runner=runner,
            render_nodes=lambda: [pathlib.Path("/dev/dri/renderD128")],
            render_access=lambda _path: True,
            xorg_log_reader=lambda: "(II) LoadModule: \"modesetting\"\n(II) modeset(0): using drv",
        ).graphics_status()

        self.assertEqual("unknown", unknown["xorg_backend"])
        self.assertEqual("modesetting", modesetting["xorg_backend"])

    def test_hardware_overview_returns_structured_cards_and_exportable_evidence(self):
        runner = FakeRunner({
            ("lspci", "-nnk"): (0, "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 620\n\tKernel driver in use: i915", ""),
            ("lsmod",): (0, "i915 123 1", ""),
            ("systemd-detect-virt", "--quiet"): (1, "", ""),
            ("cat", "/proc/cmdline"): (0, "quiet", ""),
            ("glxinfo", "-B"): (0, "OpenGL renderer string: Mesa Intel HD Graphics 620", ""),
            ("vainfo", "--display", "drm"): (0, "VAProfileH264High\nVAProfileVP9Profile0", ""),
            ("aplay", "-l"): (0, "card 0: PCH [HDA Intel PCH]", ""),
            ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"): (0, "wlan0:wifi:disconnected", ""),
        })
        service = self.hardware.HardwareStatus(
            runner=runner, render_nodes=lambda: [pathlib.Path("/dev/dri/renderD128")])
        result = service.status()

        self.assertEqual({"graphics", "audio", "network"}, set(result["devices"]))
        self.assertTrue(all({"model", "driver", "state", "recommendation"}.issubset(card)
                            for card in result["devices"].values()))
        self.assertIn("graphics", service.export())


class HardwareStatusDeploymentTests(unittest.TestCase):
    def test_desktop_module_installs_the_hardware_status_command(self):
        self.assertIn("ming-hardware-status.py", DESKTOP_MODULE)
        self.assertIn(
            'install -m 0755 "${asset_dir}/ming-hardware-status.py" /usr/local/bin/ming-hardware-status',
            DESKTOP_MODULE,
        )
