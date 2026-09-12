# The image is a wrapper around the wheel. No browser here — the browser lives
# on the Grid, which is the whole point; this only speaks WebDriver to it.
#
# Three stages, and `runner` is the whole point of the shape: it is `FROM
# deps`, so it carries the runtime dependencies and NOTHING else — no git,
# no build backend, no source. Those live in `wheel`, which is also FROM
# deps and which nothing inherits from.
#
# The split exists because installing the third-party dependency set IS this
# build, and it used to be done TWICE: the build stage installed the runtime
# dependencies (and twine) in order to build a wheel that needs neither, and
# the runner then installed them all again. Removing that duplication is what
# takes the build from ~9m30 to ~4m15.
#
# What is left is linux/arm64 under emulation. The identical dependency install
# measures 21s on amd64 and 222s on arm64; native arm64 runners, not this file,
# are what would close that.
#
# The split is also what a layer cache needs in order to be useful, since the
# install no longer sits behind `COPY . .` — which every commit invalidated,
# .git being in the context for setuptools_scm. That half is done. It buys
# nothing across runs YET: `docker buildx bake` is invoked from a plain shell
# step with no ACTIONS_RUNTIME_TOKEN in its environment, so the `type=gha`
# cache configured in docker-compose.yaml is silently a no-op — measured, not
# assumed: two identical builds in a row, zero CACHED layers, no cache manifest
# imported or exported. Wiring it up is a change to kubed-io/actions.
ARG PY_VERSION=3.14

# ---- deps: the third-party install, and NOTHING that changes with the source.
#      Everything below is FROM this, so anything added here ships.
FROM python:${PY_VERSION}-slim AS deps

WORKDIR /app

# The only two files that decide the dependency set, so this layer's cache key
# is their digest and a source edit cannot invalidate it. See the note above
# about what still has to happen for that to mean a cache HIT.
COPY pyproject.toml ./
COPY scripts/requirements.py ./scripts/

RUN <<'SHELL'
set -eu
# Read out of pyproject.toml rather than restated here: a second copy is a
# second thing to keep in step, and the way that fails is an image built
# against dependencies nobody declared. [redis] is baked in so that turning on
# shared saved sessions is a matter of setting REDIS_URL, not of building a
# different image — and so the runner's install below finds it already there.
python scripts/requirements.py runtime --extra redis > /tmp/requirements.txt
pip install --no-cache-dir --upgrade pip
pip install --no-cache-dir -r /tmp/requirements.txt
# This stage IS the runtime image, so the source it was configured from does
# not stay in it. The wheel stage copies back what it needs.
rm -rf scripts pyproject.toml /tmp/requirements.txt
SHELL

# ---- wheel: our own code, built with tools that must not reach the runner.
#      git is installed HERE and only here. Putting it in `deps` would ship it.
FROM deps AS wheel

# Before the source, so the backend install is cached on these two alone.
COPY pyproject.toml ./
COPY scripts/requirements.py ./scripts/

RUN <<'SHELL'
set -eu
# setuptools_scm resolves the version from git history, so git has to be here
# and .git has to survive .dockerignore.
apt-get update
apt-get install -y --no-install-recommends git
rm -rf /var/lib/apt/lists/*
# `build` is the frontend; what it needs to run is [build-system].requires,
# which pyproject.toml already names and pins.
python scripts/requirements.py build > /tmp/build-requirements.txt
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

# ---- runner: the dependency layer, plus our wheel. No git, no build backend.
FROM deps AS runner

COPY --from=wheel /app/dist ./dist/

RUN <<'SHELL'
set -eu
# An ordinary install, not `--no-deps`. Everything it needs is already in the
# layer underneath, so pip reports each one already satisfied and installs only
# our wheel — and if the two ever drift it fixes that rather than shipping an
# ImportError. `--no-deps` would also silently ignore the [redis] extra.
pip install --no-cache-dir "$(echo ./dist/*.whl)[redis]"
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
