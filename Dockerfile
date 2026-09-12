# The image is a wrapper around the wheel. No browser here — the browser lives
# on the Grid, which is the whole point; this only speaks WebDriver to it.
#
# Three stages, split so the expensive one is cached on the one file that
# decides it. Installing the third-party dependency set IS most of this build,
# and roughly twice over, because linux/arm64 is built under emulation. It used
# to sit behind `COPY . .`, so every source edit paid for it again — and in
# practice *every commit* did, because .git is in the context (setuptools_scm
# needs it) and .git changes even for a docs-only push. The GitHub Actions
# cache was configured the whole time and could never hit.
#
# Now the dependency stage sees pyproject.toml and nothing else, so its cache
# key is that file's digest: edit the admin UI, and it is a cache hit.
ARG PY_VERSION=3.14

# ---- deps: the third-party install, and NOTHING that changes with the source.
FROM python:${PY_VERSION}-slim AS deps

WORKDIR /app

COPY pyproject.toml ./

RUN <<'SHELL'
set -eu
python - <<'PY' > /tmp/requirements.txt
import sys
try:
    import tomllib
except ModuleNotFoundError:  # 3.10 has no tomllib, and nothing builds on 3.10
    sys.exit("PY_VERSION must be 3.11 or newer to build this image")
project = tomllib.load(open("pyproject.toml", "rb"))["project"]
# Read rather than restated: pyproject.toml is where the pins live, and a list
# repeated in a Dockerfile is a second one to keep in step. [redis] is baked in
# so that turning on shared saved sessions is a matter of setting REDIS_URL,
# not of building a different image.
extras = project.get("optional-dependencies", {})
print(*project["dependencies"], *extras["redis"], sep="\n")
PY
pip install --no-cache-dir --upgrade pip
pip install --no-cache-dir -r /tmp/requirements.txt
# Nothing of the source belongs in the runtime image, and this stage IS the
# runtime image.
rm -f pyproject.toml /tmp/requirements.txt
SHELL

# ---- wheel: our own code, and the build backend that turns it into one.
FROM python:${PY_VERSION}-slim AS wheel

WORKDIR /app

# Before the source, so the backend install is cached on pyproject.toml too.
COPY pyproject.toml ./

RUN <<'SHELL'
set -eu
# setuptools_scm resolves the version from git history, so git has to be here
# and .git has to survive .dockerignore. It never reaches the runtime image.
apt-get update
apt-get install -y --no-install-recommends git
rm -rf /var/lib/apt/lists/*
python - <<'PY' > /tmp/build-requirements.txt
import tomllib
# [build-system].requires already names and pins the backend — it is the one
# list that has to be right for `pip install .` to work anywhere. `build` is
# the frontend that reads it and is deliberately not part of it.
print(*tomllib.load(open("pyproject.toml", "rb"))["build-system"]["requires"], sep="\n")
PY
pip install --no-cache-dir --upgrade pip
pip install --no-cache-dir build -r /tmp/build-requirements.txt
SHELL

COPY . .

RUN <<'SHELL'
set -eu
git config --global --add safe.directory /app
# --wheel only: the runner installs the wheel, and the sdist beside it was
# built and thrown away on every build.
python -m build --wheel --no-isolation
SHELL

# ---- runner: the dependency layer, plus our wheel and nothing else.
FROM deps AS runner

COPY --from=wheel /app/dist ./dist/

RUN <<'SHELL'
set -eu
# --no-deps because everything the wheel needs is already in this layer,
# installed from the same pyproject.toml the wheel was built from. `pip check`
# is what makes that safe to assert: if the two ever disagreed, this fails the
# build instead of shipping an ImportError to a running pod.
pip install --no-cache-dir --no-deps ./dist/*.whl
pip check
rm -rf ./dist
SHELL

ENV TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8000 \
    ROUTE_PREFIX=/browser

EXPOSE 8000
# Numeric UID, not the name: with runAsNonRoot set, the kubelet cannot verify a
# non-numeric USER and refuses to start the container.
USER 65534

ENTRYPOINT ["selenium-flow"]
