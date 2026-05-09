import json
import re

from core.db import ensure_database
from core.utils import with_db_connection, DatabaseTransaction
from core.config_loader import get_data_dir


# ==================== 显示宽度工具 ====================

def _cjk_width(ch: str) -> int:
    cp = ord(ch)
    return 2 if (0x4E00 <= cp <= 0x9FFF or 0xFF01 <= cp <= 0xFF60
                 or 0xFE30 <= cp <= 0xFE4F or 0x3000 <= cp <= 0x303F
                 or 0x20000 <= cp <= 0x2FA1F) else 1


def _str_width(s: str) -> int:
    return sum(_cjk_width(c) for c in s)


def _truncate(s: str, max_w: int) -> str:
    w = 0
    for i, c in enumerate(s):
        cw = _cjk_width(c)
        if w + cw > max_w:
            return s[:i] + "…"
        w += cw
    return s


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _str_width(s))


# ==================== CRUD 操作 ====================

def add_outline_foreshadow(novel_name: str, description: str,
                           category: str, plant_chapter: int,
                           resolve_chapter: int, importance: int = 3,
                           notes: str = "") -> str:
    ensure_database(novel_name)
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            row = conn.execute(
                "SELECT fid FROM outline_foreshadowing "
                "WHERE novel_name=? ORDER BY fid DESC LIMIT 1",
                (novel_name,)
            ).fetchone()
            if row:
                try:
                    num = int(row["fid"].replace("OF", ""))
                except ValueError:
                    num = 0
            else:
                num = 0
            fid = f"OF{num + 1:03d}"
            conn.execute("""
                INSERT INTO outline_foreshadowing
                (novel_name, fid, description, category,
                 plant_chapter, resolve_chapter, importance, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (novel_name, fid, description, category,
                  plant_chapter, resolve_chapter, importance, notes))
    return fid


def list_outline_foreshadow(novel_name: str, status: str = None) -> list:
    ensure_database(novel_name)
    with with_db_connection(novel_name) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM outline_foreshadowing "
                "WHERE novel_name=? AND status=? "
                "ORDER BY plant_chapter ASC",
                (novel_name, status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM outline_foreshadowing "
                "WHERE novel_name=? ORDER BY plant_chapter ASC",
                (novel_name,)
            ).fetchall()
    return [dict(r) for r in rows]


def update_outline_foreshadow(novel_name: str, fid: str, **kwargs) -> bool:
    allowed = {
        "description", "category", "plant_chapter",
        "resolve_chapter", "status", "importance", "notes",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return False
    ensure_database(novel_name)
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [novel_name, fid]
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            cur = conn.execute(
                f"UPDATE outline_foreshadowing SET {set_clause} "
                f"WHERE novel_name=? AND fid=?",
                values,
            )
            return cur.rowcount > 0


def delete_outline_foreshadow(novel_name: str, fid: str) -> bool:
    ensure_database(novel_name)
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            cur = conn.execute(
                "DELETE FROM outline_foreshadowing "
                "WHERE novel_name=? AND fid=?",
                (novel_name, fid),
            )
            return cur.rowcount > 0


def get_chapter_outline_tasks(novel_name: str, chapter_num: int) -> dict:
    """
    返回当前章节的大纲伏笔任务，供任务卡生成时注入。
    失败时返回空 dict，不抛异常。
    """
    empty = {"to_plant": [], "to_resolve": []}
    try:
        ensure_database(novel_name)
        with with_db_connection(novel_name) as conn:
            to_plant = conn.execute(
                "SELECT fid, description, importance "
                "FROM outline_foreshadowing "
                "WHERE novel_name=? AND plant_chapter=? AND status='planned'",
                (novel_name, chapter_num),
            ).fetchall()
            to_resolve = conn.execute(
                "SELECT fid, description, importance "
                "FROM outline_foreshadowing "
                "WHERE novel_name=? AND resolve_chapter=? "
                "AND status IN ('planned', 'planted')",
                (novel_name, chapter_num),
            ).fetchall()
        return {
            "to_plant": [dict(r) for r in to_plant],
            "to_resolve": [dict(r) for r in to_resolve],
        }
    except Exception:
        return empty


def mark_outline_foreshadow_status(novel_name: str, fid: str,
                                   status: str) -> bool:
    """
    标记大纲伏笔状态（planned/planted/resolved），审稿通过时自动调用。
    失败时返回 False，不抛异常。
    """
    if status not in ("planned", "planted", "resolved"):
        return False
    try:
        return update_outline_foreshadow(novel_name, fid, status=status)
    except Exception:
        return False


# ==================== AI 辅助生成 ====================

def ai_suggest_outline_foreshadow(novel_name: str):
    """AI 根据总大纲生成伏笔建议列表，供用户选择录入。"""
    # 1. 读取大纲
    outline_path = get_data_dir(novel_name) / "master_outline.md"
    if not outline_path.exists():
        print("  [错误] 未找到总大纲文件（master_outline.md），请先创建大纲")
        return
    outline_text = outline_path.read_text(encoding="utf-8").strip()
    if not outline_text:
        print("  [错误] 总大纲文件为空")
        return
    outline_text = outline_text[:4000]

    # 2. 读取目标章数
    target_path = get_data_dir(novel_name) / "target_chapters.txt"
    if target_path.exists():
        try:
            target_chapters = int(target_path.read_text(encoding="utf-8").strip())
        except ValueError:
            target_chapters = 0
    else:
        target_chapters = 0
    target_str = str(target_chapters) if target_chapters > 0 else "未知"

    # 3. 查询已完成的最大章节号
    ensure_database(novel_name)
    max_chapter = 0
    try:
        with with_db_connection(novel_name) as conn:
            row = conn.execute(
                "SELECT MAX(chapter_num) AS mc FROM chapters "
                "WHERE status='approved'",
            ).fetchone()
            if row and row["mc"] is not None:
                max_chapter = row["mc"]
    except Exception:
        pass

    # 4. 调用作者模型
    system_prompt = "你是专业的网络小说策划师，擅长设计伏笔结构。"
    user_message = (
        f"=== 小说总大纲 ===\n{outline_text}\n\n"
        f"当前已完成章节：第{max_chapter}章\n"
        f"目标总章节：{target_str}章\n\n"
        "请根据大纲，设计20-30个关键伏笔，要求：\n"
        "1. 每个伏笔必须有明确的埋入章节和兑现章节\n"
        f"2. 埋入章节必须大于第{max_chapter}章（已完成章节不可再埋）\n"
        "3. 分类：情节伏笔/人物伏笔/世界观/宏观悬念\n"
        "4. 重要度1-5分\n"
        "5. 严格输出JSON数组，不要任何其他内容：\n"
        '[\n'
        '  {\n'
        '    "description": "伏笔描述",\n'
        '    "category": "情节伏笔",\n'
        '    "plant_chapter": 60,\n'
        '    "resolve_chapter": 80,\n'
        '    "importance": 4\n'
        '  }\n'
        ']'
    )

    print("  正在调用 AI 生成伏笔建议，请稍候...")
    try:
        from core.api_client import call_author_api
        raw = call_author_api(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=4096,
            temperature=0.7,
        )
    except Exception as e:
        print(f"  [错误] API 调用失败：{e}")
        return

    # 5. 清除思考块 + 解析 JSON
    raw_clean = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    # 尝试提取 JSON 数组
    json_match = re.search(r'\[.*\]', raw_clean, re.DOTALL)
    if not json_match:
        print("  [错误] AI 返回中未找到 JSON 数组")
        print(f"  原始返回：\n{raw_clean[:500]}")
        print("  请手动在菜单中录入伏笔（选项2）")
        return

    try:
        suggestions = json.loads(json_match.group())
    except json.JSONDecodeError:
        print("  [错误] JSON 解析失败")
        print(f"  原始返回：\n{raw_clean[:500]}")
        print("  请手动在菜单中录入伏笔（选项2）")
        return

    if not isinstance(suggestions, list) or len(suggestions) == 0:
        print("  [提示] AI 未返回有效建议")
        return

    # 6. 打印候选列表
    print(f"\n  AI 共生成 {len(suggestions)} 条伏笔建议：\n")
    header = f"  {'序号':<5}{'描述':<28}{'分类':<10}{'埋入章':<8}{'兑现章':<8}{'重要度'}"
    print(header)
    print("  " + "─" * 70)
    for i, s in enumerate(suggestions, 1):
        desc = (s.get("description", "") or "")[:26]
        cat = (s.get("category", "") or "")[:8]
        plant = s.get("plant_chapter", "?")
        resolve = s.get("resolve_chapter", "?")
        imp = s.get("importance", "?")
        stars = "★" * int(imp) if isinstance(imp, int) else str(imp)
        print(f"  {i:<5}{desc:<28}{cat:<10}第{plant:<6}第{resolve:<6}{stars}")

    # 7. 用户选择录入方式
    print("\n  录入方式：")
    print("    a. 全部录入")
    print("    b. 逐条确认（y录入/n跳过/e编辑后录入）")
    print("    c. 取消")
    mode = input("  请选择：").strip().lower()

    if mode == "a":
        count = 0
        for s in suggestions:
            desc = (s.get("description") or "").strip()
            if not desc:
                continue
            add_outline_foreshadow(
                novel_name,
                description=desc,
                category=s.get("category", "情节伏笔"),
                plant_chapter=int(s.get("plant_chapter", 0)),
                resolve_chapter=int(s.get("resolve_chapter", 0)),
                importance=int(s.get("importance", 3)),
            )
            count += 1
        print(f"\n  [OK] 已录入 {count} 条伏笔")

    elif mode == "b":
        count = 0
        for i, s in enumerate(suggestions, 1):
            desc = (s.get("description") or "").strip()
            cat = s.get("category", "情节伏笔")
            plant = s.get("plant_chapter", 0)
            resolve = s.get("resolve_chapter", 0)
            imp = s.get("importance", 3)
            print(f"\n  [{i}] {desc}  ({cat}, 埋入:{plant}, 兑现:{resolve}, 重要度:{imp})")
            sub = input("  y录入/n跳过/e编辑：").strip().lower()
            if sub == "y":
                add_outline_foreshadow(
                    novel_name, desc, cat, int(plant), int(resolve), int(imp),
                )
                count += 1
                print("    [OK] 已录入")
            elif sub == "e":
                new_desc = input(f"    描述 [{desc}]: ").strip() or desc
                new_cat = input(f"    分类 [{cat}]: ").strip() or cat
                new_plant = input(f"    埋入章 [{plant}]: ").strip()
                new_resolve = input(f"    兑现章 [{resolve}]: ").strip()
                new_imp = input(f"    重要度 [{imp}]: ").strip()
                add_outline_foreshadow(
                    novel_name,
                    description=new_desc,
                    category=new_cat,
                    plant_chapter=int(new_plant) if new_plant else int(plant),
                    resolve_chapter=int(new_resolve) if new_resolve else int(resolve),
                    importance=int(new_imp) if new_imp else int(imp),
                )
                count += 1
                print("    [OK] 已录入（编辑后）")
            else:
                print("    [跳过]")
        print(f"\n  [OK] 逐条确认完毕，共录入 {count} 条")
    else:
        print("  已取消")


# ==================== 交互菜单 ====================

_STATUS_LIST = ["planned", "planted", "resolved"]
_CATEGORY_LIST = ["情节伏笔", "人物伏笔", "世界观", "宏观悬念"]


def manage_outline_foreshadow(novel_name: str):
    ensure_database(novel_name)

    while True:
        print("\n" + "=" * 50)
        print("  大纲伏笔管理")
        print("=" * 50)
        print("1. 查看全部伏笔规划")
        print("2. 新增伏笔")
        print("3. 编辑伏笔")
        print("4. 删除伏笔")
        print("5. AI辅助生成大纲伏笔建议")
        print("0. 返回主菜单")

        choice = input("\n请选择：").strip()

        if choice == "1":
            _list_items(novel_name)
        elif choice == "2":
            _add_item(novel_name)
        elif choice == "3":
            _edit_item(novel_name)
        elif choice == "4":
            _delete_item(novel_name)
        elif choice == "5":
            ai_suggest_outline_foreshadow(novel_name)
        elif choice == "0":
            break


def _list_items(novel_name: str):
    items = list_outline_foreshadow(novel_name)
    if not items:
        print("\n暂无伏笔规划")
        return

    hdr = (f"{'FID':<7}{'描述':<22}{'分类':<10}"
           f"{'埋入':<8}{'兑现':<8}{'状态':<10}{'重要度'}")
    sep = "─" * 75
    print(f"\n{hdr}")
    print(sep)
    for it in items:
        desc = _truncate(it.get("description", "") or "", 20)
        plant = f"第{it['plant_chapter']}章" if it.get("plant_chapter") else "-"
        resolve = f"第{it['resolve_chapter']}章" if it.get("resolve_chapter") else "-"
        stars = "★" * (it.get("importance") or 3)
        print(f"{it['fid']:<7}"
              f"{_pad(desc, 22)}"
              f"{_pad(it.get('category', '') or '', 10)}"
              f"{_pad(plant, 8)}"
              f"{_pad(resolve, 8)}"
              f"{_pad(it.get('status', '') or '', 10)}"
              f"{stars}")
    print(f"\n共 {len(items)} 条伏笔规划")


def _add_item(novel_name: str):
    print("\n--- 新增伏笔 ---")
    desc = input("伏笔描述：").strip()
    if not desc:
        print("  [提示] 描述为空，已取消")
        return

    print(f"分类（1-情节伏笔 2-人物伏笔 3-世界观 4-宏观悬念，默认1）：")
    cat_input = input("  > ").strip()
    cat_idx = int(cat_input) - 1 if cat_input.isdigit() and 1 <= int(cat_input) <= 4 else 0
    category = _CATEGORY_LIST[cat_idx]

    plant = _input_chapter("计划埋入章节")
    resolve = _input_chapter("计划兑现章节")
    if plant is None or resolve is None:
        return

    imp_input = input("重要度（1-5，默认3）：").strip()
    importance = int(imp_input) if imp_input.isdigit() and 1 <= int(imp_input) <= 5 else 3

    notes = input("备注（可为空）：").strip()

    fid = add_outline_foreshadow(
        novel_name, desc, category, plant, resolve, importance, notes,
    )
    print(f"\n[OK] 已新增伏笔 {fid}：{desc}")


def _edit_item(novel_name: str):
    items = list_outline_foreshadow(novel_name)
    if not items:
        print("\n暂无伏笔可编辑")
        return

    _list_items(novel_name)
    fid = input("\n请输入要编辑的 FID（0取消）：").strip().upper()
    if fid == "0" or not fid:
        return

    target = next((it for it in items if it["fid"] == fid), None)
    if not target:
        print(f"  未找到 fid: {fid}")
        return

    print(f"\n当前信息 [{fid}]：")
    print(f"  描述：{target['description']}")
    print(f"  分类：{target['category']}")
    plant_str = f"第{target['plant_chapter']}章" if target.get("plant_chapter") else "-"
    resolve_str = f"第{target['resolve_chapter']}章" if target.get("resolve_chapter") else "-"
    print(f"  埋入：{plant_str}")
    print(f"  兑现：{resolve_str}")
    print(f"  状态：{target['status']}")
    print(f"  重要度：{target['importance']}")
    print(f"  备注：{target.get('notes') or ''}")
    print("\n（直接回车跳过不修改的字段）")

    new_desc = input(f"描述 [{target['description']}]: ").strip()
    print(f"分类（1-情节伏笔 2-人物伏笔 3-世界观 4-宏观悬念）[{target['category']}]: ")
    new_cat_input = input("  > ").strip()
    new_plant = input(f"埋入章节 [{target['plant_chapter']}]: ").strip()
    new_resolve = input(f"兑现章节 [{target['resolve_chapter']}]: ").strip()
    print(f"状态（1-planned 2-planted 3-resolved）[{target['status']}]: ")
    new_status_input = input("  > ").strip()
    new_imp = input(f"重要度(1-5) [{target['importance']}]: ").strip()
    new_notes = input(f"备注 [{target.get('notes') or ''}]: ").strip()

    updates = {}
    if new_desc:
        updates["description"] = new_desc
    if new_cat_input:
        idx = int(new_cat_input) - 1 if new_cat_input.isdigit() and 1 <= int(new_cat_input) <= 4 else -1
        if 0 <= idx < len(_CATEGORY_LIST):
            updates["category"] = _CATEGORY_LIST[idx]
    if new_plant:
        try:
            updates["plant_chapter"] = int(new_plant)
        except ValueError:
            print("  [警告] 埋入章节无效，已跳过")
    if new_resolve:
        try:
            updates["resolve_chapter"] = int(new_resolve)
        except ValueError:
            print("  [警告] 兑现章节无效，已跳过")
    if new_status_input:
        s_idx = int(new_status_input) - 1 if new_status_input.isdigit() and 1 <= int(new_status_input) <= 3 else -1
        if 0 <= s_idx < len(_STATUS_LIST):
            updates["status"] = _STATUS_LIST[s_idx]
    if new_imp:
        try:
            v = int(new_imp)
            if 1 <= v <= 5:
                updates["importance"] = v
        except ValueError:
            print("  [警告] 重要度无效，已跳过")
    if new_notes:
        updates["notes"] = new_notes

    if not updates:
        print("  [提示] 未作任何修改")
        return

    ok = update_outline_foreshadow(novel_name, fid, **updates)
    if ok:
        print(f"\n[OK] {fid} 已更新：")
        for k, v in updates.items():
            print(f"  {k} = {v}")
    else:
        print(f"  [错误] 更新失败")


def _delete_item(novel_name: str):
    items = list_outline_foreshadow(novel_name)
    if not items:
        print("\n暂无伏笔可删除")
        return

    _list_items(novel_name)
    fid = input("\n请输入要删除的 FID（0取消）：").strip().upper()
    if fid == "0" or not fid:
        return

    target = next((it for it in items if it["fid"] == fid), None)
    if not target:
        print(f"  未找到 fid: {fid}")
        return

    print(f"\n即将删除：{fid} - {target['description']}")
    confirm = input("确认删除？（输入 yes 确认）：").strip().lower()
    if confirm != "yes":
        print("  已取消删除")
        return

    ok = delete_outline_foreshadow(novel_name, fid)
    if ok:
        print(f"[OK] 已删除 {fid}")
    else:
        print(f"  [错误] 删除失败")


# ==================== 工具函数 ====================

def _input_chapter(label: str):
    raw = input(f"{label}（章节数）：").strip()
    if not raw:
        print("  [提示] 章节号为空，已取消")
        return None
    try:
        return int(raw)
    except ValueError:
        print("  [错误] 无效章节数，已取消")
        return None
