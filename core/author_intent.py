"""共用的创作意图和当前焦点模板。"""

from pathlib import Path

TEMPLATES = {
    "author_intent.md": (
        "# 创作意图\n\n"
        "## 这本书想成为什么\n（基调、主题、核心吸引力）\n\n"
        "## 不想变成什么样\n（避免的风格、雷区）\n"
    ),
    "current_focus.md": (
        "# 当前焦点\n\n"
        "## 最近 1-3 章的重点\n（当前弧线的核心冲突、需要推进的线索）\n"
    ),
}

TITLES = {
    "author_intent.md": "创作意图（长期方向）",
    "current_focus.md": "当前焦点（近期重点）",
}


def read_filled_intent(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        return ""
    return "" if content == TEMPLATES[path.name].strip() else content


def build_intent_block(data_dir) -> str:
    if not data_dir:
        return ""
    parts = []
    for name, title in TITLES.items():
        content = read_filled_intent(Path(data_dir) / name)
        if content:
            parts.append(f"\n=== {title}===\n{content}\n")
    return "".join(parts)
