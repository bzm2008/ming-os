#!/usr/bin/env python3
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path


def main():
    output_dir = Path.home() / "Desktop"
    if not output_dir.is_dir():
        output_dir = Path(tempfile.gettempdir())
    target = output_dir / "Ming-OS-diagnostic-bundle.tar.gz"
    with tempfile.TemporaryDirectory(prefix="ming-diagnostic-") as directory:
        root = Path(directory)
        report = {"schema": "ming.diagnostic.v1", "commands": {}}
        for name, command in (("uname", ["uname", "-a"]), ("memory", ["free", "-h"]), ("failed", ["systemctl", "--failed", "--no-pager"])):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
                report["commands"][name] = {"returncode": result.returncode, "stdout": result.stdout[-20000:], "stderr": result.stderr[-4000:]}
            except (OSError, subprocess.SubprocessError) as error:
                report["commands"][name] = {"error": str(error)}
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with tarfile.open(target, "w:gz") as archive:
            archive.add(root / "report.json", arcname="report.json")
    print(target)


if __name__ == "__main__":
    main()
