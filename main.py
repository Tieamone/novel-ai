import sys
import os
import shutil
import json
import signal
import logging
from pathlib import Path

from core.memory_manager import MemoryManager
from core.planner import run_planner, extend_tasks, get_style_choice, split_outline_to_tasks
from core.reviewer import write_and_review
from core.exporter import export_chapter, export_all, get_safe_output_dir
from core.db import get_connection, clean_duplicate_chapters
from core.utils import with_db_connection, DatabaseTransaction, execute_with_retry
from core.config_loader import (
    get as cfg,
    get_data_dir,
    get_project_root,
)
from core.outline_manager import manage_outline_foreshadow

MAX_NOVEL_NAME_LEN = 64
INVALID_NOVEL_NAME_CHARS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
TASK_PENDING = "待处理"
TASK_IN_PROGRESS = "进行中"
TASK_COMPLETED = "已完成"
TASK_REVIEW_FAILED = "审稿失败"

CHAPTER_STATUS_DRAFT = "草稿"
CHAPTER_STATUS_APPROVED = "已审核"
CHAPTER_STATUS_FORCE_APPROVED = "强制通过"
CHAPTER_STATUS_REVIEW_FAILED = "审稿失败"
CHAPTER_STATUS_DRAFT_ISSUES = "草稿(有问题)"


def _data_dir(novel_name: str) -> Path:
    return get_data_dir(novel_name)


def _output_dir(novel_name: str) -> Path:
    return get_safe_output_dir(novel_name)


def _get_target_chapters(novel_name: str) -> int:
    """读取小说目标章数。新建时由策划器写入，旧小说默认100。"""
    path = _data_dir(novel_name) / "target_chapters.txt"
    if path.exists():
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return 100


def _validate_novel_name(novel_name: str) -> str:
    if not novel_name:
        return "小说名称不能为空"
    if novel_name in {".", ".."}:
        return "小说名称不能是 . 或 .."
    if len(novel_name) > MAX_NOVEL_NAME_LEN:
        return f"小说名称不能超过 {MAX_NOVEL_NAME_LEN} 个字符"
    if any(ord(ch) < 32 for ch in novel_name):
        return "小说名称包含不可见控制字符"
    if any(ch in INVALID_NOVEL_NAME_CHARS for ch in novel_name):
        return "小说名称包含非法字符：<>:\"/\\|?*"
    if novel_name.endswith(" ") or novel_name.endswith("."):
        return "小说名称不能以空格或句点结尾"
    if novel_name.upper() in WINDOWS_RESERVED_NAMES:
        return "小说名称是系统保留名，请更换"
    return ""


def _list_novels() -> list:
    """
    扫描已有小说，兼容旧库（没有 novel_info 时显示友好提示）。
    返回 [(novel_name, genre)] 列表。
    """
    data_base = Path(cfg("paths", "data_dir", "data"))
    if not data_base.exists():
        return []

    novels = []
    for d in sorted(data_base.iterdir()):
        if not d.is_dir() or not (d / "novel.db").exists():
            continue
        name = d.name
        genre = "未记录"
        try:
            with with_db_connection(name) as conn:
                row = conn.execute(
                    "SELECT genre FROM novel_info WHERE id=1"
                ).fetchone()
                if row and row["genre"]:
                    genre = row["genre"]
        except Exception:
            pass
        novels.append((name, genre))

    return novels


def _upsert_task_status(novel_name: str, chapter_num: int,
                        plot_goal: str, emotion_tag: str,
                        status: str):
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            conn.execute("""
                INSERT OR REPLACE INTO chapter_tasks
                (chapter_num, plot_goal, emotion_tag, status)
                VALUES (?, ?, ?, ?)
            """, (chapter_num, plot_goal, emotion_tag, status))


def _claim_task_for_writing(novel_name: str, chapter_num: int,
                            plot_goal: str, emotion_tag: str) -> tuple:
    """
    原子认领任务（并发安全）：
    - 使用 BEGIN IMMEDIATE 获取排他锁，防止竞态条件
    - 使用 execute_with_retry 自动重试数据库锁定错误
    - 仅允许 pending -> in_progress 的状态转换
    - 返回 (是否成功, 当前状态)
    """
    with with_db_connection(novel_name) as conn:
        try:
            execute_with_retry(conn, "BEGIN IMMEDIATE")

            execute_with_retry(conn, """
                INSERT OR IGNORE INTO chapter_tasks
                (chapter_num, plot_goal, emotion_tag, status)
                VALUES (?, ?, ?, ?)
            """, (chapter_num, plot_goal, emotion_tag, TASK_PENDING))

            row = execute_with_retry(conn, """
                SELECT plot_goal, emotion_tag, status
                FROM chapter_tasks
                WHERE chapter_num=?
            """, (chapter_num,)).fetchone()

            saved_goal = ((row["plot_goal"] if row else "") or "").strip()
            saved_tag = ((row["emotion_tag"] if row else "") or "").strip()
            final_goal = saved_goal or plot_goal
            final_tag = saved_tag or emotion_tag or "铺垫"

            cur = execute_with_retry(conn, """
                UPDATE chapter_tasks
                SET plot_goal=?, emotion_tag=?, status=?,
                    updated_at=datetime('now','localtime')
                WHERE chapter_num=? AND COALESCE(status, ?) = ?
            """, (
                final_goal, final_tag, TASK_IN_PROGRESS,
                chapter_num, TASK_PENDING, TASK_PENDING
            ))
            conn.commit()

            if cur.rowcount == 1:
                return True, TASK_IN_PROGRESS

            row2 = execute_with_retry(conn,
                "SELECT status FROM chapter_tasks WHERE chapter_num=?",
                (chapter_num,),
            ).fetchone()
            status = row2["status"] if row2 and row2["status"] else TASK_PENDING
            return False, status
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


def _update_task_status(novel_name: str, chapter_num: int, status: str):
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            cur = conn.execute("""
                UPDATE chapter_tasks
                SET status=?,
                    updated_at=datetime('now','localtime')
                WHERE chapter_num=?
            """, (status, chapter_num))
            if cur.rowcount == 0:
                row = conn.execute("""
                    SELECT plot_goal, emotion_tag FROM chapters
                    WHERE chapter_num=?
                """, (chapter_num,)).fetchone()
                plot_goal = row["plot_goal"] if row else ""
                emotion_tag = row["emotion_tag"] if row else "铺垫"
                conn.execute("""
                    INSERT OR REPLACE INTO chapter_tasks
                    (chapter_num, plot_goal, emotion_tag, status)
                    VALUES (?, ?, ?, ?)
                """, (chapter_num, plot_goal, emotion_tag, status))


def _get_chapter_status(novel_name: str, chapter_num: int) -> str:
    with with_db_connection(novel_name) as conn:
        row = conn.execute(
            "SELECT status FROM chapters WHERE chapter_num=?",
            (chapter_num,),
        ).fetchone()
    return row["status"] if row else ""


def _has_chapter_summary(novel_name: str, chapter_num: int) -> bool:
    with with_db_connection(novel_name) as conn:
        row = conn.execute(
            "SELECT summary FROM chapters WHERE chapter_num=?",
            (chapter_num,),
        ).fetchone()
    if not row:
        return False
    return bool((row["summary"] or "").strip())


def show_progress(novel_name: str):
    with with_db_connection(novel_name) as conn:
        approved = conn.execute(
            "SELECT COUNT(*) as cnt FROM chapters "
            "WHERE status IN ('已审核', '强制通过')"
        ).fetchone()["cnt"]

        draft = conn.execute(
            "SELECT COUNT(*) as cnt FROM chapters "
            "WHERE status IN ('草稿', '草稿(有问题)')"
        ).fetchone()["cnt"]

        total_chars = conn.execute(
            "SELECT SUM(LENGTH(content)) as s FROM chapters"
        ).fetchone()["s"] or 0

        recent = conn.execute(
            "SELECT chapter_num, status, LENGTH(content) as chars "
            "FROM chapters ORDER BY chapter_num DESC LIMIT 3"
        ).fetchall()

        foreshadow_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM foreshadowing WHERE status='active'"
        ).fetchone()["cnt"]

        task_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM chapter_tasks"
        ).fetchone()["cnt"]

        task_status_rows = conn.execute("""
            SELECT COALESCE(status, ?) as status, COUNT(*) as cnt
            FROM chapter_tasks
            GROUP BY COALESCE(status, ?)
        """, (TASK_PENDING, TASK_PENDING)).fetchall()

        review_window = int(cfg("novel", "progress_review_window", 10))
        if review_window <= 0:
            review_window = 10
        try:
            review_rows = conn.execute("""
                SELECT chapter_num,
                       review_score_total,
                       review_score_l1,
                       review_score_l2,
                       review_score_l3,
                       review_veto_items
                FROM chapters
                WHERE review_score_total IS NOT NULL
                ORDER BY chapter_num DESC
                LIMIT ?
            """, (review_window,)).fetchall()
        except Exception:
            review_rows = []

    task_status_map = {
        TASK_PENDING: 0,
        TASK_IN_PROGRESS: 0,
        TASK_COMPLETED: 0,
        TASK_REVIEW_FAILED: 0,
    }
    for row in task_status_rows:
        status = row["status"] or TASK_PENDING
        if status in task_status_map:
            task_status_map[status] = row["cnt"]

    quality_count = len(review_rows)
    avg_total = avg_l1 = avg_l2 = avg_l3 = 0.0
    veto_hits = 0
    if quality_count:
        sum_total = sum((r["review_score_total"] or 0) for r in review_rows)
        sum_l1 = sum((r["review_score_l1"] or 0) for r in review_rows)
        sum_l2 = sum((r["review_score_l2"] or 0) for r in review_rows)
        sum_l3 = sum((r["review_score_l3"] or 0) for r in review_rows)
        avg_total = sum_total / quality_count
        avg_l1 = sum_l1 / quality_count
        avg_l2 = sum_l2 / quality_count
        avg_l3 = sum_l3 / quality_count

        for r in review_rows:
            raw = (r["review_veto_items"] or "").strip()
            if not raw or raw == "[]":
                continue
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and len(parsed) > 0:
                    veto_hits += 1
                elif parsed:
                    veto_hits += 1
            except Exception:
                veto_hits += 1

    style_path = _data_dir(novel_name) / "style.txt"
    style_name = "未设置"
    if style_path.exists():
        raw_style = style_path.read_text(encoding="utf-8").strip()
        if raw_style.startswith("custom:"):
            style_name = "自定义风格"
        else:
            from core.writer import AUTHOR_STYLES
            style_name = AUTHOR_STYLES.get(raw_style, {}).get("name", "未知")

    from core.api_client import get_session_stats
    stats = get_session_stats()
    cost = stats["total_cost_yuan"]
    total_tokens = (stats["total_input_tokens"] +
                    stats["total_output_tokens"])

    # ★ 使用动态目标章数（新建时由策划器写入，旧小说默认100）
    target = _get_target_chapters(novel_name)
    bar_len = 30
    filled = int(bar_len * min(approved, target) / target)
    bar = "█" * filled + "░" * (bar_len - filled)
    percent = min(approved * 100 // target, 100)

    chars_display = (
        f"~{total_chars/10000:.1f}万字"
        if total_chars >= 10000 else f"{total_chars}字"
    )

    print("\n" + "=" * 50)
    print(f"  《{novel_name}》  [{style_name}]")
    print("=" * 50)
    print(f"  [{bar}] {percent}%")
    print(f"  目标：{target}章  |  已完成：{approved}章  |  草稿：{draft}章")
    print(f"  累计字数：{total_chars:,} 字  ({chars_display})")
    print(f"  未兑现伏笔：{foreshadow_cnt} 个  |  任务卡：{task_cnt} 张")
    print("  任务状态："
          f"待处理 {task_status_map[TASK_PENDING]}  |  "
          f"进行中 {task_status_map[TASK_IN_PROGRESS]}  |  "
          f"已完成 {task_status_map[TASK_COMPLETED]}  |  "
          f"审稿失败 {task_status_map[TASK_REVIEW_FAILED]}")
    if quality_count:
        print(
            f"  审稿质量（最近{quality_count}章）：均分 {avg_total:.1f}/100  |  "
            f"L1 {avg_l1:.1f}/45  L2 {avg_l2:.1f}/25  L3 {avg_l3:.1f}/30"
        )
        print(f"  否决命中：{veto_hits}章  |  统计窗口：最近{review_window}章")
    else:
        print(f"  审稿质量：暂无数据（统计窗口：最近{review_window}章）")
    print("-" * 50)
    print(f"  本次会话：调用{stats['total_calls']}次  "
          f"共{total_tokens:,} token  "
          f"费用约 ¥{cost:.4f}")
    print("-" * 50)

    if recent:
        print("  最近章节：")
        for r in reversed(recent):
            icon = "✓" if r["status"] in (
                CHAPTER_STATUS_APPROVED, CHAPTER_STATUS_FORCE_APPROVED) else "○"
            print(f"    {icon} 第{r['chapter_num']}章  "
                  f"{r['chars']}字  [{r['status']}]")
    print("=" * 50)


def get_next_chapter_goal(novel_name: str, chapter_num: int) -> tuple:
    with with_db_connection(novel_name) as conn:
        task = conn.execute(
            "SELECT plot_goal, emotion_tag FROM chapter_tasks "
            "WHERE chapter_num=?", (chapter_num,)
        ).fetchone()
        max_task = conn.execute(
            "SELECT MAX(chapter_num) as mx FROM chapter_tasks"
        ).fetchone()["mx"] or 0

    if task and task["plot_goal"]:
        return task["plot_goal"], task["emotion_tag"]

    if chapter_num > max_task:
        print(f"  [提示] 任务卡已用完（最大第{max_task}章），正在扩展...")
        extend_tasks(novel_name, max_task + 1)
        with with_db_connection(novel_name) as conn:
            task = conn.execute(
                "SELECT plot_goal, emotion_tag FROM chapter_tasks "
                "WHERE chapter_num=?", (chapter_num,)
            ).fetchone()
        if task and task["plot_goal"]:
            return task["plot_goal"], task["emotion_tag"]

    # 兜底
    outline_path = _data_dir(novel_name) / "master_outline.md"
    if not outline_path.exists():
        return "按照大纲继续推进剧情", "铺垫"

    outline = outline_path.read_text(encoding="utf-8")
    from core.api_client import call_api
    import re
    result = call_api(
        system_prompt="你是小说策划师，根据大纲拆解章节任务。",
        user_message=f"""根据以下总大纲，为第{chapter_num}章生成具体情节目标。

总大纲：
{outline}

只按以下格式输出：
情节目标：xxx（50字以内，只写一个核心节点）
情绪标签：xxx（从铺垫/冲突/爽点/低谷/反转选一个）""",
        temperature=0.7,
        max_tokens=150,
    )
    goal_match = re.search(r'情节目标[：:]\s*(.+)', result)
    tag_match = re.search(r'情绪标签[：:]\s*(.+)', result)
    goal = goal_match.group(1).strip() if goal_match else "按大纲推进剧情"
    tag = tag_match.group(1).strip() if tag_match else "铺垫"
    if tag not in ["铺垫", "冲突", "爽点", "低谷", "反转"]:
        tag = "铺垫"
    return goal, tag


def generate_chapter_auto(novel_name: str):
    global _current_novel_name, _current_chapter_num
    # Bug修复5: 改从 chapter_tasks 找最小的待处理章节，
    # 避免 MAX(chapters) + 1 在中间有空缺时跳号
    with with_db_connection(novel_name) as conn:
        pending_row = conn.execute("""
            SELECT MIN(chapter_num) as next_num FROM chapter_tasks
            WHERE status IN (?, ?)
        """, (TASK_PENDING, "待处理")).fetchone()

        if pending_row and pending_row["next_num"]:
            next_num = pending_row["next_num"]
        else:
            # 兜底：fallback 到旧逻辑（所有任务已完成或任务表为空）
            row = conn.execute(
                "SELECT MAX(chapter_num) as max_num FROM chapters"
            ).fetchone()
            last_num = row["max_num"] if (row and row["max_num"]) else 0
            next_num = last_num + 1

    # 记录当前写作章节，供 Ctrl+C 处理器使用
    _current_novel_name = novel_name
    _current_chapter_num = next_num

    print(f"\n正在获取第{next_num}章任务...")
    plot_goal, emotion_tag = get_next_chapter_goal(novel_name, next_num)
    print(f"  情节目标：{plot_goal}")
    print(f"  情绪标签：{emotion_tag}")
    try:
        claimed, current_status = _claim_task_for_writing(
            novel_name, next_num, plot_goal, emotion_tag
        )
    except Exception as e:
        print(f"  [错误] 任务认领失败：{e}")
        return

    if not claimed:
        if current_status == TASK_IN_PROGRESS:
            print("  [提示] 该章节任务已被其他写作进程认领，已跳过")
        elif current_status == TASK_COMPLETED:
            print("  [提示] 该章节任务已完成，已跳过")
        elif current_status == TASK_REVIEW_FAILED:
            print("  [提示] 该章节任务处于审稿失败状态，请走恢复入口处理")
        else:
            print(f"  [提示] 该章节任务当前状态为 {current_status}，已跳过")
        return

    content = write_and_review(
        novel_name=novel_name,
        chapter_num=next_num,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
    )

    # ★ 需求1：三次重写仍未通过时，提示切换模型重新生成
    while content:
        chapter_status = _get_chapter_status(novel_name, next_num)
        if chapter_status != CHAPTER_STATUS_FORCE_APPROVED:
            break  # approved 或其他状态，正常往下走

        max_retry_num = cfg("novel", "max_retry", 3)
        print(f"\n{'=' * 50}")
        print(f"  [提示] 第{next_num}章经{max_retry_num}次写作仍未通过审稿。")
        print(f"  当前以最后版本暂存（强制通过）。")
        print(f"  1. 保留当前版本继续")
        print(f"  2. 切换模型重新生成本章")
        print('=' * 50)
        switch = input("请选择（默认1）：").strip()
        if switch != "2":
            break
        from core.api_client import select_model_interactive
        select_model_interactive()
        print(f"\n[提示] 开始用新模型重新生成第{next_num}章...")
        content = write_and_review(
            novel_name=novel_name,
            chapter_num=next_num,
            plot_goal=plot_goal,
            emotion_tag=emotion_tag,
        )
        # 循环回去检查新一轮结果

    # 章节完成/中断后清除全局追踪
    _current_chapter_num = None

    if content:
        chapter_status = _get_chapter_status(novel_name, next_num)
        if chapter_status in (CHAPTER_STATUS_APPROVED, CHAPTER_STATUS_FORCE_APPROVED):
            _save_chapter_memory(novel_name, next_num, content, plot_goal)
            export_path = export_chapter(novel_name, next_num)
            _update_task_status(novel_name, next_num, TASK_COMPLETED)
            from core.api_client import get_session_stats
            stats = get_session_stats()
            print(f"\n[完成] 第{next_num}章已生成并导出")
            print(f"文件：{export_path}")
            print(f"本次会话累计费用：¥{stats['total_cost_yuan']:.4f} 元")
        elif chapter_status == CHAPTER_STATUS_REVIEW_FAILED:
            _update_task_status(novel_name, next_num, TASK_REVIEW_FAILED)
            print("\n[中断] 本章因审稿异常标记为 审稿失败。")
            print('可在章节菜单使用"恢复审稿失败章节"继续处理。')
        else:
            _update_task_status(novel_name, next_num, TASK_PENDING)
            print("\n[中断] 本章未通过审核，任务状态已回退为 待处理。")
    else:
        chapter_status = _get_chapter_status(novel_name, next_num)
        if chapter_status == CHAPTER_STATUS_REVIEW_FAILED:
            _update_task_status(novel_name, next_num, TASK_REVIEW_FAILED)
            print("\n[中断] 本章因审稿异常标记为 审稿失败。")
            print('可在章节菜单使用"恢复审稿失败章节"继续处理。')
        else:
            _update_task_status(novel_name, next_num, TASK_PENDING)
            print("\n[中断] 本章未完成，任务状态已回退为 待处理。")


def _save_chapter_memory(novel_name: str, chapter_num: int,
                         content: str, plot_goal: str = "") -> str:
    from core.api_client import call_api
    import json
    import re
    mm = MemoryManager(novel_name)
    print("  正在提取章节记忆...")

    # Bug修复6: 改用审核模型（更稳定的JSON输出），max_tokens提升到1200
    from core.api_client import call_reviewer_api as _call_reviewer_api
    raw = _call_reviewer_api(
        system_prompt="你是小说编辑，提取章节关键信息，只输出JSON，不要Markdown代码块。",
        user_message=f"""分析以下章节，严格按JSON格式输出：

提取规则：
- expected_redeem：必须填写具体章节范围，格式为'第X-Y章'，X至少比当前章节大5，禁止填'待定'或空字符串

{{
  "summary": "100字以内情节摘要",
  "character_updates": [
    {{
      "name": "角色名",
      "location": "当前位置",
      "status": "当前状态"
    }}
  ],
  "relationship_updates": [
    {{
      "name": "角色A",
      "with": "角色B",
      "new_relationship": "新的关系描述"
    }}
  ],
  "new_foreshadowing": [
    {{
      "fid": "F{chapter_num:03d}_1",
      "description": "伏笔描述",
      "expected_redeem": "第{chapter_num+10}-{chapter_num+20}章（示例）"
    }}
  ],
  "redeemed_foreshadowing": ["已兑现伏笔ID，没有则为空列表"]
}}

章节内容：
{content[:8000]}""",
        temperature=0.15,
        max_tokens=1200,
    )

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        mm.add_summary(chapter_num, raw[:200])
        print("  [OK] 摘要已保存（简单模式）")
        _trigger_compression(novel_name, mm)
        return raw[:200]

    try:
        data = json.loads(match.group())
    except Exception:
        mm.add_summary(chapter_num, raw[:200])
        print("  [OK] 摘要已保存（简单模式）")
        _trigger_compression(novel_name, mm)
        return raw[:200]

    summary = data.get("summary", "")
    mm.add_summary(chapter_num, summary)
    mm.update_chapter_summary(chapter_num, summary)
    print(f"  [OK] 摘要：{summary[:60]}...")

    for c in data.get("character_updates", []):
        name = c.get("name", "")
        loc = c.get("location", "")
        status = c.get("status", "")
        if name and (loc or status):
            mm.update_character_status(name, loc, status, chapter_num)
            print(f"  [OK] 人物更新：{name} → {loc} / {status}")

    for r in data.get("relationship_updates", []):
        name = r.get("name", "")
        with_name = r.get("with", "")
        new_rel = r.get("new_relationship", "")
        if name and with_name and new_rel:
            mm.update_character_relationship(
                name, with_name, new_rel, chapter_num
            )
            print(f"  [OK] 关系更新（双向）：{name} ↔ {with_name} → {new_rel}")

    # 2026-05 伏笔系统改造：禁自动提取动态伏笔，改用大纲伏笔。旧代码保留以备回滚
    # for f in data.get("new_foreshadowing", []):
    #     fid = f.get("fid", "")
    #     desc = f.get("description", "")
    #     redeem = f.get("expected_redeem", "待定")
    #     if not redeem or redeem.strip() in ("待定", "", "未定", "暂定"):
    #         redeem = f"第{chapter_num+10}-{chapter_num+20}章"
    #     if fid and desc:
    #         mm.add_foreshadowing(fid, chapter_num, desc, redeem)
    #         print(f"  [OK] 新伏笔：{desc[:30]}...")

    for fid in data.get("redeemed_foreshadowing", []):
        if fid:
            mm.redeem_foreshadowing(fid, chapter_num)
            print(f"  [OK] 伏笔兑现：{fid}")

    _trigger_compression(novel_name, mm)
    return summary


def _trigger_compression(novel_name: str, mm: MemoryManager):
    with with_db_connection(novel_name) as conn:
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM summaries WHERE is_compressed=0"
        ).fetchone()["cnt"]
    if count > cfg("novel", "compress_after_chapters", 20):
        mm.compress_old_summaries()


def _list_chapters(novel_name: str):
    with with_db_connection(novel_name) as conn:
        rows = conn.execute(
            "SELECT chapter_num, status, emotion_tag, retry_count, "
            "LENGTH(content) as chars "
            "FROM chapters ORDER BY chapter_num"
        ).fetchall()
    if not rows:
        print("\n暂无章节")
        return
    print(f"\n{'':2}{'章节':<8} {'情绪':<6} {'重试':<4} {'状态':<16} {'字数'}")
    print("-" * 52)
    for row in rows:
        icon = "✓" if row["status"] in (
            CHAPTER_STATUS_APPROVED, CHAPTER_STATUS_FORCE_APPROVED) else "○"
        retry = row["retry_count"] or 0
        tag = row["emotion_tag"] or "-"
        print(f"  {icon} 第{row['chapter_num']}章  "
              f"{tag:<6} "
              f"{'×'+str(retry) if retry else '-':<4} "
              f"{row['status']:<16} "
              f"{row['chars'] or 0}字")


def _view_tasks(novel_name: str):
    PAGE = 30
    with with_db_connection(novel_name) as conn:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM chapter_tasks"
        ).fetchone()["cnt"]

    if not total:
        print("\n暂无任务卡")
        print("提示：旧小说可在继续写作时自动扩展任务卡")
        return

    offset = 0
    while True:
        with with_db_connection(novel_name) as conn:
            rows = conn.execute(
                "SELECT chapter_num, emotion_tag, status, plot_goal FROM chapter_tasks "
                "ORDER BY chapter_num LIMIT ? OFFSET ?",
                (PAGE, offset)
            ).fetchall()

        if not rows:
            print("  已到末尾")
            break

        start_ch = rows[0]["chapter_num"]
        end_ch = rows[-1]["chapter_num"]
        print(f"\n章节任务卡（第{start_ch}-{end_ch}章，共{total}张）：")
        print("-" * 60)
        for row in rows:
            print(f"  第{row['chapter_num']:>3}章 "
                  f"[{row['emotion_tag']}] "
                  f"[{row['status'] or TASK_PENDING}] "
                  f"{row['plot_goal']}")

        if offset + PAGE >= total:
            break

        print(f"\n[n] 下一页  [q] 退出")
        nav = input("请选择：").strip().lower()
        if nav != "n":
            break
        offset += PAGE


def _recover_review_failed(novel_name: str):
    with with_db_connection(novel_name) as conn:
        rows = conn.execute("""
            SELECT chapter_num, title, plot_goal, emotion_tag, content,
                   retry_count, LENGTH(content) as chars
            FROM chapters
            WHERE status='审稿失败'
            ORDER BY chapter_num
        """).fetchall()

    if not rows:
        print("\n[提示] 当前没有审稿失败的章节")
        return

    print("\n审稿失败章节：")
    for r in rows:
        print(f"  第{r['chapter_num']}章  "
              f"[{r['emotion_tag'] or '-'}]  "
              f"{r['chars'] or 0}字  "
              f"重试×{r['retry_count'] or 0}")

    try:
        while True:
            chapter_num_input = input("\n请输入要处理的章节号（0取消）：").strip()
            if not chapter_num_input.isdigit():
                print(f"  ⚠️  无效输入: '{chapter_num_input}' 不是有效的章节号")
                print(f"  💡 提示: 请输入一个正整数，如 '5' 或 '12'，或输入 '0' 取消")
                continue
            chapter_num = int(chapter_num_input)
            if chapter_num < 0:
                print(f"  ⚠️  章节号不能为负数，您输入的是 {chapter_num}")
                continue
            break
    except KeyboardInterrupt:
        print("\n[提示] 已取消操作")
        return

    if chapter_num == 0:
        return

    target = None
    for r in rows:
        if r["chapter_num"] == chapter_num:
            target = r
            break
    if not target:
        print("[错误] 章节号不在审稿失败列表中")
        return

    print("\n处理方式：")
    print("1. 仅重试审稿（不重写）")
    print("2. 重写并重审")
    print("3. 强制通过并导出")
    print("0. 取消")
    action = input("请选择：").strip()

    from core.reviewer import review_chapter

    if action == "1":
        content = target["content"] or ""
        if not content.strip():
            print("[错误] 该章节正文为空，无法仅重试审稿")
            return
        result = review_chapter(
            novel_name, chapter_num, content, target["plot_goal"] or ""
        )
        mm = MemoryManager(novel_name)
        if result.get("pass"):
            from core.reader_reviewer import reader_review_chapter
            reader_result = reader_review_chapter(novel_name, chapter_num, content)
            if not reader_result.get("pass"):
                mm.update_chapter_status(chapter_num, CHAPTER_STATUS_REVIEW_FAILED)
                _update_task_status(novel_name, chapter_num, TASK_REVIEW_FAILED)
                print(f"[提示] 第{chapter_num}章读者视角仍未通过，保持审稿失败状态")
                return
            mm.update_chapter_status(chapter_num, CHAPTER_STATUS_APPROVED)
            _update_task_status(novel_name, chapter_num, TASK_COMPLETED)
            if not _has_chapter_summary(novel_name, chapter_num):
                _save_chapter_memory(
                    novel_name, chapter_num, content, target["plot_goal"] or ""
                )
            export_chapter(novel_name, chapter_num)
            print(f"[OK] 第{chapter_num}章已恢复为 已审核 并导出")
        else:
            mm.update_chapter_status(chapter_num, CHAPTER_STATUS_REVIEW_FAILED)
            _update_task_status(novel_name, chapter_num, TASK_REVIEW_FAILED)
            print(f"[提示] 第{chapter_num}章仍未通过审稿，保持审稿失败状态")
        return

    if action == "2":
        plot_goal = target["plot_goal"] or "按大纲推进剧情"
        emotion_tag = target["emotion_tag"] or "铺垫"
        content = write_and_review(
            novel_name=novel_name,
            chapter_num=chapter_num,
            plot_goal=plot_goal,
            emotion_tag=emotion_tag,
        )
        if content:
            chapter_status = _get_chapter_status(novel_name, chapter_num)
            if chapter_status in (CHAPTER_STATUS_APPROVED, CHAPTER_STATUS_FORCE_APPROVED):
                if not _has_chapter_summary(novel_name, chapter_num):
                    _save_chapter_memory(novel_name, chapter_num, content, plot_goal)
                export_chapter(novel_name, chapter_num)
                _update_task_status(novel_name, chapter_num, TASK_COMPLETED)
                print(f"[OK] 第{chapter_num}章重试完成")
            elif chapter_status == CHAPTER_STATUS_REVIEW_FAILED:
                _update_task_status(novel_name, chapter_num, TASK_REVIEW_FAILED)
                print(f"[提示] 第{chapter_num}章仍未恢复")
            else:
                _update_task_status(novel_name, chapter_num, TASK_PENDING)
                print(f"[提示] 第{chapter_num}章未通过审核，未导出")
        else:
            chapter_status = _get_chapter_status(novel_name, chapter_num)
            if chapter_status == CHAPTER_STATUS_REVIEW_FAILED:
                _update_task_status(novel_name, chapter_num, TASK_REVIEW_FAILED)
            else:
                _update_task_status(novel_name, chapter_num, TASK_PENDING)
            print(f"[提示] 第{chapter_num}章仍未恢复")
        return

    if action == "3":
        content = target["content"] or ""
        if not content.strip():
            print("[错误] 该章节正文为空，无法强制通过")
            return
        mm = MemoryManager(novel_name)
        mm.update_chapter_status(chapter_num, CHAPTER_STATUS_FORCE_APPROVED)
        _update_task_status(novel_name, chapter_num, TASK_COMPLETED)
        if not _has_chapter_summary(novel_name, chapter_num):
            _save_chapter_memory(
                novel_name, chapter_num, content, target["plot_goal"] or ""
            )
        export_chapter(novel_name, chapter_num)
        print(f"[OK] 第{chapter_num}章已强制通过并导出")
        return

    print("[提示] 已取消")


def _change_style(novel_name: str):
    from core.writer import AUTHOR_STYLES
    print("\n" + "=" * 50)
    print("  更换写作风格")
    print("=" * 50)
    for key, style in AUTHOR_STYLES.items():
        print(f"  {key}. {style['name']:<12} {style['desc']}")
    print("  7. 自定义风格（自己描述写作风格）")
    print("  8. 上传参考文本生成文法指纹")
    print()
    choice = input("请输入编号（默认1）：").strip() or "1"
    if choice == "7":
        custom_desc = input("  请描述你想要的写作风格：").strip()
        if custom_desc:
            (_data_dir(novel_name) / "style.txt").write_text(
                f"custom:{custom_desc}", encoding="utf-8"
            )
            print("[OK] 自定义风格已保存，下一章开始生效")
            return
    if choice == "8":
        _generate_style_fingerprint(novel_name)
        return
    if choice not in AUTHOR_STYLES:
        choice = "1"
    (_data_dir(novel_name) / "style.txt").write_text(choice, encoding="utf-8")
    print(f"[OK] 风格已更换为：{AUTHOR_STYLES[choice]['name']}，下一章开始生效")


def _generate_style_fingerprint(novel_name: str):
    """从参考文本生成文法指纹（优化D）"""
    from core.style_analyzer import analyze_style, save_fingerprint
    print("\n" + "=" * 50)
    print("  生成文法指纹")
    print("=" * 50)
    print("  请提供参考文本文件路径（.txt 文件）")
    print("  参考文本应该是你想模仿的作者的章节或片段")
    path = input("\n  文件路径：").strip().strip('"')
    if not path:
        print("  [已取消]")
        return
    from pathlib import Path
    ref_path = Path(path)
    if not ref_path.exists():
        print(f"  [错误] 文件不存在: {path}")
        return
    try:
        reference_text = ref_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  [错误] 读取文件失败: {e}")
        return
    if len(reference_text) < 200:
        print(f"  [错误] 参考文本太短（{len(reference_text)}字），至少需要200字以上")
        return
    print(f"  [OK] 已读取 {len(reference_text)} 字参考文本")
    fingerprint = analyze_style(reference_text)
    save_fingerprint(novel_name, fingerprint)
    print(f"\n  [OK] 文法指纹生成完成！下一章写作时将自动应用。")


def _edit_author_intent(novel_name: str):
    """编辑创作意图文件（优化B：输入治理控制面）"""
    import os
    dd = _data_dir(novel_name)
    print("\n" + "=" * 50)
    print("  编辑创作意图")
    print("=" * 50)
    print("  1. 编辑长期意图（author_intent.md）")
    print("  2. 编辑当前焦点（current_focus.md）")
    print("  0. 返回")
    c = input("\n选择：").strip()
    if c == "1":
        path = dd / "author_intent.md"
    elif c == "2":
        path = dd / "current_focus.md"
    else:
        return
    if not path.exists():
        print(f"[提示] 文件不存在: {path}")
        return
    try:
        os.startfile(str(path))
        print(f"[OK] 已打开 {path.name}，编辑后保存即可生效")
    except Exception:
        print(f"[提示] 无法自动打开，请手动编辑: {path}")


# ★ 需求2：章节删除功能
def _delete_chapter(novel_name: str):
    """
    删除指定章节的数据库记录，重置任务卡为 pending，可重新生成。
    注意：伏笔记录不自动回滚，摘要压缩历史无法撤销。
    """
    with with_db_connection(novel_name) as conn:
        rows = conn.execute(
            "SELECT chapter_num, status, LENGTH(content) as chars, emotion_tag "
            "FROM chapters ORDER BY chapter_num"
        ).fetchall()

    if not rows:
        print("\n[提示] 当前没有章节可删除")
        return

    _list_chapters(novel_name)

    try:
        while True:
            chapter_num_input = input("\n请输入要删除的章节号（0取消）：").strip()
            if not chapter_num_input.isdigit():
                print(f"  ⚠️  无效输入: '{chapter_num_input}' 不是有效的章节号")
                print(f"  💡 提示: 请输入一个正整数，如 '5' 或 '12'，或输入 '0' 取消")
                continue
            chapter_num = int(chapter_num_input)
            if chapter_num < 0:
                print(f"  ⚠️  章节号不能为负数，您输入的是 {chapter_num}")
                continue
            break
    except KeyboardInterrupt:
        print("\n[提示] 已取消操作")
        return

    if chapter_num == 0:
        return

    # 检查章节是否存在
    with with_db_connection(novel_name) as conn:
        row = conn.execute(
            "SELECT chapter_num, status, LENGTH(content) as chars FROM chapters WHERE chapter_num=?",
            (chapter_num,)
        ).fetchone()

    if not row:
        print(f"[错误] 第{chapter_num}章不存在")
        return

    print(f"\n⚠️  即将执行删除操作:")
    print(f"   📖 小说: {novel_name}")
    print(f"   📑 章节: 第{chapter_num}章 ({row['chars'] or 0}字)")
    print(f"   📊 当前状态: {row['status']}")
    print(f"   ⚠️  此操作不可撤销！\n")

    confirm = input("确认删除? (输入 'yes' 确认): ").strip().lower()
    if confirm != 'yes':
        print("  ✅ 已取消删除操作")
        return

    confirm2 = input("  最后确认，输入 YES 执行删除：").strip()
    if confirm2 != "YES":
        print("  ✅ 已取消删除")
        return

    # 执行删除
    mm = MemoryManager(novel_name)
    mm.delete_chapter(chapter_num)

    # 重置任务卡为 pending
    _update_task_status(novel_name, chapter_num, TASK_PENDING)

    # 删除导出文件（如果存在）
    out_file = _output_dir(novel_name) / f"第{str(chapter_num).zfill(3)}章.txt"
    if out_file.exists():
        out_file.unlink()
        print(f"  [OK] 已删除导出文件：{out_file.name}")

    print(f"\n[OK] 第{chapter_num}章已删除，任务卡已重置为 待处理。")
    print(f"  提示：直接选择「自动生成下一章」时会跳过此章（已有更新章节）。")
    print(f"  若需重新生成第{chapter_num}章，请先删除第{chapter_num}章之后的所有章节，")
    print(f"  或通过「恢复审稿失败」入口手动处理。")


def _delete_chapters_batch(novel_name: str):
    """
    批量删除章节（按范围删除）。
    复用 _delete_chapter 的核心逻辑，支持批量操作。
    """
    with with_db_connection(novel_name) as conn:
        rows = conn.execute(
            "SELECT chapter_num, status, LENGTH(content) as chars, emotion_tag "
            "FROM chapters ORDER BY chapter_num"
        ).fetchall()

    if not rows:
        print("\n[提示] 当前没有章节可删除")
        return

    _list_chapters(novel_name)

    try:
        print("\n请输入要删除的章节范围（例如：2-5 删除第2到第5章）")
        range_input = input("输入章节范围（0取消）：").strip()
        
        if range_input == "0":
            return
        
        # 解析范围
        if "-" in range_input:
            parts = range_input.split("-")
            start_num = int(parts[0].strip())
            end_num = int(parts[1].strip())
        else:
            # 单个数字的情况
            start_num = int(range_input)
            end_num = start_num
        
        if start_num <= 0 or end_num <= 0:
            print("[错误] 章节号必须大于0")
            return
        
        if start_num > end_num:
            print("[错误] 起始章节不能大于结束章节")
            return
        
        # 检查范围内的章节是否存在
        existing_chapters = [row["chapter_num"] for row in rows]
        
        invalid_chapters = []
        for i in range(start_num, end_num + 1):
            if i not in existing_chapters:
                invalid_chapters.append(i)
        
        if invalid_chapters:
            print(f"[错误] 章节 {invalid_chapters} 不存在")
            return
        
        # 显示将删除的章节信息
        print(f"\n准备删除第{start_num}章到第{end_num}章，共{end_num - start_num + 1}章")
        
        # 获取总字数统计
        total_chars = 0
        for row in rows:
            if start_num <= row["chapter_num"] <= end_num:
                total_chars += (row["chars"] or 0)
        
        print(f"  当前状态：{len([r for r in rows if start_num <= r['chapter_num'] <= end_num])}章")
        print(f"  总字数：{total_chars}字")
        print()
        print("  删除后效果：")
        print("  - 章节正文和摘要将被清除")
        print("  - 任务卡状态重置为 pending，可重新生成")
        print("  - 导出文件（如已存在）将被删除")
        print("  ⚠ 注意：这些章节已提取的伏笔记录不会自动撤销")
        print()
        
        # 确认删除
        confirm = input(f"  输入范围 {start_num}-{end_num} 确认删除（其他键取消）：").strip()
        if confirm != f"{start_num}-{end_num}":
            print("[取消] 已取消删除")
            return
        
        confirm2 = input("  最后确认，输入 YES 执行删除：").strip()
        if confirm2 != "YES":
            print("[取消] 已取消删除")
            return
        
        # 执行批量删除
        mm = MemoryManager(novel_name)
        deleted_count = 0
        deleted_files = []
        
        for chapter_num in range(start_num, end_num + 1):
            # 删除数据库记录
            mm.delete_chapter(chapter_num)
            
            # 重置任务卡为 pending
            _update_task_status(novel_name, chapter_num, TASK_PENDING)
            
            # 删除导出文件（如果存在）
            out_file = _output_dir(novel_name) / f"第{str(chapter_num).zfill(3)}章.txt"
            if out_file.exists():
                out_file.unlink()
                deleted_files.append(out_file.name)
            
            deleted_count += 1
        
        print(f"\n[OK] 已删除第{start_num}章到第{end_num}章，共{deleted_count}章")
        print(f"  任务卡已重置为待处理")
        if deleted_files:
            print(f"  已删除导出文件：{len(deleted_files)}个")
        print(f"  提示：可直接选择「自动生成下一章」或「批量自动生成」重新生成这些章节。")
        
    except ValueError:
        print("[错误] 请输入有效格式（如：2-5 或 3）")
    except Exception as e:
        print(f"[错误] 删除失败：{e}")


def _delete_novel(novel_name: str) -> bool:
    """
    删除小说，含二次确认。
    删除范围：data/{novel_name}/ 和 output/{novel_name}/
    返回 True 表示已删除，False 表示取消。
    """
    data_path = _data_dir(novel_name)
    out_path = _output_dir(novel_name)

    chapter_count = 0
    try:
        with with_db_connection(novel_name) as conn:
            chapter_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM chapters"
            ).fetchone()["cnt"]
    except Exception:
        pass

    print(f"\n  准备删除《{novel_name}》")
    print(f"  已有章节：{chapter_count} 章")
    print(f"  数据目录：{data_path}")
    print(f"  导出目录：{out_path}")
    print()
    print("  ⚠ 此操作不可撤销，将永久删除所有数据和导出文件。")
    print()

    confirm1 = input(
        f'  请输入小说名称确认删除（输入"{novel_name}"）：').strip()
    if confirm1 != novel_name:
        print("  [取消] 名称不匹配，已取消删除")
        return False

    confirm2 = input("  最后确认，输入 YES 执行删除：").strip()
    if confirm2 != "YES":
        print("  [取消] 已取消删除")
        return False

    try:
        if data_path.exists():
            shutil.rmtree(data_path)
            print(f"  [OK] 已删除数据目录：{data_path}")
        if out_path.exists():
            shutil.rmtree(out_path)
            print(f"  [OK] 已删除导出目录：{out_path}")
        print(f"\n  《{novel_name}》已完全删除。")
        return True
    except Exception as e:
        print(f"  [错误] 删除失败：{e}")
        return False


def _edit_task_card(novel_name: str):
    """手动查看并编辑指定章节的任务卡（情节目标 / 情绪标签）"""
    VALID_TAGS = ["铺垫", "冲突", "爽点", "低谷", "反转"]

    try:
        chapter_num_input = input("\n请输入要编辑的章节号（0取消）：").strip()
        if not chapter_num_input.isdigit():
            print("  [提示] 无效输入，已取消")
            return
        chapter_num = int(chapter_num_input)
        if chapter_num == 0:
            return
    except KeyboardInterrupt:
        print("\n[提示] 已取消操作")
        return

    with with_db_connection(novel_name) as conn:
        row = conn.execute(
            "SELECT chapter_num, plot_goal, emotion_tag, status, "
            "original_plot_goal, rewrite_count "
            "FROM chapter_tasks WHERE chapter_num=?",
            (chapter_num,)
        ).fetchone()

    if not row:
        print(f"  [错误] 第{chapter_num}章任务卡不存在")
        return

    print(f"\n第{chapter_num}章当前任务卡：")
    print(f"  情节目标：{row['plot_goal']}")
    print(f"  情绪标签：{row['emotion_tag']}")
    print(f"  状    态：{row['status']}")
    if row["rewrite_count"]:
        print(f"  已重写过：{row['rewrite_count']} 次")
        if row["original_plot_goal"]:
            print(f"  原始目标：{row['original_plot_goal']}")

    print(f"\n请输入新的情节目标（40-80字，直接回车保持不变）：")
    new_goal = input("  > ").strip()

    print(f"请输入新的情绪标签（{'/'.join(VALID_TAGS)}，直接回车保持不变）：")
    new_tag = input("  > ").strip()

    if not new_goal and not new_tag:
        print("  [提示] 未作任何修改")
        return

    if new_goal and len(new_goal) < 10:
        print("  [警告] 情节目标过短（建议40-80字），已取消")
        return

    if new_tag and new_tag not in VALID_TAGS:
        print(f"  [错误] 情绪标签无效，必须是：{'/'.join(VALID_TAGS)}")
        return

    final_goal = new_goal or row["plot_goal"]
    final_tag  = new_tag  or row["emotion_tag"]

    # 首次手动编辑时保存原始目标
    original = row["original_plot_goal"] or row["plot_goal"]
    rewrite_count = (row["rewrite_count"] or 0) + 1

    with with_db_connection(novel_name) as conn:
        conn.execute(
            "UPDATE chapter_tasks "
            "SET plot_goal=?, emotion_tag=?, "
            "original_plot_goal=?, rewrite_count=?, "
            "updated_at=datetime('now','localtime') "
            "WHERE chapter_num=?",
            (final_goal, final_tag, original, rewrite_count, chapter_num)
        )
        conn.commit()

    print(f"\n[OK] 第{chapter_num}章任务卡已更新：")
    print(f"  情节目标：{final_goal}")
    print(f"  情绪标签：{final_tag}")


def show_foreshadow_report(novel_name: str):
    """显示伏笔健康度报告"""
    with with_db_connection(novel_name) as conn:
        row = conn.execute(
            "SELECT MAX(chapter_num) as mx FROM chapters"
        ).fetchone()
    current_chapter = row["mx"] if row and row["mx"] else 0

    if current_chapter == 0:
        print("\n[提示] 当前没有已写章节，无法生成伏笔报告")
        return

    mm = MemoryManager(novel_name)
    report = mm.get_foreshadow_report(current_chapter)

    trend = report["trend"]
    trend_str = f"+{trend}" if trend > 0 else str(trend)

    print("\n" + "=" * 50)
    print("  伏笔健康度报告")
    print("=" * 50)
    print(f"  未兑现总数：{report['total_active']} 个"
          f"  |  趋势：每章净增 {trend_str} 个")
    print(f"  最近10章新增：{report['recent_added']} 个"
          f"  |  已兑现：{report['recent_redeemed']} 个")
    print("-" * 50)

    due_soon = report["due_soon"]
    print(f"  【即将到期】（5章内需兑现，共{len(due_soon)}个）")
    if due_soon:
        for f in due_soon:
            print(f"  - {f['fid']}: {f['description']}"
                  f"（第{f['plant_chapter']}章埋，计划{f['expected_redeem']}）")
    else:
        print("    （无）")

    overdue = report["overdue"]
    print(f"\n  【严重超期】（沉睡20章以上，共{len(overdue)}个）")
    if overdue:
        for f in overdue[:10]:
            print(f"  - {f['fid']}: {f['description']}"
                  f"（第{f['plant_chapter']}章埋，已沉睡{f['age']}章）")
        if len(overdue) > 10:
            print(f"  ... 还有 {len(overdue) - 10} 条")
    else:
        print("    （无）")

    macro = report["macro"]
    print(f"\n  【宏观悬念】（沉睡30章以上的长线伏笔，共{len(macro)}个）")
    if macro:
        for f in macro[:10]:
            print(f"  - {f['fid']}: {f['description']}"
                  f"（第{f['plant_chapter']}章埋，已沉睡{f['age']}章）")
        if len(macro) > 10:
            print(f"  ... 还有 {len(macro) - 10} 条")
    else:
        print("    （无）")

    print("-" * 50)
    print("  提示：超期伏笔过多会影响L2审核分数")
    if macro:
        print("  注：宏观悬念类伏笔需在故事高潮章节统一处理，")
        print("      建议在第120-140章集中兑现")
    print("=" * 50)


def _breakpoint_menu(novel_name: str):
    """断点管理菜单"""
    while True:
        print("\n========== 断点管理 ==========")
        print("  [1] 查看异常任务（进行中超过30分钟）")
        print("  [2] 强制重置任务状态 → 待处理")
        print("  [3] 查看章节写作历史")
        print("  [4] 重写指定章节（保留摘要）")
        print("  [5] 清除节拍缓存（指定章节）")
        print("  [0] 返回")
        choice = input("请选择：").strip()

        if choice == "0":
            break

        elif choice == "1":
            with with_db_connection(novel_name) as conn:
                rows = conn.execute("""
                    SELECT chapter_num, status, updated_at
                    FROM chapter_tasks
                    WHERE status = ?
                    AND updated_at < datetime('now', '-30 minutes', 'localtime')
                    ORDER BY chapter_num
                """, (TASK_IN_PROGRESS,)).fetchall()
            if not rows:
                print("  ✅ 无异常任务（进行中任务均在30分钟内）")
            else:
                print(f"\n  发现 {len(rows)} 个异常任务：")
                for r in rows:
                    print(f"  第{r[0]}章 | 状态：{r[1]} | 最后更新：{r[2]}")
                print("  提示：可用 [2] 强制重置这些任务")

        elif choice == "2":
            try:
                num = int(input("  请输入要重置的章节号：").strip())
            except ValueError:
                print("  ❌ 请输入有效的章节号")
                continue
            with with_db_connection(novel_name) as conn:
                row = conn.execute(
                    "SELECT status, updated_at FROM chapter_tasks WHERE chapter_num=?",
                    (num,)
                ).fetchone()
            if not row:
                print(f"  ❌ 第{num}章任务不存在")
                continue
            print(f"  当前状态：{row[0]} | 最后更新：{row[1]}")
            confirm = input(f"  确认将第{num}章重置为「待处理」？(yes/no)：").strip()
            if confirm.lower() != "yes":
                print("  已取消")
                continue
            _update_task_status(novel_name, num, TASK_PENDING)
            import core.writer as _writer_mod
            _writer_mod._cached_beat_plan = ""
            print(f"  ✅ 第{num}章已重置为「待处理」，节拍内存缓存已清除")

        elif choice == "3":
            try:
                num = int(input("  请输入要查看的章节号：").strip())
            except ValueError:
                print("  ❌ 请输入有效的章节号")
                continue
            with with_db_connection(novel_name) as conn:
                rows = conn.execute("""
                    SELECT attempt, started_at, ended_at, end_reason, word_count, review_score
                    FROM writing_sessions
                    WHERE chapter_num=?
                    ORDER BY attempt
                """, (num,)).fetchall()
            if not rows:
                print(f"  第{num}章暂无写作历史记录（功能持续完善中）")
            else:
                print(f"\n  第{num}章写作历史（共{len(rows)}次）：")
                for r in rows:
                    score_str = f"{r[5]:.0f}分" if r[5] else "未评分"
                    print(f"  第{r[0]}次 | {r[1]} → {r[2]} | {r[3]} | {r[4]}字 | {score_str}")

        elif choice == "4":
            try:
                num = int(input("  请输入要重写的章节号：").strip())
            except ValueError:
                print("  ❌ 请输入有效的章节号")
                continue
            with with_db_connection(novel_name) as conn:
                ch = conn.execute(
                    "SELECT title, word_count FROM chapters WHERE chapter_num=?",
                    (num,)
                ).fetchone()
            if not ch:
                print(f"  ❌ 第{num}章不存在")
                continue
            print(f"  将重写：第{num}章《{ch[0]}》（{ch[1]}字）")
            print("  ⚠️  章节正文将被清空，摘要保留，任务状态重置为待处理")
            confirm = input("  确认？(yes/no)：").strip()
            if confirm.lower() != "yes":
                print("  已取消")
                continue
            with with_db_connection(novel_name) as conn:
                with DatabaseTransaction(conn):
                    conn.execute(
                        "UPDATE chapters SET content=NULL, status='草稿', "
                        "review_score_total=NULL, review_score_l1=NULL, "
                        "review_score_l2=NULL, review_score_l3=NULL, "
                        "reader_review_score=NULL, reader_review_passed=NULL, "
                        "updated_at=datetime('now','localtime') "
                        "WHERE chapter_num=?", (num,)
                    )
                    conn.execute(
                        "UPDATE chapter_tasks SET status=?, "
                        "updated_at=datetime('now','localtime') "
                        "WHERE chapter_num=?", (TASK_PENDING, num)
                    )
            print(f"  ✅ 第{num}章已重置，摘要完整保留，可重新写作")

        elif choice == "5":
            try:
                num = int(input("  请输入要清除节拍缓存的章节号：").strip())
            except ValueError:
                print("  ❌ 请输入有效的章节号")
                continue
            with with_db_connection(novel_name) as conn:
                b = conn.execute(
                    "SELECT beats_text, created_at FROM beats_cache WHERE chapter_num=?",
                    (num,)
                ).fetchone()
            if not b:
                print(f"  第{num}章无节拍缓存")
                continue
            print(f"  缓存时间：{b[1]}")
            print(f"  内容预览：{b[0][:60]}...")
            confirm = input("  确认清除？(yes/no)：").strip()
            if confirm.lower() != "yes":
                print("  已取消")
                continue
            with with_db_connection(novel_name) as conn:
                with DatabaseTransaction(conn):
                    conn.execute("DELETE FROM beats_cache WHERE chapter_num=?", (num,))
            import core.writer as _writer_mod
            _writer_mod._cached_beat_plan = ""
            print(f"  ✅ 第{num}章节拍缓存已清除（DB + 内存）")

        else:
            print("  ❌ 无效选项")


def chapters_menu(novel_name: str):
    clean_duplicate_chapters(novel_name)

    while True:
        show_progress(novel_name)

        print("\n1. 自动生成下一章")
        print("2. 批量自动生成")
        print("3. 导出所有已审核章节")
        print("4. 查看完整章节列表")
        print("5. 查看章节任务卡")
        print("6. 更换写作风格")
        print("7. 查看详细费用统计")
        print("8. 恢复审稿失败章节")
        print("9. 删除章节（重新生成）")
        print("10. 批量删除章节（按范围）")
        print("11. 高级模型配置（作者/审核/读者视角模型）")
        print("12. 查看失败统计与模型切换历史")
        print("13. 手动编辑任务卡（修改情节目标/情绪标签）")
        print("14. 查看伏笔健康度报告")
        print("15. 大纲伏笔管理")
        print("16. 断点管理")
        print("17. 编辑创作意图")
        print("0. 返回主菜单")

        choice = input("\n请选择：").strip()

        if choice == "1":
            generate_chapter_auto(novel_name)

        elif choice == "2":
            try:
                count = 0
                cancelled = False
                while True:
                    count_input = input("批量生成几章？").strip()
                    if not count_input.isdigit():
                        print(f"  ⚠️  无效输入: '{count_input}' 不是有效的数字")
                        print(f"  💡 提示: 请输入一个正整数，如 '3' 或 '5'")
                        continue
                    count = int(count_input)
                    if count < 1:
                        print(f"  ⚠️  数量必须 >= 1，您输入的是 {count}")
                        continue
                    if count > 50:
                        print(f"  ⚠️  单次批量生成建议不超过 50 章，您输入的是 {count}")
                        confirm_batch = input("  确认继续？(y/n): ").strip().lower()
                        if confirm_batch != 'y':
                            print("  ✅ 已取消批量生成")
                            cancelled = True
                            break
                    break
                if cancelled:
                    continue
                if count >= 1:
                    for i in range(count):
                        print(f"\n===== 进度：{i+1}/{count} =====")
                        generate_chapter_auto(novel_name)
            except KeyboardInterrupt:
                print("\n[提示] 已停止批量生成")

        elif choice == "3":
            results = export_all(novel_name)
            print(f"\n[OK] 共导出 {len(results)} 章到 {_output_dir(novel_name)}/")

        elif choice == "4":
            _list_chapters(novel_name)

        elif choice == "5":
            _view_tasks(novel_name)

        elif choice == "6":
            _change_style(novel_name)

        elif choice == "7":
            from core.api_client import print_session_stats
            print_session_stats()

        elif choice == "8":
            _recover_review_failed(novel_name)

        elif choice == "9":
            _delete_chapter(novel_name)  # ★ 新增

        elif choice == "10":
            _delete_chapters_batch(novel_name)

        elif choice == "11":
            from core.api_client import select_all_models_interactive
            select_all_models_interactive()

        elif choice == "12":
            from core.api_client import get_failure_stats, get_switch_history
            from core.api_client import get_author_model, get_reviewer_model, get_reader_reviewer_model
            print("\n" + "=" * 60)
            print("  失败统计与模型状态")
            print("=" * 60)
            print(f"\n【当前模型】")
            print(f"  作者模型：{get_author_model()}")
            print(f"  审核模型：{get_reviewer_model()}")
            print(f"  读者视角模型：{get_reader_reviewer_model()}")
            fail_stats = get_failure_stats()
            print(f"\n【失败计数】")
            print(f"  作者模型失败：{fail_stats['author_failures']}")
            print(f"  审核模型失败：{fail_stats['reviewer_failures']}")
            print(f"  读者视角失败：{fail_stats['reader_reviewer_failures']}")
            switch_history = get_switch_history()
            if switch_history:
                print(f"\n【模型切换历史】（共{len(switch_history)}次）")
                for i, entry in enumerate(switch_history[-5:], 1):
                    import datetime
                    ts = datetime.datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M:%S")
                    print(f"  {i}. [{ts}] {entry['type']}: {entry['old_model']} → {entry['new_model']}")
            print("\n" + "=" * 60)

        elif choice == "13":
            _edit_task_card(novel_name)

        elif choice == "14":
            show_foreshadow_report(novel_name)

        elif choice == "15":
            manage_outline_foreshadow(novel_name)

        elif choice == "16":
            _breakpoint_menu(novel_name)

        elif choice == "17":
            _edit_author_intent(novel_name)

        elif choice == "0":
            from core.api_client import get_session_stats
            stats = get_session_stats()
            if stats["total_calls"] > 0:
                from core.api_client import print_session_stats
                print_session_stats()
            break


def setup_novel():
    print("\n" + "=" * 50)
    while True:
        novel_name = input("小说名称：").strip()
        err = _validate_novel_name(novel_name)
        if not err:
            break
        print(f"[错误] {err}")
    genre = input("小说类型（如：玄幻修仙 / 都市悬疑）：").strip()
    keywords = input("关键词（如：废材逆袭、记忆修复师）：").strip()
    return novel_name, genre, keywords


def _write_novel_info(novel_name: str, genre: str):
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            conn.execute("""
                INSERT OR REPLACE INTO novel_info (id, name, genre)
                VALUES (1, ?, ?)
            """, (novel_name, genre))


def _extract_field(content: str, field_name: str) -> str:
    """从文本中提取字段值"""
    import re
    pattern = rf'{field_name}[：:]\s*(.+?)(?:\n|$)'
    match = re.search(pattern, content)
    return match.group(1).strip() if match else ""


def _extract_section(content: str, section_header: str) -> str:
    """从文本中提取章节内容"""
    import re
    pattern = rf'{re.escape(section_header)}\s*\n(.*?)(?=\n【|\Z)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        text = match.group(1).strip()
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return '\n'.join(lines)
    return ""


def _import_from_text_file():
    """从 newbook.txt 导入小说信息"""
    from pathlib import Path

    txt_path = get_project_root() / "newbook.txt"
    if not txt_path.exists():
        print("\n[错误] 未找到 newbook.txt")
        print("  请先将文件放在项目根目录（d:\\novel-ai\\）")
        print("  文件格式参考：newbook_template.txt（如不存在将自动创建）")
        _create_newbook_template()
        return None

    try:
        content = txt_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"\n[错误] 读取文件失败：{e}")
        return None

    print(f"\n{'=' * 50}")
    print(f"  正在解析 newbook.txt...")
    print(f"{'=' * 50}")

    novel_name = _extract_field(content, "书名")
    genre = _extract_field(content, "类型")
    target_chapters_str = _extract_field(content, "目标章数")

    try:
        target_chapters = int(target_chapters_str) if target_chapters_str else 100
    except ValueError:
        target_chapters = 100
        print(f"  [提示] 未检测到有效目标章数，默认100章")
    if not 10 <= target_chapters <= 1000:
        print("  [提示] 目标章数需在10-1000之间，已默认100章")
        target_chapters = 100

    outline = _extract_section(content, "【大纲】")
    characters_text = _extract_section(content, "【主要角色】")
    world_setting = _extract_section(content, "【世界观设定】")
    timeline = _extract_section(content, "【时间线】")
    core_conflict = _extract_section(content, "【核心冲突】")

    if not novel_name:
        print("\n[错误] 未检测到书名！请在 newbook.txt 中添加：")
        print("  书名：你的小说名称")
        return None
    name_error = _validate_novel_name(novel_name)
    if name_error:
        print(f"\n[错误] 书名无效：{name_error}")
        return None

    print(f"\n  解析结果预览：")
    print(f"  {'─' * 40}")
    print(f"  📖 书名：{novel_name}")
    print(f"  📂 类型：{genre or '（未指定）'}")
    print(f"  📊 目标章数：{target_chapters}")
    print(f"  📝 大纲：{(outline[:80] + '...') if len(outline) > 80 else (outline or '（未提供）')}")
    print(f"  👥 角色：{(characters_text[:80] + '...') if len(characters_text) > 80 else (characters_text or '（未提供）')}")
    print(f"  🌍 世界观：{(world_setting[:60] + '...') if len(world_setting) > 60 else (world_setting or '（未提供）')}")

    missing = []
    if not timeline:
        missing.append("时间线")
    if not core_conflict:
        missing.append("核心冲突")
    if not outline:
        missing.append("大纲")
    if not characters_text:
        missing.append("主要角色")

    if missing:
        print(f"\n  ⚠️  检测到缺少以下信息：{', '.join(missing)}")
        supplement = input(f"  是否现在补充？（y/n，默认y）：").strip().lower() or "y"
        if supplement == 'y':
            if "大纲" in missing:
                outline = input("  请输入大纲内容（可粘贴）：\n").strip()
            if "主要角色" in missing:
                characters_text = input("  请输入主要角色信息：\n").strip()
            if "时间线" in missing:
                timeline = input("  请输入时间线信息：\n").strip()
            if "核心冲突" in missing:
                core_conflict = input("  请输入核心冲突：\n").strip()

    print(f"\n{'─' * 40}")
    confirm = input(f"  确认导入《{novel_name}》？（yes/NO）：").strip()
    if confirm.lower() != 'yes':
        print("  已取消导入")
        return None

    return {
        "novel_name": novel_name,
        "genre": genre,
        "target_chapters": target_chapters,
        "outline": outline,
        "characters_text": characters_text,
        "world_setting": world_setting,
        "timeline": timeline,
        "core_conflict": core_conflict,
    }


def _parse_and_save_characters(mm, characters_text: str):
    """解析角色文本并保存"""
    import re

    if not characters_text:
        print("  [跳过] 角色信息为空")
        return

    lines = [ln.strip() for ln in characters_text.split('\n') if ln.strip()]
    characters = []

    for line in lines:
        cleaned = re.sub(r'^\s*(?:[-*]|\d+[\.\、\)\s]+)\s*', '', line)
        if not cleaned:
            continue
        parts = re.split(r'\s*[-—–]\s*', cleaned, maxsplit=1)
        name_part = parts[0].strip()
        desc = parts[1].strip() if len(parts) > 1 else ""
        role = "待确认"
        role_match = re.search(r'[（(]([^）)]+)[）)]', name_part)
        if role_match:
            role = role_match.group(1).strip() or role
            name = re.sub(r'[（(][^）)]+[）)]', '', name_part).strip()
        else:
            name = name_part
        if not name:
            continue
        characters.append({
            "name": name,
            "role": role,
            "appearance": "",
            "personality": desc,
            "secret": "",
            "weakness": "",
            "current_location": "",
            "current_status": desc,
            "relationships": {},
        })

    if characters:
        mm.save_characters_batch(characters)
        char_list = "\n".join([f"- {c['name']}" for c in characters])
        (mm.data_dir / "character_names.txt").write_text(
            f"# 角色名单\n\n{char_list}", encoding="utf-8"
        )
        print(f"  [OK] 已保存 {len(characters)} 个角色")


def _create_newbook_template():
    """创建 newbook.txt 模板文件"""
    template = """书名：时光当铺
类型：都市奇幻 / 悬疑
目标章数：150

【大纲】
在这里粘贴你从其他AI获取的完整小说大纲...
（建议包含：故事背景、主要情节线、关键转折点、结局走向）

【主要角色】
1. 林深（主角）- 心理咨询师，30岁，性格特点...
2. 顾念（配角）- 当铺掌柜，年龄不详，神秘莫测...
3. （继续添加其他重要角色）

【世界观设定】
拾光当铺：一家存在于时间缝隙中的神秘店铺...
（详细描述你的世界规则、特殊设定、社会结构等）

【时间线】
- 故事开始：2025年冬，林深事业受挫
- 核心事件：林深偶然发现当铺
- 发展阶段：逐渐了解当铺的秘密
- 高潮：当铺面临关闭危机
- 结局：林深成为新任掌柜

【核心冲突】
时间 vs 命运 - 主角能否改变过去的悲剧？
（描述推动故事发展的核心矛盾和冲突）
"""

    from pathlib import Path
    template_path = get_project_root() / "newbook_template.txt"
    if not template_path.exists():
        template_path.write_text(template, encoding="utf-8")
        print(f"  ✨ 已创建模板文件：{template_path}")
        print(f"     可复制该文件并重命名为 newbook.txt 填写内容")


# ==================== 日志配置（Fix Bug10） ====================
def _setup_logging():
    """初始化日志：控制台输出 INFO，文件记录 DEBUG（含 API 错误）"""
    log_dir = get_project_root() / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "run.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    # 只向文件写日志，不干扰 print 的控制台输出
    logger = logging.getLogger("novel_ai")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(file_handler)

    return logger


# ==================== Ctrl+C 状态回滚（Fix Bug7） ====================
_current_novel_name: str = None
_current_chapter_num: int = None


def _register_sigint_handler():
    """注册 Ctrl+C 处理器，保证章节状态正常回滚"""
    def _handle_sigint(sig, frame):
        print("\n\n[中断] 检测到 Ctrl+C，正在回滚章节状态...")
        if _current_novel_name and _current_chapter_num:
            try:
                with with_db_connection(_current_novel_name) as conn:
                    with DatabaseTransaction(conn):
                        conn.execute(
                            "UPDATE chapter_tasks SET status='待处理', "
                            "updated_at=datetime('now','localtime') "
                            "WHERE chapter_num=? AND status='进行中'",
                            (_current_chapter_num,)
                        )
                        conn.execute(
                            "UPDATE chapters SET status='草稿' "
                            "WHERE chapter_num=? AND status='writing'",
                            (_current_chapter_num,)
                        )
                print(f"[OK] 第{_current_chapter_num}章状态已回滚为【待处理】")
            except Exception as e:
                print(f"[警告] 状态回滚失败（可在章节菜单手动恢复）：{e}")
        from core.api_client import print_session_stats, get_session_stats
        stats = get_session_stats()
        if stats["total_calls"] > 0:
            print_session_stats()
        print("已安全退出。")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)


def main():
    print("\n" + "=" * 50)
    print("       AI 网文写作系统 v2.0")
    print("=" * 50)

    _setup_logging()
    _register_sigint_handler()

    from core.api_client import select_model_interactive
    select_model_interactive()

    while True:
        print("\n1. 新建小说")
        print("2. 继续写作已有小说")
        print("3. 删除小说")
        print("4. 高级模型配置（分别设置作者/审核/读者视角模型）")
        print("0. 退出")

        choice = input("\n请选择：").strip()

        if choice == "1":
            print("\n新建小说方式：")
            print("  1. 交互式创建（向导模式）")
            print("  2. 从文本文件导入（newbook.txt）")

            mode = input("请选择（默认1）：").strip() or "1"

            if mode == "2":
                import_data = _import_from_text_file()
                if not import_data:
                    continue

                novel_name = import_data["novel_name"]
                genre = import_data["genre"]

                from core.db import init_database
                init_database(novel_name)
                _write_novel_info(novel_name, genre)

                mm = MemoryManager(novel_name)

                if import_data.get("outline"):
                    (mm.data_dir / "master_outline.md").write_text(
                        f"# 总大纲\n\n{import_data['outline']}", encoding="utf-8"
                    )
                    print(f"  [OK] 大纲已保存")

                if import_data.get("world_setting"):
                    mm.save_world_settings(import_data["world_setting"])
                    print(f"  [OK] 世界观已保存")

                if import_data.get("characters_text"):
                    _parse_and_save_characters(mm, import_data["characters_text"])

                style_key = get_style_choice()
                (mm.data_dir / "style.txt").write_text(style_key, encoding="utf-8")

                # 生成创作意图模板（优化B）
                _intent_path = mm.data_dir / "author_intent.md"
                if not _intent_path.exists():
                    _intent_path.write_text(
                        "# 创作意图\n\n"
                        "## 这本书想成为什么\n"
                        "（基调、主题、核心吸引力）\n\n"
                        "## 不想变成什么样\n"
                        "（避免的风格、雷区）\n",
                        encoding="utf-8"
                    )
                _focus_path = mm.data_dir / "current_focus.md"
                if not _focus_path.exists():
                    _focus_path.write_text(
                        "# 当前焦点\n\n"
                        "## 最近 1-3 章的重点\n"
                        "（当前弧线的核心冲突、需要推进的线索）\n",
                        encoding="utf-8"
                    )

                target_chapters = import_data.get("target_chapters", 100)
                (mm.data_dir / "target_chapters.txt").write_text(
                    str(target_chapters), encoding="utf-8"
                )

                from core.outline_manager import generate_outline_foreshadow
                generate_outline_foreshadow(novel_name, target_chapters, review_mode=False)

                split_outline_to_tasks(
                    import_data.get("outline", ""), novel_name,
                    target_chapters=target_chapters,
                    full_batch=True,
                )

                print(f"\n{'=' * 50}")
                print(f"✅ 导入完成！《{novel_name}》已就绪")
                print(f"目标篇幅：{target_chapters}章 | 任务卡：{target_chapters}张")
                print(f"{'=' * 50}\n")

                chapters_menu(novel_name)
            else:
                novel_name, genre, keywords = setup_novel()

                from core.db import init_database
                init_database(novel_name)
                _write_novel_info(novel_name, genre)

                _, style_key = run_planner(novel_name, genre, keywords)

            style_path = _data_dir(novel_name) / "style.txt"
            style_path.write_text(style_key, encoding="utf-8")

            # 生成创作意图模板（优化B）
            dd = _data_dir(novel_name)
            _intent_path = dd / "author_intent.md"
            if not _intent_path.exists():
                _intent_path.write_text(
                    "# 创作意图\n\n"
                    "## 这本书想成为什么\n"
                    "（基调、主题、核心吸引力）\n\n"
                    "## 不想变成什么样\n"
                    "（避免的风格、雷区）\n",
                    encoding="utf-8"
                )
            _focus_path = dd / "current_focus.md"
            if not _focus_path.exists():
                _focus_path.write_text(
                    "# 当前焦点\n\n"
                    "## 最近 1-3 章的重点\n"
                    "（当前弧线的核心冲突、需要推进的线索）\n",
                    encoding="utf-8"
                )

            print("\n策划完成！开始写章节。")
            chapters_menu(novel_name)

        elif choice == "2":
            novels = _list_novels()
            if not novels:
                print("[提示] 暂无已有小说，请先新建")
                continue

            print("\n已有小说：")
            for i, (name, genre) in enumerate(novels, 1):
                genre_display = (
                    f"  [{genre}]" if genre != "未记录"
                    else "  [类型未记录]"
                )
                print(f"{i}. {name}{genre_display}")

            try:
                while True:
                    idx_input = input("\n请选择编号：").strip()
                    if not idx_input.isdigit():
                        print(f"  ⚠️  无效输入: '{idx_input}' 不是有效的编号")
                        print(f"  💡 提示: 请输入列表中的数字编号，如 '1' 或 '2'")
                        continue
                    idx = int(idx_input) - 1
                    if idx < 0 or idx >= len(novels):
                        print(f"  ⚠️  编号超出范围，有效范围: 1-{len(novels)}")
                        continue
                    break
                novel_name = novels[idx][0]
                print(f"\n继续写作《{novel_name}》")
                chapters_menu(novel_name)
            except (ValueError, IndexError):
                print("  ⚠️  无效选择，请重新输入")
            except KeyboardInterrupt:
                print("\n[提示] 已取消操作")

        elif choice == "3":
            novels = _list_novels()
            if not novels:
                print("[提示] 暂无可删除的小说")
                continue

            print("\n已有小说：")
            for i, (name, genre) in enumerate(novels, 1):
                print(f"{i}. {name}")

            try:
                while True:
                    idx_input = input("\n请选择要删除的编号（0取消）：").strip()
                    if not idx_input.isdigit():
                        print(f"  ⚠️  无效输入: '{idx_input}' 不是有效的编号")
                        print(f"  💡 提示: 请输入列表中的数字编号，或 '0' 取消")
                        continue
                    idx = int(idx_input)
                    if idx == 0:
                        break
                    if idx < 1 or idx > len(novels):
                        print(f"  ⚠️  编号超出范围，有效范围: 0-{len(novels)}")
                        continue
                    break
                if idx == 0:
                    continue
                novel_name = novels[idx - 1][0]
                _delete_novel(novel_name)
            except (ValueError, IndexError):
                print("  ⚠️  无效选择，请重新输入")
            except KeyboardInterrupt:
                print("\n[提示] 已取消操作")

        elif choice == "4":
            from core.api_client import select_all_models_interactive
            select_all_models_interactive()

        elif choice == "0":
            from core.api_client import get_session_stats, print_session_stats
            stats = get_session_stats()
            if stats["total_calls"] > 0:
                print_session_stats()
            print("\n再见！")
            break


if __name__ == "__main__":
    main()
