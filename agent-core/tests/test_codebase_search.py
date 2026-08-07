"""Tests for codebase_search / codebase_index (Pack 5 · IT-550–553)."""

from __future__ import annotations

import os
import secrets
import time
import unittest
from unittest.mock import patch

from codebase_index import (
    EmbeddingError,
    StoredChunk,
    build_index,
    embed_opt_in_enabled,
    index_dir,
    index_is_stale,
    iter_indexable_files,
    load_chunks,
    refresh_index,
    resolve_index_backend,
    resolve_index_scope,
    search_codebase,
    validate_path_prefix,
)
from paths import AgentPaths, PathOutOfBoundsError
from project_mode import create_project, normalize_project_id, project_dir
from tools.builtin import codebase_search

from tests.isolation_helpers import make_temp_agent_paths


class CodebaseIndexWalkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"cs-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)

    def test_it552_node_modules_excluded(self) -> None:
        nm = self.root / "node_modules"
        nm.mkdir()
        (nm / "foo.js").write_text("export const x = 1\n", encoding="utf-8")
        (self.root / "auth.py").write_text("JWT = 'secret'\n", encoding="utf-8")
        scope = resolve_index_scope(
            self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        rels = {rel for rel, _ in iter_indexable_files(scope, self.paths)}
        self.assertIn("auth.py", rels)
        self.assertFalse(any("node_modules" in rel for rel in rels))

    def test_it551b_deny_paths_not_indexed(self) -> None:
        (self.root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (self.root / "credentials.json").write_text("{}\n", encoding="utf-8")
        (self.root / "auth.py").write_text("token = 1\n", encoding="utf-8")
        scope = resolve_index_scope(
            self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        rels = {rel for rel, _ in iter_indexable_files(scope, self.paths)}
        self.assertIn("auth.py", rels)
        self.assertNotIn(".env", rels)
        self.assertNotIn("credentials.json", rels)


class CodebaseSearchToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"cs-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        (self.root / "auth.py").write_text(
            "def verify_jwt_login(token: str) -> bool:\n"
            "    return bool(token)\n",
            encoding="utf-8",
        )
        (self.root / "login_handler.ts").write_text(
            "export function handleLogin() { return 'ui'; }\n",
            encoding="utf-8",
        )

    def test_it550_jwt_login_hits_auth(self) -> None:
        result = codebase_search.run(
            {
                "query": "JWT login",
                "top_k": 5,
                "force_refresh": True,
            },
            paths=self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        self.assertTrue(result.ok, result.error)
        hits = result.data.get("hits") or []
        self.assertTrue(hits)
        paths = [h.get("path") for h in hits]
        self.assertIn("auth.py", paths)

    def test_it551_path_prefix_out_of_scope_rejected(self) -> None:
        scope = resolve_index_scope(
            self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        with self.assertRaises(PathOutOfBoundsError):
            validate_path_prefix(scope, self.paths, "../other")

    def test_it553_stale_then_force_refresh(self) -> None:
        scope = resolve_index_scope(
            self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        build_index(scope, self.paths)
        meta, _ = load_chunks(self.paths, self.pid)
        self.assertFalse(index_is_stale(meta, scope, self.paths))
        auth = self.root / "auth.py"
        time.sleep(0.02)
        auth.write_text(
            auth.read_text(encoding="utf-8") + "\n# changed\n",
            encoding="utf-8",
        )
        meta2, _ = load_chunks(self.paths, self.pid)
        self.assertTrue(index_is_stale(meta2, scope, self.paths))
        data = search_codebase(
            self.paths,
            query="JWT",
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
            force_refresh=True,
        )
        self.assertFalse(data.get("index_stale"))
        meta3, _ = load_chunks(self.paths, self.pid)
        self.assertFalse(index_is_stale(meta3, scope, self.paths))

    def test_index_persisted_under_data_indexes(self) -> None:
        codebase_search.run(
            {"query": "JWT", "force_refresh": True},
            paths=self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        idx = index_dir(self.paths, self.pid)
        self.assertTrue((idx / "meta.json").is_file())
        self.assertTrue((idx / "chunks.jsonl").is_file())

    def test_incremental_refresh_keeps_unchanged_file_chunks(self) -> None:
        (self.root / "other.py").write_text("unchanged marker alpha\n", encoding="utf-8")
        scope = resolve_index_scope(
            self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        build_index(scope, self.paths)
        _, chunks_before = load_chunks(self.paths, self.pid)
        other_chunks = [c for c in chunks_before if c.path == "other.py"]
        self.assertTrue(other_chunks)
        time.sleep(0.02)
        auth = self.root / "auth.py"
        auth.write_text(
            auth.read_text(encoding="utf-8") + "\n# incremental touch\n",
            encoding="utf-8",
        )
        meta, _ = load_chunks(self.paths, self.pid)
        self.assertTrue(index_is_stale(meta, scope, self.paths))
        refresh_index(scope, self.paths, existing_meta=meta, existing_chunks=chunks_before)
        _, chunks_after = load_chunks(self.paths, self.pid)
        other_after = [c for c in chunks_after if c.path == "other.py"]
        self.assertEqual(other_after, other_chunks)
        auth_after = [c for c in chunks_after if c.path == "auth.py"]
        self.assertTrue(any("incremental touch" in c.text for c in auth_after))

    def test_embed_opt_in_defaults_to_bm25(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "MY_AGENT_CODEBASE_EMBED"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(embed_opt_in_enabled())
            self.assertEqual(resolve_index_backend(), "bm25")

    def test_embed_opt_in_uses_embed_when_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {"MY_AGENT_CODEBASE_EMBED": "1", "LLM_API_KEY": "test-key"},
            clear=False,
        ):
            self.assertTrue(embed_opt_in_enabled())
            self.assertEqual(resolve_index_backend(), "embed")

    def test_embed_build_falls_back_to_bm25_on_api_error(self) -> None:
        scope = resolve_index_scope(
            self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        with patch.dict(
            os.environ,
            {"MY_AGENT_CODEBASE_EMBED": "1", "LLM_API_KEY": "test-key"},
            clear=False,
        ), patch(
            "codebase_index.fetch_embeddings",
            side_effect=EmbeddingError("api down"),
        ):
            meta, notice = refresh_index(scope, self.paths, force_full=True)
        self.assertEqual(meta.get("backend"), "bm25")
        self.assertIsNotNone(notice)
        _, chunks = load_chunks(self.paths, self.pid)
        self.assertTrue(chunks)
        self.assertIsNone(chunks[0].embedding)

    def test_embed_search_uses_mocked_vectors(self) -> None:
        import json

        scope = resolve_index_scope(
            self.paths,
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
        )
        vec_a = (1.0, 0.0)
        vec_b = (0.0, 1.0)
        chunks = [
            StoredChunk("auth.py", 1, 2, "jwt login verify", embedding=vec_a),
            StoredChunk("login_handler.ts", 1, 2, "ui only", embedding=vec_b),
        ]
        idx = index_dir(self.paths, scope.index_key)
        idx.mkdir(parents=True, exist_ok=True)
        with (idx / "chunks.jsonl").open("w", encoding="utf-8") as fh:
            for chunk in chunks:
                fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
        (idx / "meta.json").write_text(
            json.dumps(
                {
                    "backend": "embed",
                    "fingerprints": {"auth.py": 1.0, "login_handler.ts": 1.0},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {"MY_AGENT_CODEBASE_EMBED": "1", "LLM_API_KEY": "test-key"},
            clear=False,
        ), patch("codebase_index.fetch_embeddings", return_value=[list(vec_a)]):
            data = search_codebase(
                self.paths,
                query="jwt login",
                project_root=f"workspace/{self.pid}",
                project_id=self.pid,
            )
        self.assertEqual(data.get("backend"), "embed")
        hits = data.get("hits") or []
        self.assertTrue(hits)
        self.assertEqual(hits[0].get("path"), "auth.py")


if __name__ == "__main__":
    unittest.main()
