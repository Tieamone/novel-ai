"""创作分工回归：全部使用隔离数据与模拟响应。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config_loader, db, outline_manager, planner, reviewer, writer
from core.memory_manager import MemoryManager


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = config_loader._get_defaults()
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

    def test_polish_accepts_large_cut_and_rejects_empty(self):
        source = '多余环境描写。' * 100 + '他把货交给了买家。'
        cut = '他把货交给了买家。'
        with patch.object(writer, 'call_author_api', return_value=cut):
            self.assertEqual(writer._polish_chapter(source, 1000, len(cut)), cut)
            self.assertEqual(writer._polish_chapter(source, 1000, len(cut) + 1), cut)
        with patch.object(writer, 'call_author_api', return_value=''):
            self.assertEqual(writer._polish_chapter(source, 1000), source)

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
