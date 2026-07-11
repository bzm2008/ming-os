# Ming GRUB Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Live and installed GRUB one reliable Ming visual language with a common black high-contrast fallback.

**Architecture:** Store one text-first theme under `assets/grub-theme/`, install it into the rootfs and ISO tree, and validate every referenced file. Menu content remains context-specific.

**Tech Stack:** GRUB 2 theme format, Bash build scripts, xorriso validation.

---

### Task 1: Theme Assets And Tests

**Files:**
- Create: `assets/grub-theme/theme.txt`
- Create: `tests/test_grub_contracts.py`

- [ ] Write failing tests proving the theme exists, uses readable colors, references only existing assets and has no viewport-dependent text overflow.
- [ ] Run `python -m unittest tests.test_grub_contracts -v`; expect missing-theme failures.
- [ ] Add a text-first dark theme with Ming green selection and no required bitmap background.
- [ ] Run theme tests.

### Task 2: Install One Theme In Both Boot Contexts

**Files:**
- Modify: `modules/01_base.sh`
- Modify: `build_onion_os.sh`
- Extend: `tests/test_grub_contracts.py`

- [ ] Add failing tests for identical theme content in rootfs and ISO and black fallback color directives.
- [ ] Install the theme into `/boot/grub/themes/ming/theme.txt` and `${ISO_DIR}/boot/grub/themes/ming/theme.txt`.
- [ ] Make ISO `grub.cfg` load the theme when graphics work and retain console input plus black text fallback.
- [ ] Keep installed GRUB pointing at the verified theme path.
- [ ] Run GRUB contract tests and `bash -n build_onion_os.sh modules/01_base.sh`.

### Task 3: Boot Validation

**Files:**
- Modify: `build_onion_os.sh`

- [ ] Validate the theme path, theme checksum, fallback colors and absence of Debian blue-theme references.
- [ ] After the later ISO build, run `xorriso -indev output/ming-os-26.3.2-home-amd64.iso -report_el_torito plain` and inspect BIOS/UEFI screenshots.

