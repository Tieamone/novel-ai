import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from core import api_client, config_loader, reader_reviewer, reviewer, writer
from tools import acceptance_run, foreshadow_metamorphic


class AcceptanceToolsTests(unittest.TestCase):
    def test_variant_changes_only_target_paragraph(self):
        replace = foreshadow_metamorphic.replace_paragraph
        self.assertEqual(replace("第一段\n\n旧段\n\n末段", "旧段", "新段"), "第一段\n\n新段\n\n末段")
        for original, old, new in (
            ("旧段\n\n旧段", "旧段", "新段"),
            ("第一段\n\n旧段", "旧段", "新段\n\n模型加的段"),
            ("旧段", "旧段", "旧段"),
            ("不同的段", "旧段", "新段"),
        ):
            with self.assertRaises(ValueError):
                replace(original, old, new)

    def test_metamorphic_metrics_and_incomplete_baseline(self):
        kinds = foreshadow_metamorphic.KINDS
        rows = [{"kind": kind, "confirmed": kind == kinds[0]} for kind in kinds] * 5
        rows[0] = {**rows[0], "confirmed": False}
        rows[1] = {**rows[1], "confirmed": True}
        stats = foreshadow_metamorphic.statistics(rows, 5)
        self.assertEqual(stats["recall"], 0.8)
        self.assertAlmostEqual(stats["false_positive_rate"], 1 / 15)
        self.assertTrue(stats["pass"])
        self.assertFalse(foreshadow_metamorphic.statistics(rows[:-1], 5)["pass"])
        self.assertFalse(foreshadow_metamorphic.statistics([dict(rows[0], scored=False), *rows[1:]], 5)["pass"])

    def test_metamorphic_ignores_model_relabeling_and_retries_only_missing(self):
        tool = foreshadow_metamorphic
        fixture = tool.FIXTURES[0]
        responses = [
            {"真兑现": "林岚用铜牌打开东侧库门。", "只提物品": fixture["paragraph"],
             "否定或传闻": "林岚没去开门。", "主谓颠倒": "东侧库门被林岚用铜牌打开了。"},
            {"只提物品": "林岚手上有一块铜牌。"},
        ]
        with (patch.object(api_client, "call_api", side_effect=[json.dumps(row) for row in responses]) as model,
              patch.object(reviewer, "score_foreshadowing", return_value={"confirmed": []})):
            result = tool.run("生成模型", "主审", "复核", 1)
        self.assertEqual(json.loads(model.call_args.args[1])["类别"], ["只提物品"])
        self.assertEqual(result["scored"], 4)
        self.assertEqual(result["cases"][-1]["replacement"], fixture["role_reversal"])
        self.assertEqual(result["cases"][-1]["source"], "固定角色互换样本")

    def test_budget_stops_before_second_transport_request(self):
        transport = Mock(return_value=("正文", 275))
        budget = acceptance_run.Budget(500, {"reviewer"})
        self.assertEqual(budget.call(transport, "系统", "正文", "reviewer", 100, 0.2), "正文")
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "预算"):
                budget.call(transport, "系统", "正文", "reviewer", 100, 0.2)
        transport.assert_called_once()
        self.assertEqual((budget.requests, budget.tokens), (1, 275))

    def test_explicit_unlimited_budget_keeps_usage_and_model_guard(self):
        transport = Mock(return_value=("正文", 200000))
        budget = acceptance_run.Budget(0, {"reviewer"})
        for _ in range(2):
            self.assertEqual(budget.call(transport, "系统", "正文", "reviewer", 100, 0.2), "正文")
        self.assertEqual((budget.requests, budget.tokens), (2, 400000))
        with self.assertRaisesRegex(ValueError, "未批准"):
            budget.call(transport, "", "", "other", 100, 0.2)
        self.assertEqual(transport.call_count, 2)

    def test_failed_transport_is_charged_and_cannot_retry(self):
        transport = Mock(side_effect=TimeoutError())
        budget = acceptance_run.Budget(1000, {"checker"})
        with self.assertRaises(TimeoutError):
            budget.call(transport, "", "", "checker", 100, 0.2)
        self.assertEqual(budget.tokens, 356)
        with self.assertRaisesRegex(RuntimeError, "中止"):
            budget.call(transport, "", "", "checker", 100, 0.2)
        transport.assert_called_once()

    def test_pair_comparison_normalizes_positions(self):
        with patch.object(acceptance_run.api_client, "call_api", side_effect=[
            '{"total":10,"kept":9,"preferred":"B"}',
            '{"total":10,"kept":10,"preferred":"A"}',
        ]) as call:
            results = acceptance_run.pair_judgments("甲" * 3500, "乙" * 2600, "交货", "裁判")
        self.assertEqual([x["polished_at"] for x in results], ["B", "A"])
        self.assertEqual([x["preferred"] for x in results], ["打磨稿", "打磨稿"])
        self.assertEqual([x["fact_retention"] for x in results], [0.9, 1])
        self.assertEqual(call.call_count, 2)

    def test_mocked_pipeline_crosses_two_compressions_through_real_entry(self):
        cfg = config_loader._get_defaults()
        cfg["novel"].update(chapter_word_min=3000, chapter_word_max=4000,
                            compress_after_chapters=6, recent_summary_count=2)
        report = {"chapters": [], "polishes": [], "checks": {}}
        lines = iter([
            "【系统提示】试机预约已登记。",
            "林岚收好老周开具的盖章借用单。",
            "林岚用借用单证明接头是借的，老陈撤销误扣款。",
            *[f"林岚和货主解决第{n}次交接困难。" for n in range(4, 13)],
        ])
        current = ""

        def body(size):
            return current + "甲" * (size - len(current) - 1) + "。"

        def mock_model(system_prompt, user_message, **kwargs):
            nonlocal current
            if system_prompt == writer.BEAT_PLANNER_SYSTEM:
                current = next(lines)
                return "1. 解决本章问题。"
            if "只输出JSON" in system_prompt and "正文事实核验员" in system_prompt:
                return '{"facts":[{"item":"解决问题","kept":true}],"task_end_kept":true}'
            if "文字打磨师" in system_prompt:
                return body(2600)
            if "独立网文编辑" in system_prompt:
                return '{"total":1,"kept":1,"preferred":"平局"}'
            if system_prompt == reviewer.FORESHADOW_REVIEWER_SYSTEM:
                return json.dumps({"decisions": [{"key": "候选1", "verdict": "confirmed",
                                                  "quote": current, "restated": current}]})
            if "独立的伏笔事实复核员" in system_prompt:
                return '{"decisions":[{"key":"候选1","equivalent":true,"reason":"一致"}]}'
            if system_prompt == reviewer.REVIEWER_SYSTEM:
                return '{"pass":true,"score_l1":42,"score_l2":23,"score_l3":27}'
            if system_prompt == reader_reviewer.READER_REVIEWER_SYSTEM:
                return ('{"pass":true,"score_total":92,"score_ai_authenticity":23,"score_logic":23,'
                        '"score_consistency":23,"score_readability":23}')
            if "提取章节关键信息" in system_prompt:
                return '{"summary":"交接完成。"}'
            if "摘要" in system_prompt:
                return "林岚解决了交接问题，完成了此前的委托。"
            return body(3500)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg["paths"] = {"data_dir": str(root / "data"), "output_dir": str(root / "exports")}
            with (patch.object(config_loader, "_config", cfg),
                  patch.object(api_client, "call_api", side_effect=mock_model),
                  patch.object(writer, "is_high_capacity_model", return_value=True)):
                acceptance_run.run_pipeline(root, 12, "模拟裁判", report, seeded=True)
        self.assertTrue(all(value is True for value in report["checks"].values()), report)
        self.assertEqual([row["stage"] for row in report["context"] if row["chapter"] in (8, 12, 13)], [5, 5, 10])
        self.assertEqual(report["metrics"]["polish_acceptance"], 1)
