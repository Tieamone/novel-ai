"""用隔离快照验证重启、跨日和查询失败，不调用模型推理。"""

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core import model_quota as quota
from core import api_client, model_manager


class ModelQuotaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "quota.json"
        self.clock = datetime(2026, 11, 14, 5, tzinfo=timezone.utc)
        self.row = dict(model="new-text", type="Text", remaining=100, total=1000,
                        expires="2026-11-15", autoStop=True)
        for target, value in (("cache_path", lambda: self.path), ("now", lambda: self.clock), ("_error", "")):
            change = patch.object(quota, target, value)
            change.start()
            self.addCleanup(change.stop)

    def seed(self):
        with patch.object(quota, "query_cli", return_value=[self.row]):
            quota.sync_quota()

    def test_restart_uses_timestamp_not_uptime(self):
        self.seed()
        with patch.object(quota, "query_cli", return_value=[self.row]) as query:
            self.clock += timedelta(hours=23)
            quota.sync_quota()
            query.assert_not_called()
            self.clock += timedelta(days=3)
            quota.sync_quota()
            query.assert_called_once()
            self.assertFalse(quota.quota_status()["stale"])

    def test_force_refresh_and_failure_preserves_success_time(self):
        self.seed()
        before = self.path.read_bytes()
        with patch.object(quota, "query_cli", side_effect=ValueError("控制台登录不可用")):
            quota.sync_quota(force=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("登录", quota.quota_status()["error"])
        with patch.object(quota, "query_cli", return_value=[]) as query:
            quota.sync_quota(force=True)
            query.assert_called_once()
        self.assertEqual(quota.read_snapshot()["models"], [])
        self.assertEqual(quota.quota_status()["error"], "")

    def test_merge_filters_audio_and_preserves_manual_metadata(self):
        rows = [self.row, dict(self.row, model="voice", type="Audio")]
        with patch.object(quota, "query_cli", return_value=rows):
            quota.sync_quota()
        merged = quota.merge_quota([dict(model="manual", name="人工配置", provider="dashscope", has_free_quota=True)])
        self.assertEqual([m["model"] for m in merged], ["new-text"])
        self.assertTrue(merged[0]["has_free_quota"])
        self.assertEqual(merged[0]["context_length"], 0)
        manual = quota.merge_quota([dict(model="new-text", name="我的名称", provider="dashscope", context_length=9000)])
        self.assertEqual(manual[0]["name"], "我的名称")
        self.assertEqual(manual[0]["context_length"], 9000)
        self.clock += timedelta(days=2)
        self.assertEqual(quota.merge_quota([]), [])

    def test_only_cli_candidates_and_no_current_model_reinsertion(self):
        from workbench.actions import role_models, set_models
        manual = [dict(model="old", provider="dashscope"), dict(model="mimo-test", provider="mimo")]
        self.assertEqual(quota.merge_quota(manual), [])
        self.seed()
        self.assertEqual([row["model"] for row in quota.merge_quota(manual)], ["new-text"])
        with patch.object(api_client, "_author_model", "old"):
            self.assertNotIn("old", role_models("author", "old"))
            with self.assertRaises(ValueError):
                set_models({"author": "old"})
        with patch.object(quota, "query_cli", return_value=[]):
            quota.sync_quota(force=True)
        self.assertEqual(quota.merge_quota(manual), [])

    def test_invalid_cache_and_invalid_cli_data(self):
        self.path.write_text('{"models":', encoding="utf-8")
        self.assertIsNone(quota.read_snapshot())
        for rows in ({}, [dict(self.row, remaining=-1)], [self.row, self.row]):
            with self.assertRaises(ValueError):
                quota.validate_rows(rows)

    def test_timeout_keeps_snapshot_and_does_not_reset_age(self):
        self.seed()
        before = self.path.read_bytes()
        self.clock += timedelta(hours=24)
        with patch.object(quota, "query_cli", side_effect=subprocess.TimeoutExpired("bl", 30)):
            quota.sync_quota()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(quota.quota_status()["stale"])
        self.assertIn("超时", quota.quota_status()["error"])

    def test_depleted_and_more_than_thirty_candidates(self):
        rows = [dict(self.row, model=f"text-{i}", remaining=0 if i == 0 else 100) for i in range(40)]
        with patch.object(quota, "query_cli", return_value=rows):
            quota.sync_quota()
        candidates = api_client.get_available_models(usage="author")
        self.assertEqual(sum(info["model"].startswith("text-") for info in candidates.values()), 39)
        self.assertNotIn("text-0", [info["model"] for info in candidates.values()])

    def test_candidate_refresh_does_not_switch_roles(self):
        before = (api_client.get_author_model(), api_client.get_reviewer_model(), api_client.get_reader_reviewer_model())
        self.seed()
        with patch.object(model_manager, "_load_custom_models", return_value=[]):
            candidates = api_client.get_available_models(usage="author")
        found = next(info for info in candidates.values() if info["model"] == "new-text")
        self.assertEqual(found["quota"]["remaining"], 100)
        self.assertEqual(before, (api_client.get_author_model(), api_client.get_reviewer_model(), api_client.get_reader_reviewer_model()))

    def test_web_refresh_requires_token_and_returns_quota(self):
        from workbench.server import create_app
        client = create_app().test_client()
        self.assertEqual(client.post("/api/models/refresh", json={}).status_code, 403)
        token = client.get("/api/bootstrap").json["token"]
        with patch.object(quota, "query_cli", return_value=[self.row]) as query:
            response = client.post("/api/models/refresh", json={}, headers={"X-Workbench-Token": token})
            query.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["quota_sync"]["stale"])
        option = next(row for row in response.json["roles"]["author"]["options"] if row["id"] == "new-text")
        self.assertIn("用完即停已开启", option["quota_label"])


if __name__ == "__main__":
    unittest.main()
