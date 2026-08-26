#!/usr/bin/env python3
"""Verify signed Spark Wine manifests before handing artifacts to Ming Toolbox."""

import hashlib
import argparse
import json
import os
import pathlib
import subprocess
import re


class SparkWinePackageInstaller:
    def __init__(self, trusted_key=None, verifier=None, runner=None):
        self.trusted_key = pathlib.Path(
            trusted_key or "/usr/share/ming-os/keys/spark-wine.pub"
        )
        self.verifier = verifier or self._verify_minisign
        self.runner = runner or self._run

    @staticmethod
    def _run(command, **kwargs):
        completed = subprocess.run(list(command), shell=False, check=False, **kwargs)
        return completed.returncode, completed.stdout, completed.stderr

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with pathlib.Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _verify_minisign(artifact, signature, trusted_key):
        if not shutil_which("minisign"):
            return False
        completed = subprocess.run(
            ["minisign", "-Vm", str(artifact), "-x", str(signature), "-P", trusted_key.read_text(encoding="utf-8").strip()],
            capture_output=True, text=True, timeout=30, check=False, shell=False,
        )
        return completed.returncode == 0

    def validate(self, manifest_path):
        manifest_path = pathlib.Path(manifest_path).resolve(strict=True)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "Spark Wine 清单不是有效 JSON。"
        required = {"schema", "app_id", "version", "artifact", "sha256", "signature", "architecture", "launch_file", "dependencies"}
        if not isinstance(data, dict) or data.get("schema") != "ming.spark.wine.v1" or not required.issubset(data):
            return None, "Spark Wine 清单字段不完整或 schema 不受支持。"
        if not isinstance(data.get("app_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", data["app_id"]):
            return None, "Spark Wine 清单 app_id 无效。"
        if not isinstance(data.get("version"), str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?", data["version"]):
            return None, "Spark Wine 清单版本无效。"
        if data["architecture"] not in {"win32", "win64"} or not isinstance(data["dependencies"], list):
            return None, "Spark Wine 清单的架构或依赖字段无效。"
        if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", item) for item in data["dependencies"]):
            return None, "Spark Wine 清单依赖名称无效。"
        launch_file = data.get("launch_file")
        if not isinstance(launch_file, str) or not launch_file or pathlib.PurePosixPath(launch_file).is_absolute() or ".." in pathlib.PurePosixPath(launch_file).parts or not launch_file.lower().endswith(".exe"):
            return None, "Spark Wine 清单启动文件路径无效。"
        artifact = (manifest_path.parent / str(data["artifact"])).resolve()
        signature = (manifest_path.parent / str(data["signature"])).resolve()
        if manifest_path.parent not in artifact.parents or manifest_path.parent not in signature.parents:
            return None, "Spark Wine 清单引用了目录外文件。"
        if artifact.suffix.lower() not in {".exe", ".msi"} or not artifact.is_file() or not signature.is_file():
            return None, "Spark Wine 清单的安装文件或签名缺失。"
        if not self.trusted_key.is_file():
            return None, "系统缺少 Spark Wine 受信任公钥。"
        if self._sha256(artifact).casefold() != str(data["sha256"]).casefold():
            return None, "Spark Wine 安装文件 SHA256 校验失败。"
        if not self.verifier(artifact, signature, self.trusted_key):
            return None, "Spark Wine Minisign 签名校验失败。"
        return {"manifest": data, "artifact": artifact}, ""

    def install(self, manifest_path):
        validated, error = self.validate(manifest_path)
        if error:
            return {"ok": False, "state": "manifest_invalid", "error": error}
        artifact = validated["artifact"]
        result = self.runner(("/usr/local/bin/ming-toolbox", "--install-windows", str(artifact)), timeout=900, capture_output=True, text=True)
        rc, output, stderr = result
        if rc != 0:
            return {"ok": False, "state": "toolbox_install_failed", "error": (stderr or output or "Ming 工具箱安装失败。")[:1000]}
        try:
            toolbox_result = json.loads(output or "")
        except (TypeError, ValueError):
            toolbox_result = None
        if isinstance(toolbox_result, dict) and not toolbox_result.get("ok", False):
            return {"ok": False, "state": toolbox_result.get("state", "toolbox_install_failed"), "error": toolbox_result.get("error", "Ming 工具箱未确认安装结果。")}
        return {"ok": True, "state": "installed", "artifact": str(artifact), "output": (output or "")[:1000]}


def shutil_which(command):
    from shutil import which
    return which(command)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-spark-wine-package")
    parser.add_argument("manifest")
    args = parser.parse_args(argv)
    result = SparkWinePackageInstaller().install(args.manifest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
