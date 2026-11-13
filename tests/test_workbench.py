import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import main as cli
from core import api_client, config_loader, db, outline_manager, planner, reader_reviewer, reviewer, writer
from core.memory_manager import MemoryManager
from core.utils import DatabaseTransaction, with_db_connection
from workbench.server import create_app


BOOK = "海边手记"
GOAL = "主角抵达旧码头寻找失踪船只留下的航行记录"
PARAGRAPH = "雨停后，她沿着码头走到仓库前，抬手推开了那扇半掩着的门。"
CONTENT = "\n\n".join([PARAGRAPH, "灯塔上的窗户亮了一下。", *[
    f"她在木板边看到了第{i}个标记，停下脚步，把手里的纸页摊在窗沿上。"
    for i in range(12)
]])


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        cfg = config_loader._get_defaults()
        cfg["paths"] = {"data_dir": str(self.root / "data"), "output_dir": str(self.root / "output")}
        cfg["novel"].update(chapter_word_min=80, chapter_word_target=300, chapter_word_max=1200)
        for target, attr, value in [
            (config_loader, "_config", cfg), (config_loader, "PROJECT_ROOT", self.root),
            (db, "_initialized_novels", set()), (db, "_wal_configured_paths", set()),
        ]:
            change = patch.object(target, attr, value)
            change.start()
            self.addCleanup(change.stop)
        db.init_database(BOOK)
        cli._write_novel_info(BOOK, "悬疑")
        self.client = create_app().test_client()
        self.headers = {"X-Workbench-Token": self.client.get("/api/bootstrap").json["token"]}

    def get(self, path):
        response = self.client.get(path, query_string={"novel": BOOK})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def submit(self, kind, novel=BOOK, **params):
        response = self.client.post("/api/jobs", json={"kind": kind, "novel": novel, **params},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        return response.json["id"]

    def wait(self, job_id, *states, prompt_id=None):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            snapshot = self.get("/api/jobs/" + job_id)
            if snapshot["status"] in states and (prompt_id is None or snapshot["prompt"]["id"] == prompt_id):
                return snapshot
            if snapshot["status"] in ("failed", "completed", "cancelled"):
                self.fail(str(snapshot))
            time.sleep(.02)
        self.fail(f"任务未到达 {states}")

    def answer(self, job_id, prompt_id, value, expected=200):
        response = self.client.post("/api/jobs/" + job_id + "/answer",
                                    json={"prompt_id": prompt_id, "value": value}, headers=self.headers)
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True))

    def completed(self, kind, **params):
        return self.wait(self.submit(kind, **params), "completed")["result"]

    def seed_task(self, chapter=1, status="待处理"):
        cli._upsert_task_status(BOOK, chapter, GOAL, "铺垫", status)

    def test_generation_resume_stop_and_export(self):
        self.seed_task()
        editor_response = json.dumps({
            "pass": True, "score_total": 94, "score_l1": 38, "score_l2": 28, "score_l3": 28,
            "cut_suggestions": [{"start": "灯塔上的窗户亮了一下。", "reason": "删去重复描写"}],
        }, ensure_ascii=False)
        reader_response = json.dumps({
            "pass": True, "score_total": 92, "score_ai_authenticity": 23,
            "score_logic": 23, "score_consistency": 23, "score_readability": 23,
        })
        with (
            patch.object(writer, "_plan_chapter_beats", return_value="查看码头的航行记录"),
            patch.object(writer, "is_high_capacity_model", return_value=True),
            patch.object(writer, "call_author_api", return_value=CONTENT),
            patch.object(writer, "_self_check_and_revise", side_effect=lambda **kwargs: kwargs["full_content"]),
            patch.object(reviewer, "call_reviewer_api", return_value=editor_response),
            patch.object(reader_reviewer, "call_reader_reviewer_api", return_value=reader_response),
            patch.object(api_client, "call_reviewer_api", return_value='{"summary":"找到了码头记录"}'),
        ):
            job_id = self.submit("generate")
            waiting = self.wait(job_id, "waiting")
            self.assertEqual(waiting["prompt"]["options"][0][1], "查看建议")
            self.assertEqual(self.get("/api/bootstrap")["job"]["id"], job_id)
            duplicate = self.client.post("/api/jobs", json={"kind": "generate", "novel": BOOK}, headers=self.headers)
            self.assertEqual(duplicate.status_code, 400)
            cancelled = self.client.post("/api/jobs/" + job_id + "/cancel", json={}, headers=self.headers)
            self.assertEqual(cancelled.status_code, 200)
            self.wait(job_id, "cancelled")
            self.assertEqual(self.get("/api/book")["tasks"][0]["status"], "待处理")
            self.assertEqual(self.get("/api/checkpoint/1")["history"][-1]["end_reason"], "写作中断")

            job_id = self.submit("generate")
            waiting = self.wait(job_id, "waiting")
            prompt_id = waiting["prompt"]["id"]
            self.answer(job_id, prompt_id, "y")
            while True:
                waiting = self.wait(job_id, "waiting")
                if waiting["prompt"]["id"] != prompt_id:
                    break
                time.sleep(.02)
            self.answer(job_id, prompt_id, "y", expected=400)
            self.answer(job_id, waiting["prompt"]["id"], "y")
            completed = self.wait(job_id, "completed")
            self.assertEqual(completed["result"]["chapter"], 1)
        chapter = self.get("/api/chapter/1")
        self.assertEqual(chapter["status"], "已审核")
        self.assertEqual(chapter["summary"], "找到了码头记录")
        self.assertNotIn("灯塔上的窗户亮了一下。", chapter["content"])
        book = self.get("/api/book")
        self.assertEqual(book["tasks"][0]["status"], "已完成")
        self.assertEqual(book["progress"]["approved"], 1)
        self.assertEqual(book["progress"]["quality_count"], 1)
        self.assertEqual(book["progress"]["avg_total"], 94)
        self.assertEqual(book["progress"]["task_status"]["已完成"], 1)
        self.assertEqual(self.completed("export")["downloads"], ["第001章.txt"])
        response = self.client.get("/api/download/第001章.txt", query_string={"novel": BOOK})
        with response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(PARAGRAPH, response.get_data(as_text=True))

    def test_task_style_and_foreshadow(self):
        self.seed_task()
        self.completed("task", chapter=1, plot_goal=GOAL + "以及仓库里的旧账本", emotion_tag="冲突")
        self.completed("style", style="custom", custom="短句与对话")
        self.completed("intent", field="focus", content="保持第一人称叙事")
        with patch("core.style_analyzer.call_author_api", return_value='{"rhythm":"短句较多"}') as provider:
            self.completed("fingerprint", content=CONTENT)
        provider.assert_called_once()
        fingerprint = self.root / "data" / BOOK / "style_fingerprint.json"
        self.assertEqual(json.loads(fingerprint.read_text(encoding="utf-8"))["style_guide"]["rhythm"], "短句较多")
        fields = {"description": "钥匙上的编号对应一艘失踪船", "category": "情节伏笔",
                  "plant_chapter": 1, "resolve_chapter": 5, "importance": 3, "notes": ""}
        self.completed("foreshadow", **fields)
        book = self.get("/api/book")
        self.assertEqual(book["intent"]["focus"], "保持第一人称叙事")
        self.assertEqual(book["style"], "custom:短句与对话")
        self.assertEqual(book["tasks"][0]["original_plot_goal"], GOAL)
        fid = book["foreshadow"][0]["fid"]
        self.completed("foreshadow", fid=fid, status="planted", **fields)
        self.assertEqual(self.get("/api/book")["foreshadow"][0]["status"], "planted")
        self.completed("delete_foreshadow", fid=fid, confirm="YES")
        self.assertEqual(self.get("/api/book")["foreshadow"], [])

    def test_failed_review_checkpoint_and_deletion(self):
        mm = MemoryManager(BOOK)
        for chapter in (1, 2):
            self.seed_task(chapter, "审稿失败" if chapter == 1 else "进行中")
            mm.save_chapter(chapter, "码头", CONTENT, "审稿失败", GOAL, "铺垫")
        mm.add_summary(1, "停在码头")
        mm.update_chapter_summary(1, "停在码头")
        with with_db_connection(BOOK) as conn, DatabaseTransaction(conn):
            conn.execute("INSERT INTO beats_cache (chapter_num, beats_text) VALUES (1, '检查码头')")
            conn.execute("UPDATE chapter_tasks SET updated_at='2000-01-01' WHERE chapter_num=2")
        self.assertEqual(self.get("/api/book")["tasks"][1]["stale"], 1)
        self.assertIn("检查码头", self.get("/api/checkpoint/1")["cache"]["preview"])
        self.completed("recover", chapter=1, method="3")
        self.assertEqual(self.get("/api/chapter/1")["status"], "强制通过")
        self.completed("reset", chapter=2, confirm="YES")
        self.completed("clear_beats", chapter=1, confirm="YES")
        self.assertIsNone(self.get("/api/checkpoint/1")["cache"])
        self.completed("rewrite", chapter=1, confirm="YES")
        self.assertIsNone(self.get("/api/chapter/1")["content"])
        with with_db_connection(BOOK) as conn:
            self.assertEqual(conn.execute("SELECT summary FROM summaries WHERE chapter_num=1").fetchone()[0], "停在码头")
        self.completed("delete_chapters", first=1, last=2, confirm="YES")
        self.assertEqual(self.get("/api/book")["chapters"], [])
        self.completed("delete_book", name_confirmation=BOOK, confirm="YES")
        self.assertEqual(self.get("/api/bootstrap")["novels"], [])

    def test_batch_and_review_recovery_modes(self):
        for chapter in (1, 2, 3):
            self.seed_task(chapter)
        with (
            patch.object(writer, "_plan_chapter_beats", return_value="查看码头的航行记录"),
            patch.object(writer, "is_high_capacity_model", return_value=True),
            patch.object(writer, "call_author_api", return_value=CONTENT),
            patch.object(writer, "_self_check_and_revise", side_effect=lambda **kwargs: kwargs["full_content"]),
            patch.object(reviewer, "call_reviewer_api", return_value='{"pass":true,"score_l1":39,"score_l2":29,"score_l3":29}'),
            patch.object(reader_reviewer, "call_reader_reviewer_api", return_value='{"pass":true,"score_total":93}'),
            patch.object(api_client, "call_reviewer_api", return_value='{"summary":"码头的记录"}'),
        ):
            result = self.completed("generate", count=2)
            self.assertEqual(result["chapter"], 2)
            self.assertEqual([row["status"] for row in self.get("/api/book")["tasks"]],
                             ["已完成", "已完成", "待处理"])
            mm = MemoryManager(BOOK)
            for chapter, method in [(1, "1"), (2, "2")]:
                mm.update_chapter_status(chapter, "审稿失败")
                self.seed_task(chapter, "审稿失败")
                self.completed("recover", chapter=chapter, method=method)
                self.assertEqual(self.get(f"/api/chapter/{chapter}")["status"], "已审核")
        self.completed("delete_chapters", first=2, confirm="YES")
        self.assertEqual([row["chapter_num"] for row in self.get("/api/book")["chapters"]], [1])

    def test_foreshadow_suggestions_require_confirmation(self):
        (self.root / "data" / BOOK / "master_outline.md").write_text("调查灯塔的航行记录。", encoding="utf-8")
        MemoryManager(BOOK).save_chapter(1, "灯塔", CONTENT, "已审核", GOAL, "铺垫")
        suggestion = {
            "description": "灯塔的刻痕与失踪船有关", "category": "情节伏笔",
            "plant_chapter": 3, "resolve_chapter": 15, "importance": 3,
        }
        with (
            patch.object(api_client, "call_author_api", return_value=json.dumps([suggestion], ensure_ascii=False)) as call,
            patch.object(outline_manager, "review_outline_foreshadow", return_value=None),
            patch.object(outline_manager, "_print_review_table"),
        ):
            job_id = self.submit("suggest")
            waiting = self.wait(job_id, "waiting")
            self.assertIn(suggestion["description"], waiting["prompt"]["context"])
            self.assertIn("当前已完成章节：第1章", call.call_args.kwargs["user_message"])
            self.assertEqual(self.get("/api/book")["foreshadow"], [])
            self.answer(job_id, waiting["prompt"]["id"], "a")
            self.wait(job_id, "completed")
        self.assertEqual(self.get("/api/book")["foreshadow"][0]["plant_chapter"], 3)

    def test_create_and_import(self):
        config_loader._config["novel"]["task_card_review_enabled"] = False
        tasks = json.dumps([{"chapter_num": num, "plot_goal": GOAL, "emotion_tag": "铺垫"}
                            for num in range(1, 11)], ensure_ascii=False)
        with (patch.object(outline_manager, "generate_outline_foreshadow"),
              patch.object(planner, "call_author_api", side_effect=[
                  "海边小镇只有一座仓库。", '[{"name":"林舟","role":"主角"}]', tasks,
              ])):
            job_id = self.submit("create", novel="城南故事", genre="都市", keywords="老书店")
            for prompt_id, value in enumerate(("5", "10", "2", "林舟来到海边，寻找旧船。", "1", "2", "林舟", "1"), 1):
                waiting = self.wait(job_id, "waiting", prompt_id=prompt_id)
                self.answer(job_id, waiting["prompt"]["id"], value)
            self.assertEqual(self.wait(job_id, "completed")["result"]["novel"], "城南故事")
        created = self.client.get("/api/book", query_string={"novel": "城南故事"}).json
        self.assertEqual(created["target"], 10)
        self.assertEqual(len(created["tasks"]), 10)
        content = "书名：导入的故事\n类型：悬疑\n目标章数：20\n【大纲】\n旧仓库\n【主要角色】\n林舟\n【时间线】\n十月\n【核心冲突】\n寻找旧船"
        imported_tasks = json.dumps([{"chapter_num": num, "plot_goal": GOAL, "emotion_tag": "铺垫"}
                                     for num in range(1, 21)], ensure_ascii=False)
        with (patch.object(outline_manager, "generate_outline_foreshadow"),
              patch.object(planner, "call_author_api", return_value=imported_tasks) as provider):
            job_id = self.submit("import", content=content)
            waiting = self.wait(job_id, "waiting")
            self.answer(job_id, waiting["prompt"]["id"], "yes")
            style = self.wait(job_id, "waiting", prompt_id=2)
            self.answer(job_id, style["prompt"]["id"], "1")
            self.assertEqual(self.wait(job_id, "completed")["result"]["novel"], "导入的故事")
        provider.assert_called_once()
        imported = self.client.get("/api/book", query_string={"novel": "导入的故事"}).json
        self.assertEqual(len(imported["tasks"]), 20)
        self.assertIn("十月", (self.root / "data" / "导入的故事" / "settings.md").read_text(encoding="utf-8"))

    def test_local_requests_and_model_session(self):
        response = self.client.post("/api/jobs", json={"kind": "export", "novel": BOOK})
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/api/bootstrap", headers={"Origin": "http://127.0.0.1:1234"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/bootstrap", headers={"Host": "example.org"}).status_code, 400)
        response = self.client.get("/api/book", query_string={"novel": "../test"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/download/other.txt", query_string={"novel": BOOK}).status_code, 404)
        option = {"name": "测试模型", "model": "test-model", "provider": "dashscope", "env_key": "TEST_API_KEY"}
        with (
            patch.dict("os.environ", {"TEST_API_KEY": "local-test-value"}),
            patch.object(api_client, "get_available_models", return_value={"1": option}),
            patch.object(api_client, "_author_model", "original-model"),
            patch.object(api_client, "_author_provider", "dashscope"),
            patch.object(api_client, "_reviewer_model", "test-model"),
            patch.object(api_client, "_reader_reviewer_model", "test-model"),
            patch.object(api_client, "_switch_history", []),
            patch.object(api_client, "call_api", return_value="ok") as provider,
        ):
            result = self.completed("models", author="test-model")
            self.assertTrue(result["saved"])
            self.assertEqual(result["verification"][0]["roles"], ["作者模型", "责任编辑", "读者审稿"])
            self.assertEqual(result["verification"][0]["detail"], "ok")
            provider.assert_called_once()
            self.assertEqual(provider.call_args.kwargs["model_name"], "test-model")
            result = self.completed("models")
            self.assertFalse(result["saved"])
            self.assertEqual(result["verification"][0]["status"], "passed")
            self.assertEqual(provider.call_count, 2)
            models = self.get("/api/models")
            self.assertEqual(models["roles"]["author"]["current"], "test-model")
            self.assertNotIn("local-test-value", json.dumps(models))

    def test_model_results_distinguish_failed_skipped_and_passed(self):
        options = {
            str(i): {"name": model, "model": model, "provider": "dashscope", "env_key": env}
            for i, (model, env) in enumerate([
                ("normal-model", "TEST_API_KEY"), ("empty-model", "TEST_API_KEY"), ("no-key", "MISSING_TEST_API_KEY"),
            ])
        }
        with (
            patch.dict("os.environ", {"TEST_API_KEY": "local-test-value", "MISSING_TEST_API_KEY": ""}),
            patch.object(api_client, "get_available_models", return_value=options),
            patch.object(api_client, "_author_model", "original-model"),
            patch.object(api_client, "call_api", side_effect=["local-test-value", ""]) as provider,
        ):
            result = self.completed("models", author="normal-model", reviewer="empty-model", reader_reviewer="no-key")
        self.assertEqual(provider.call_count, 2)
        self.assertFalse(result["saved"])
        self.assertEqual([row["status"] for row in result["verification"]], ["passed", "failed", "skipped"])
        self.assertEqual(result["verification"][0]["detail"], "已隐藏")
        self.assertIsInstance(result["verification"][0]["elapsed_ms"], int)
        self.assertNotIn("local-test-value", json.dumps(result))

    def test_provider_errors_do_not_report_generated_artifacts(self):
        (self.root / "data" / BOOK / "master_outline.md").write_text("调查灯塔的航行记录。", encoding="utf-8")
        with patch.object(api_client, "call_author_api", side_effect=RuntimeError("请求失败")):
            error = self.wait(self.submit("suggest"), "failed")
            self.assertIn("模型调用失败", error["error"])
        with patch("core.style_analyzer.call_author_api", side_effect=RuntimeError("请求失败")):
            error = self.wait(self.submit("fingerprint", content=CONTENT), "failed")
            self.assertIn("模型未完成", error["error"])
        self.assertFalse((self.root / "data" / BOOK / "style_fingerprint.json").exists())
        self.assertEqual(self.get("/api/book")["foreshadow"], [])
        self.assertIn("没有可导出", self.wait(self.submit("export"), "failed")["error"])
        MemoryManager(BOOK).save_chapter(1, "空章", "", "审稿失败", GOAL, "铺垫")
        self.assertIn("正文为空", self.wait(self.submit("recover", chapter=1, method="1"), "failed")["error"])

    def test_legacy_book_opens_read_only_and_asks_before_migration(self):
        name = "旧版小说"
        with with_db_connection(name) as conn, DatabaseTransaction(conn):
            conn.execute("CREATE TABLE chapters (id INTEGER PRIMARY KEY, chapter_num INTEGER UNIQUE, content TEXT)")
            conn.execute("CREATE TABLE chapter_tasks (chapter_num INTEGER UNIQUE, plot_goal TEXT, created_at TEXT)")
            conn.execute("INSERT INTO chapters VALUES (1, 1, '旧版正文')")
        db_path = self.root / "data" / name / "novel.db"
        before = db_path.read_bytes()
        response = self.client.get("/api/book", query_string={"novel": name})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.json["legacy"])
        self.assertEqual(response.json["chapters"][0]["chars"], 4)
        self.assertEqual(db_path.read_bytes(), before)
        job_id = self.submit("intent", novel=name, field="focus", content="保存之前确认")
        waiting = self.wait(job_id, "waiting")
        self.answer(job_id, waiting["prompt"]["id"], "no")
        self.wait(job_id, "completed")
        self.assertEqual(db_path.read_bytes(), before)
        job_id = self.submit("intent", novel=name, field="focus", content="确认后保存")
        waiting = self.wait(job_id, "waiting")
        self.answer(job_id, waiting["prompt"]["id"], "yes")
        self.wait(job_id, "completed")
        self.assertEqual((db_path.parent / "current_focus.md").read_text(encoding="utf-8"), "确认后保存")
        self.assertFalse(self.client.get("/api/book", query_string={"novel": name}).json["legacy"])


if __name__ == "__main__":
    unittest.main()
