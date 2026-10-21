#!/usr/bin/env bash
# G8 security (ARCHITECTURE.md §9, §13): gitleaks, Semgrep, pip-audit, pnpm audit and Trivy.
#
#   make security
#
# Every scanner reports PASS, FAIL or NOT RUN (with the reason); a scanner that cannot run is never
# counted as passed. Exit status: 0 all passed, 1 a scanner failed, 2 nothing failed but one did
# not run. Accepted findings are waived only in security/waivers.md (with a reason and an expiry)
# and the tool's own ignore file that the waiver points to.
set -uo pipefail

cd "$(dirname "$0")/.."
SEMGREP_VERSION=1.179.0
PIP_AUDIT_VERSION=2.10.1
TRIVY=${TRIVY:-trivy}
GITLEAKS=${GITLEAKS:-gitleaks}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
declare -a RESULTS
FAILED=0
SKIPPED=0

record() { # name status detail
  RESULTS+=("$(printf '%-12s %-8s %s' "$1" "$2" "$3")")
  case "$2" in
    FAIL) FAILED=1 ;;
    "NOT RUN") SKIPPED=1 ;;
  esac
}

run() { # name detail command...
  local name=$1 detail=$2
  shift 2
  echo "== $name"
  if "$@"; then record "$name" PASS "$detail"; else record "$name" FAIL "$detail (see output above)"; fi
}

# gitleaks: the whole git history.
if command -v "$GITLEAKS" >/dev/null 2>&1; then
  run gitleaks "full git history" "$GITLEAKS" git --config .gitleaks.toml --redact --no-banner .
else
  record gitleaks "NOT RUN" "gitleaks is not installed (CI installs a checksum-verified binary)"
fi

# Semgrep: the registry's default ruleset, which needs semgrep.dev.
if curl -sSf -o /dev/null --max-time 20 https://semgrep.dev/c/p/default 2>/dev/null; then
  run semgrep "p/default, errors fail" uvx --from "semgrep==$SEMGREP_VERSION" semgrep scan \
    --config p/default --error --metrics=off --quiet \
    --exclude node_modules --exclude .venv --exclude .next --exclude dist --exclude bench/results .
else
  record semgrep "NOT RUN" "semgrep.dev (rule registry) is not reachable from this network"
fi

# pip-audit: every locked Python package, against the PyPI advisory database.
uv export --frozen --all-packages --no-hashes --no-emit-workspace --no-header -o "$TMP/requirements.txt" >/dev/null 2>&1
if [ -s "$TMP/requirements.txt" ]; then
  ignores=()
  while read -r id; do ignores+=(--ignore-vuln "$id"); done < <(grep -oE '^- \*\*pip-audit\*\* `[^`]+`' security/waivers.md | grep -oE '`[^`]+`' | tr -d '`')
  run pip-audit "$(grep -c "==" "$TMP/requirements.txt") locked packages" \
    uvx --from "pip-audit==$PIP_AUDIT_VERSION" pip-audit --disable-pip --no-deps --progress-spinner off \
    -r "$TMP/requirements.txt" "${ignores[@]}"
else
  record pip-audit FAIL "could not export uv.lock"
fi

# pnpm audit: every package in pnpm-lock.yaml, production and development.
run "pnpm audit" "all workspace dependencies" pnpm audit --audit-level low

# Trivy: dependency vulnerabilities, secrets and misconfigurations in the tree, then the images.
if command -v "$TRIVY" >/dev/null 2>&1; then
  run "trivy fs" "vuln, secret, misconfig; HIGH and CRITICAL" "$TRIVY" fs --quiet --exit-code 1 \
    --severity HIGH,CRITICAL --scanners vuln,secret,misconfig --ignorefile .trivyignore \
    --skip-dirs node_modules --skip-dirs .venv --skip-dirs apps/console/.next --skip-dirs bench/results .
  if docker info >/dev/null 2>&1; then
    for svc in api worker; do
      build=(docker build -q -f "services/$svc/Dockerfile" -t "sunkcost-$svc:security" .)
      [ -n "${DOCKER_BUILD_ARGS:-}" ] && read -ra extra <<<"$DOCKER_BUILD_ARGS" && build=(docker build "${extra[@]}" -q -f "services/$svc/Dockerfile" -t "sunkcost-$svc:security" .)
      if "${build[@]}" >/dev/null; then
        # Unfixed OS packages of the pinned base are listed in security/waivers.md, not ignored silently.
        run "trivy $svc" "image; HIGH and CRITICAL with a fix" "$TRIVY" image --quiet --exit-code 1 \
          --severity HIGH,CRITICAL --ignore-unfixed --ignorefile .trivyignore "sunkcost-$svc:security"
      else
        record "trivy $svc" FAIL "the image did not build"
      fi
    done
  else
    record "trivy images" "NOT RUN" "Docker is not available"
  fi
else
  record trivy "NOT RUN" "trivy is not installed (CI installs a checksum-verified binary)"
fi

echo
echo "G8 security summary"
printf '%s\n' "${RESULTS[@]}"
if [ "$FAILED" -ne 0 ]; then echo "G8 security: FAIL"; exit 1; fi
if [ "$SKIPPED" -ne 0 ]; then echo "G8 security: NOT RUN (some scanners could not run)"; exit 2; fi
echo "G8 security: PASS"
