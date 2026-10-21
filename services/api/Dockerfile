# syntax=docker/dockerfile:1
# Sunkcost API (ARCHITECTURE.md §11.2). Build from the repository root:
#   docker build -f services/api/Dockerfile .
# Behind a TLS-intercepting proxy, pass its CA as a build secret (never stored in a layer):
#   docker build --secret id=extra_ca,src=/path/to/ca.crt -f services/api/Dockerfile .
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir uv==0.12.18 && useradd --create-home --uid 10001 sunkcost
WORKDIR /app

# The workspace manifest and lockfile pin every dependency; only the API's are installed.
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services services
COPY adapters adapters
COPY bench/pyproject.toml bench/pyproject.toml
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package sunkcost-api --no-editable

USER sunkcost
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["/app/.venv/bin/python", "-m", "sunkcost_api.app"]
