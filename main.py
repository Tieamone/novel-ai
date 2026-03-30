import sys
import os
import shutil
import json
from pathlib import Path

from core.memory_manager import MemoryManager
from core.planner import run_planner, extend_tasks
from core.reviewer import write_and_review
from core.exporter import export_chapter, export_all
from core.db import get_connection, clean_duplicate_chapters
from core.config_loader import get as cfg

MAX_NOVEL_NAME_LEN = 64
INVALID_NOVEL_NAME_CHARS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
TASK_PENDING = "pending"
TASK_IN_PROGRESS = "in_progress"
TASK_COMPLETED = "completed"
TASK_REVIEW_FAILED = "review_failed"


def _data_dir(novel_name: str) -> Path:
    return Path(cfg("paths", "data_dir", "data")) / novel_name


def _output_dir(novel_name: str) -> Path:
    return Path(cfg("paths", "output_dir", "output")) / novel_name


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
    返回 [(novel_name, display_name, genre)] 列表。
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
            conn = get_connection(name)
            row = conn.execute(
                "SELECT genre FROM novel_info WHERE id=1"
            ).fetchone()
            conn.close()
            if row and row["genre"]:
                genre = row["genre"]
        except Exception:
            pass
        novels.append((name, genre))

    return novels


def _upsert_task_status(novel_name: str, chapter_num: int,
                        plot_goal: str, emotion_tag: str,
                        status: str):
    conn = get_connection(novel_name)
    conn.execute("""
        INSERT OR REPLACE INTO chapter_tasks
        (chapter_num, plot_goal, emotion_tag, status)
        VALUES (?, ?, ?, ?)
    """, (chapter_num, plot_goal, emotion_tag, status))
    conn.commit()
    conn.close()


def _claim_task_for_writing(novel_name: str, chapter_num: int,
                            plot_goal: str, emotion_tag: str) -> tuple:
    """
    原子认领任务：
    仅允许 pending -> in_progress。
    返回 (是否成功, 当前状态)。
    """
    conn = get_connection(novel_name)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            INSERT OR IGNORE INTO chapter_tasks
            (chapter_num, plot_goal, emotion_tag, status)
            VALUES (?, ?, ?, ?)
        """, (chapter_num, plot_goal, emotion_tag, TASK_PENDING))

        row = conn.execute("""
            SELECT plot_goal, emotion_tag, status
            FROM chapter_tasks
            WHERE chapter_num=?
        """, (chapter_num,)).fetchone()

        saved_goal = ((row["plot_goal"] if row else "") or "").strip()
        saved_tag = ((row["emotion_tag"] if row else "") or "").strip()
        final_goal = saved_goal or plot_goal
        final_tag = saved_tag or emotion_tag or "铺垫"

        cur = conn.execute("""
            UPDATE chapter_tasks
            SET plot_goal=?, emotion_tag=?, status=?
            WHERE chapter_num=? AND COALESCE(status, ?) = ?
        """, (
            final_goal, final_tag, TASK_IN_PROGRESS,
            chapter_num, TASK_PENDING, TASK_PENDING
        ))
        conn.commit()

        if cur.rowcount == 1:
            return True, TASK_IN_PROGRESS

        row2 = conn.execute(
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
    finally:
        conn.close()


def _update_task_status(novel_name: str, chapter_num: int, status: str):
    conn = get_connection(novel_name)
    cur = conn.execute("""
        UPDATE chapter_tasks
        SET status=?
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
    conn.commit()
    conn.close()


def _get_chapter_status(novel_name: str, chapter_num: int) -> str:
    conn = get_connection(novel_name)
    row = conn.execute(
        "SELECT status FROM chapters WHERE chapter_num=?",
        (chapter_num,),
    ).fetchone()
    conn.close()
    return row["status"] if row else ""


def _has_chapter_summary(novel_name: str, chapter_num: int) -> bool:
    conn = get_connection(novel_name)
    row = conn.execute(
        "SELECT summary FROM chapters WHERE chapter_num=?",
        (chapter_num,),
    ).fetchone()
    conn.close()
    if not row:
        return False
    return bool((row["summary"] or "").strip())


def show_progress(novel_name: str):
    conn = get_connection(novel_name)

    approved = conn.execute(
        "SELECT COUNT(*) as cnt FROM chapters "
        "WHERE status IN ('approved','force_approved')"
    ).fetchone()["cnt"]

    draft = conn.execute(
        "SELECT COUNT(*) as cnt FROM chapters WHERE status='draft'"
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

    conn.close()

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

    target = 100
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
          f"pending {task_status_map[TASK_PENDING]}  |  "
          f"in_progress {task_status_map[TASK_IN_PROGRESS]}  |  "
          f"completed {task_status_map[TASK_COMPLETED]}  |  "
          f"review_failed {task_status_map[TASK_REVIEW_FAILED]}")
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
                "approved", "force_approved") else "○"
            print(f"    {icon} 第{r['chapter_num']}章  "
                  f"{r['chars']}字  [{r['status']}]")
    print("=" * 50)


def get_next_chapter_goal(novel_name: str, chapter_num: int) -> tuple:
    conn = get_connection(novel_name)
    task = conn.execute(
        "SELECT plot_goal, emotion_tag FROM chapter_tasks "
        "WHERE chapter_num=?", (chapter_num,)
    ).fetchone()
    max_task = conn.execute(
        "SELECT MAX(chapter_num) as mx FROM chapter_tasks"
    ).fetchone()["mx"] or 0
    conn.close()

    if task and task["plot_goal"]:
        return task["plot_goal"], task["emotion_tag"]

    if chapter_num > max_task:
        print(f"  [提示] 任务卡已用完（最大第{max_task}章），正在扩展...")
        extend_tasks(novel_name, max_task + 1)
        conn = get_connection(novel_name)
        task = conn.execute(
            "SELECT plot_goal, emotion_tag FROM chapter_tasks "
            "WHERE chapter_num=?", (chapter_num,)
        ).fetchone()
        conn.close()
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
情节目标：xxx（50字以内）
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
    conn = get_connection(novel_name)
    row = conn.execute(
        "SELECT MAX(chapter_num) as max_num FROM chapters"
    ).fetchone()
    conn.close()

    last_num = row["max_num"] if row["max_num"] else 0
    next_num = last_num + 1

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
            print("  [提示] 该章节任务处于 review_failed，请走恢复入口处理")
        else:
            print(f"  [提示] 该章节任务当前状态为 {current_status}，已跳过")
        return

    content = write_and_review(
        novel_name=novel_name,
        chapter_num=next_num,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
    )

    if content:
        _save_chapter_memory(novel_name, next_num, content, plot_goal)
        export_chapter(novel_name, next_num)
        chapter_status = _get_chapter_status(novel_name, next_num)
        if chapter_status in ("approved", "force_approved"):
            _update_task_status(novel_name, next_num, TASK_COMPLETED)
        elif chapter_status == "review_failed":
            _update_task_status(novel_name, next_num, TASK_REVIEW_FAILED)
        else:
            _update_task_status(novel_name, next_num, TASK_PENDING)
        from core.api_client import get_session_stats
        stats = get_session_stats()
        print(f"\n[完成] 第{next_num}章已生成并导出")
        print(f"文件：{_output_dir(novel_name)}/第{str(next_num).zfill(3)}章.txt")
        print(f"本次会话累计费用：¥{stats['total_cost_yuan']:.4f} 元")
    else:
        chapter_status = _get_chapter_status(novel_name, next_num)
        if chapter_status == "review_failed":
            _update_task_status(novel_name, next_num, TASK_REVIEW_FAILED)
            print("\n[中断] 本章因审稿异常标记为 review_failed。")
            print("可在章节菜单使用“恢复review_failed章节”继续处理。")
        else:
            _update_task_status(novel_name, next_num, TASK_PENDING)
            print("\n[中断] 本章未完成，任务状态已回退为 pending。")


def _save_chapter_memory(novel_name: str, chapter_num: int,
                         content: str, plot_goal: str = "") -> str:
    from core.api_client import call_api
    import json
    import re
    mm = MemoryManager(novel_name)
    print("  正在提取章节记忆...")

    raw = call_api(
        system_prompt="你是小说编辑，提取章节关键信息，只输出JSON。",
        user_message=f"""分析以下章节，严格按JSON格式输出：

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
      "expected_redeem": "预计兑现章节"
    }}
  ],
  "redeemed_foreshadowing": ["已兑现伏笔ID，没有则为空列表"]
}}

章节内容：
{content[:3000]}""",
        temperature=0.2,
        max_tokens=800,
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

    for f in data.get("new_foreshadowing", []):
        fid = f.get("fid", "")
        desc = f.get("description", "")
        redeem = f.get("expected_redeem", "待定")
        if fid and desc:
            mm.add_foreshadowing(fid, chapter_num, desc, redeem)
            print(f"  [OK] 新伏笔：{desc[:30]}...")

    for fid in data.get("redeemed_foreshadowing", []):
        if fid:
            mm.redeem_foreshadowing(fid, chapter_num)
            print(f"  [OK] 伏笔兑现：{fid}")

    _trigger_compression(novel_name, mm)
    return summary


def _trigger_compression(novel_name: str, mm: MemoryManager):
    conn = get_connection(novel_name)
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM summaries WHERE is_compressed=0"
    ).fetchone()["cnt"]
    conn.close()
    if count > cfg("novel", "compress_after_chapters", 20):
        mm.compress_old_summaries()


def _list_chapters(novel_name: str):
    conn = get_connection(novel_name)
    rows = conn.execute(
        "SELECT chapter_num, status, emotion_tag, retry_count, "
        "LENGTH(content) as chars "
        "FROM chapters ORDER BY chapter_num"
    ).fetchall()
    conn.close()
    if not rows:
        print("\n暂无章节")
        return
    print(f"\n{'':2}{'章节':<8} {'情绪':<6} {'重试':<4} {'状态':<16} {'字数'}")
    print("-" * 52)
    for row in rows:
        icon = "✓" if row["status"] in (
            "approved", "force_approved") else "○"
        retry = row["retry_count"] or 0
        tag = row["emotion_tag"] or "-"
        print(f"  {icon} 第{row['chapter_num']}章  "
              f"{tag:<6} "
              f"{'×'+str(retry) if retry else '-':<4} "
              f"{row['status']:<16} "
              f"{row['chars'] or 0}字")


def _view_tasks(novel_name: str):
    conn = get_connection(novel_name)
    rows = conn.execute(
        "SELECT chapter_num, emotion_tag, status, plot_goal FROM chapter_tasks "
        "ORDER BY chapter_num LIMIT 30"
    ).fetchall()
    conn.close()
    if not rows:
        print("\n暂无任务卡")
        print("提示：旧小说可在继续写作时自动扩展任务卡")
        return
    print(f"\n章节任务卡（显示前{len(rows)}张）：")
    print("-" * 60)
    for row in rows:
        print(f"  第{row['chapter_num']:>3}章 "
              f"[{row['emotion_tag']}] "
              f"[{row['status'] or TASK_PENDING}] "
              f"{row['plot_goal']}")


def _recover_review_failed(novel_name: str):
    conn = get_connection(novel_name)
    rows = conn.execute("""
        SELECT chapter_num, title, plot_goal, emotion_tag, content,
               retry_count, LENGTH(content) as chars
        FROM chapters
        WHERE status='review_failed'
        ORDER BY chapter_num
    """).fetchall()
    conn.close()

    if not rows:
        print("\n[提示] 当前没有 review_failed 章节")
        return

    print("\nreview_failed 章节：")
    for r in rows:
        print(f"  第{r['chapter_num']}章  "
              f"[{r['emotion_tag'] or '-'}]  "
              f"{r['chars'] or 0}字  "
              f"重试×{r['retry_count'] or 0}")

    try:
        chapter_num = int(input("\n请输入要处理的章节号（0取消）：").strip())
    except ValueError:
        print("[错误] 请输入数字")
        return

    if chapter_num == 0:
        return

    target = None
    for r in rows:
        if r["chapter_num"] == chapter_num:
            target = r
            break
    if not target:
        print("[错误] 章节号不在 review_failed 列表中")
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
            mm.update_chapter_status(chapter_num, "approved")
            _update_task_status(novel_name, chapter_num, TASK_COMPLETED)
            if not _has_chapter_summary(novel_name, chapter_num):
                _save_chapter_memory(
                    novel_name, chapter_num, content, target["plot_goal"] or ""
                )
            export_chapter(novel_name, chapter_num)
            print(f"[OK] 第{chapter_num}章已恢复为 approved 并导出")
        else:
            mm.update_chapter_status(chapter_num, "review_failed")
            _update_task_status(novel_name, chapter_num, TASK_REVIEW_FAILED)
            print(f"[提示] 第{chapter_num}章仍未通过审稿，保持 review_failed")
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
            if not _has_chapter_summary(novel_name, chapter_num):
                _save_chapter_memory(novel_name, chapter_num, content, plot_goal)
            export_chapter(novel_name, chapter_num)
            chapter_status = _get_chapter_status(novel_name, chapter_num)
            if chapter_status in ("approved", "force_approved"):
                _update_task_status(novel_name, chapter_num, TASK_COMPLETED)
            elif chapter_status == "review_failed":
                _update_task_status(novel_name, chapter_num, TASK_REVIEW_FAILED)
            else:
                _update_task_status(novel_name, chapter_num, TASK_PENDING)
            print(f"[OK] 第{chapter_num}章重试完成")
        else:
            chapter_status = _get_chapter_status(novel_name, chapter_num)
            if chapter_status == "review_failed":
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
        mm.update_chapter_status(chapter_num, "force_approved")
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
    if choice not in AUTHOR_STYLES:
        choice = "1"
    (_data_dir(novel_name) / "style.txt").write_text(choice, encoding="utf-8")
    print(f"[OK] 风格已更换为：{AUTHOR_STYLES[choice]['name']}，下一章开始生效")


def _delete_novel(novel_name: str) -> bool:
    """
    删除小说，含二次确认。
    删除范围：data/{novel_name}/ 和 output/{novel_name}/
    返回 True 表示已删除，False 表示取消。
    """
    data_path = _data_dir(novel_name)
    out_path = _output_dir(novel_name)

    # 统计内容
    chapter_count = 0
    try:
        conn = get_connection(novel_name)
        chapter_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM chapters"
        ).fetchone()["cnt"]
        conn.close()
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
        print("8. 恢复review_failed章节")
        print("0. 返回主菜单")

        choice = input("\n请选择：").strip()

        if choice == "1":
            generate_chapter_auto(novel_name)

        elif choice == "2":
            try:
                count = int(input("批量生成几章？").strip())
                for i in range(count):
                    print(f"\n===== 进度：{i+1}/{count} =====")
                    generate_chapter_auto(novel_name)
            except ValueError:
                print("[错误] 请输入数字")
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
    conn = get_connection(novel_name)
    conn.execute("""
        INSERT OR REPLACE INTO novel_info (id, name, genre)
        VALUES (1, ?, ?)
    """, (novel_name, genre))
    conn.commit()
    conn.close()


def main():
    print("\n" + "=" * 50)
    print("       AI 网文写作系统 v1.0")
    print("=" * 50)

    from core.api_client import select_model_interactive
    select_model_interactive()

    while True:
        print("1. 新建小说")
        print("2. 继续写作已有小说")
        print("3. 删除小说")
        print("0. 退出")

        choice = input("\n请选择：").strip()

        if choice == "1":
            novel_name, genre, keywords = setup_novel()

            from core.db import init_database
            init_database(novel_name)
            _write_novel_info(novel_name, genre)

            _, style_key = run_planner(novel_name, genre, keywords)

            style_path = _data_dir(novel_name) / "style.txt"
            style_path.write_text(style_key, encoding="utf-8")

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
                idx = int(input("\n请选择编号：").strip()) - 1
                novel_name = novels[idx][0]
                print(f"\n继续写作《{novel_name}》")
                chapters_menu(novel_name)
            except (ValueError, IndexError):
                print("[错误] 无效选择")

        elif choice == "3":
            novels = _list_novels()
            if not novels:
                print("[提示] 暂无可删除的小说")
                continue

            print("\n已有小说：")
            for i, (name, genre) in enumerate(novels, 1):
                print(f"{i}. {name}")

            try:
                idx = int(input("\n请选择要删除的编号（0取消）：").strip())
                if idx == 0:
                    continue
                novel_name = novels[idx - 1][0]
                _delete_novel(novel_name)
            except (ValueError, IndexError):
                print("[错误] 无效选择")

        elif choice == "0":
            from core.api_client import get_session_stats, print_session_stats
            stats = get_session_stats()
            if stats["total_calls"] > 0:
                print_session_stats()
            print("\n再见！")
            break


if __name__ == "__main__":
    main()
