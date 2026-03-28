import sqlite3
import json
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_connection, init_database


class MemoryManager:
    def __init__(self, novel_name: str):
        self.novel_name = novel_name
        self.data_dir = Path("data") / novel_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        init_database(novel_name)

    def save_world_settings(self, content: str):
        conn = get_connection(self.novel_name)
        conn.execute("""
            INSERT OR REPLACE INTO world_settings (id, content, updated_at)
            VALUES (1, ?, ?)
        """, (content, datetime.now()))
        conn.commit()
        conn.close()
        self._write_md("settings.md", f"# 世界观设定\n\n{content}")

    def load_world_settings(self) -> str:
        conn = get_connection(self.novel_name)
        row = conn.execute(
            "SELECT content FROM world_settings WHERE id=1"
        ).fetchone()
        conn.close()
        return row["content"] if row else ""

    def save_character(self, name: str, data: dict):
        conn = get_connection(self.novel_name)
        conn.execute("""
            INSERT OR REPLACE INTO characters
            (name, role, appearance, personality, secret, weakness,
             current_location, current_status, relationships, updated_chapter)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            data.get("role", ""),
            data.get("appearance", ""),
            data.get("personality", ""),
            data.get("secret", ""),
            data.get("weakness", ""),
            data.get("current_location", ""),
            data.get("current_status", ""),
            json.dumps(data.get("relationships", {}), ensure_ascii=False),
            data.get("updated_chapter", 0),
        ))
        conn.commit()
        conn.close()
        self._refresh_characters_md()

    def load_characters(self) -> list:
        conn = get_connection(self.novel_name)
        rows = conn.execute("SELECT * FROM characters").fetchall()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            d["relationships"] = json.loads(d["relationships"] or "{}")
            result.append(d)
        return result

    def update_character_status(self, name: str, location: str,
                                status: str, chapter_num: int):
        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE characters
            SET current_location=?, current_status=?, updated_chapter=?
            WHERE name=?
        """, (location, status, chapter_num, name))
        conn.commit()
        conn.close()
        self._refresh_characters_md()

    def _refresh_characters_md(self):
        chars = self.load_characters()
        lines = ["# 人物档案\n"]
        for c in chars:
            lines.append(f"## {c['name']}  [{c['role']}]")
            lines.append(f"- 外貌：{c['appearance']}")
            lines.append(f"- 性格：{c['personality']}")
            lines.append(f"- 隐藏秘密：{c['secret']}")
            lines.append(f"- 致命弱点：{c['weakness']}")
            lines.append(f"- 当前位置：{c['current_location']}")
            lines.append(f"- 当前状态：{c['current_status']}")
            lines.append("")
        self._write_md("characters.md", "\n".join(lines))

    def add_foreshadowing(self, fid: str, plant_chapter: int,
                          description: str, expected_redeem: str):
        conn = get_connection(self.novel_name)
        conn.execute("""
            INSERT OR IGNORE INTO foreshadowing
            (fid, plant_chapter, description, expected_redeem, status)
            VALUES (?, ?, ?, ?, 'active')
        """, (fid, plant_chapter, description, expected_redeem))
        conn.commit()
        conn.close()
        self._refresh_foreshadowing_md()

    def redeem_foreshadowing(self, fid: str, chapter_num: int):
        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE foreshadowing
            SET status='redeemed', redeemed_chapter=?
            WHERE fid=?
        """, (chapter_num, fid))
        conn.commit()
        conn.close()
        self._refresh_foreshadowing_md()

    def load_active_foreshadowing(self) -> list:
        conn = get_connection(self.novel_name)
        rows = conn.execute(
            "SELECT * FROM foreshadowing WHERE status='active'"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def _refresh_foreshadowing_md(self):
        conn = get_connection(self.novel_name)
        rows = conn.execute("SELECT * FROM foreshadowing").fetchall()
        conn.close()
        lines = ["# 伏笔追踪\n",
                 "| ID | 埋下章节 | 描述 | 预计兑现 | 状态 |",
                 "|---|---|---|---|---|"]
        for row in rows:
            status = "✓已兑现" if row["status"] == "redeemed" else "未兑现"
            lines.append(
                f"| {row['fid']} | 第{row['plant_chapter']}章 "
                f"| {row['description']} "
                f"| {row['expected_redeem']} | {status} |"
            )
        self._write_md("foreshadowing.md", "\n".join(lines))

    def add_summary(self, chapter_num: int, summary: str):
        conn = get_connection(self.novel_name)
        conn.execute("""
            INSERT INTO summaries (chapter_num, summary)
            VALUES (?, ?)
        """, (chapter_num, summary))
        conn.commit()
        conn.close()
        self._refresh_summaries_md()

    def load_recent_summaries(self, count: int = 5) -> list:
        conn = get_connection(self.novel_name)
        rows = conn.execute("""
            SELECT chapter_num, summary FROM summaries
            WHERE is_compressed=0
            ORDER BY chapter_num DESC LIMIT ?
        """, (count,)).fetchall()
        conn.close()
        return [dict(row) for row in reversed(rows)]

    def _refresh_summaries_md(self):
        recent = self.load_recent_summaries(10)
        lines = ["# 近期章节摘要\n"]
        for s in recent:
            lines.append(f"## 第{s['chapter_num']}章摘要")
            lines.append(s["summary"])
            lines.append("")
        self._write_md("recent_summaries.md", "\n".join(lines))

    def save_chapter(self, chapter_num: int, title: str,
                     content: str, status: str = "draft"):
        conn = get_connection(self.novel_name)
        conn.execute("""
            INSERT OR REPLACE INTO chapters
            (chapter_num, title, content, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (chapter_num, title, content, status, datetime.now()))
        conn.commit()
        conn.close()

    def update_chapter_status(self, chapter_num: int, status: str):
        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE chapters SET status=?, updated_at=?
            WHERE chapter_num=?
        """, (status, datetime.now(), chapter_num))
        conn.commit()
        conn.close()

    def load_chapter(self, chapter_num: int) -> dict:
        conn = get_connection(self.novel_name)
        row = conn.execute(
            "SELECT * FROM chapters WHERE chapter_num=?",
            (chapter_num,)
        ).fetchone()
        conn.close()
        return dict(row) if row else {}

    def load_context(self, chapter_num: int) -> dict:
        return {
            "world_settings": self.load_world_settings(),
            "characters": self.load_characters(),
            "active_foreshadowing": self.load_active_foreshadowing(),
            "recent_summaries": self.load_recent_summaries(5),
            "chapter_num": chapter_num,
        }

    def _write_md(self, filename: str, content: str):
        path = self.data_dir / filename
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    mm = MemoryManager("测试小说")
    mm.save_world_settings("测试世界观")
    mm.save_character("测试角色", {
        "role": "主角", "appearance": "英俊", "personality": "冷静",
        "secret": "身世之谜", "weakness": "情感", "current_location": "城市",
        "current_status": "活跃", "relationships": {}
    })
    mm.add_foreshadowing("F001", 1, "测试伏笔", "第10章")
    mm.add_summary(1, "测试摘要内容")
    ctx = mm.load_context(2)
    print(f"[OK] 记忆模块正常，上下文字段：{list(ctx.keys())}")
    import shutil
    shutil.rmtree("data/测试小说", ignore_errors=True)
