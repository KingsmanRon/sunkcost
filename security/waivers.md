# Security waivers

A finding from `make security` (G8) that is accepted rather than fixed is listed here, with why it
does not apply or cannot be fixed yet, who accepted it, and when the waiver expires. The scanner's
own ignore file (`.trivyignore`) carries the same IDs; pip-audit reads the IDs from this file.

Format, one line per finding:

- **<scanner>** `<ID>`: <package and version>. <why>. Accepted by <name> on <date>, expires <date>.

## Policy: unfixed operating-system packages (proposed; needs owner acceptance)

Trivy scans the images with `--ignore-unfixed`. A HIGH or CRITICAL finding in a Debian package
of the pinned `python:3.12-slim` base that Debian has **no fixed version** for cannot be fixed by
this repository; it is listed here instead, and is re-checked at every release. Dependabot moves
the base image digest when Debian ships fixes, and the scan then requires them. Anything with a
fix available fails the gate.

Unfixed findings in both service images on 2026-10-21 (Debian 13.7):

| ID | Severity | Packages | Debian status |
|---|---|---|---|
| `CVE-2025-69720` | HIGH | libncursesw6, libtinfo6, ncurses-base, ncurses-bin | affected |
| `CVE-2026-16742` | HIGH | libsystemd0, libudev1 | affected |
| `CVE-2026-54369` | HIGH | libacl1 | affected |
| `CVE-2026-76642` | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | affected |
| `CVE-2026-78408` | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | affected |
| `CVE-2026-78409` | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | affected |
| `CVE-2026-78410` | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | affected |
| `CVE-2026-9538` | HIGH | perl-base | fix deferred |

None of these packages is used by the services at runtime except through the base system; the
containers run as an unprivileged user with no shell entry point. Proposed by the implementing
engineer on 2026-10-21; **not yet accepted** by the owner. Expires at the next release.

## Waivers

- **semgrep** `package_managers.uv.uv-missing-dependency-cooldown.uv-missing-dependency-cooldown`:
  `pyproject.toml` has no `exclude-newer = "7 days"` yet. The dependency floors were set this
  week (for example `fastapi>=0.156`), so a 7-day cooldown cannot resolve until they age; the
  other ecosystems already have one (pnpm `minimumReleaseAge: 10080`, Dependabot
  `cooldown: 7`). Proposed by the implementing engineer on 2026-10-21, not yet accepted by the
  owner; expires 2026-10-28, when the cooldown should be added and the lock re-resolved.

Suppressed inline (`# nosemgrep` on the line, with the reason in the comment above it):

- `bench/sunkcost_bench/common.py`, SHA-1: git object ids are SHA-1 by definition; the hash
  matches downloads to pinned upstream blobs and signs nothing.
- `bench/sunkcost_bench/common.py`, dynamic `urlopen`: the URL is checked to be https and comes
  from the fixed list in `bench/DATASETS.md`.
- `packages/sdk-python/sunkcost/prices.py`, `importlib.resources` on Python 3.7: every supported
  Python (>= 3.10) has it.
- `.github/workflows/staging.yml`, checkout in a `workflow_run`: the jobs run only for a push to
  `main` of this repository (the `migrate` job's condition), never for pull-request code.
