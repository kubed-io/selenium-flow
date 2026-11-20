"""The embedded skill: how to use this server well, shipped inside it.

``plugin/skills/selenium-flow/`` is package data, so the guidance ships with the
version of the server it describes. FastMCP's ``SkillProvider`` serves it at
``skill://<name>/SKILL.md``, the one row ``resources/list`` shows. The
``_manifest`` and every supporting file are not listed but read by exact URI,
which is what FastMCP's client helpers (``list_skills``, ``download_skill``)
use. Each folder is an unlisted directory resource with ``_index.json`` and
``_index.md`` beside it (``directories``).

SkillProvider takes the skill's name from its folder, not the frontmatter:
renaming the folder renames the skill.

The Skills extension's ``skills/list`` and ``skills/get`` are in
``skill_extension``.
"""

from __future__ import annotations

import logging
import posixpath
from pathlib import Path
from urllib.parse import quote

from fastmcp.resources import Resource
from fastmcp.server.providers.skills.skill_provider import (
    SkillFileResource,
    SkillProvider,
)
from fastmcp.utilities.versions import VersionSpec

from . import directories, skill_extension

log = logging.getLogger(__name__)

PLUGIN_DIR = "plugin"
SKILLS_DIR = "skills"
SKILL_NAME = "selenium-flow"
ENTRY = "SKILL.md"


def plugin_folder(module: str, *parts: str) -> Path:
    """``plugin/<parts>`` beside ``module``, else at the root of a source checkout.

    ``plugin/`` is at the repo root, and pyproject maps it beside the modules of
    ``mcp/`` in a wheel. ``module`` is the caller's ``__file__``.
    """
    here = Path(module).parent
    packaged = here.joinpath(PLUGIN_DIR, *parts)
    if packaged.is_dir():
        return packaged
    # Up from mcp/: the package, kubed, the repo root.
    return here.parents[2].joinpath(PLUGIN_DIR, *parts)


def skill_path() -> Path:
    """The skill directory, installed or in a source checkout."""
    return plugin_folder(__file__, SKILLS_DIR, SKILL_NAME)


def _heading(path: Path) -> str | None:
    """A Markdown file's first ``# `` heading, if it has one."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


class EntryOnlySkillProvider(SkillProvider):
    """A SkillProvider whose listing names the main file and nothing else.

    ``_get_resource`` still answers the manifest and every supporting file by
    exact URI, and also each folder's two index files. The provider is the
    ``directories.Folders`` of its skill: a folder is every directory on disk
    under the skill, the skill's own included, so an empty one has no children.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        info = self.skill_info
        root = info.path
        found = {""} | {
            d.relative_to(root).as_posix()
            for d in root.rglob("*")
            if d.is_dir() and d.resolve().is_relative_to(root)
        }
        # folder path -> (its files, its subfolders), paths from the skill root.
        self._folders: dict[str, tuple[list[str], list[str]]] = {
            rel: ([], []) for rel in found
        }
        for rel in found - {""}:
            self._folders[posixpath.dirname(rel)][1].append(rel)
        for file in info.files:
            self._folders[posixpath.dirname(file.path)][0].append(file.path)
        self._by_uri = {self.folder_uri(rel): rel for rel in found}
        # Package data cannot change under a running process.
        self._titles = {
            file.path: title
            for file in info.files
            if file.path.endswith(".md") and file.path != info.main_file
            if (title := _heading(root / file.path))
        }

    def folder_uri(self, rel: str) -> str:
        """The URI of the folder at ``rel``, without a trailing slash."""
        root = self.skill_info.uri_prefix.rstrip("/")
        return f"{root}/{quote(rel, safe='/')}" if rel else root

    async def children(self, uri: str) -> list[dict] | None:
        rel = self._by_uri.get(uri)
        if rel is None:
            return None
        files, subfolders = self._folders[rel]
        name = self.skill_info.name
        rows = [
            directories.folder(
                self.folder_uri(sub), f"{name}/{sub}", f"A folder of the {name} skill"
            )
            for sub in subfolders
        ]
        for path in files:
            uri = f"{self.skill_info.uri_prefix}{quote(path, safe='/')}"
            rows.append(directories.wire(await self._get_resource(uri)))
        return sorted(rows, key=lambda row: row["uri"])

    async def _list_resources(self) -> list[Resource]:
        main = quote(self.skill_info.main_file, safe="/")
        entry = f"{self.skill_info.uri_prefix}{main}"
        return [r for r in await super()._list_resources() if str(r.uri) == entry]

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        if uri.startswith(self.skill_info.uri_prefix):
            index = await directories.index_resource(self, uri)
            if index is not None:
                return index
        resource = await super()._get_resource(uri, version)
        if isinstance(resource, SkillFileResource):
            title = self._titles.get(resource.file_path)
            if title:
                resource = resource.model_copy(update={"description": title})
        return resource


def load() -> EntryOnlySkillProvider | None:
    """The packaged skill as a provider, or None if it is not installed.

    Missing package data is logged, not fatal: the tools work without it.
    """
    path = skill_path()
    if not (path / ENTRY).is_file():
        log.warning("no packaged skill at %s", path)
        return None
    try:
        # "resources" reads every file by exact URI; the subclass trims the
        # listing to SKILL.md (PLG-14, ruling 26). Not "template": it serves
        # files as application/octet-stream and lists a template row.
        return EntryOnlySkillProvider(path, supporting_files="resources")
    except Exception as exc:  # noqa: BLE001 - bad package data must not stop the boot
        log.warning("packaged skill at %s could not be loaded: %s", path, exc)
        return None


def register(mcp, provider: EntryOnlySkillProvider) -> None:
    """Serve the skill's resources, and offer it through the Skills extension."""
    mcp.add_provider(provider)
    mcp.add_middleware(directories.NameTheIndex(provider))
    skill_extension.register(mcp, provider)
