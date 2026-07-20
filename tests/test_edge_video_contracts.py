import os
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
BASH = (
    r"C:\Program Files\Git\bin\bash.exe"
    if os.name == "nt" and pathlib.Path(r"C:\Program Files\Git\bin\bash.exe").is_file()
    else "bash"
)


class EdgeVideoContracts(unittest.TestCase):
    def test_media_sample_generator_executes_both_codecs_and_probes_outputs(self):
        start = APPS.index("generate_edge_video_samples() {")
        end = APPS.index("\n}", start) + 2
        function = APPS[start:end]
        harness = r'''sample_root="$(mktemp -d)"
export MING_EDGE_SAMPLE_DIR="${sample_root}"
ffmpeg() {
  printf 'FFMPEG %s\n' "$*"
  out="${@: -1}"
  mkdir -p "$(dirname "$out")"
  printf sample >"$out"
}
ffprobe() {
  case "${@: -1}" in *h264.mp4) printf 'h264\n' ;; *vp9.webm) printf 'vp9\n' ;; esac
}
'''
        script = (harness + function + "\ngenerate_edge_video_samples\n" +
                  'wc -c "${sample_root}/h264.mp4" "${sample_root}/vp9.webm"\n')
        result = subprocess.run([BASH], input=script.encode(), capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        output = result.stdout.decode(errors="replace")
        self.assertIn("libx264", output)
        self.assertIn("libvpx-vp9", output)
        self.assertRegex(output, r"[1-9][0-9]* .*h264\.mp4")
        self.assertRegex(output, r"[1-9][0-9]* .*vp9\.webm")

    def test_media_sample_generator_blocks_when_encoding_fails(self):
        start = APPS.index("generate_edge_video_samples() {")
        end = APPS.index("\n}", start) + 2
        function = APPS[start:end]
        harness = 'MING_EDGE_SAMPLE_DIR="$(mktemp -d)"\nffmpeg() { return 1; }\nffprobe() { return 0; }\n'
        result = subprocess.run(
            [BASH], input=(harness + function + "\ngenerate_edge_video_samples\n").encode())
        self.assertNotEqual(0, result.returncode)

    def test_edge_always_uses_x11_and_avoids_unstable_forced_features(self):
        self.assertIn("edge_args=(--ozone-platform=x11)", APPS)
        self.assertNotIn("--use-gl=egl", APPS)
        self.assertNotIn("UseMultiPlaneFormatForHardwareVideo", APPS)

    def test_edge_graphics_helper_selects_active_render_node_and_real_decode_gate(self):
        start = APPS.index("cat > /usr/local/bin/ming-edge-graphics")
        end = APPS.index("\nMINGEDGEGRAPHICS", start)
        helper = APPS[start:end]
        self.assertIn("renderD*", helper)
        self.assertNotIn("renderD128", helper)
        self.assertIn("ffmpeg", helper)
        self.assertIn("test-video", helper)
        self.assertIn("set-mode", helper)
        self.assertIn("compat", helper)
        self.assertIn("successful_codecs", helper)

    def test_edge_enables_gpu_only_after_structured_hardware_validation(self):
        start = APPS.index("cat > /usr/local/bin/ming-edge << 'MINGEDGE'")
        end = APPS.index("\nMINGEDGE", start + len("cat > /usr/local/bin/ming-edge << 'MINGEDGE'"))
        wrapper = APPS[start:end]
        self.assertIn("ming-hardware-status status --json", wrapper)
        self.assertIn("ming-edge-graphics test-video", wrapper)
        self.assertIn("--disable-gpu", wrapper)
        self.assertIn("nomodeset", wrapper)
