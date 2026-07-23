# Papyrus Release Staging

Place the verified Papyrus release asset in this directory before building a
release ISO. The binary payload is intentionally ignored by Git.

Preferred asset:

```text
Papyrus_1.0.0_amd64.deb
SHA256: 2A6ED8AB5AA65172E9624DB9B05FF14208814DD2381E8D27E05197266088D4EE
```

Fallback asset:

```text
Papyrus_1.0.0_amd64.AppImage
SHA256: 8B86F8CB1F9E6E39F0A3FEF9E7B36C57EB8700F7899AD4FEBD8344D0D05531B4
```

The build script and `04_papyrus.sh` independently validate the expected
filename and SHA-256. A release build stops when no valid payload is staged.
