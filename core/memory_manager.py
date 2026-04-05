import sqlite3
import json
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_connection, ensure_database
from core.utils import with_db_connection, DatabaseTransaction


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
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    INSERT OR REPLACE INTO world_settings (id, content, updated_at)
                    VALUES (1, ?, ?)
                """, (content, datetime.now()))
        self._write_md("settings.md", f"# 世界观设定\n\n{content}")

    def load_world_settings(self) -> str:
        with with_db_connection(self.novel_name) as conn:
            row = conn.execute(
                "SELECT content FROM world_settings WHERE id=1"
            ).fetchone()
        return row["content"] if row else ""

    # ==================== 人物 ====================

    def save_character(self, name: str, data: dict):
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
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
        self._refresh_characters_md()

    def load_characters(self) -> list:
        with with_db_connection(self.novel_name) as conn:
            rows = conn.execute("SELECT * FROM characters").fetchall()
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
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE characters
                    SET current_location=?, current_status=?, updated_chapter=?
                    WHERE name=?
                """, (location, status, chapter_num, name))
        self._refresh_characters_md()

    def update_character_relationship(self, name_a: str, name_b: str,
                                      relationship: str, chapter_num: int):
        """双向更新人物关系：A→B + 自动镜像 B→A"""
        self._set_relationship(name_a, name_b, relationship, chapter_num)
        mirror = _mirror_relationship(relationship)
        self._set_relationship(name_b, name_a, mirror, chapter_num)
        self._refresh_characters_md()

    def _set_relationship(self, name: str, other: str,
                          rel: str, chapter_num: int):
        with with_db_connection(self.novel_name) as conn:
            row = conn.execute(
                "SELECT relationships FROM characters WHERE name=?", (name,)
            ).fetchone()
        if not row:
            return
        try:
            rels = json.loads(row["relationships"] or "{}")
        except Exception:
            rels = {}
        rels[other] = rel
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE characters
                    SET relationships=?, updated_chapter=?
                    WHERE name=?
                """, (json.dumps(rels, ensure_ascii=False), chapter_num, name))

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
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO foreshadowing
                        (fid, plant_chapter, description, expected_redeem, status)
                        VALUES (?, ?, ?, ?, 'active')
                    """, (fid, plant_chapter, description, expected_redeem))
                except Exception:
                    pass
        self._refresh_foreshadowing_md()

    def redeem_foreshadowing(self, fid: str, chapter_num: int):
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE foreshadowing
                    SET status='redeemed', redeemed_chapter=?
                    WHERE fid=?
                """, (chapter_num, fid))
        self._refresh_foreshadowing_md()

    def load_active_foreshadowing(self) -> list:
        with with_db_connection(self.novel_name) as conn:
            rows = conn.execute(
                "SELECT * FROM foreshadowing WHERE status='active'"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_foreshadow_hints(self, chapter_num: int) -> list:
        """
        根据当前章节号，智能匹配应该在本章处理（兑现/铺垫）的伏笔。
        
        匹配规则：
        1. expected_redeem 包含当前章节号或范围（如"第10-15章"，当前是12章）
        2. expected_redeem 为"待定"或模糊表述的，按时间顺序优先展示近期该处理的
        3. 返回格式化的提示字符串列表
        
        Returns:
            list: 适合直接放入提示词的伏笔提示字符串列表
        """
        import re

        all_active = self.load_active_foreshadowing()
        hints = []

        for f in all_active:
            fid = f.get('fid', '')
            desc = f.get('description', '')
            expected = f.get('expected_redeem', '待定')
            planted_at = f.get('plant_chapter', 0)

            should_handle = False
            handle_type = "铺垫"

            if not expected or expected == "待定":
                if chapter_num - planted_at <= 10:
                    should_handle = True
                    handle_type = "可铺垫"
            else:
                expected_str = str(expected)

                range_match = re.search(r'第?(\d+)[~\-–到至]+(\d+)', expected_str)
                if range_match:
                    start_ch = int(range_match.group(1))
                    end_ch = int(range_match.group(2))
                    if start_ch <= chapter_num <= end_ch:
                        should_handle = True
                        handle_type = "应兑现"
                else:
                    single_match = re.search(r'第?(\d+)', expected_str)
                    if single_match:
                        target_ch = int(single_match.group(1))
                        if abs(target_ch - chapter_num) <= 3:
                            should_handle = True
                            handle_type = "应兑现" if target_ch <= chapter_num else "即将兑现"
                    elif any(keyword in expected_str for keyword in ['高潮', '结局', '终章', '决战']):
                        if chapter_num >= planted_at + 5:
                            should_handle = True
                            handle_type = "可推进"

            if should_handle:
                hint = f"[{handle_type}] {desc}"
                if expected and expected != "待定":
                    hint += f"（计划：{expected}）"
                hints.append({
                    "hint": hint,
                    "handle_type": handle_type,
                    "priority": 0 if handle_type == "应兑现" else (1 if handle_type == "即将兑现" else 2),
                })

        hints.sort(key=lambda x: (x["priority"], x["hint"]))
        return [h["hint"] for h in hints]

    def _refresh_foreshadowing_md(self):
        with with_db_connection(self.novel_name) as conn:
            rows = conn.execute("SELECT * FROM foreshadowing").fetchall()
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
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    INSERT OR REPLACE INTO summaries (chapter_num, summary)
                    VALUES (?, ?)
                """, (chapter_num, summary))
        self._refresh_summaries_md()

    def load_recent_summaries(self, count: int = 5) -> list:
        from core.config_loader import get as cfg
        count = cfg("novel", "recent_summary_count", count)
        with with_db_connection(self.novel_name) as conn:

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

        result = []
        if compressed:
            result.append(dict(compressed))
        result.extend([dict(r) for r in reversed(recent)])
        return result

    def compress_old_summaries(self):
        from core.api_client import call_api
        from core.config_loader import get as cfg

        with with_db_connection(self.novel_name) as conn:
            to_compress = conn.execute("""
                SELECT id, chapter_num, summary FROM summaries
                WHERE is_compressed=0
                ORDER BY chapter_num
            """).fetchall()

        if len(to_compress) < 5:
            return

        BATCH_SIZE = 10
        all_compressed_texts = []
        total_batches = (len(to_compress) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_idx in range(0, len(to_compress), BATCH_SIZE):
            batch = to_compress[batch_idx:batch_idx + BATCH_SIZE]
            current_batch_num = batch_idx // BATCH_SIZE + 1

            first_ch = batch[0]["chapter_num"]
            last_ch = batch[-1]["chapter_num"]

            batch_parts = []
            for r in batch:
                batch_parts.append(f"第{r['chapter_num']}章：{r['summary']}")
            summaries_text = "\n".join(batch_parts)
            del batch_parts

            print(f"  [压缩] 正在处理第{current_batch_num}/{total_batches}批 "
                  f"（第{first_ch}-{last_ch}章，共{len(batch)}条）...")

            compressed_text = call_api(
                system_prompt=(
                    "你是专业小说编辑，负责压缩章节摘要。\n"
                    "要求：\n"
                    "1. 压缩为250字以内的阶段性摘要\n"
                    "2. 必须保留：主要人物当前位置与状态、已揭示的关键信息、"
                    "主要人物关系的最新变化、已激活但未兑现的伏笔线索\n"
                    "3. 用'谁+在哪+做了什么+知道了什么'的结构来组织信息\n"
                    "4. 直接输出摘要内容，不加前缀"
                ),
                user_message=(
                    f"请将第{first_ch}-{last_ch}章的摘要压缩为阶段性摘要：\n\n"
                    f"{summaries_text}"
                ),
                temperature=0.3,
                max_tokens=400,
            )

            del summaries_text
            all_compressed_texts.append({
                "first_ch": first_ch,
                "last_ch": last_ch,
                "ids": [r["id"] for r in batch],
                "text": compressed_text
            })

        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                for compressed in all_compressed_texts:
                    conn.execute(
                        f"UPDATE summaries SET is_compressed=1 "
                        f"WHERE id IN ({','.join('?' * len(compressed['ids']))})",
                        compressed["ids"]
                    )
                    conn.execute("""
                        INSERT INTO summaries (chapter_num, summary, is_compressed)
                        VALUES (?, ?, 1)
                    """, (compressed["last_ch"],
                          f"[阶段摘要 第{compressed['first_ch']}-{compressed['last_ch']}章] {compressed['text']}"))

        overall_first = to_compress[0]["chapter_num"]
        overall_last = to_compress[-1]["chapter_num"]
        del to_compress, all_compressed_texts
        print(f"  [OK] 摘要压缩完成，第{overall_first}-{overall_last}章已合并（共{total_batches}批）")
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
                     content: str, status: str = "草稿",
                     plot_goal: str = "", emotion_tag: str = "",
                     word_target=None):
        from core.config_loader import get as cfg
        if word_target is None:
            word_target = cfg("novel", "chapter_word_target", 3000)
        try:
            word_target = int(word_target)
        except Exception:
            word_target = int(cfg("novel", "chapter_word_target", 3000))
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    INSERT OR REPLACE INTO chapters
                    (chapter_num, title, content, status,
                     plot_goal, emotion_tag, word_target, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (chapter_num, title, content, status,
                      plot_goal, emotion_tag, word_target, datetime.now()))

    def update_chapter_status(self, chapter_num: int, status: str):
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE chapters SET status=?, updated_at=?
                    WHERE chapter_num=?
                """, (status, datetime.now(), chapter_num))

    def update_chapter_summary(self, chapter_num: int, summary: str):
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE chapters SET summary=?, updated_at=?
                    WHERE chapter_num=?
                """, (summary, datetime.now(), chapter_num))

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

        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
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

    def increment_retry_count(self, chapter_num: int):
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE chapters SET retry_count = retry_count + 1
                    WHERE chapter_num=?
                """, (chapter_num,))

    def load_chapter(self, chapter_num: int) -> dict:
        with with_db_connection(self.novel_name) as conn:
            row = conn.execute(
                "SELECT * FROM chapters WHERE chapter_num=?",
                (chapter_num,)
            ).fetchone()
        return dict(row) if row else {}

    def delete_chapter(self, chapter_num: int):
        """
        删除章节的数据库记录及其摘要。
        任务卡重置（status→待处理）由调用方负责。
        注意：伏笔记录不自动回滚，需人工处理。
        """
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute(
                    "DELETE FROM chapters WHERE chapter_num=?", (chapter_num,)
                )
                conn.execute(
                    "DELETE FROM summaries WHERE chapter_num=?", (chapter_num,)
                )

    def load_context(self, chapter_num: int) -> dict:
        """
        加载写作上下文，供 writer/reviewer 使用。
        包含：世界观、人物、活跃伏笔、近期摘要、章节号、本章伏笔提示。
        """
        return {
            "world_settings": self.load_world_settings(),
            "characters": self.load_characters(),
            "active_foreshadowing": self.load_active_foreshadowing(),
            "foreshadow_hints": self.get_foreshadow_hints(chapter_num),
            "recent_summaries": self.load_recent_summaries(),
            "chapter_num": chapter_num,
        }

    def get_last_chapter_ending(self, chapter_num: int, chars: int = 250) -> str:
        prev_num = chapter_num - 1
        if prev_num < 1:
            return ""
        with with_db_connection(self.novel_name) as conn:
            row = conn.execute(
                "SELECT content FROM chapters WHERE chapter_num=?",
                (prev_num,)
            ).fetchone()
        if not row or not row["content"]:
            return ""
        content = row["content"]
        return content[-chars:] if len(content) > chars else content

    # ==================== 工具 ====================

    def _write_md(self, filename: str, content: str):
        path = self.data_dir / filename
        path.write_text(content, encoding="utf-8")


def _mirror_relationship(rel: str) -> str:
    """
    生成镜像关系描述。
    规则：在原描述前加"被"，或使用预定义的镜像词。
    如：A→B "信任" => B→A "被信任"
    """
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
        "支持": "被支持",
        "排斥": "被排斥",
        "吸引": "被吸引",
        "警惕": "被警惕",
    }
    if rel in mirror_map:
        return mirror_map[rel]
    for k, v in mirror_map.items():
        if k in rel:
            return rel.replace(k, v)
    return f"{rel}（对方视角）"
