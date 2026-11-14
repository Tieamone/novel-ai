"""旧书只读体检与选定伏笔归档。"""

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

from core.author_intent import TEMPLATES, read_filled_intent
from core.config_loader import get_data_dir, get_output_dir
from core.exporter import EXPORTABLE_STATUSES, get_safe_output_dir
from core.interaction import ask, say
from core.utils import with_db_connection, DatabaseTransaction


def inspect_book(novel):
    data_dir = get_data_dir(novel)
    with with_db_connection(novel, readonly=True) as conn:
        def rows(table, sql):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                return []
            return [dict(row) for row in conn.execute(sql)]

        chapters = rows("chapters", "SELECT chapter_num, status, LENGTH(TRIM(content)) AS size FROM chapters")
        summary_columns = {row["name"] for row in conn.execute("PRAGMA table_info(summaries)")}
        selection = "is_compressed" if "is_compressed" in summary_columns else "0 AS is_compressed"
        summaries = rows("summaries", f"SELECT chapter_num, {selection} FROM summaries")
        outline = rows("outline_foreshadowing", "SELECT fid, plant_chapter, resolve_chapter, status FROM outline_foreshadowing")
        legacy = rows("foreshadowing", "SELECT fid, plant_chapter, description, expected_redeem, status FROM foreshadowing WHERE status='active' ORDER BY plant_chapter, fid")
        tasks = {row["chapter_num"]: row["status"] for row in rows("chapter_tasks", "SELECT chapter_num, status FROM chapter_tasks")}

    coverage = max((row["chapter_num"] for row in summaries if row["is_compressed"]), default=0)
    individual = {row["chapter_num"] for row in summaries if not row["is_compressed"]}
    approved = {row["chapter_num"] for row in chapters if row["status"] in EXPORTABLE_STATUSES and row["size"]}
    output = get_safe_output_dir(novel)
    exports = {int(path.stem[1:-1]) for path in output.glob("第*章.txt") if path.stem[1:-1].isdigit()}
    suspicious = [
        item for item in outline
        if (item["status"] in ("planted", "resolved") and item["plant_chapter"] not in approved)
        or (item["status"] == "resolved" and item["resolve_chapter"] not in approved)
    ]
    return {
        "coverage": coverage, "missing_summaries": sorted(n for n in approved if n > coverage and n not in individual),
        "missing_exports": sorted(approved - exports), "stale_exports": sorted(exports - approved),
        "incomplete_tasks": sorted(n for n in approved if n in tasks and tasks[n] not in ("已完成", "completed")),
        "outline_count": len(outline), "outline_needs_review": [item["fid"] for item in suspicious],
        "legacy": legacy, "empty_intent": [name for name in TEMPLATES if not read_filled_intent(data_dir / name)],
    }


def show_book_health(novel, *, interactive=False):
    report = inspect_book(novel)
    say(f"阶段摘要覆盖至第 {report['coverage']} 章。旧活跃伏笔 {len(report['legacy'])} 条，大纲伏笔 {report['outline_count']} 条。")
    for field, label in {
        "missing_summaries": "缺少逐章摘要",
        "missing_exports": "缺少导出",
        "stale_exports": "导出但无对应已审正文",
        "incomplete_tasks": "已审但任务未完成",
        "outline_needs_review": "大纲伏笔章号需人工复核",
        "empty_intent": "缺失或未填的创作资料",
    }.items():
        say(f"{label}：{', '.join(map(str, report[field])) or '无'}")
    for item in report["legacy"]:
        say(f"{item['fid']} · 第{item['plant_chapter']}章 · {item['description']} · 计划 {item['expected_redeem'] or '待定'}")
    if not interactive or not report["legacy"]:
        return "只读体检完成"
    selected = ask("输入要归档的伏笔 ID，以逗号或空格分隔，留空返回。").replace("，", ",").replace(",", " ").split()
    if not selected:
        return "只读体检完成"
    candidates = {item["fid"]: item for item in report["legacy"]}
    if any(fid not in candidates for fid in selected):
        raise ValueError("只能选择本次体检中的活跃旧伏笔")
    chosen = [candidates[fid] for fid in dict.fromkeys(selected)]
    if len(chosen) > 20:
        raise ValueError("为便于核对，每次最多归档 20 条")
    preview = "\n".join(f"{item['fid']} · {item['description']}" for item in chosen)
    say(preview)
    if ask(f"确认将以上 {len(chosen)} 条活跃伏笔归档？系统将先备份小说数据库。",
           options=(("y", "备份并归档"), ("n", "取消")), context=preview).strip().lower() != "y":
        return "已取消归档"
    backup = archive_selected(novel, chosen)
    say(f"数据库备份：{backup}")
    return f"已归档 {len(chosen)} 条旧伏笔"


def archive_selected(novel, chosen):
    # 用项目的导出根目录隔离备份，库内仍保留原 ID 和描述。
    directory = get_output_dir() / ".book-backups"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{novel}-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    backup = directory / Path(filename).name
    with with_db_connection(novel, readonly=True) as source, closing(sqlite3.connect(backup)) as dest:
        source.backup(dest)
        if dest.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("旧书数据库备份校验失败")
    with with_db_connection(novel) as conn, DatabaseTransaction(conn):
        for item in chosen:
            updated = conn.execute(
                "UPDATE foreshadowing SET status='archived' WHERE fid=? AND status='active' "
                "AND description IS ? AND plant_chapter IS ? AND expected_redeem IS ?",
                (item["fid"], item["description"], item["plant_chapter"], item["expected_redeem"]),
            )
            if updated.rowcount != 1:
                raise ValueError(f"伏笔 {item['fid']} 已发生变化，请重新体检")
    return backup
