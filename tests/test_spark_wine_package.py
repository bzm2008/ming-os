import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "spark_wine_package", ROOT / "assets/ming-spark-wine-package.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SparkWinePackageTests(unittest.TestCase):
    def _manifest(self, root, include_signature=True):
        artifact = root / "demo.exe"
        artifact.write_bytes(b"MZ signed")
        signature = root / "demo.exe.minisig"
        signature.write_text("signature", encoding="utf-8")
        payload = {
            "schema": "ming.spark.wine.v1",
            "app_id": "demo",
            "version": "1.0.0",
            "artifact": artifact.name,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "architecture": "win64",
            "launch_file": "Program Files/Demo/demo.exe",
            "dependencies": [],
        }
        if include_signature:
            payload["signature"] = signature.name
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return manifest

    def test_signed_manifest_delegates_only_after_integrity_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            key = root / "spark-wine.pub"
            key.write_text("public key", encoding="utf-8")
            calls = []
            installer = MODULE.SparkWinePackageInstaller(
                trusted_key=key,
                verifier=lambda artifact, signature, trusted_key: True,
                runner=lambda command, **kwargs: calls.append(tuple(command)) or (0, "ok", ""),
            )
            result = installer.install(self._manifest(root))
            self.assertTrue(result["ok"])
            self.assertIn("ming-toolbox", calls[0][0])
            self.assertEqual("--install-windows", calls[0][1])

    def test_missing_signature_or_trusted_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self._manifest(root, include_signature=False)
            result = MODULE.SparkWinePackageInstaller(trusted_key=root / "missing.pub").install(manifest)
            self.assertFalse(result["ok"])
            self.assertEqual("manifest_invalid", result["state"])


if __name__ == "__main__":
    unittest.main()
