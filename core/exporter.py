import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from pathlib import Path
from core.memory_manager import MemoryManager
from core.db import get_connection
from core.config_loader import get_output_dir

SENSITIVE_WORDS_PATH = Path("sensitive_words.txt")

_sensitive_words_cache = None
_sensitive_words_cache_time = 0
_sensitive_words_pattern = None


def load_sensitive_words(cache_seconds: int = 300) -> list:
    global _sensitive_words_cache, _sensitive_words_cache_time, _sensitive_words_pattern

    current_time = time.time()
    if (_sensitive_words_cache is not None and
            current_time - _sensitive_words_cache_time < cache_seconds):
        return _sensitive_words_cache

    if not SENSITIVE_WORDS_PATH.exists():
        _sensitive_words_cache = []
        _sensitive_words_cache_time = current_time
        _sensitive_words_pattern = None
        return []

    words = []
    for line in SENSITIVE_WORDS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words.append(line)

    _sensitive_words_cache = words
    _sensitive_words_cache_time = current_time

    if len(words) > 100:
        _sensitive_words_pattern = re.compile(
            '|'.join(re.escape(w) for w in words)
        )
    else:
        _sensitive_words_pattern = None

    return words


def clean_for_export(text: str) -> str:
    text = re.sub(r'【[^】]*】', '', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'^\s*-{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)

    words = load_sensitive_words()
    if not words or not text:
        return text.strip()

    global _sensitive_words_pattern
    if _sensitive_words_pattern is not None:
        text = _sensitive_words_pattern.sub(
            lambda m: '*' * len(m.group()), text
        )
    else:
        for word in words:
            text = text.replace(word, '*' * len(word))

    return text.strip()


def export_chapter(novel_name: str, chapter_num: int) -> str:
    mm = MemoryManager(novel_name)
    chapter = mm.load_chapter(chapter_num)

    if not chapter or not chapter.get("content"):
        print(f"  [警告] 第{chapter_num}章内容为空，跳过导出")
        return ""

    content = clean_for_export(chapter["content"])

    out_dir = get_output_dir(novel_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"第{str(chapter_num).zfill(3)}章.txt"
    out_path = out_dir / filename
    out_path.write_text(content, encoding="utf-8")

    print(f"  [OK] 已导出：{out_path}")
    print(f"       字数：{len(content)} 字")
    return str(out_path)


def export_all(novel_name: str) -> list:
    from core.utils import with_db_connection
    with with_db_connection(novel_name) as conn:
        rows = conn.execute(
            "SELECT chapter_num FROM chapters "
            "WHERE status IN ('已审核', '强制通过') "
            "ORDER BY chapter_num"
        ).fetchall()

    results = []
    for row in rows:
        path = export_chapter(novel_name, row["chapter_num"])
        if path:
            results.append(path)
    return results