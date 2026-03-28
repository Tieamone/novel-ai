import sys
import os
from pathlib import Path

from core.memory_manager import MemoryManager
from core.planner import run_planner
from core.reviewer import write_and_review
from core.exporter import export_chapter, export_all
from core.db import get_connection, clean_duplicate_chapters


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

    conn.close()

    style_path = Path("data") / novel_name / "style.txt"
    style_name = "未设置"
    if style_path.exists():
        from core.writer import AUTHOR_STYLES
        key = style_path.read_text(encoding="utf-8").strip()
        style_name = AUTHOR_STYLES.get(key, {}).get("name", "未知")

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
    print(f"  未兑现伏笔：{foreshadow_cnt} 个")
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
    outline_path = Path("data") / novel_name / "master_outline.md"
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

    print(f"\n正在从大纲分析第{next_num}章任务...")
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
        _save_chapter_memory(novel_name, next_num, content)
        export_chapter(novel_name, next_num)

        from core.api_client import get_session_stats
        stats = get_session_stats()
        print(f"\n[完成] 第{next_num}章已生成并导出")
        print(f"文件：output/{novel_name}/第{str(next_num).zfill(3)}章.txt")
        print(f"本次会话累计费用：¥{stats['total_cost_yuan']:.4f} 元")


def _save_chapter_memory(novel_name: str, chapter_num: int, content: str):
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
      "status": "当前状态",
      "relationship_change": "关系变化，没有填空字符串"
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
        max_tokens=600,
    )

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        mm.add_summary(chapter_num, raw[:200])
        print("  [OK] 摘要已保存（简单模式）")
        return

    try:
        data = json.loads(match.group())
    except Exception:
        mm.add_summary(chapter_num, raw[:200])
        print("  [OK] 摘要已保存（简单模式）")
        return

    summary = data.get("summary", "")
    mm.add_summary(chapter_num, summary)
    print(f"  [OK] 摘要：{summary[:60]}...")

    for c in data.get("character_updates", []):
        name = c.get("name", "")
        loc = c.get("location", "")
        status = c.get("status", "")
        if name and (loc or status):
            mm.update_character_status(name, loc, status, chapter_num)
            print(f"  [OK] 人物更新：{name} → {loc} / {status}")

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


def _list_chapters(novel_name: str):
    conn = get_connection(novel_name)
    rows = conn.execute(
        "SELECT chapter_num, status, LENGTH(content) as chars "
        "FROM chapters ORDER BY chapter_num"
    ).fetchall()
    conn.close()

    if not rows:
        print("\n暂无章节")
        return

    print(f"\n{'':4}{'章节':<8} {'状态':<16} {'字数'}")
    print("-" * 38)
    for row in rows:
        icon = "✓" if row["status"] in (
            "approved", "force_approved") else "○"
        print(f"  {icon} 第{row['chapter_num']}章   "
              f"{row['status']:<16} "
              f"{row['chars'] or 0}字")


def _change_style(novel_name: str):
    from core.writer import select_style_interactive
    style_key = select_style_interactive()
    style_path = Path("data") / novel_name / "style.txt"
    style_path.write_text(style_key, encoding="utf-8")
    print("[OK] 风格已更新，下一章开始生效")


def chapters_menu(novel_name: str):
    clean_duplicate_chapters(novel_name)

    while True:
        show_progress(novel_name)

        print("\n1. 自动生成下一章")
        print("2. 批量自动生成")
        print("3. 导出所有已审核章节")
        print("4. 查看完整章节列表")
        print("5. 更换写作风格")
        print("6. 查看详细费用统计")
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
            print(f"\n[OK] 共导出 {len(results)} 章到 output/{novel_name}/")

        elif choice == "4":
            _list_chapters(novel_name)

        elif choice == "5":
            _change_style(novel_name)

        elif choice == "6":
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
    genre = input("小说类型（如：玄幻修仙）：").strip()
    keywords = input("关键词（如：废材逆袭、家族复仇）：").strip()
    print("请输入角色名字，用逗号分隔（如：陆辰，苏婉，陆天行）")
    names_input = input("角色名单：").strip()
    character_names = [
        n.strip() for n in names_input.replace(",", "，").split("，")
        if n.strip()
    ]
    from core.writer import select_style_interactive
    style_key = select_style_interactive()
    return novel_name, genre, keywords, character_names, style_key


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
        novel_name, genre, keywords, character_names, style_key = setup_novel()

        style_path = Path("data") / novel_name / "style.txt"
        style_path.parent.mkdir(parents=True, exist_ok=True)
        style_path.write_text(style_key, encoding="utf-8")

        print(f"\n开始策划《{novel_name}》...\n")
        run_planner(novel_name, genre, keywords, character_names)
        print("\n策划完成！开始写章节。")
        chapters_menu(novel_name)

    elif choice == "2":
        data_dir = Path("data")
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
