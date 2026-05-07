#!/usr/bin/env python3
"""
一次性清理脚本：批量归档旧伏笔中的「场景描写误记录」。

用法：python tools/cleanup_foreshadow.py <novel_name>
"""

import sqlite3
import sys
from pathlib import Path

# ==================== 配置 ====================

# 情节动词关键词——description 不含任何一条则视为疑似误记录
PLOT_KEYWORDS = [
    "发现", "知道", "得知", "揭露", "查明", "追查", "逃脱", "对抗",
    "背叛", "牺牲", "决定", "选择", "暗示", "伏笔", "秘密", "真相",
    "阴谋", "约定", "誓言",
]

# 描述长度阈值（小于此值视为疑似误记录）
MIN_DESC_LEN = 15


# ==================== 工具函数 ====================

def get_db_path(novel_name: str) -> Path:
    """定位数据库路径：data/{novel_name}/novel.db"""
    # 从脚本位置向上推两级到项目根目录
    project_root = Path(__file__).resolve().parent.parent
    db_path = project_root / "data" / novel_name / "novel.db"
    if not db_path.exists():
        print(f"[错误] 数据库不存在: {db_path}")
        sys.exit(1)
    return db_path


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def has_plot_keyword(description: str) -> bool:
    """description 是否包含任何情节动词关键词"""
    return any(kw in description for kw in PLOT_KEYWORDS)


def classify_reasons(desc: str, plant_chapter) -> list:
    """返回该伏笔命中的疑似误记录原因列表"""
    reasons = []
    if len(desc) < MIN_DESC_LEN:
        reasons.append(f"描述过短({len(desc)}字)")
    if not has_plot_keyword(desc):
        reasons.append("无情节动词")
    if not plant_chapter:
        reasons.append("无埋入章节")
    return reasons


# ==================== 主流程 ====================

def main():
    if len(sys.argv) < 2:
        print("用法: python tools/cleanup_foreshadow.py <novel_name>")
        sys.exit(1)

    novel_name = sys.argv[1]
    db_path = get_db_path(novel_name)
    conn = connect(db_path)

    # 1. 读取所有活跃伏笔
    rows = conn.execute(
        "SELECT fid, description, plant_chapter, status "
        "FROM foreshadowing WHERE status='active' "
        "ORDER BY plant_chapter ASC"
    ).fetchall()

    total_active = len(rows)
    if total_active == 0:
        print("[提示] 没有活跃伏笔，无需清理。")
        conn.close()
        return

    # 2. 筛选疑似误记录
    candidates = []
    for row in rows:
        desc = row["description"] or ""
        reasons = classify_reasons(desc, row["plant_chapter"])
        if reasons:
            candidates.append({
                "fid": row["fid"],
                "description": desc,
                "plant_chapter": row["plant_chapter"],
                "reasons": reasons,
            })

    if not candidates:
        print(f"[提示] 共 {total_active} 条活跃伏笔，未发现疑似误记录。")
        conn.close()
        return

    # 3. 打印候选列表
    print(f"\n{'=' * 70}")
    print(f"  疑似场景描写误记录：{len(candidates)} / {total_active} 条")
    print(f"{'=' * 70}")
    print(f"{'序号':<5}{'FID':<8}{'描述':<30}{'埋入章节':<10}{'命中原因'}")
    print("-" * 70)
    for i, c in enumerate(candidates, 1):
        desc_display = c["description"][:26] + "..." if len(c["description"]) > 26 else c["description"]
        plant_display = f"第{c['plant_chapter']}章" if c["plant_chapter"] else "-"
        reason_display = " + ".join(c["reasons"])
        print(f"{i:<5}{c['fid']:<8}{desc_display:<30}{plant_display:<10}{reason_display}")
    print("-" * 70)

    # 4. 交互菜单
    print(f"\n请选择操作：")
    print(f"  a. 全部归档（批量标记 status='archived'）")
    print(f"  b. 逐条确认")
    print(f"  c. 跳过不处理")

    choice = input("\n请输入 (a/b/c)：").strip().lower()

    archived_count = 0

    if choice == "a":
        # 批量归档
        for c in candidates:
            conn.execute(
                "UPDATE foreshadowing SET status='archived' WHERE fid=?",
                (c["fid"],)
            )
        conn.commit()
        archived_count = len(candidates)
        print(f"\n[OK] 已批量归档 {archived_count} 条。")

    elif choice == "b":
        # 逐条确认
        for i, c in enumerate(candidates, 1):
            desc_display = c["description"][:40] + "..." if len(c["description"]) > 40 else c["description"]
            reason_display = " + ".join(c["reasons"])
            print(f"\n[{i}/{len(candidates)}] {c['fid']}: {desc_display}")
            print(f"  命中原因: {reason_display}")
            sub = input("  归档？(y=归档 / n=跳过 / q=退出)：").strip().lower()
            if sub == "q":
                print("  已退出逐条确认。")
                break
            elif sub == "y":
                conn.execute(
                    "UPDATE foreshadowing SET status='archived' WHERE fid=?",
                    (c["fid"],)
                )
                conn.commit()
                archived_count += 1
                print(f"  [OK] 已归档 {c['fid']}")
            else:
                print(f"  [跳过] {c['fid']}")

    else:
        print("\n[提示] 已跳过，未做任何修改。")
        conn.close()
        return

    # 5. 统计
    remaining = conn.execute(
        "SELECT COUNT(*) FROM foreshadowing WHERE status='active'"
    ).fetchone()[0]

    conn.close()

    print(f"\n{'=' * 40}")
    print(f"  归档完成")
    print(f"{'=' * 40}")
    print(f"  本次归档: {archived_count} 条")
    print(f"  剩余活跃: {remaining} 条")
    print(f"{'=' * 40}")


if __name__ == "__main__":
    main()
