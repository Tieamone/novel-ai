import os
from tempfile import NamedTemporaryFile
import time
import re

from pathlib import Path
from core.interaction import say as print
from core.memory_manager import MemoryManager
from core.utils import with_db_connection
from core.config_loader import get_output_dir, get_project_root

SENSITIVE_WORDS_PATH = get_project_root() / "sensitive_words.txt"

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
        print("  [提示] 未找到敏感词文件，本次导出跳过敏感词替换")
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
    """
    清理 AI 生成物中的 Markdown 残留和明确的结尾说明，输出纯净正文。
    """
    # 去除 **加粗**
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    # 去除 *斜体*（不跨行）
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    # 去除 ## 标题符号
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 去除 --- 分隔线
    text = re.sub(r'^\s*-{3,}\s*$', '', text, flags=re.MULTILINE)
    # 去除 `行内代码`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 合并多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 去除章节结尾 AI 偶发的说明文字（"（本章完）"之后的模型旁白）
    text = re.sub(
        r'(?m)^[ \t]*[（(]本章完[）)][ \t]*(?:\n[ \t]*\n[ \t]*(?:作者有话说|作者说明|模型说明)[：:][^\n]*)?[ \t\n]*\Z',
        '', text,
    )

    # 敏感词替换
    # Bug修复13: 改用 □ 代替 *，中文字符宽度一致，更难猜测原词
    words = load_sensitive_words()
    if not words or not text:
        return text.strip()

    global _sensitive_words_pattern
    if _sensitive_words_pattern is not None:
        text = _sensitive_words_pattern.sub(
            lambda m: '□' * len(m.group()), text
        )
    else:
        for word in words:
            text = text.replace(word, '□' * len(word))

    return text.strip()


EXPORTABLE_STATUSES = ("已审核", "强制通过", "approved")


def get_safe_output_dir(novel_name: str) -> Path:
    # 书名在入口校验，导出目录与小说库目录使用同一名称。
    return get_output_dir(novel_name)


def export_chapter(novel_name: str, chapter_num: int) -> str:
    mm = MemoryManager(novel_name)
    chapter = mm.load_chapter(chapter_num)

    if not chapter or not chapter.get("content"):
        print(f"  [警告] 第{chapter_num}章内容为空，跳过导出")
        return ""

    if chapter["status"] not in EXPORTABLE_STATUSES:
        raise ValueError(f"第{chapter_num}章尚未审核通过，无法导出")
    content = clean_for_export(chapter["content"])
    if not content:
        raise ValueError(f"第{chapter_num}章清理后的正文为空，无法导出")

    out_dir = get_safe_output_dir(novel_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"第{str(chapter_num).zfill(3)}章.txt"
    out_path = out_dir / filename
    temp_path = None
    try:
        with NamedTemporaryFile(dir=out_dir, delete=False, mode="w", encoding="utf-8") as draft:
            temp_path = Path(draft.name)
            draft.write(content)
        os.replace(temp_path, out_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    print(f"  [OK] 已导出：{out_path}")
    print(f"       字数：{len(content)} 字")
    return str(out_path)


def export_all(novel_name: str) -> list:
    with with_db_connection(novel_name) as conn:
        rows = conn.execute(
            "SELECT chapter_num FROM chapters WHERE status IN (?, ?, ?) ORDER BY chapter_num",
            EXPORTABLE_STATUSES,
        ).fetchall()

    results = []
    for row in rows:
        path = export_chapter(novel_name, row["chapter_num"])
        if path:
            results.append(path)
    return results
