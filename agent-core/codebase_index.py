"""Local codebase index + BM25 / optional embedding search (Pack 5 · CODEBASE-SEARCH)."""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from paths import AgentPaths, PathOutOfBoundsError
from tools.builtin.glob_file_search import _is_ignored_path
from tools.http_client import make_httpx_client

CHUNK_LINES = 100
CHUNK_OVERLAP = 20
MAX_FILE_BYTES = 512 * 1024
DEFAULT_TOP_K = 5
HARD_TOP_K = 8
META_NAME = "meta.json"
CHUNKS_NAME = "chunks.jsonl"
EMBED_BATCH_SIZE = 32
DEFAULT_EMBEDDING_MODEL = "text-embedding-v2"
EMBED_TIMEOUT_SEC = 60.0

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_DENY_INDEX_PATH_RE = re.compile(
    r"(^|[/\\])\.env(\.|$)|credentials|secret|\.pem$|\.key$",
    re.IGNORECASE,
)
_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".sql",
        ".md",
        ".txt",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".html",
        ".css",
        ".scss",
        ".vue",
        ".xml",
        ".sh",
        ".bat",
        ".ps1",
        ".cs",
        ".cpp",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".gradle",
        ".properties",
    }
)


class EmbeddingError(Exception):
    """Embedding API unavailable or failed."""


@dataclass(frozen=True, slots=True)
class IndexScope:
    index_key: str
    root: Path
    root_rel: str
    partial_agent: bool


@dataclass(frozen=True, slots=True)
class CodeChunk:
    path: str
    start_line: int
    end_line: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class StoredChunk:
    path: str
    start_line: int
    end_line: int
    text: str
    embedding: tuple[float, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        row = {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
        }
        if self.embedding is not None:
            row["embedding"] = list(self.embedding)
        return row

    @classmethod
    def from_code_chunk(cls, chunk: CodeChunk) -> StoredChunk:
        return cls(
            path=chunk.path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            text=chunk.text,
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> StoredChunk:
        raw_emb = row.get("embedding")
        embedding: tuple[float, ...] | None = None
        if isinstance(raw_emb, list) and raw_emb:
            embedding = tuple(float(x) for x in raw_emb)
        return cls(
            path=str(row.get("path") or ""),
            start_line=int(row.get("start_line") or 1),
            end_line=int(row.get("end_line") or 1),
            text=str(row.get("text") or ""),
            embedding=embedding,
        )


def embed_opt_in_enabled() -> bool:
    """CS-9: external embedding requires MY_AGENT_CODEBASE_EMBED=1."""
    raw = os.environ.get("MY_AGENT_CODEBASE_EMBED", "").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def embedding_model_name() -> str:
    return (os.environ.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL).strip()


def resolve_index_backend() -> str:
    """Return ``embed`` when opt-in + API key; else ``bm25``."""
    if not embed_opt_in_enabled():
        return "bm25"
    if not (os.environ.get("LLM_API_KEY") or "").strip():
        return "bm25"
    return "embed"


def resolve_index_scope(
    paths: AgentPaths,
    *,
    project_root: str = "",
    project_id: str = "",
) -> IndexScope:
    root_text = (project_root or "").strip()
    if root_text:
        root = paths.resolve_under_agent(root_text)
        pid = (project_id or "").strip()
        if pid:
            from project_mode import normalize_project_id

            key = normalize_project_id(pid)
        else:
            key = paths.to_agent_relative(root).replace("/", "_")
        return IndexScope(
            index_key=key,
            root=root,
            root_rel=paths.to_agent_relative(root),
            partial_agent=False,
        )
    return IndexScope(
        index_key="_agent",
        root=paths.agent_root,
        root_rel=".",
        partial_agent=True,
    )


def index_dir(paths: AgentPaths, index_key: str) -> Path:
    safe = re.sub(r"[^\w\-.]", "_", (index_key or "_agent").strip()) or "_agent"
    return paths.data / "indexes" / safe


def is_denied_index_path(rel_posix: str) -> bool:
    norm = rel_posix.replace("\\", "/")
    if _DENY_INDEX_PATH_RE.search(norm):
        return True
    name = Path(norm).name.lower()
    if fnmatch.fnmatch(name, "*.min.js") or fnmatch.fnmatch(name, "*.map"):
        return True
    return False


def should_index_file(path: Path, *, rel_posix: str, search_root: Path) -> bool:
    if not path.is_file():
        return False
    if is_denied_index_path(rel_posix):
        return False
    if _is_ignored_path(rel_posix, search_root=search_root):
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size > MAX_FILE_BYTES:
        return False
    suffix = path.suffix.lower()
    if suffix and suffix not in _TEXT_SUFFIXES:
        return False
    return True


def iter_indexable_files(scope: IndexScope, paths: AgentPaths) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if scope.partial_agent:
        roots = [paths.evolve, paths.workspace]
    else:
        roots = [scope.root]
    for base in roots:
        if not base.is_dir():
            continue
        search_root = base.resolve()
        prefix = paths.to_agent_relative(search_root)
        for path in sorted(search_root.rglob("*")):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(search_root).as_posix()
            except ValueError:
                continue
            if not should_index_file(path, rel_posix=rel, search_root=search_root):
                continue
            if scope.partial_agent:
                stored_rel = f"{prefix}/{rel}" if rel else prefix
            else:
                stored_rel = rel
            out.append((stored_rel, path))
    return out


def chunk_file_text(text: str, *, path: str) -> list[CodeChunk]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[CodeChunk] = []
    step = max(1, CHUNK_LINES - CHUNK_OVERLAP)
    for start in range(0, len(lines), step):
        end = min(len(lines), start + CHUNK_LINES)
        body = "\n".join(lines[start:end])
        if not body.strip():
            continue
        chunks.append(
            CodeChunk(
                path=path,
                start_line=start + 1,
                end_line=end,
                text=body,
            )
        )
        if end >= len(lines):
            break
    return chunks


def tokenize(text: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    spaced = spaced.replace("_", " ")
    return [t for t in _TOKEN_RE.findall(spaced.lower()) if t]


class Bm25Ranker:
    def __init__(self, chunks: list[StoredChunk]) -> None:
        self._chunks = chunks
        self._doc_tokens: list[list[str]] = []
        self._doc_lens: list[int] = []
        doc_freq: Counter[str] = Counter()
        for chunk in chunks:
            toks = tokenize(chunk.text)
            self._doc_tokens.append(toks)
            self._doc_lens.append(len(toks))
            for term in set(toks):
                doc_freq[term] += 1
        self._doc_freq = doc_freq
        self._n = len(chunks)
        self._avgdl = (sum(self._doc_lens) / self._n) if self._n else 0.0

    def score(self, query: str) -> list[tuple[int, float]]:
        if not self._chunks:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        k1, b = 1.5, 0.75
        scores: list[tuple[int, float]] = []
        for i, toks in enumerate(self._doc_tokens):
            tf = Counter(toks)
            dl = self._doc_lens[i]
            total = 0.0
            for term in q_tokens:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                df = self._doc_freq.get(term, 0)
                idf = math.log((self._n - df + 0.5) / (df + 0.5) + 1.0)
                denom = freq + k1 * (1.0 - b + b * (dl / self._avgdl if self._avgdl else 0.0))
                total += idf * (freq * (k1 + 1.0)) / denom
            scores.append((i, total))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores


class EmbeddingRanker:
    def __init__(self, chunks: list[StoredChunk]) -> None:
        self._chunks = chunks
        self._vectors = [c.embedding for c in chunks]

    def score(self, query_vector: tuple[float, ...]) -> list[tuple[int, float]]:
        scores: list[tuple[int, float]] = []
        for i, vec in enumerate(self._vectors):
            if not vec:
                continue
            sim = _cosine_similarity(query_vector, vec)
            if sim > 0:
                scores.append((i, sim))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return ""
    return raw.decode("utf-8", errors="replace")


def _file_map(scope: IndexScope, paths: AgentPaths) -> dict[str, Path]:
    return dict(iter_indexable_files(scope, paths))


def _chunks_for_paths(
    file_map: dict[str, Path],
    rel_paths: set[str],
) -> list[StoredChunk]:
    out: list[StoredChunk] = []
    for rel in sorted(rel_paths):
        abs_path = file_map.get(rel)
        if abs_path is None:
            continue
        try:
            text = _read_text_file(abs_path)
        except OSError:
            continue
        for chunk in chunk_file_text(text, path=rel):
            out.append(StoredChunk.from_code_chunk(chunk))
    return out


def _diff_fingerprints(
    stored: dict[str, float],
    current: dict[str, float],
) -> tuple[set[str], set[str], set[str]]:
    stored_keys = set(stored)
    current_keys = set(current)
    added = current_keys - stored_keys
    removed = stored_keys - current_keys
    modified = {
        rel
        for rel in stored_keys & current_keys
        if abs(float(stored[rel]) - float(current[rel])) > 0.001
    }
    return added, removed, modified


def _embedding_api_config() -> tuple[str, str, str]:
    from llm_client import DEFAULT_BASE_URL, load_config

    config = load_config()
    api_key = (config.api_key or "").strip()
    if not api_key:
        raise EmbeddingError("LLM_API_KEY is not set")
    base = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
    model = embedding_model_name()
    return api_key, base, model


def fetch_embeddings(texts: list[str]) -> list[list[float]]:
    """OpenAI-compatible /v1/embeddings batch call."""
    if not texts:
        return []
    api_key, base, model = _embedding_api_config()
    url = f"{base}/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    out: list[list[float]] = []
    with make_httpx_client(timeout=EMBED_TIMEOUT_SEC) as client:
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            payload = {"model": model, "input": batch}
            try:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(str(exc)) from exc
            body = response.json()
            data = body.get("data")
            if not isinstance(data, list):
                raise EmbeddingError("embedding response missing data")
            rows = sorted(data, key=lambda row: int(row.get("index", 0)))
            for row in rows:
                vec = row.get("embedding")
                if not isinstance(vec, list) or not vec:
                    raise EmbeddingError("embedding vector missing")
                out.append([float(x) for x in vec])
    if len(out) != len(texts):
        raise EmbeddingError("embedding count mismatch")
    return out


def _apply_embeddings(chunks: list[StoredChunk]) -> list[StoredChunk]:
    pending = [i for i, chunk in enumerate(chunks) if chunk.embedding is None]
    if not pending:
        return chunks
    texts = [chunks[i].text for i in pending]
    vectors = fetch_embeddings(texts)
    updated = list(chunks)
    for idx, vec in zip(pending, vectors, strict=True):
        old = updated[idx]
        updated[idx] = StoredChunk(
            path=old.path,
            start_line=old.start_line,
            end_line=old.end_line,
            text=old.text,
            embedding=tuple(vec),
        )
    return updated


def _chunks_have_embeddings(chunks: list[StoredChunk]) -> bool:
    return bool(chunks) and all(c.embedding is not None for c in chunks)


def _write_index(
    scope: IndexScope,
    paths: AgentPaths,
    chunks: list[StoredChunk],
    fingerprints: dict[str, float],
    backend: str,
) -> dict[str, Any]:
    idx_path = index_dir(paths, scope.index_key)
    idx_path.mkdir(parents=True, exist_ok=True)
    meta = {
        "backend": backend,
        "indexed_at": time.time(),
        "root_rel": scope.root_rel,
        "index_key": scope.index_key,
        "partial_agent": scope.partial_agent,
        "fingerprints": fingerprints,
        "chunk_count": len(chunks),
    }
    if backend == "embed":
        meta["embedding_model"] = embedding_model_name()
    (idx_path / META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (idx_path / CHUNKS_NAME).open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    return meta


def refresh_index(
    scope: IndexScope,
    paths: AgentPaths,
    *,
    existing_meta: dict[str, Any] | None = None,
    existing_chunks: list[StoredChunk] | None = None,
    force_full: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """Incremental or full index refresh. Returns (meta, optional notice)."""
    notice: str | None = None
    requested_backend = resolve_index_backend()
    backend = requested_backend
    file_map = _file_map(scope, paths)
    fingerprints = {rel: path.stat().st_mtime for rel, path in file_map.items()}

    if force_full or not existing_meta or not existing_chunks:
        chunks = _chunks_for_paths(file_map, set(file_map))
    else:
        stored_fp = existing_meta.get("fingerprints")
        if not isinstance(stored_fp, dict):
            chunks = _chunks_for_paths(file_map, set(file_map))
        else:
            added, removed, modified = _diff_fingerprints(stored_fp, fingerprints)
            if not (added or removed or modified):
                return existing_meta, notice
            changed = added | modified
            kept = [c for c in existing_chunks if c.path not in (removed | modified)]
            chunks = kept + _chunks_for_paths(file_map, changed)

    if backend == "embed":
        try:
            chunks = _apply_embeddings(chunks)
        except EmbeddingError:
            backend = "bm25"
            notice = "embedding API unavailable; index stored with backend=bm25"
            chunks = [
                StoredChunk(
                    path=c.path,
                    start_line=c.start_line,
                    end_line=c.end_line,
                    text=c.text,
                    embedding=None,
                )
                for c in chunks
            ]

    meta = _write_index(scope, paths, chunks, fingerprints, backend)
    return meta, notice


def build_index(scope: IndexScope, paths: AgentPaths) -> dict[str, Any]:
    meta, _ = refresh_index(scope, paths, force_full=True)
    return meta


def load_chunks(paths: AgentPaths, index_key: str) -> tuple[dict[str, Any], list[StoredChunk]]:
    idx_path = index_dir(paths, index_key)
    meta_path = idx_path / META_NAME
    chunks_path = idx_path / CHUNKS_NAME
    if not meta_path.is_file() or not chunks_path.is_file():
        return {}, []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    chunks: list[StoredChunk] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        chunks.append(StoredChunk.from_row(json.loads(line)))
    return meta, chunks


def current_fingerprints(scope: IndexScope, paths: AgentPaths) -> dict[str, float]:
    return {rel: path.stat().st_mtime for rel, path in iter_indexable_files(scope, paths)}


def index_is_stale(
    meta: dict[str, Any],
    scope: IndexScope,
    paths: AgentPaths,
) -> bool:
    if not meta:
        return True
    stored = meta.get("fingerprints")
    if not isinstance(stored, dict):
        return True
    current = current_fingerprints(scope, paths)
    if set(stored.keys()) != set(current.keys()):
        return True
    for rel, mtime in current.items():
        if abs(float(stored.get(rel, 0)) - float(mtime)) > 0.001:
            return True
    return False


def validate_path_prefix(
    scope: IndexScope,
    paths: AgentPaths,
    path_prefix: str,
) -> str:
    raw = (path_prefix or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.startswith("/") or ".." in raw.split("/"):
        raise PathOutOfBoundsError(
            f"path_prefix escapes index scope: {path_prefix!r}",
            path=path_prefix,
            boundary=scope.root_rel,
        )
    if scope.partial_agent:
        norm = raw.strip("/")
        if not (norm.startswith("evolve/") or norm.startswith("workspace/")):
            raise PathOutOfBoundsError(
                f"path_prefix must stay under evolve/ or workspace/: {path_prefix!r}",
                path=path_prefix,
                boundary="evolve+workspace",
            )
        return norm
    try:
        resolved = (scope.root / raw).resolve()
        _ = resolved.relative_to(scope.root.resolve())
    except ValueError as exc:
        raise PathOutOfBoundsError(
            f"path_prefix escapes project_root: {path_prefix!r}",
            path=path_prefix,
            boundary=scope.root_rel,
        ) from exc
    return raw.strip("/")


def search_codebase(
    paths: AgentPaths,
    *,
    query: str,
    project_root: str = "",
    project_id: str = "",
    top_k: int = DEFAULT_TOP_K,
    path_prefix: str = "",
    force_refresh: bool = False,
) -> dict[str, Any]:
    scope = resolve_index_scope(paths, project_root=project_root, project_id=project_id)
    prefix = validate_path_prefix(scope, paths, path_prefix)
    meta, chunks = load_chunks(paths, scope.index_key)
    stale = index_is_stale(meta, scope, paths)
    notice: str | None = None

    if force_refresh or not chunks:
        meta, notice = refresh_index(
            scope,
            paths,
            existing_meta=meta or None,
            existing_chunks=chunks or None,
            force_full=not chunks,
        )
        _, chunks = load_chunks(paths, scope.index_key)
        stale = False

    backend = str(meta.get("backend") or "bm25")
    scored: list[tuple[int, float]] = []
    if backend == "embed" and _chunks_have_embeddings(chunks):
        try:
            query_vec = tuple(fetch_embeddings([query])[0])
            scored = EmbeddingRanker(chunks).score(query_vec)
        except EmbeddingError:
            backend = "bm25"
            notice = notice or "embedding API unavailable; search used backend=bm25"
            scored = Bm25Ranker(chunks).score(query)
    else:
        if backend == "embed":
            backend = "bm25"
            notice = notice or "embedding vectors missing; search used backend=bm25"
        scored = Bm25Ranker(chunks).score(query)

    hits: list[dict[str, Any]] = []
    for idx, score in scored:
        if score <= 0:
            continue
        chunk = chunks[idx]
        if prefix and not chunk.path.replace("\\", "/").startswith(prefix):
            continue
        snippet = chunk.text
        if len(snippet) > 400:
            snippet = snippet[:397] + "..."
        hits.append(
            {
                "path": chunk.path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "score": round(score, 4),
                "snippet": snippet,
            }
        )
        if len(hits) >= top_k:
            break

    out: dict[str, Any] = {
        "hits": hits,
        "index_stale": stale,
        "backend": backend,
        "index_key": scope.index_key,
    }
    if notice:
        out["notice"] = notice
    return out
