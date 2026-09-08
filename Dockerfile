ARG PY_VERSION=3.13

# Stage 1: source + build tooling. setuptools_scm reads .git for the version,
# so git must be installed and .git must survive .dockerignore.
FROM python:${PY_VERSION} AS setup

WORKDIR /app

COPY . .

RUN <<SHELL
apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
git config --global --add safe.directory /app
pip install --no-cache-dir --upgrade pip
pip install --no-cache-dir .[build]
SHELL

FROM setup AS builder
RUN python -m build --no-isolation

# Final stage: just the wheel. No browser here — the browser lives on the Grid,
# which is the whole point; this image only speaks WebDriver to it.
FROM python:${PY_VERSION}-slim AS runner

WORKDIR /app

COPY --from=builder /app/dist ./dist/
RUN pip install --no-cache-dir ./dist/*.whl && rm -rf ./dist

ENV TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8000 \
    ROUTE_PREFIX=/browser

EXPOSE 8000
# Numeric UID, not the name: with runAsNonRoot set, the kubelet cannot verify a
# non-numeric USER and refuses to start the container.
USER 65534

ENTRYPOINT ["selenium-flow"]
