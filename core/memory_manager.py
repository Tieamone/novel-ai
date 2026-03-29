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

    # ==================== 世界观 ====================

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

    # ==================== 人物 ====================

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

    def update_character_relationship(self, name: str,
                                      other_name: str,
                                      new_relationship: str,
                                      chapter_num: int):
        """更新人物之间的关系"""
        conn = get_connection(self.novel_name)
        row = conn.execute(
            "SELECT relationships FROM characters WHERE name=?", (name,)
        ).fetchone()
        conn.close()

        if not row:
            return

        try:
            relationships = json.loads(row["relationships"] or "{}")
        except Exception:
            relationships = {}

        relationships[other_name] = new_relationship

        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE characters
            SET relationships=?, updated_chapter=?
            WHERE name=?
        """, (
            json.dumps(relationships, ensure_ascii=False),
            chapter_num,
            name,
        ))
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
            if c["relationships"]:
                lines.append(f"- 人物关系：{json.dumps(c['relationships'], ensure_ascii=False)}")
            lines.append("")
        self._write_md("characters.md", "\n".join(lines))

    # ==================== 伏笔 ====================

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

    # ==================== 摘要 ====================

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
        from core.config_loader import get as cfg
        count = cfg("novel", "recent_summary_count", count)

        conn = get_connection(self.novel_name)
        compressed = conn.execute("""
            SELECT chapter_num, summary FROM summaries
            WHERE is_compressed=1
            ORDER BY chapter_num DESC LIMIT 1
        """).fetchone()

        recent = conn.execute("""
            SELECT chapter_num, summary FROM summaries
            WHERE is_compressed=0
            ORDER BY chapter_num DESC LIMIT ?
        """, (count,)).fetchall()
        conn.close()

        result = []
        if compressed:
            result.append(dict(compressed))
        result.extend([dict(r) for r in reversed(recent)])
        return result

    def compress_old_summaries(self):
        from core.config_loader import get as cfg
        threshold = cfg("novel", "compress_after_chapters", 20)
        keep_recent = cfg("novel", "recent_summary_count", 5)

        conn = get_connection(self.novel_name)
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM summaries WHERE is_compressed=0"
        ).fetchone()["cnt"]

        if total <= threshold:
            conn.close()
            return

        to_compress = conn.execute("""
            SELECT id, chapter_num, summary FROM summaries
            WHERE is_compressed=0
            ORDER BY chapter_num ASC
            LIMIT ?
        """, (total - keep_recent,)).fetchall()
        conn.close()

        if not to_compress:
            return

        print(f"  [压缩] 正在压缩第"
              f"{to_compress[0]['chapter_num']}-"
              f"{to_compress[-1]['chapter_num']}章摘要...")

        from core.api_client import call_api
        summaries_text = "\n".join([
            f"第{r['chapter_num']}章：{r['summary']}"
            for r in to_compress
        ])

        compressed_text = call_api(
            system_prompt="你是小说编辑，将多章摘要压缩为简洁的阶段摘要。",
            user_message=(
                f"请将以下章节摘要压缩为200字以内的阶段性摘要，"
                f"保留关键情节和人物变化：\n\n{summaries_text}"
            ),
            temperature=0.3,
            max_tokens=300,
        )

        conn = get_connection(self.novel_name)
        ids = [r["id"] for r in to_compress]
        conn.execute(
            f"UPDATE summaries SET is_compressed=1 "
            f"WHERE id IN ({','.join('?' * len(ids))})",
            ids
        )

        first_ch = to_compress[0]["chapter_num"]
        last_ch = to_compress[-1]["chapter_num"]
        conn.execute("""
            INSERT INTO summaries (chapter_num, summary, is_compressed)
            VALUES (?, ?, 1)
        """, (last_ch,
              f"[阶段摘要 第{first_ch}-{last_ch}章] {compressed_text}"))

        conn.commit()
        conn.close()
        print(f"  [OK] 摘要压缩完成，第{first_ch}-{last_ch}章已合并")
        self._refresh_summaries_md()

    def _refresh_summaries_md(self):
        recent = self.load_recent_summaries(10)
        lines = ["# 近期章节摘要\n"]
        for s in recent:
            lines.append(f"## 第{s['chapter_num']}章摘要")
            lines.append(s["summary"])
            lines.append("")
        self._write_md("recent_summaries.md", "\n".join(lines))

    # ==================== 章节 ====================

    def save_chapter(self, chapter_num: int, title: str,
                     content: str, status: str = "draft",
                     plot_goal: str = "", emotion_tag: str = ""):
        """保存章节，同时写入 plot_goal 和 emotion_tag"""
        conn = get_connection(self.novel_name)
        conn.execute("""
            INSERT OR REPLACE INTO chapters
            (chapter_num, title, content, status,
             plot_goal, emotion_tag, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (chapter_num, title, content, status,
              plot_goal, emotion_tag, datetime.now()))
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

    def update_chapter_summary(self, chapter_num: int, summary: str):
        """章节审核通过后，将摘要回写到 chapters 表"""
        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE chapters SET summary=?, updated_at=?
            WHERE chapter_num=?
        """, (summary, datetime.now(), chapter_num))
        conn.commit()
        conn.close()

    def increment_retry_count(self, chapter_num: int):
        """记录重试次数"""
        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE chapters SET retry_count = retry_count + 1
            WHERE chapter_num=?
        """, (chapter_num,))
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
            "recent_summaries": self.load_recent_summaries(),
            "chapter_num": chapter_num,
        }

    # ==================== 工具 ====================

    def _write_md(self, filename: str, content: str):
        path = self.data_dir / filename
        path.write_text(content, encoding="utf-8")