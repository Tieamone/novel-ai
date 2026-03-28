import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from pathlib import Path
from core.memory_manager import MemoryManager
from core.db import get_connection

SENSITIVE_WORDS = []


def clean_for_export(text: str) -> str:
    text = re.sub(r'【[^】]*】', '', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'^\s*-{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    for word in SENSITIVE_WORDS:
        text = text.replace(word, '*' * len(word))
    return text.strip()


def export_chapter(novel_name: str, chapter_num: int) -> str:
    mm = MemoryManager(novel_name)
    chapter = mm.load_chapter(chapter_num)

    if not chapter or not chapter.get("content"):
        print(f"  [警告] 第{chapter_num}章内容为空，跳过导出")
        return ""

    content = clean_for_export(chapter["content"])

    out_dir = Path("output") / novel_name
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"第{str(chapter_num).zfill(3)}章.txt"
    out_path = out_dir / filename
    out_path.write_text(content, encoding="utf-8")

    print(f"  [OK] 已导出：output/{novel_name}/{filename}")
    print(f"       字数：{len(content)} 字")
    return str(out_path)


def export_all(novel_name: str) -> list:
    conn = get_connection(novel_name)
    rows = conn.execute(
        "SELECT chapter_num FROM chapters "
        "WHERE status IN ('approved','force_approved') "
        "ORDER BY chapter_num"
    ).fetchall()
    conn.close()

    results = []
    for row in rows:
        path = export_chapter(novel_name, row["chapter_num"])
        if path:
            results.append(path)
    return results
