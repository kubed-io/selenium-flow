# The image is a wrapper around the wheel. No browser here — the browser lives
# on the Grid, which is the whole point; this only speaks WebDriver to it.
#
# Two stages, and the thing that passes between them is a VIRTUALENV.
#
# A venv inside a container looks like ceremony — the container is already an
# isolated box with one Python and one project in it. It is not here for
# isolation. It is here for RELOCATION: it makes the whole installed program
# one directory with a layout that does not depend on how the base image's
# Python was packaged, so `COPY --from` can move it in a single instruction.
#
# The alternative is `pip install --prefix=/install` and copying that onto
# /usr/local, which needs no venv and no PATH. It works on these images, but
# where it lands is decided by the interpreter's sysconfig scheme: a
# Debian-packaged Python writes <prefix>/local/lib/pythonX/dist-packages, and a
# python.org one writes <prefix>/lib/pythonX/site-packages. The official
# python:X images are the second, so it would work — until a base image change
# makes it the first, silently. The venv has no such variance. It also keeps
# pip able to SEE the dependency layer, which is what lets the project install
# below be an ordinary one instead of `--no-deps`.
#
# That is the part this used to get wrong. The builder installed the runtime
# dependencies to build a wheel that does not need them, and then the runner
# installed them ALL OVER AGAIN from that wheel. Two full dependency installs,
# and arm64 runs under emulation where the same install costs 222s instead of
# 21s. Copying the venv is what removes the second one.
#
# Dependencies are installed before the source, so that layer is keyed on
# pyproject.toml rather than on every commit — .git is in the build context for
# setuptools_scm, so `COPY . .` in front of the install invalidated it even for
# a docs-only push. That only pays off with a layer cache, and the `type=gha`
# one configured in docker-compose.yaml had never worked: buildx is run from a
# shell step, which the runner does not give ACTIONS_RUNTIME_TOKEN. Fixed in
# kubed-io/actions' build-image, so every repo calling it got the cache it had
# been configuring all along.
#
# Measured end to end, multi-arch: 9m30 before, 4m15 once the duplicate install
# was gone, and ~2m20 once the cache actually hit — 139s and 145s over two
# runs, both with 9 CACHED layers and nothing reinstalled. The two effects are
# independent, and the first needs no cache at all, which is why they are
# described separately.
ARG PY_VERSION=3.14

# ---- builder: the FAT image, because nothing in it ships.
#      python:${PY_VERSION} already carries git — which setuptools_scm needs to
#      resolve the version — and a toolchain for any dependency that has no
#      wheel for the target architecture. Using -slim here would mean an
#      apt-get to put git back.
FROM python:${PY_VERSION} AS builder

WORKDIR /app

# Everything installs in here, and this is what the runner receives.
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# The only two files that decide the dependency set. Keeping the source out of
# this layer is what keeps a UI edit from reinstalling selenium.
COPY pyproject.toml ./
COPY scripts/requirements.py ./scripts/

RUN <<'SHELL'
set -eu
# Read out of pyproject.toml rather than restated here: a second copy is a
# second thing to keep in step, and the way that fails is an image built
# against dependencies nobody declared. [redis] is baked in so that turning on
# shared saved sessions is a matter of setting REDIS_URL, not a different image.
pip install --no-cache-dir --upgrade pip
# The reader needs a TOML parser, and tomllib is 3.11+. PY_VERSION is an ARG
# and the project supports 3.10, so the marker supplies the backport there and
# installs nothing anywhere else — the same marker pyproject.toml's own [test]
# extra uses.
pip install --no-cache-dir "tomli; python_version < '3.11'"
python scripts/requirements.py runtime --extra redis > /tmp/requirements.txt
pip install --no-cache-dir -r /tmp/requirements.txt
SHELL

# Then our own code, which changes on every commit and installs in seconds.
COPY . .

RUN <<'SHELL'
set -eu
git config --global --add safe.directory /app
# An ordinary install, extra and all: pip reports every dependency already
# satisfied by the layer above and installs only this project. It self-heals if
# the two ever drift, which `--no-deps` would instead ship as an ImportError —
# and `--no-deps` silently ignores [redis] as well.
pip install --no-cache-dir .[redis]
# pip is a build tool, and /opt/venv is copied into the runner WHOLE — so
# leaving it here ships it, at whatever version happened to be latest on the
# day, into a production image that has no use for it. Removing it is also what
# keeps .hadolint.yaml's waiver true: the unpinned pip never reaches the image.
pip uninstall --yes pip
SHELL

# ---- runner: slim, and it receives one directory.
#      No git, no toolchain, no source, and no second dependency install.
FROM python:${PY_VERSION}-slim AS runner

COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# Copied to the SAME path it was created at, which is the one rule. A venv is
# this project's node_modules — one self-contained directory you move across
# and call it done — except that node_modules is relocatable and a venv is not:
# it records its own absolute path in pyvenv.cfg and in every console script's
# shebang. Land it anywhere else and it points at an interpreter that is not
# there.
#
# The venv is built against python:${PY_VERSION} and run on its -slim variant:
# same Debian, same interpreter at the same path, so the symlinks and
# pyvenv.cfg still resolve. That is an assumption worth failing the BUILD over
# rather than a running pod, so it is checked here — this imports the whole
# dependency tree, which is what would break if a wheel needed a shared library
# that only the fat image has.
RUN python -c "from kubed.selenium_flow.server import SeleniumMCP"

ENV TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8000 \
    ROUTE_PREFIX=/browser

EXPOSE 8000
# Numeric UID, not the name: with runAsNonRoot set, the kubelet cannot verify a
# non-numeric USER and refuses to start the container.
USER 65534

ENTRYPOINT ["selenium-flow"]
