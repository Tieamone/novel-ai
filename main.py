import sys
import os
from pathlib import Path

from core.memory_manager import MemoryManager
from core.planner import run_planner, extend_tasks
from core.reviewer import write_and_review
from core.exporter import export_chapter, export_all
from core.db import get_connection, clean_duplicate_chapters
from core.config_loader import get as cfg


def _data_dir(novel_name: str) -> Path:
    base = cfg("paths", "data_dir", "data")
    return Path(base) / novel_name


def _output_dir(novel_name: str) -> Path:
    base = cfg("paths", "output_dir", "output")
    return Path(base) / novel_name


def show_progress(novel_name: str):
    conn = get_connection(novel_name)

    approved = conn.execute(
        "SELECT COUNT(*) as cnt FROM chapters "
        "WHERE status IN ('approved','force_approved')"
    ).fetchone()["cnt"]

    draft = conn.execute(
        "SELECT COUNT(*) as cnt FROM chapters "
        "WHERE status='draft'"
    ).fetchone()["cnt"]

    total_chars = conn.execute(
        "SELECT SUM(LENGTH(content)) as s FROM chapters"
    ).fetchone()["s"] or 0

    recent = conn.execute(
        "SELECT chapter_num, status, LENGTH(content) as chars "
        "FROM chapters ORDER BY chapter_num DESC LIMIT 3"
    ).fetchall()

    foreshadow_cnt = conn.execute(
        "SELECT COUNT(*) as cnt FROM foreshadowing "
        "WHERE status='active'"
    ).fetchone()["cnt"]

    task_cnt = conn.execute(
        "SELECT COUNT(*) as cnt FROM chapter_tasks"
    ).fetchone()["cnt"]

    conn.close()

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

    if total_chars >= 10000:
        chars_display = f"~{total_chars/10000:.1f}万字"
    else:
        chars_display = f"{total_chars}字"

    print("\n" + "=" * 50)
    print(f"  《{novel_name}》  [{style_name}]")
    print("=" * 50)
    print(f"  [{bar}] {percent}%")
    print(f"  目标：{target}章  |  已完成：{approved}章  |  草稿：{draft}章")
    print(f"  累计字数：{total_chars:,} 字  ({chars_display})")
    print(f"  未兑现伏笔：{foreshadow_cnt} 个  |  任务卡：{task_cnt} 张")
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
        "WHERE chapter_num=?",
        (chapter_num,)
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
            "WHERE chapter_num=?",
            (chapter_num,)
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

    content = write_and_review(
        novel_name=novel_name,
        chapter_num=next_num,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
    )

    if content:
        summary = _save_chapter_memory(novel_name, next_num, content,
                                       plot_goal)
        export_chapter(novel_name, next_num)

        from core.api_client import get_session_stats
        stats = get_session_stats()
        out_path = _output_dir(novel_name) / f"第{str(next_num).zfill(3)}章.txt"
        print(f"\n[完成] 第{next_num}章已生成并导出")
        print(f"文件：{out_path}")
        print(f"本次会话累计费用：¥{stats['total_cost_yuan']:.4f} 元")


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
      "new_relationship": "新的关系描述（如：由信任转为怀疑）"
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

    # 1. 摘要
    summary = data.get("summary", "")
    mm.add_summary(chapter_num, summary)
    # 回写到 chapters 表
    mm.update_chapter_summary(chapter_num, summary)
    print(f"  [OK] 摘要：{summary[:60]}...")

    # 2. 人物位置/状态
    for c in data.get("character_updates", []):
        name = c.get("name", "")
        loc = c.get("location", "")
        status = c.get("status", "")
        if name and (loc or status):
            mm.update_character_status(name, loc, status, chapter_num)
            print(f"  [OK] 人物更新：{name} → {loc} / {status}")

    # 3. 人物关系变化（新增，真正写回）
    for r in data.get("relationship_updates", []):
        name = r.get("name", "")
        with_name = r.get("with", "")
        new_rel = r.get("new_relationship", "")
        if name and with_name and new_rel:
            mm.update_character_relationship(
                name, with_name, new_rel, chapter_num
            )
            print(f"  [OK] 关系更新：{name} ↔ {with_name} → {new_rel}")

    # 4. 新增伏笔
    for f in data.get("new_foreshadowing", []):
        fid = f.get("fid", "")
        desc = f.get("description", "")
        redeem = f.get("expected_redeem", "待定")
        if fid and desc:
            mm.add_foreshadowing(fid, chapter_num, desc, redeem)
            print(f"  [OK] 新伏笔：{desc[:30]}...")

    # 5. 兑现伏笔
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
    threshold = cfg("novel", "compress_after_chapters", 20)
    if count > threshold:
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
    print("-" * 50)
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
        "SELECT chapter_num, emotion_tag, plot_goal FROM chapter_tasks "
        "ORDER BY chapter_num LIMIT 30"
    ).fetchall()
    conn.close()
    if not rows:
        print("\n暂无任务卡")
        return
    print(f"\n章节任务卡（显示前{len(rows)}张）：")
    print("-" * 60)
    for row in rows:
        print(f"  第{row['chapter_num']:>3}章 "
              f"[{row['emotion_tag']}] "
              f"{row['plot_goal']}")


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
        print()
        custom_desc = input("  请描述你想要的写作风格：").strip()
        if custom_desc:
            style_key = f"custom:{custom_desc}"
            style_path = _data_dir(novel_name) / "style.txt"
            style_path.write_text(style_key, encoding="utf-8")
            print("[OK] 自定义风格已保存，下一章开始生效")
            return

    if choice not in AUTHOR_STYLES:
        choice = "1"

    style_path = _data_dir(novel_name) / "style.txt"
    style_path.write_text(choice, encoding="utf-8")
    print(f"[OK] 风格已更换为：{AUTHOR_STYLES[choice]['name']}，下一章开始生效")


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
            out = _output_dir(novel_name)
            print(f"\n[OK] 共导出 {len(results)} 章到 {out}/")

        elif choice == "4":
            _list_chapters(novel_name)

        elif choice == "5":
            _view_tasks(novel_name)

        elif choice == "6":
            _change_style(novel_name)

        elif choice == "7":
            from core.api_client import print_session_stats
            print_session_stats()

        elif choice == "0":
            from core.api_client import get_session_stats
            stats = get_session_stats()
            if stats["total_calls"] > 0:
                from core.api_client import print_session_stats
                print_session_stats()
            break


def setup_novel():
    print("\n" + "=" * 50)
    novel_name = input("小说名称：").strip()
    genre = input("小说类型（如：玄幻修仙 / 都市悬疑）：").strip()
    keywords = input("关键词（如：废材逆袭、记忆修复师）：").strip()
    return novel_name, genre, keywords


def _write_novel_info(novel_name: str, genre: str):
    """写入 novel_info 表"""
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

    print("1. 新建小说")
    print("2. 继续写作已有小说")

    choice = input("\n请选择：").strip()

    if choice == "1":
        novel_name, genre, keywords = setup_novel()

        # 写入 novel_info
        from core.db import init_database
        init_database(novel_name)
        _write_novel_info(novel_name, genre)

        # 策划流程
        _, style_key = run_planner(novel_name, genre, keywords)

        # 保存风格
        style_path = _data_dir(novel_name) / "style.txt"
        style_path.write_text(style_key, encoding="utf-8")

        print("\n策划完成！开始写章节。")
        chapters_menu(novel_name)

    elif choice == "2":
        data_base = cfg("paths", "data_dir", "data")
        data_dir = Path(data_base)
        novels = [
            d.name for d in data_dir.iterdir()
            if d.is_dir() and (d / "novel.db").exists()
        ] if data_dir.exists() else []

        if not novels:
            print("[提示] 暂无已有小说，请先新建")
            return

        print("\n已有小说：")
        for i, name in enumerate(novels, 1):
            print(f"{i}. {name}")

        try:
            idx = int(input("\n请选择编号：").strip()) - 1
            novel_name = novels[idx]
            print(f"\n继续写作《{novel_name}》")
            chapters_menu(novel_name)
        except (ValueError, IndexError):
            print("[错误] 无效选择")

    from core.api_client import get_session_stats, print_session_stats
    stats = get_session_stats()
    if stats["total_calls"] > 0:
        print_session_stats()


if __name__ == "__main__":
    main()