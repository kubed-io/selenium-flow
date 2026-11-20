"""The embedded skill: how to use this server well, shipped inside it.

``plugin/skills/selenium-flow/`` is package data, so the guidance ships with the
version of the server it describes. FastMCP's ``fastmcp.skills`` serves it at
``skill://<name>/SKILL.md``, the one row ``resources/list`` shows. The
``_manifest`` and every other file are not listed but read by exact URI, which
is what FastMCP's client (``read_skill``) uses. Each folder is an unlisted
directory resource with ``_index.json`` and ``_index.md`` beside it
(``directories``).

The skill is served only when it is a valid Agent Skill, its folder named as
its frontmatter names it. The Skills extension's ``skills/list`` and
``skills/get`` are in ``skill_extension``.
"""

from __future__ import annotations

import logging
import posixpath
from pathlib import Path
from urllib.parse import quote

from fastmcp.skills.provider import SkillCatalogProvider, SkillFileResource
from kubed.mcp import Resource
from kubed.mcp.protocol import VersionSpec
from kubed.mcp.skills import Skill
from pydantic import ValidationError

from . import directories, skill_extension
from .extensions import AdvertiseLegacyExtensions

log = logging.getLogger(__name__)

PLUGIN_DIR = "plugin"
SKILLS_DIR = "skills"
SKILL_NAME = "selenium-flow"
ENTRY = "SKILL.md"
# FastMCP answers skill://<name>/_manifest with the skill's file listing.
MANIFEST = "_manifest"


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


def _heading(data: bytes) -> str | None:
    """A Markdown file's first ``# `` heading outside a code fence, if it has one.

    A file that is not UTF-8 has none, so it is a plain file of the skill
    rather than taking the skill down with it.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    fence = None
    for line in text.splitlines():
        marker = line.lstrip()[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
        elif fence is None and line.startswith("# "):
            return line[2:].strip() or None
    return None


def _all_folders(paths) -> set[str]:
    """Every folder the files at ``paths`` sit in, below the root."""
    return {
        "/".join(parts[:depth])
        for path in paths
        for parts in [path.split("/")[:-1]]
        for depth in range(1, len(parts) + 1)
    }


def _folders_of(paths) -> set[str]:
    """The skill's folders, the root ``""`` included.

    A folder with an index's name is none, and neither is any folder under it;
    nor is a ``_manifest`` at the root, whose URI reads as the manifest.
    """
    return {""} | {
        rel
        for rel in _all_folders(paths)
        if not any(directories.reserved(part) for part in rel.split("/"))
        and rel.split("/")[0] != MANIFEST
    }


def _without_reserved(skill: Skill) -> Skill:
    """The skill minus any file whose URI the server answers otherwise, each named.

    That is a file with an index's name, which reads as its folder's index, and
    a ``_manifest`` at the root, which reads as the manifest. FastMCP would find
    such a file unreadable as listed and drop the whole skill from
    ``skills/list``. A ``_manifest`` below the root is an ordinary file.

    A folder at such a URI is named too, though its files stay in the skill.
    """
    for rel in sorted(_all_folders(skill.files)):
        if directories.reserved(rel):
            log.warning(
                "skill %s: folder %s is not served, its parent's index "
                "answers that URI",
                skill.name,
                rel,
            )
        elif rel == MANIFEST:
            log.warning(
                "skill %s: folder %s is not served, the skill's manifest "
                "answers that URI",
                skill.name,
                rel,
            )
    reserved = [name for name in skill.files if directories.reserved(name)]
    for name in reserved:
        log.warning(
            "skill %s: %s is not served, its folder's index answers that URI",
            skill.name,
            name,
        )
    if MANIFEST in skill.files:
        reserved.append(MANIFEST)
        log.warning(
            "skill %s: %s is not served, the skill's manifest answers that URI",
            skill.name,
            MANIFEST,
        )
    if not reserved:
        return skill
    return Skill.from_files(
        {name: data for name, data in skill.files.items() if name not in reserved}
    )


class EntryOnlySkillProvider(SkillCatalogProvider):
    """One skill, whose listing names its main file and nothing else.

    ``_get_resource`` still answers the manifest and every other file by exact
    URI, and also each folder's two index files. The provider is the
    ``directories.Folders`` of its skill: a folder is every one the skill's
    files sit in, so it is the same for a skill built from code. A file whose
    URI the server answers otherwise is left out of the skill it serves.
    """

    def __init__(self, skill: Skill):
        skill = _without_reserved(skill)
        super().__init__([skill])
        self.skill = skill
        self.name = skill.name
        self._prefix = f"skill://{quote(skill.name, safe='')}/"
        self._file = f"File from {skill.name} skill"
        found = _folders_of(skill.files)
        # folder path -> (its files, its subfolders), paths from the skill root.
        self._folders: dict[str, tuple[list[str], list[str]]] = {
            rel: ([], []) for rel in found
        }
        for rel in found - {""}:
            self._folders[posixpath.dirname(rel)][1].append(rel)
        for path in skill.files:
            parent = posixpath.dirname(path)
            if parent in self._folders:  # else under a folder that is not served
                self._folders[parent][0].append(path)
        self._by_uri = {self.folder_uri(rel): rel for rel in found}
        self._titles = {
            path: title
            for path, data in skill.files.items()
            if path.endswith(".md") and path != ENTRY
            if (title := _heading(data))
        }

    def folder_uri(self, rel: str) -> str:
        """The URI of the folder at ``rel``, without a trailing slash."""
        root = self._prefix.rstrip("/")
        return f"{root}/{quote(rel, safe='/')}" if rel else root

    def _file_uri(self, path: str) -> str:
        return f"{self._prefix}{quote(path, safe='/')}"

    async def children(self, uri: str) -> list[dict] | None:
        rel = self._by_uri.get(uri)
        if rel is None:
            return None
        files, subfolders = self._folders[rel]
        rows = [
            directories.folder(
                self.folder_uri(sub),
                f"{self.name}/{sub}",
                f"A folder of the {self.name} skill",
            )
            for sub in subfolders
        ]
        for path in files:
            rows.append(
                directories.wire(await self._get_resource(self._file_uri(path)))
            )
        return sorted(rows, key=lambda row: row["uri"])

    async def _list_resources(self) -> list[Resource]:
        entry = self._file_uri(ENTRY)
        return [r for r in await super()._list_resources() if str(r.uri) == entry]

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        if uri.startswith(self._prefix):
            index = await directories.index_resource(self, uri)
            if index is not None:
                return index
        resource = await super()._get_resource(uri, version)
        if isinstance(resource, SkillFileResource) and resource.file_path != ENTRY:
            # Still a SkillFileResource with the same `file`: FastMCP offers
            # the entry only while every file resolves to the one it lists.
            title = self._titles.get(resource.file_path) or self._file
            resource = resource.model_copy(update={"description": title})
        return resource


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
    return str(exc) or type(exc).__name__


def load(path: Path | None = None) -> EntryOnlySkillProvider | None:
    """The skill at ``path`` (the packaged one by default) as a provider, or None.

    No ``SKILL.md`` is a warning and an invalid skill an error, neither fatal:
    the tools work without the skill.
    """
    path = skill_path() if path is None else path
    if not (path / ENTRY).is_file():
        log.warning("no SKILL.md at %s", path)
        return None
    try:
        skill = Skill.from_directory(path)
    except (OSError, ValueError) as exc:
        log.error(
            "skill at %s is not a valid Agent Skill, so it is not served: %s",
            path,
            _reason(exc),
        )
        return None
    return EntryOnlySkillProvider(skill)


def register(mcp, provider: EntryOnlySkillProvider) -> None:
    """Serve the skill's resources, and offer it through the Skills extension.

    The extension replaces the one FastMCP's provider brings.
    """
    mcp.add_provider(provider)
    mcp.add_extension(skill_extension.SkillsExtension())
    mcp.add_middleware(
        AdvertiseLegacyExtensions(
            {skill_extension.EXTENSION_ID: skill_extension.SETTINGS}
        )
    )
    mcp.add_middleware(directories.NameTheIndex(provider))
