from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from work_records_semantic import SearchResult, WorkRecordCandidate
from work_records_prefilter import (
    LocalHybridRetriever, PrefilterStats, guard_semantic_result, prefilter_work_records,
)


def records(count: int) -> list[WorkRecordCandidate]:
    return [WorkRecordCandidate(f"r{i:03}", f"工作记录 {i}", f"内容编号 {i}", "session-1") for i in range(count)]


class WorkRecordsPrefilterTests(unittest.TestCase):
    def test_999_records_shrinks_candidates_and_reports_incomplete_coverage_and_cost(self):
        data = records(999)
        data[731] = WorkRecordCandidate("r731", "购买键盘", "为开发工作购置输入设备", "session-1")
        started = time.perf_counter()
        result = prefilter_work_records("买了什么输入设备", data, max_candidates=12)
        wall_ms = (time.perf_counter() - started) * 1000
        self.assertIn("r731", result.candidate_ids)
        self.assertLessEqual(result.stats.candidate_count, 12)
        self.assertEqual(result.stats.input_count, 999)
        self.assertEqual(result.coverage, "lexical_only")
        self.assertTrue(result.incomplete)
        self.assertTrue(result.uncertain)
        self.assertGreaterEqual(result.stats.elapsed_ms, 0)
        self.assertGreaterEqual(wall_ms, 0)

    def test_explicit_synonym_expansion_can_recall_without_shared_query_keyword(self):
        data = [WorkRecordCandidate("a", "出差安排", "去杭州参加客户会议", "s")]
        result = prefilter_work_records("商务旅行", data, max_candidates=4)
        self.assertEqual(result.candidate_ids, ("a",))
        self.assertTrue(result.uncertain)
        self.assertEqual(result.coverage, "lexical_only")

    def test_ambiguous_matches_keep_multiple_candidates(self):
        data = [
            WorkRecordCandidate("a", "购买电脑", "购买工作电脑", "s"),
            WorkRecordCandidate("b", "购置电脑", "为工作买电脑", "s"),
            WorkRecordCandidate("c", "午餐", "团队午餐", "s"),
        ]
        result = prefilter_work_records("买了电脑", data, max_candidates=5)
        self.assertEqual(set(result.candidate_ids), {"a", "b"})
        self.assertTrue(result.incomplete)
        self.assertTrue(result.uncertain)

    def test_mixed_sessions_fail_closed_before_retriever(self):
        data = [
            WorkRecordCandidate("a", "购买电脑", "当前会话记录", "session-1"),
            WorkRecordCandidate("b", "购买电脑", "另一会话记录", "session-2"),
        ]
        calls = []

        def retriever(question, candidates, limit):
            calls.append((question, len(candidates), limit))
            return ["a"]

        result = prefilter_work_records("购买电脑", data, max_candidates=4, retriever=retriever)
        self.assertEqual(result.candidates, ())
        self.assertEqual(calls, [])
        self.assertTrue(result.incomplete)
        self.assertTrue(result.uncertain)
        self.assertIn("session", result.reason)

    def test_full_unreduced_candidates_allow_semantic_match(self):
        data = records(2)
        prefiltered = prefilter_work_records(
            "工作记录", data, max_candidates=3, retriever=lambda q, cs, limit: [c.record_id for c in cs]
        )
        model_result = SearchResult("match", candidate_id="r000", candidate_ids=("r000",), evidence="支持匹配")
        guarded = guard_semantic_result(prefiltered, model_result)
        self.assertEqual(guarded.status, "match")
        self.assertEqual(guarded.candidate_id, "r000")
        self.assertIn("不代表全库唯一匹配", guarded.evidence)

    def test_subset_candidate_match_is_always_downgraded_to_incomplete(self):
        data = records(3)
        prefiltered = prefilter_work_records(
            "工作记录", data, max_candidates=2, retriever=lambda q, cs, limit: ["r000"]
        )
        model_result = SearchResult("match", candidate_id="r000", candidate_ids=("r000",), evidence="模型称唯一")
        guarded = guard_semantic_result(prefiltered, model_result)
        self.assertEqual(guarded.status, "incomplete")
        self.assertIsNone(guarded.candidate_id)
        self.assertEqual(guarded.candidate_ids, ("r000",))
        self.assertIn("未完成", guarded.evidence)

    def test_complete_set_not_found_is_explicitly_bounded_not_a_library_claim(self):
        data = records(2)
        prefiltered = prefilter_work_records(
            "没有记录", data, max_candidates=3, retriever=lambda q, cs, limit: [c.record_id for c in cs]
        )
        model_result = SearchResult("not_found", evidence="全库没有")
        guarded = guard_semantic_result(prefiltered, model_result)
        self.assertEqual(guarded.status, "not_found")
        self.assertIn("本次提供的候选集合", guarded.evidence)
        self.assertIn("不代表全库无此事", guarded.evidence)
        self.assertNotIn("全库没有", guarded.evidence)

    def test_unknown_retriever_id_is_rejected_not_silently_dropped(self):
        result = prefilter_work_records("费用", records(3), max_candidates=2,
                                        retriever=lambda q, cs, limit: ["not-a-record"])
        self.assertEqual(result.candidates, ())
        self.assertTrue(result.incomplete)
        self.assertTrue(result.uncertain)
        self.assertIsNotNone(result.reason)

    def test_no_relevant_match_within_budget_falls_back_to_full_set(self):
        data = records(3)
        result = prefilter_work_records("火星基地", data, max_candidates=4)
        self.assertEqual(result.candidates, tuple(data))
        self.assertTrue(result.uncertain)
        self.assertFalse(result.incomplete)
        self.assertEqual(result.coverage, "lexical_only")

    def test_over_budget_never_returns_prefix_as_if_complete(self):
        data = records(999)
        result = prefilter_work_records("没有相关词", data, max_candidates=8,
                                        retriever=lambda q, cs, limit: cs[:limit])
        self.assertTrue(result.incomplete)
        self.assertTrue(result.uncertain)
        self.assertEqual(result.stats.budget, 8)
        self.assertLessEqual(len(result.candidates), 8)

    def test_default_retriever_is_pure_python_and_rejects_bad_budget(self):
        self.assertTrue(callable(LocalHybridRetriever()))
        with self.assertRaises(ValueError):
            prefilter_work_records("x", records(1), max_candidates=0)


if __name__ == "__main__":
    unittest.main()
