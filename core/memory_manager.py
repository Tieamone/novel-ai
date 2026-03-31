import sqlite3
import json
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_connection, ensure_database


class MemoryManager:
    def __init__(self, novel_name: str):
        self.novel_name = novel_name
        from core.config_loader import get as cfg
        base = cfg("paths", "data_dir", "data")
        self.data_dir = Path(base) / novel_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        ensure_database(novel_name)

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
            try:
                d["relationships"] = json.loads(d.get("relationships") or "{}")
            except Exception:
                d["relationships"] = {}
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

    def update_character_relationship(self, name_a: str, name_b: str,
                                      relationship: str, chapter_num: int):
        """
        双向更新人物关系：
        A→B 更新为 relationship
        B→A 自动镜像（对调主谓，如"信任"变"被信任"）
        """
        self._set_relationship(name_a, name_b, relationship, chapter_num)

        # 镜像描述：简单在原描述前加"被"或直接复用
        mirror = _mirror_relationship(relationship)
        self._set_relationship(name_b, name_a, mirror, chapter_num)

        self._refresh_characters_md()

    def _set_relationship(self, name: str, other: str,
                          rel: str, chapter_num: int):
        conn = get_connection(self.novel_name)
        row = conn.execute(
            "SELECT relationships FROM characters WHERE name=?", (name,)
        ).fetchone()
        conn.close()
        if not row:
            return
        try:
            rels = json.loads(row["relationships"] or "{}")
        except Exception:
            rels = {}
        rels[other] = rel
        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE characters
            SET relationships=?, updated_chapter=?
            WHERE name=?
        """, (json.dumps(rels, ensure_ascii=False), chapter_num, name))
        conn.commit()
        conn.close()

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
            if c.get("relationships"):
                rels_str = "、".join(
                    f"{k}：{v}" for k, v in c["relationships"].items()
                )
                lines.append(f"- 人物关系：{rels_str}")
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
            ORDER BY chapter_num ASC LIMIT ?
        """, (total - keep_recent,)).fetchall()
        conn.close()

        if not to_compress:
            return

        print(f"  [压缩] 压缩第"
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
                     plot_goal: str = "", emotion_tag: str = "",
                     word_target=None):
        from core.config_loader import get as cfg
        if word_target is None:
            word_target = cfg("novel", "chapter_word_target", 3000)
        try:
            word_target = int(word_target)
        except Exception:
            word_target = int(cfg("novel", "chapter_word_target", 3000))
        conn = get_connection(self.novel_name)
        conn.execute("""
            INSERT OR REPLACE INTO chapters
            (chapter_num, title, content, status,
             plot_goal, emotion_tag, word_target, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (chapter_num, title, content, status,
              plot_goal, emotion_tag, word_target, datetime.now()))
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
        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE chapters SET summary=?, updated_at=?
            WHERE chapter_num=?
        """, (summary, datetime.now(), chapter_num))
        conn.commit()
        conn.close()

    def update_chapter_review_result(self, chapter_num: int, review: dict):
        if not isinstance(review, dict):
            review = {}

        def _safe_int(v):
            try:
                return int(v)
            except Exception:
                return None

        score_total = _safe_int(review.get("score_total"))
        if score_total is None:
            legacy_score = review.get("score")
            if legacy_score is not None:
                try:
                    score_total = int(float(legacy_score))
                    if score_total <= 10:
                        score_total *= 10
                except Exception:
                    score_total = None

        score_l1 = _safe_int(review.get("score_l1"))
        score_l2 = _safe_int(review.get("score_l2"))
        score_l3 = _safe_int(review.get("score_l3"))
        veto_items = review.get("veto_items", [])
        failure_attr = review.get("failure_attribution", {})

        conn = get_connection(self.novel_name)
        conn.execute("""
            UPDATE chapters
            SET review_score_total=?,
                review_score_l1=?,
                review_score_l2=?,
                review_score_l3=?,
                review_veto_items=?,
                review_failure_attribution=?,
                review_updated_at=?,
                updated_at=?
            WHERE chapter_num=?
        """, (
            score_total,
            score_l1,
            score_l2,
            score_l3,
            json.dumps(veto_items, ensure_ascii=False),
            json.dumps(failure_attr, ensure_ascii=False),
            datetime.now(),
            datetime.now(),
            chapter_num
        ))
        conn.commit()
        conn.close()

    def increment_retry_count(self, chapter_num: int):
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

    # ★ 新增方法：获取上一章结尾，用于新章衔接
    def get_last_chapter_ending(self, chapter_num: int, chars: int = 200) -> str:
        """
        获取上一章最后 N 字，供新章开头衔接使用。
        chapter_num 为当前要写的章节号，自动读取 chapter_num-1 的内容。
        """
        prev_num = chapter_num - 1
        if prev_num < 1:
            return ""
        conn = get_connection(self.novel_name)
        row = conn.execute(
            "SELECT content FROM chapters WHERE chapter_num=?",
            (prev_num,)
        ).fetchone()
        conn.close()
        if not row or not row["content"]:
            return ""
        content = row["content"]
        return content[-chars:] if len(content) > chars else content

    # ==================== 工具 ====================

    def _write_md(self, filename: str, content: str):
        path = self.data_dir / filename
        path.write_text(content, encoding="utf-8")
def get_last_chapter_ending(self, current_chapter_num: int, chars: int = 500) -> str:
    """获取上一章结尾片段，用于续写衔接。"""
    prev_num = current_chapter_num - 1
    if prev_num < 1:
        return ""
    try:
        conn = get_connection(self.novel_name)
        row = conn.execute(
            "SELECT content FROM chapters WHERE chapter_num = ?",
            (prev_num,)
        ).fetchone()
        conn.close()
        if row and row["content"]:
            return row["content"][-chars:]
        return ""
    except Exception:
        return ""

def _mirror_relationship(rel: str) -> str:
    """
    生成镜像关系描述。
    规则：在原描述前加"（对方）"前缀，保留原意但标注视角。
    如：A→B "信任" => B→A "被信任"
    如：A→B "怀疑并监视" => B→A "被怀疑并监视"
    """
    # 常见关系的镜像映射
    mirror_map = {
        "信任": "被信任",
        "怀疑": "被怀疑",
        "保护": "被保护",
        "监视": "被监视",
        "利用": "被利用",
        "喜欢": "被喜欢",
        "爱慕": "被爱慕",
        "敌视": "被敌视",
        "追杀": "被追杀",
        "崇拜": "被崇拜",
        "依赖": "被依赖",
        "控制": "被控制",
        "欺骗": "被欺骗",
    }
    # 精确匹配
    if rel in mirror_map:
        return mirror_map[rel]
    # 包含匹配
    for k, v in mirror_map.items():
        if k in rel:
            return rel.replace(k, v)
    # 兜底：加"（对方视角）"
    return f"{rel}（对方视角）"