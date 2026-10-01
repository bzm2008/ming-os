# Repository Guidelines

## Project Structure

Ming OS is a Debian-based desktop image built from shell orchestration and Python tools. The root contains ISO entry points such as `build_onion_os.sh`, `rebuild_iso.sh`, `fast_build_iso.sh`, and `resume_build.sh`. Desktop, installer, OTA, hardware, and store utilities live in `assets/`; build-state helpers are under `scripts/`. Boot and installer configuration is kept in root-level files such as `grub.cfg`, `grub_optimized.cfg`, and `live-config.conf`. Regression and contract tests are in `tests/`. Keep generated ISOs and temporary state outside the repository (defaults use `/var/tmp/ming-os-build`).

## Build, Test, and Development Commands

- `sudo ./build_onion_os.sh --profile release` — perform a full release build.
- `sudo ./fast_build_iso.sh` — create a faster test ISO using the `fast-test` profile.
- `sudo ./resume_build.sh` — resume from the last validated checkpoint.
- `sudo ./build_onion_os.sh --fresh --from modules` — rebuild from a selected stage.
- `python3 -m pytest -q` — run the complete regression suite.
- `python3 -m pytest -q tests/test_release_gate.py` — run the release-gate checks only.

Builds require a Debian 13/Trixie-capable Linux host and root privileges. Review `--help` before changing profiles or checkpoints.

## Coding Style & Naming

Use Bash with `set -euo pipefail`, quoted variables, and small functions with explicit error handling. Use four-space indentation in Python, `snake_case` for functions and variables, and descriptive `ming_*` prefixes for project utilities. Preserve localized comments and strings. Keep shell entry points executable and run focused tests after changes.

## Testing Guidelines

Tests use `pytest` and are named `tests/test_*.py`. Add regression coverage for new installer, boot, desktop, OTA, security, or store behavior. Prefer contract tests over full ISO builds. Run the narrowest relevant test first, then the full suite before release work.

## Commits & Pull Requests

Recent history uses short imperative subjects with prefixes such as `feat:`, `fix:`, and `docs:` (for example, `fix: repair store provider refresh`). Keep commits focused. Pull requests should summarize the affected build path, list validation commands and results, call out root/network assumptions, and include screenshots or boot-test notes for visible changes. Link the relevant issue or release checklist when one exists.

## Security & Configuration

Do not commit passwords, private keys, downloaded credentials, or machine-specific paths. Treat mirror URLs, signing keys, OTA metadata, and release checksums as security-sensitive inputs. Use environment variables documented by the build scripts for local overrides, and verify generated artifacts with SHA256 before publishing.
