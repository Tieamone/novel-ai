"""创作分工回归：全部使用隔离数据与模拟响应。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json
import sqlite3

from core import api_client, book_health, config_loader, db, outline_manager, planner, reviewer, writer
from core.memory_manager import MemoryManager


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = config_loader._get_defaults()
        config['novel'].update(chapter_word_min=3000, chapter_word_max=4000)
        config['paths'] = {'data_dir': str(self.root / 'data'),
                           'output_dir': str(self.root / 'output')}
        for target, attr, value in (
            (config_loader, '_config', config),
            (db, '_initialized_novels', set()),
            (db, '_wal_configured_paths', set()),
        ):
            change = patch.object(target, attr, value)
            change.start()
            self.addCleanup(change.stop)
        self.mm = MemoryManager('创作分工测试')

    def test_old_overdue_hint_does_not_become_writing_or_review_task(self):
        self.mm.add_foreshadowing('OLD', 1, '紫色石头里藏着未知身份', '第2章')
        ctx = self.mm.load_context(10)
        self.assertTrue(ctx['active_foreshadowing'])
        for prompt in (
            writer.build_full_chapter_prompt(ctx, 10, '去码头送货', '铺垫', ''),
            writer.build_writer_prompt(ctx, 10, '去码头送货', '铺垫'),
            reviewer.build_review_prompt(ctx, 10, '他把货送到了。', '去码头送货'),
        ):
            self.assertIn('本章无伏笔任务', prompt)
            self.assertNotIn('紫色石头', prompt)
        self.assertEqual(reviewer._build_foreshadow_list(ctx)['to_resolve'], [])

    def test_editor_blocks_unplanned_foreshadowing_even_with_high_scores(self):
        result = reviewer._normalize_review_result({
            'pass': True, 'score_l1': 43, 'score_l2': 24, 'score_l3': 28,
            'veto_items': [{'code': 'unplanned_foreshadowing', 'reason': '章末加入无名人的警告'}],
        })
        self.assertFalse(result['pass'])
        self.assertEqual(result['veto_items'][0]['code'], 'unplanned_foreshadowing')

    def test_beats_and_both_writer_paths_share_macro_task(self):
        (self.mm.data_dir / 'master_outline.md').write_text('第3章发现仓库停运。', encoding='utf-8')
        fid = outline_manager.add_outline_foreshadow(
            self.mm.novel_name, '埋入：收好通行证；兑现：用证件进入仓库', '情节伏笔', 2, 3)
        ctx = self.mm.load_context(2)
        block = outline_manager.format_chapter_foreshadow(ctx, 2)
        self.assertIn(fid, block)
        self.assertNotIn('进入仓库', block)
        candidates = reviewer._build_foreshadow_list(ctx)
        self.assertEqual(candidates['to_plant'][0]['description'], '收好通行证')
        self.assertFalse(self.mm.load_context(3)['outline_tasks']['to_resolve'])
        self.assertIn(f'第3章建议兑现: {fid}',
                      planner._add_outline_fs_to_prompt('任务', self.mm.novel_name, 1, 3, 'guided'))
        with patch.object(writer, 'call_author_api', return_value='1. 收好通行证') as call:
            writer._plan_chapter_beats(ctx, 2, '收货', '铺垫')
            self.assertIn(block, call.call_args.kwargs['user_message'])
            self.assertNotIn('仓库停运', call.call_args.kwargs['user_message'])
        for prompt in (
            writer.build_full_chapter_prompt(ctx, 2, '收货', '铺垫', ''),
            writer.build_writer_prompt(ctx, 2, '收货', '铺垫'),
            writer.build_continue_prompt(2, '收货', '铺垫', '前半章', foreshadow_block=block),
            reviewer.build_review_prompt(ctx, 2, '正文', '收货'),
        ):
            self.assertIn(block, prompt)
            self.assertNotIn('仓库停运', prompt)

    def test_light_polish_applies_only_located_phrases_after_fact_check(self):
        config_loader._config["novel"]["chapter_polish_enabled"] = True
        original = "第1章 交货\n\n他不由自主地看了一眼秤。\n\n“少了一斤。”她收回了签字笔。"
        candidate = original.replace("不由自主地看了一眼", "瞥了眼")
        edits = '{"edits":[{"old":"不由自主地看了一眼","new":"瞥了眼"}]}'
        good = '{"facts":[{"item":"交货","kept":true}],"task_end_kept":true}'
        lost = '{"facts":[{"item":"交货","kept":false}],"task_end_kept":true}'
        end_lost = '{"facts":[{"item":"交货","kept":true}],"task_end_kept":false}'
        for facts in (good, lost, end_lost, '{"facts":[],"task_end_kept":true}', OSError("核验中断")):
            with (self.subTest(facts=facts), patch.object(writer, 'call_author_api', return_value=edits),
                  patch.object(writer, 'call_reviewer_api', side_effect=[facts]) as check):
                text, report = writer._polish_chapter_ex(original, 4096, plot_goal="交货")
                self.assertEqual(text, candidate if facts == good else original)
                self.assertEqual(report["accepted"], facts == good)
                self.assertIn("交货", check.call_args.kwargs["user_message"])
        for raw in ("整章重写。", '{"edits":[]}', '{"edits":[{"old":"秤","new":""}]}',
                    '{"edits":[{"old":"不存在","new":"改词"}]}',
                    '{"edits":[{"old":"了","new":"着"}]}',
                    '{"edits":[{"old":"看了一眼","new":"看了看"},{"old":"一眼秤","new":"秤盘"}]}'):
            with (self.subTest(raw=raw), patch.object(writer, 'call_author_api', return_value=raw),
                  patch.object(writer, 'call_reviewer_api') as check):
                self.assertEqual(writer._polish_chapter(original, 4096), original)
                check.assert_not_called()
        with (patch.object(writer, 'call_author_api', side_effect=OSError),
              patch.object(writer, 'call_reviewer_api') as check):
            self.assertEqual(writer._polish_chapter(original, 4096), original)
            check.assert_not_called()
        self.assertFalse(writer.chapter_length_issue(original))
        self.assertTrue(writer.chapter_length_issue("  "))
        self.assertTrue(writer.chapter_length_issue("甲" * 4001))

    def test_macro_audit_failures_never_insert(self):
        (self.mm.data_dir / "master_outline.md").write_text("主角核对凭证。", encoding="utf-8")
        suggestion = '[{"description":"保留凭证","plant_chapter":1,"resolve_chapter":3}]'
        for response in (OSError("模拟服务故障"), "没有JSON", "{无效:}"):
            with patch.object(api_client, "call_author_api", return_value=suggestion), \
                    patch.object(api_client, "call_reviewer_api", side_effect=[response] * 3) as call:
                self.assertEqual(outline_manager.generate_outline_foreshadow(self.mm.novel_name, 3), 0)
                self.assertEqual(call.call_count, 3)
                self.assertEqual(outline_manager.list_outline_foreshadow(self.mm.novel_name), [])

    def test_macro_audit_supplements_missing_and_parses_strict_booleans(self):
        with patch.object(api_client, "call_reviewer_api", side_effect=[
            '{"1":{"passed":"false"},"3":{"passed":"true"},"4":{"passed":1},"5":{"passed":"是"}}',
            '{"2":{"passed":true}}',
        ]) as call:
            result = outline_manager.review_outline_foreshadow("", [{}] * 5, "", "")
            self.assertEqual([r["passed"] for r in result.values()], [False, True, True, False, True])
            self.assertEqual(call.call_count, 2)

    def test_writing_context_raises_database_read_failure(self):
        with patch.object(outline_manager, "with_db_connection", side_effect=sqlite3.OperationalError("模拟")):
            with self.assertRaises(sqlite3.OperationalError):
                self.mm.load_context(1)
            self.assertEqual(outline_manager.get_chapter_outline_tasks(self.mm.novel_name, 1, readonly=True),
                             {"to_plant": [], "to_resolve": []})
            result = reviewer.review_chapter(self.mm.novel_name, 1, "甲" * 3499 + "。", "交货")
            self.assertTrue(result["review_error"])

    def test_split_and_revision_apply_same_polish_guard_without_extra_supplement(self):
        config_loader._config["novel"]["chapter_polish_enabled"] = True
        goal = "本章停在交货完成"
        first, second = "甲方看了一眼笔尖。", "“少了一斤。”她收回了签字笔。"
        original = first + "\n\n" + second
        edits = '{"edits":[{"old":"看了一眼","new":"瞥了眼"}]}'
        expected = original.replace("看了一眼", "瞥了眼")
        facts = '{"facts":[{"item":"交货","kept":true}],"task_end_kept":true}'
        with (patch.object(writer, "is_high_capacity_model", return_value=False),
              patch.object(writer, "_plan_chapter_beats", return_value="1. 买家拒签。\n2. 发现秤偏。\n3. 复秤后签收。"),
              patch.object(writer, "call_reviewer_api", return_value=facts) as verify,
              patch.object(writer, "call_author_api", side_effect=[first, second, edits]) as author):
            self.assertEqual(writer.write_chapter(self.mm.novel_name, 1, goal), expected)
            self.assertEqual(author.call_count, 3)
            self.assertNotIn("复秤后签收", author.call_args_list[0].kwargs["user_message"])
            self.assertIn("复秤后签收", author.call_args_list[1].kwargs["user_message"])
            for call in author.call_args_list[:2]:
                self.assertEqual(sum(call.kwargs[key].count(writer.NARRATIVE_DETAIL_RULE)
                                     for key in ("system_prompt", "user_message")), 1)
            self.assertIn(goal, verify.call_args.kwargs["user_message"])
        with (patch.object(writer, "call_reviewer_api", return_value=facts),
              patch.object(writer, "call_author_api", side_effect=[original, edits]) as author):
            self.assertEqual(writer.revise_chapter(self.mm.novel_name, 1, original, "按原文修订", goal), expected)
            self.assertEqual(author.call_count, 2)

    def test_short_revision_goes_to_review_without_supplement(self):
        config_loader._config["novel"]["chapter_polish_enabled"] = False
        short = "甲" * 1190 + "。\n\n仓库终于交货。"
        with (patch.object(writer, "call_author_api", side_effect=[short]) as author,
              patch.object(writer, "call_reviewer_api") as check):
            actual = writer.revise_chapter(self.mm.novel_name, 1, "甲" * 3499 + "。", "删去无关尾声", "仓库交货")
        self.assertEqual(actual, short)
        self.assertEqual(author.call_count, 1)
        check.assert_not_called()
        self.assertFalse(writer.chapter_length_issue(actual))

    def test_whole_draft_with_polish_off_preserves_scene(self):
        config_loader._config["novel"]["chapter_polish_enabled"] = False
        body = "第1章 称重\n\n“少了一斤。”她把签字笔收了回去。\n\n买家把秤推回柜台。"
        with (patch.object(writer, "is_high_capacity_model", return_value=True),
              patch.object(writer, "_plan_chapter_beats", return_value="1. 买家要求复称。"),
              patch.object(writer, "call_author_api", return_value=body) as author,
              patch.object(writer, "call_reviewer_api") as verify):
            self.assertEqual(writer.write_chapter(self.mm.novel_name, 1, "买家要求复称"), body)
        author.assert_called_once()
        prompt = author.call_args.kwargs
        self.assertEqual((prompt["system_prompt"] + prompt["user_message"]).count(writer.NARRATIVE_DETAIL_RULE), 1)
        verify.assert_not_called()

    def test_tail_cut_is_located_and_reaudited_before_commit(self):
        prefix = "甲" * 1699 + "。\n\n他确认设备存在隐患。"
        tail = "他给主管发去了一条挑衅短信。\n\n他决定连夜取证。"
        draft = prefix + "\n\n" + tail
        rejected = {"pass": False, "score_total": 68, "tail_cut": {
            "goal_complete": True, "start": tail.split("\n\n")[0]},
            "foreshadow_reviewed_content": draft, "foreshadow_score": {"confirmed": [{"fid": "旧结论"}]}}
        self.assertEqual(reviewer._trim_reviewed_tail(draft, rejected), prefix)
        for cut in ({"goal_complete": False, "start": tail},
                    {"goal_complete": "true", "start": tail},
                    {"goal_complete": True, "start": "不存在的原句。"},
                    {"goal_complete": True, "start": "给主管"}):
            self.assertEqual(reviewer._trim_reviewed_tail(draft, {**rejected, "tail_cut": cut}), draft)
        self.assertEqual(reviewer._trim_reviewed_tail(draft + "\n\n" + tail, rejected), draft + "\n\n" + tail)
        approved = {"pass": True, "score_total": 90, "foreshadow_reviewed_content": prefix,
                    "foreshadow_score": {"confirmed": []}}
        self.mm.save_chapter(1, "正文", draft)
        with (patch.object(writer, "write_chapter", return_value=draft),
              patch.object(reviewer, "review_chapter", side_effect=[rejected, approved]) as review,
              patch.object(reviewer, "reader_review_chapter", return_value={"pass": True, "score_total": 90}) as reader):
            actual = reviewer.write_and_review(self.mm.novel_name, 1, "确认设备隐患", max_retry=1)
        self.assertEqual(actual, prefix)
        self.assertEqual(review.call_args.args[2], prefix)
        self.assertEqual(reader.call_args.args[2], prefix)
        self.assertEqual(self.mm.load_chapter(1)["content"], prefix)

    def test_short_synopsis_still_needs_editor_approval(self):
        novel = self.mm.novel_name
        self.mm.save_chapter(1, "梗概", "主角发现故障，修好机器。")
        verdict = {"pass": False, "score_l1": 20, "score_l2": 10, "score_l3": 15,
                   "suggestions": "缺少发现故障到完成修复的必要因果"}
        with patch.object(reviewer, "call_reviewer_api", return_value=json.dumps(verdict)) as model:
            result = reviewer.review_chapter(novel, 1, "主角发现故障，修好机器。", "修复机器")
        self.assertTrue(model.called)
        self.assertFalse(result["pass"])

    def test_minor_overflow_cuts_only_selected_middle_paragraph(self):
        redundant = "雨声又响了一阵，他再次看了看窗外。"
        closing = "“订单就这么定了。”她把合同交给他。"
        room = 4015 - len(redundant) - len(closing) - 4
        beginning = "甲" * (room - 1) + "。"
        original = "\n\n".join((beginning, redundant, closing))
        with patch.object(writer, "call_author_api", return_value='{"paragraph":1}') as call:
            actual = writer._fit_chapter_length(original, 2250, 4000)
            self.assertEqual(actual, beginning + "\n\n" + closing)
            self.assertLessEqual(len(actual), 4000)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.kwargs["max_tokens"], 128)
        for response in ('{"paragraph":0}', '{"paragraph":2}', '{"paragraph":true}', '{"paragraph":null}', '非法输出'):
            with self.subTest(response=response), patch.object(writer, "call_author_api", return_value=response):
                self.assertEqual(writer._fit_chapter_length(original, 2250, 4000), original)
        duplicate = original.replace(closing, redundant + "\n\n" + closing)
        with patch.object(writer, "call_author_api") as call:
            self.assertEqual(writer._fit_chapter_length(duplicate, 2250, 4000), duplicate)
            call.assert_not_called()

    def test_plant_deferral_confirmation_and_delete(self):
        novel = self.mm.novel_name
        fid = outline_manager.add_outline_foreshadow(novel, "收起借用单", "情节伏笔", 1, 4)
        body = "他接过借用单。"

        def commit(chapter, confirmed=False):
            self.mm.save_chapter(chapter, "正文", body)
            reviewer.commit_approved_chapter(novel, chapter, body, {
                "pass": True, "foreshadow_reviewed_content": body,
                "foreshadow_score": {"confirmed": [{"fid": fid, "source": "大纲", "action": "plant"}] if confirmed else [],
                                     "unconfirmed_plant": [] if confirmed else [fid]},
            })
            return outline_manager.list_outline_foreshadow(novel)[0]

        self.assertEqual(commit(1)["plant_chapter"], 2)
        self.mm.save_chapter(2, "强制稿", body)
        reviewer._force_approve_chapter(novel, 2, body, self.mm)
        self.assertEqual(outline_manager.list_outline_foreshadow(novel)[0]["plant_chapter"], 2)
        self.assertEqual(commit(2)["plant_chapter"], 3)
        with patch.object(reviewer, "print", create=True) as log:
            self.assertEqual(commit(3)["plant_chapter"], 3)
            self.assertIn("无顺延空间", str(log.call_args_list))
        self.assertFalse(self.mm.load_context(4)["outline_tasks"]["to_resolve"])
        self.mm.save_chapter(4, "后续章", body)
        self.assertIn(fid, book_health.inspect_book(novel)["stale_planned"])
        self.assertEqual(commit(3, confirmed=True)["status"], "planted")
        self.assertEqual(self.mm.load_context(4)["outline_tasks"]["to_resolve"][0]["fid"], fid)
        self.mm.delete_chapter(3)
        row = outline_manager.list_outline_foreshadow(novel)[0]
        self.assertEqual((row["plant_chapter"], row["status"]), (3, "planned"))
        outline_manager.update_outline_foreshadow(novel, fid, resolve_chapter=None)
        self.assertEqual(commit(3)["plant_chapter"], 3)

    def test_crosscheck_downgrades_invalid_or_missing_evidence(self):
        candidate = {"fid": "OF1", "description": "铜牌开门", "source": "大纲", "action": "resolve"}
        decision = {"key": "候选1", "quote": "林岚用铜牌打开了门。", "verdict": "confirmed"}
        for restated, equivalent, expected in (
            ("", True, False), ("铜牌打不开门", False, False), ("铜牌开门", "true", False),
            ("铜牌开门", OSError("模拟故障"), False), ("铜牌开门", True, True),
        ):
            response = (equivalent if isinstance(equivalent, Exception) else
                        json.dumps({"decisions": [{"key": "候选1", "equivalent": equivalent, "reason": "比对"}]}))
            with patch.object(reviewer, "call_reviewer_api", return_value=json.dumps({
                "decisions": [{**decision, "restated": restated}],
            })), patch.object(reviewer, "call_cross_checker_api", side_effect=[response]) as cross:
                result = reviewer.score_foreshadowing("前文。" + decision["quote"] + "后续。", {"to_resolve": [candidate]})
                self.assertEqual(bool(result["confirmed"]), expected)
                self.assertEqual(cross.call_count, bool(restated))

    def test_incomplete_foreshadow_review_preserves_plan(self):
        novel = self.mm.novel_name
        fid = outline_manager.add_outline_foreshadow(novel, "收起借用单", "情节伏笔", 1, 4)
        body = "他接过借用单。"
        candidate = {"fid": fid, "description": "收起借用单", "source": "大纲", "action": "plant"}
        decision = {"key": "候选1", "verdict": "unconfirmed"}
        for entries in ([], [decision, decision], [{**decision, "key": "未知"}],
                        [{**decision, "verdict": "unknown"}], [None], [{"key": []}]):
            with self.subTest(entries=entries), patch.object(reviewer, "call_reviewer_api", return_value=json.dumps({
                "decisions": entries,
            })), patch.object(reviewer, "call_cross_checker_api") as cross:
                score = reviewer.score_foreshadowing(body, {"to_plant": [candidate]}, 1)
                self.assertIsNone(score)
                cross.assert_not_called()
                self.mm.save_chapter(1, "正文", body)
                reviewer.commit_approved_chapter(novel, 1, body, {
                    "pass": True, "foreshadow_reviewed_content": body, "foreshadow_score": score,
                })
                row = outline_manager.list_outline_foreshadow(novel)[0]
                self.assertEqual((row["plant_chapter"], row["status"]), (1, "planned"))

    def test_cross_checker_fallback_warns(self):
        with patch.object(api_client, "call_api", return_value="{}") as call, \
                patch.object(api_client, "print") as log:
            api_client.call_cross_checker_api("系统", "内容")
            self.assertEqual(call.call_args.kwargs["model_name"], api_client.get_reviewer_model())
            self.assertIn("同家族复核", str(log.call_args_list))

    def test_empty_macro_plan_is_valid_without_review_request(self):
        (self.mm.data_dir / 'master_outline.md').write_text('送货并收取货款。', encoding='utf-8')
        with patch('core.api_client.call_author_api', return_value='[]'), \
                patch('core.api_client.call_reviewer_api', side_effect=AssertionError('不应调用审稿')):
            self.assertEqual(outline_manager.generate_outline_foreshadow(self.mm.novel_name, 3), 0)
            self.assertEqual(outline_manager.review_outline_foreshadow('', [], '', ''), {})

    def test_planner_does_not_silently_drop_macro_tasks(self):
        for index in range(3):
            outline_manager.add_outline_foreshadow(self.mm.novel_name, f'必要铺垫{index}', '情节伏笔', 2, 5)
        prompt = planner._add_outline_fs_to_prompt('任务', self.mm.novel_name, 1, 3)
        ctx = self.mm.load_context(2)
        self.assertEqual(len(ctx['outline_tasks']['to_plant']), 3)
        for task in ctx['outline_tasks']['to_plant']:
            self.assertIn(task['fid'], prompt)


if __name__ == '__main__':
    unittest.main()
