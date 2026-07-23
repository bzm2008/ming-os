# Papyrus integration

The image build treats Papyrus as an optional local application. To include
it, place a verified `Papyrus_*.deb` or `Papyrus_*.AppImage` in
`/tmp/ming-build/papyrus-assets` (or set `PAPYRUS_ASSET` to one local file)
before the module runs. Debian assets must report package name `papyrus` via
`dpkg-deb --info`; AppImages must be ELF files. No network installer is used.

When an asset is present, module 04 extracts it under `/opt/papyrus`, creates
`/usr/bin/papyrus`, and installs `/usr/share/applications/papyrus.desktop`.
The launcher keeps the normal XDG configuration and data homes, so Papyrus
user data is not removed during application upgrades. If no verified asset is
available, the module exits successfully without creating a placeholder
executable or desktop entry.
