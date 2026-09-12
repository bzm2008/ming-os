#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path


REPOSITORY = "bzm2008/ming-os"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--public-key-fingerprint", required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    applications = []
    descriptions = {
        "ming-store": ("Ming 应用商店", "GPL-3.0-or-later"),
        "ming-settings": ("Ming 设置中心", "GPL-3.0-or-later"),
        "ming-diagnostic": ("Ming 诊断工具", "GPL-3.0-or-later"),
    }
    for package, (name, license_name) in descriptions.items():
        artifact = args.input_dir / f"{package}_{args.version}_amd64.deb"
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        applications.append({
            "app_id": package,
            "name": name,
            "package_name": package,
            "version": args.version,
            "architectures": ["amd64"],
            "install_method": "deb",
            "dependencies": [],
            "license": license_name,
            "identity": {"type": "minisign", "signature": "external", "sha256": digest},
            "download_url": f"https://github.com/{REPOSITORY}/releases/download/{args.release_tag}/{artifact.name}",
            "sha256": digest,
            "release_tag": args.release_tag,
            "enabled": True,
            "protected": False,
        })
    document = {
        "schema": "ming.store.catalog.v1",
        "source": {
            "id": "ming-official",
            "name": "Ming 官方软件",
            "priority": 1,
            "trust": "minisign-required",
            "repository": REPOSITORY,
            "release_tag": args.release_tag,
            "public_key_fingerprint": args.public_key_fingerprint.upper(),
        },
        "applications": applications,
    }
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
