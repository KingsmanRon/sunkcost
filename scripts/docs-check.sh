#!/usr/bin/env bash
# G8 docs-check (ARCHITECTURE.md §13, S6):
#   1. every relative link in the repository's Markdown files points at a file that exists;
#   2. the README quickstart (the block between the docs-check markers) runs end to end in a clean
#      container that has only the documented prerequisites: Node.js 22 with Corepack, uv and make.
#
#   make docs-check
#
# The container gets a copy of the working tree (tracked and unignored files only), so nothing
# from this machine's caches or virtual environments leaks in. Behind a TLS-intercepting proxy,
# set DOCS_CHECK_CA to its CA bundle and HTTPS_PROXY to the proxy.
set -euo pipefail

cd "$(dirname "$0")/.."
IMAGE="node:22.22.2-bookworm@sha256:62e4daa6819762bbd3072af77cc282ab72c631c4aed30dd7980192babaf385b3"
UV_VERSION=0.12.18
UV_SHA256=89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "== Markdown links"
python3 - <<'EOF'
import re, subprocess, sys
from pathlib import Path

files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "*.md"],
                       capture_output=True, text=True, check=True).stdout.split()
link = re.compile(r"\]\(([^)\s]+)\)")
broken = []
for name in files:
    path = Path(name)
    text = path.read_text("utf-8")
    text = re.sub(r"```.*?```", "", text, flags=re.S)  # links inside code blocks are examples
    for target in link.findall(text):
        if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        rel = target.split("#", 1)[0]
        if rel and not (path.parent / rel).exists():
            broken.append(f"{name}: {target}")
print(f"{len(files)} Markdown files checked")
if broken:
    print("broken links:\n  " + "\n  ".join(broken))
    sys.exit(1)
EOF

echo "== README quickstart in a clean container"
quickstart=$(awk '/<!-- docs-check:start -->/{on=1; next} /<!-- docs-check:end -->/{on=0} on' README.md \
  | sed -n '/^```sh$/,/^```$/p' | grep -v '^```' | sed 's/[[:space:]]*#.*$//' | grep -v '^[[:space:]]*$')
if [ -z "$quickstart" ]; then echo "no quickstart block between the docs-check markers in README.md"; exit 1; fi
printf 'commands:\n%s\n' "$quickstart"

git ls-files -co --exclude-standard -z | tar --null -T - -cf "$TMP/tree.tar"
curl -sSfL -o "$TMP/uv.tar.gz" \
  "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
echo "${UV_SHA256}  $TMP/uv.tar.gz" | sha256sum --check --strict --quiet
printf '%s\n' "$quickstart" >"$TMP/quickstart.sh"

proxy=()
if [ -n "${DOCS_CHECK_CA:-}" ]; then
  cp "$DOCS_CHECK_CA" "$TMP/extra-ca.crt"
  proxy=(-e HTTPS_PROXY -e HTTP_PROXY -e NO_PROXY -e https_proxy -e http_proxy -e no_proxy)
fi

start=$(date +%s)
docker run --rm --network host "${proxy[@]}" -v "$TMP:/in:ro" "$IMAGE" bash -euo pipefail -c '
  if [ -f /in/extra-ca.crt ]; then
    cp /in/extra-ca.crt /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates >/dev/null
    export NODE_EXTRA_CA_CERTS=/in/extra-ca.crt UV_SYSTEM_CERTS=1
  fi
  mkdir -p /opt/uv /work && tar -xzf /in/uv.tar.gz -C /opt/uv --strip-components=1
  export PATH=/opt/uv:$PATH COREPACK_ENABLE_DOWNLOAD_PROMPT=0
  corepack enable
  cd /work && tar -xf /in/tree.tar
  bash -euo pipefail -x /in/quickstart.sh
'
elapsed=$(( $(date +%s) - start ))
echo "docs-check: PASS (quickstart in a clean container: ${elapsed} s; S6 budget 1800 s)"
[ "$elapsed" -lt 1800 ]
