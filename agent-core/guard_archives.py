"""Read-only guard inventory and explicit, verified ZIP archival; never deletes sources.

No third-party dependencies. Archives contain private session/source content;
keep them private. This is not a cleanup command or an archive extractor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

from guard_snapshots import GUARDS, SESSION_FILES, STAMP, is_link, safe_container, snapshot_payload

MANIFEST = "__guard_manifest__.json"
CHUNK = 1024 * 1024


def _files(root: Path) -> tuple[list[Path], int]:
    root = root.absolute()
    files: list[Path] = []
    skipped = 0
    for name in sorted(GUARDS):
        guard = root / "data" / name
        if not guard.exists():
            continue
        if is_link(guard):
            raise ValueError("Refusing linked guard root")
        for container in sorted(guard.iterdir()):
            if not safe_container(container):
                skipped += 1
                continue
            for entry in sorted(container.iterdir()):
                if entry.name == "latest":
                    continue
                payload = snapshot_payload(entry, container)
                if payload is None:
                    skipped += 1
                    continue
                files.extend(sorted(payload))
    return files, skipped


def inventory_guard_snapshots(root: Path) -> dict:
    files, skipped = _files(root)
    return {"root": str(root.absolute()), "files": len(files),
            "bytes": sum(p.stat().st_size for p in files), "skipped_entries": skipped,
            "mode": "read_only", "excludes": ["latest", "links", "unknown entries"]}


def _signature(path: Path) -> tuple[int, int, int, int]:
    s = path.stat(follow_symlinks=False)
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def _valid_member(name: str) -> bool:
    p = PurePosixPath(name)
    if (p.is_absolute() or ".." in p.parts or "\\" in name
            or len(p.parts) not in (4, 5) or p.parts[0] != "data"
            or p.parts[1] not in GUARDS or p.as_posix() != name):
        return False
    if not STAMP.fullmatch(p.parts[3]):
        return False
    return ((p.parts[1] == ".file-guard" and len(p.parts) == 4)
            or (p.parts[1] == ".session-guard" and len(p.parts) == 5
                and p.parts[4] in SESSION_FILES))


def verify_guard_archive(output: Path) -> dict:
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read(MANIFEST))
        if (not isinstance(manifest, dict) or manifest.get("version") != 1
                or not isinstance(manifest.get("entries"), list)):
            raise ValueError("Invalid guard archive manifest")
        entries = manifest["entries"]
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                    or type(entry.get("size")) is not int or entry["size"] < 0
                    or not isinstance(entry.get("sha256"), str)
                    or len(entry["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in entry["sha256"])):
                raise ValueError("Invalid guard archive entry")
        names = [e["path"] for e in entries]
        if len(set(names)) != len(names) or any(not _valid_member(n) for n in names):
            raise ValueError("Duplicate or unsafe archive member")
        members = archive.namelist()
        if len(members) != len(names) + 1 or set(members) != set(names) | {MANIFEST}:
            raise ValueError("Archive members do not match manifest")
        for entry in entries:
            if archive.getinfo(entry["path"]).file_size != entry["size"]:
                raise ValueError("Archive size mismatch")
            digest = hashlib.sha256()
            with archive.open(entry["path"]) as source:
                while chunk := source.read(CHUNK):
                    digest.update(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError("Archive checksum mismatch")
        return {"verified": True, "files": len(entries),
                "bytes": sum(e["size"] for e in entries)}


def archive_guard_snapshots(root: Path, output: Path) -> dict:
    root = root.absolute()
    output = output.absolute()
    for guard in GUARDS:
        if output.resolve().is_relative_to((root / "data" / guard).resolve()):
            raise ValueError("Archive must be outside source guard trees")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    files, skipped = _files(root)
    entries = []
    created = False
    try:
        # 'x' reserves a NEW file atomically, including on exFAT; never overwrite.
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            created = True
            for source in files:
                container = source.parent if source.parent.parent.name == ".file-guard" else source.parent.parent
                snapshot = source if container.parent.name == ".file-guard" else source.parent
                if not safe_container(container) or snapshot_payload(snapshot, container) is None:
                    raise ValueError("Snapshot changed or became unsafe during archive")
                before = _signature(source)
                name = source.relative_to(root).as_posix()
                if not _valid_member(name):
                    raise ValueError("Unsafe source member")
                digest = hashlib.sha256()
                with source.open("rb") as reader, archive.open(name, "w", force_zip64=True) as writer:
                    while chunk := reader.read(CHUNK):
                        writer.write(chunk)
                        digest.update(chunk)
                if before != _signature(source) or is_link(source):
                    raise ValueError("Source changed while archiving; original backups preserved")
                entries.append({"path": name, "size": before[2], "sha256": digest.hexdigest()})
            archive.writestr(MANIFEST, json.dumps({"version": 1, "entries": entries}, ensure_ascii=False))
        # Verify all payloads, not just that a ZIP file exists or its CRC is readable.
        result = verify_guard_archive(output)
        return {**result, "archive": str(output), "archive_bytes": output.stat().st_size,
                "skipped_entries": skipped, "sources_deleted": 0}
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Repository whose guard snapshots are read")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--archive", type=Path, help="Create a NEW private ZIP; never deletes sources")
    mode.add_argument("--verify", type=Path, help="Verify all manifest sizes and SHA-256 values")
    args = parser.parse_args()
    try:
        if args.verify:
            result = verify_guard_archive(args.verify)
        else:
            if args.root is None:
                parser.error("--root is required for inventory/archive")
            result = (archive_guard_snapshots(args.root, args.archive) if args.archive
                      else inventory_guard_snapshots(args.root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
