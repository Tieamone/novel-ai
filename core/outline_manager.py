from core.db import ensure_database
from core.utils import with_db_connection, DatabaseTransaction


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
