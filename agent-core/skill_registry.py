"""Trusted, request-scoped Skill registry for first-party workflow guidance."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


class SkillRegistryError(ValueError):
    """Skill metadata/body is invalid or outside the trusted registry root."""


@dataclass(frozen=True, slots=True)
class SkillIdentity:
    """Stable identity for one loaded Skill package."""

    skill_id: str
    version: str
    source_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    name: str
    description: str
    triggers: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    risk_level: str
    verification: str
    version: str
    path: Path
    source_id: str = "builtin-directory"
    content_hash: str = ""
    api_version: str = "1"
    status: str = "stable"
    compatibility_models: tuple[str, ...] = ()
    runtime_min_version: str = ""
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillPackage:
    descriptor: SkillDescriptor
    body: str

    @property
    def identity(self) -> SkillIdentity:
        return SkillIdentity(
            skill_id=self.descriptor.name,
            version=self.descriptor.version,
            source_id=self.descriptor.source_id,
            content_hash=self.descriptor.content_hash,
        )


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """One request-scoped Skill selection, including the loaded package."""

    descriptor: SkillDescriptor
    package: SkillPackage
    score: int

    @property
    def identity(self) -> SkillIdentity:
        return self.package.identity


class SkillSource(Protocol):
    """Replaceable source of trusted Skill packages."""

    @property
    def source_id(self) -> str: ...

    @property
    def root(self) -> Path: ...

    def discover(self) -> tuple[tuple[SkillPackage, ...], tuple[str, ...]]: ...

    def load(self, name: str) -> SkillPackage: ...


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_MAX_INDEX_DESCRIPTION = 240


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _parse_list(
    value: str,
    lines: list[str],
    index: int,
    *,
    parent_indent: int = 0,
) -> tuple[tuple[str, ...], int]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        raw = value[1:-1]
        return tuple(_parse_scalar(item) for item in raw.split(",") if item.strip()), index
    items: list[str] = []
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if _indent_width(line) <= parent_indent or not line.strip().startswith("-"):
            break
        items.append(_parse_scalar(line.strip()[1:].strip()))
        index += 1
    return tuple(items), index - 1


def _parse_skill(path: Path, *, source_id: str = "builtin-directory") -> SkillPackage:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillRegistryError(f"missing SKILL.md frontmatter: {path}")
    raw, body = match.groups()
    lines = raw.splitlines()
    values: dict[str, str] = {}
    lists: dict[str, tuple[str, ...]] = {}
    current_section: str | None = None
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.strip()
        indent = _indent_width(raw_line)
        index += 1
        if not line or line.startswith("#"):
            continue
        if indent > 0:
            if current_section != "compatibility" or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "models":
                parsed, consumed = _parse_list(
                    value, lines, index, parent_indent=indent
                )
                lists["compatibility_models"] = parsed
                index = consumed + 1
            elif key == "runtime":
                values["runtime_min_version"] = _parse_scalar(value)
            else:
                raise SkillRegistryError(f"unsupported compatibility field: {key}")
            continue
        if ":" not in line:
            continue
        current_section = None
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"triggers", "allowed_tools", "capabilities"}:
            parsed, consumed = _parse_list(
                value, lines, index, parent_indent=indent
            )
            lists[key] = parsed
            index = consumed + 1
        elif key == "compatibility":
            if value:
                raise SkillRegistryError("compatibility must be a mapping")
            current_section = key
        else:
            values[key] = _parse_scalar(value)
    name = values.get("name", "")
    if not _SAFE_NAME_RE.fullmatch(name):
        raise SkillRegistryError(f"invalid skill name: {name!r}")
    for key in ("description", "risk_level", "verification", "version"):
        if not values.get(key):
            raise SkillRegistryError(f"missing skill metadata: {key}")
    descriptor = SkillDescriptor(
        name=name,
        description=values["description"],
        triggers=lists.get("triggers", ()),
        allowed_tools=lists.get("allowed_tools", ()),
        risk_level=values["risk_level"],
        verification=values["verification"],
        version=values["version"],
        path=path,
        source_id=source_id,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        api_version=values.get("api_version", "1"),
        status=values.get("status", "stable"),
        compatibility_models=lists.get("compatibility_models", ()),
        runtime_min_version=values.get("runtime_min_version", ""),
        capabilities=lists.get("capabilities", ()),
    )
    return SkillPackage(descriptor=descriptor, body=body.strip())


class DirectorySkillSource:
    """Load Skills from one trusted directory tree."""

    def __init__(
        self,
        root: Path,
        *,
        source_id: str = "builtin-directory",
        max_body_bytes: int = 64 * 1024,
    ) -> None:
        self._root = Path(root).resolve()
        self._source_id = source_id
        self.max_body_bytes = max_body_bytes

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def root(self) -> Path:
        return self._root

    def discover(self) -> tuple[tuple[SkillPackage, ...], tuple[str, ...]]:
        packages: list[SkillPackage] = []
        diagnostics: list[str] = []
        if not self.root.is_dir():
            return (), (f"skill root unavailable: {self.root}",)
        for skill_dir in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if not skill_dir.is_dir() or not _SAFE_NAME_RE.fullmatch(skill_dir.name):
                continue
            path = skill_dir / "SKILL.md"
            if not path.is_file():
                diagnostics.append(f"missing SKILL.md: {skill_dir.name}")
                continue
            try:
                package = _parse_skill(path, source_id=self.source_id)
                if package.descriptor.name != skill_dir.name:
                    raise SkillRegistryError("metadata name does not match directory")
                packages.append(package)
            except (OSError, UnicodeError, SkillRegistryError) as exc:
                diagnostics.append(str(exc))
        return tuple(packages), tuple(diagnostics)

    def load(self, name: str) -> SkillPackage:
        if not isinstance(name, str) or not _SAFE_NAME_RE.fullmatch(name):
            raise SkillRegistryError(f"invalid skill name: {name!r}")
        path = (self.root / name / "SKILL.md").resolve()
        if path.parent.parent != self.root:
            raise SkillRegistryError(f"skill path escapes registry: {path}")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise SkillRegistryError(f"cannot stat skill: {name}") from exc
        if size > self.max_body_bytes:
            raise SkillRegistryError(f"skill body exceeds limit: {name}")
        package = _parse_skill(path, source_id=self.source_id)
        if package.descriptor.name != name:
            raise SkillRegistryError("metadata name does not match directory")
        return package


class SkillRegistry:
    def __init__(
        self,
        root: Path,
        packages: Iterable[SkillPackage] = (),
        diagnostics: Iterable[str] = (),
        *,
        max_body_bytes: int = 64 * 1024,
        sources: Iterable[SkillSource] = (),
    ) -> None:
        self.root = root.resolve()
        self._packages = {package.descriptor.name: package for package in packages}
        self.diagnostics = tuple(diagnostics)
        self.max_body_bytes = max_body_bytes
        self._sources = tuple(sources)
        self._sources_by_id = {source.source_id: source for source in self._sources}
        self._source_roots = tuple(source.root for source in self._sources)

    @property
    def skills(self) -> tuple[SkillDescriptor, ...]:
        return tuple(
            package.descriptor
            for _name, package in sorted(self._packages.items())
        )

    @classmethod
    def discover(cls, root: Path, *, max_body_bytes: int = 64 * 1024) -> "SkillRegistry":
        source = DirectorySkillSource(root, max_body_bytes=max_body_bytes)
        packages, diagnostics = source.discover()
        return cls(
            source.root,
            packages,
            diagnostics,
            max_body_bytes=max_body_bytes,
            sources=(source,),
        )

    @classmethod
    def from_sources(
        cls,
        sources: Iterable[SkillSource],
        *,
        max_body_bytes: int = 64 * 1024,
    ) -> "SkillRegistry":
        source_list = tuple(sources)
        packages: dict[str, SkillPackage] = {}
        diagnostics: list[str] = []
        for source in source_list:
            discovered, source_diagnostics = source.discover()
            diagnostics.extend(source_diagnostics)
            for package in discovered:
                packages.setdefault(package.descriptor.name, package)
        root = source_list[0].root if source_list else Path(".")
        return cls(
            root,
            packages.values(),
            diagnostics,
            max_body_bytes=max_body_bytes,
            sources=source_list,
        )

    def _package(self, name: str) -> SkillPackage:
        if not isinstance(name, str) or not _SAFE_NAME_RE.fullmatch(name):
            raise SkillRegistryError(f"invalid skill name: {name!r}")
        package = self._packages.get(name)
        if package is None:
            raise SkillRegistryError(f"unknown skill: {name}")
        resolved = package.descriptor.path.resolve()
        if self._source_roots and not any(
            resolved.parent.parent == source_root for source_root in self._source_roots
        ):
            raise SkillRegistryError(f"skill path escapes registry: {resolved}")
        if not self._source_roots and resolved.parent.parent != self.root:
            raise SkillRegistryError(f"skill path escapes registry: {resolved}")
        return package

    def load(self, name: str) -> SkillPackage:
        package = self._package(name)
        source = self._sources_by_id.get(package.descriptor.source_id)
        if source is not None:
            return source.load(name)
        try:
            size = package.descriptor.path.stat().st_size
        except OSError as exc:
            raise SkillRegistryError(f"cannot stat skill: {name}") from exc
        if size > self.max_body_bytes:
            raise SkillRegistryError(f"skill body exceeds limit: {name}")
        return _parse_skill(package.descriptor.path, source_id=package.descriptor.source_id)

    @staticmethod
    def _score(descriptor: SkillDescriptor, folded: str) -> int:
        score = 0
        for trigger in descriptor.triggers:
            if trigger and trigger.casefold() in folded:
                score += 3 if len(trigger) >= 4 else 1
        if descriptor.name.casefold() in folded:
            score += 8
        return score

    def select_packages(self, user_text: str, *, limit: int = 1) -> tuple[SkillSelection, ...]:
        if limit <= 0:
            return ()
        folded = (user_text or "").casefold()
        scored: list[tuple[int, SkillPackage]] = []
        for package in self._packages.values():
            score = self._score(package.descriptor, folded)
            if score:
                scored.append((score, package))
        scored.sort(key=lambda item: (-item[0], item[1].descriptor.name))
        return tuple(
            SkillSelection(descriptor=package.descriptor, package=package, score=score)
            for score, package in scored[:limit]
        )

    def select(self, user_text: str, *, limit: int = 1) -> tuple[SkillDescriptor, ...]:
        return tuple(
            selection.descriptor
            for selection in self.select_packages(user_text, limit=limit)
        )

    def format_index(self) -> str:
        if not self.skills:
            return "[Skill 索引]\n当前没有可用的内置 Skill。"
        lines = ["[Skill 索引]（按需加载，不自动执行 Skill 脚本）"]
        for descriptor in self.skills:
            description = descriptor.description[:_MAX_INDEX_DESCRIPTION]
            lines.append(
                f"- {descriptor.name} v{descriptor.version} [{descriptor.status}]: {description}"
            )
        return "\n".join(lines)

    def format_selection_context(self, selection: SkillSelection) -> str:
        descriptor = selection.descriptor
        identity = selection.identity
        return (
            f"[Skill 上下文 · {descriptor.name} v{descriptor.version}]\n"
            f"skill_identity: {identity.source_id}/{identity.skill_id}@{identity.version}#{identity.content_hash}\n"
            f"api_version: {descriptor.api_version}\n"
            f"status: {descriptor.status}\n"
            f"compatibility_models: {', '.join(descriptor.compatibility_models) or '(any)'}\n"
            f"runtime_min_version: {descriptor.runtime_min_version or '(none)'}\n"
            f"capabilities: {', '.join(descriptor.capabilities) or '(none)'}\n"
            f"risk_level: {descriptor.risk_level}\n"
            f"allowed_tools (advisory): {', '.join(descriptor.allowed_tools) or '(none)'}\n"
            f"verification: {descriptor.verification}\n\n"
            f"{selection.package.body}"
        )

    def format_context(self, name: str) -> str:
        package = self.load(name)
        return self.format_selection_context(
            SkillSelection(descriptor=package.descriptor, package=package, score=0)
        )
