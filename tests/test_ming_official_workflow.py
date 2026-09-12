import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ming-official-release.yml"
BUILDER = ROOT / "tools" / "build-ming-official-debs.sh"


class MingOfficialWorkflowTests(unittest.TestCase):
    def test_workflow_is_manual_or_release_tag_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", text)
        self.assertIn('tags: ["v26.4.1-rc4"]', text)
        self.assertIn('release_tag:', text)
        self.assertIn('github.event.inputs.release_tag || github.ref_name', text)

    def test_workflow_requires_ci_private_key_without_echoing_it(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("MING_OFFICIAL_MINISIGN_KEY", text)
        self.assertIn("MING_OFFICIAL_MINISIGN_PUBLIC_KEY", text)
        self.assertIn("if [ -z \"$MING_OFFICIAL_MINISIGN_KEY\" ]", text)
        self.assertNotIn("echo \"$MING_OFFICIAL_MINISIGN_KEY\"", text)
        self.assertNotIn("set -x", text)

    def test_workflow_signs_catalog_and_uploads_integrity_metadata(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for marker in (
            "tools/build-ming-official-debs.sh",
            "tools/generate-ming-official-catalog.py",
            "minisign -Sm",
            "sha256sum",
            "ming-official.json.minisig",
            "SHA256SUMS",
            "gh release upload",
            "minisign -Vm release/ming-official.json",
            "ming-official-catalog.minisign.pub.sha256",
            "chmod +x release/staging/ming-store/usr/local/bin/ming-store",
            "chmod +x release/staging/ming-settings/usr/local/bin/ming-settings",
            "chmod +x release/staging/ming-diagnostic/usr/local/bin/ming-diagnostic-bundle",
        ):
            self.assertIn(marker, text)

    def test_package_builder_accepts_staged_component_directories(self):
        text = BUILDER.read_text(encoding="utf-8")
        self.assertIn('if [[ -d "${source_path}" ]]; then', text)
        self.assertIn('cp -a "${source_path}/."', text)


if __name__ == "__main__":
    unittest.main()
