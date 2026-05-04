import sqlite3
import json
from pathlib import Path
from datetime import datetime

from core.db import get_connection, ensure_database
from core.utils import with_db_connection, DatabaseTransaction


class MemoryManager:
    def __init__(self, novel_name: str):
        self.novel_name = novel_name
        from core.config_loader import get_data_dir
        self.data_dir = get_data_dir(novel_name)
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

    def save_character(self, name: str, data: dict, _batch: bool = False):
        """保存人物档案。_batch=True 时跳过 MD 刷新，批量操作后手动调 refresh_characters_md()"""
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
        if not _batch:
            self._refresh_characters_md()

    def save_characters_batch(self, characters: list):
        """批量保存人物档案，使用单一连接和单一事务，最后只刷新一次 MD"""
        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                for char_data in characters:
                    name = char_data.get("name", "")
                    if not name:
                        continue
                    conn.execute("""
                        INSERT OR REPLACE INTO characters
                        (name, role, appearance, personality, secret, weakness,
                         current_location, current_status, relationships, updated_chapter)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        name,
                        char_data.get("role", ""),
                        char_data.get("appearance", ""),
                        char_data.get("personality", ""),
                        char_data.get("secret", ""),
                        char_data.get("weakness", ""),
                        char_data.get("current_location", ""),
                        char_data.get("current_status", ""),
                        json.dumps(char_data.get("relationships", {}), ensure_ascii=False),
                        char_data.get("updated_chapter", 0),
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
        1. 有明确计划章节的：精确窗口匹配，逾期的强制提醒
        2. 待定/模糊的：按"沉睡时长"分级，越久未处理越紧迫，永不遗忘
        3. 智能分批：每章最多 MAX_HINTS 条，但紧急项（逾期/久悬）全部保留

        priority 越小越紧迫：
          0 = 应兑现 / 逾期未兑现
          1 = 即将兑现（3章内）
          2 = 久悬未兑现（待定且沉睡 >URGENT_AGE 章）
          3 = 待推进（待定且沉睡 NORMAL_AGE~URGENT_AGE 章）
          4 = 可铺垫（待定且沉睡 <NORMAL_AGE 章）
          5 = 可推进（关键词模糊匹配）
        """
        import re

        MAX_HINTS   = 8     # 每章最多提示条数，避免提示词过载
        URGENT_AGE  = 20    # 沉睡超过此章节数 → 久悬，强提醒
        NORMAL_AGE  = 10    # 沉睡超过此章节数 → 待推进

        all_active = self.load_active_foreshadowing()
        hints = []

        for f in all_active:
            desc        = f.get('description', '')
            expected    = f.get('expected_redeem', '待定')
            # planted_at=0 是数据库默认值，统一视为第1章埋下
            planted_at  = max(f.get('plant_chapter', 1) or 1, 1)
            age         = chapter_num - planted_at   # 该伏笔已沉睡几章

            should_handle = False
            handle_type   = "可铺垫"
            priority      = 4

            expected_clean = str(expected).strip() if expected else ""

            if not expected_clean or expected_clean == "待定":
                # ── 待定伏笔：永不丢弃，按沉睡时长分级 ──────────────────
                should_handle = True
                if age >= URGENT_AGE:
                    handle_type = "久悬未兑现"
                    priority    = 2
                elif age >= NORMAL_AGE:
                    handle_type = "待推进"
                    priority    = 3
                else:
                    handle_type = "可铺垫"
                    priority    = 4
            else:
                # ── 有计划的伏笔：精确匹配 ───────────────────────────────
                range_match = re.search(r'第?(\d+)[~\-–到至]+(\d+)', expected_clean)
                if range_match:
                    start_ch = int(range_match.group(1))
                    end_ch   = int(range_match.group(2))
                    if start_ch <= chapter_num <= end_ch:
                        should_handle = True
                        handle_type   = "应兑现"
                        priority      = 0
                    elif chapter_num > end_ch:
                        # 已过计划窗口还没兑现 → 强提醒
                        should_handle = True
                        handle_type   = "逾期未兑现"
                        priority      = 0
                else:
                    single_match = re.search(r'第?(\d+)', expected_clean)
                    if single_match:
                        target_ch = int(single_match.group(1))
                        if chapter_num > target_ch:
                            # 已过计划章节还没兑现 → 强提醒
                            should_handle = True
                            handle_type   = "逾期未兑现"
                            priority      = 0
                        elif abs(target_ch - chapter_num) <= 3:
                            should_handle = True
                            handle_type   = "应兑现" if target_ch <= chapter_num else "即将兑现"
                            priority      = 0 if target_ch <= chapter_num else 1
                    elif any(kw in expected_clean for kw in ['高潮', '结局', '终章', '决战']):
                        if age >= 5:
                            should_handle = True
                            handle_type   = "可推进"
                            priority      = 5

            if should_handle:
                hint = f"[{handle_type}] {desc}"
                if expected_clean and expected_clean != "待定":
                    hint += f"（计划：{expected_clean}）"
                hints.append({
                    "hint":        hint,
                    "handle_type": handle_type,
                    "priority":    priority,
                    "age":         age,
                })

        # 排序：优先级升序 → 同优先级内沉睡越久越靠前
        hints.sort(key=lambda x: (x["priority"], -x["age"]))

        # 分批：紧急项（priority<=2）全部保留，其余截取到 MAX_HINTS
        urgent  = [h for h in hints if h["priority"] <= 2]
        others  = [h for h in hints if h["priority"] >  2]
        selected = urgent + others[:max(0, MAX_HINTS - len(urgent))]

        return [h["hint"] for h in selected]

    _MACRO_KEYWORDS = ("暗示", "背后", "阴谋", "命运", "秘密", "真相", "警告")

    def _is_macro_foreshadow(self, desc: str, age: int) -> bool:
        """判断是否为宏观悬念类伏笔"""
        if len(desc) <= 8:
            return False
        return any(kw in desc for kw in self._MACRO_KEYWORDS)

    def get_foreshadow_report(self, current_chapter: int) -> dict:
        """
        生成伏笔健康度报告。
        返回：overdue / due_soon / macro / recent_added /
              recent_redeemed / total_active / trend
        """
        import re

        with with_db_connection(self.novel_name) as conn:
            # 当前未兑现伏笔
            active_rows = conn.execute(
                "SELECT * FROM foreshadowing WHERE status='active'"
            ).fetchall()

            # 最近10章新增的伏笔
            recent_added = conn.execute(
                "SELECT COUNT(*) as cnt FROM foreshadowing "
                "WHERE plant_chapter > ?",
                (max(0, current_chapter - 10),)
            ).fetchone()["cnt"]

            # 最近10章兑现的伏笔
            recent_redeemed = conn.execute(
                "SELECT COUNT(*) as cnt FROM foreshadowing "
                "WHERE status='redeemed' AND redeemed_chapter > ?",
                (max(0, current_chapter - 10),)
            ).fetchone()["cnt"]

        overdue = []    # 沉睡超过20章
        due_soon = []   # 5章内即将到期
        macro = []      # 宏观悬念

        for row in active_rows:
            f = dict(row)
            fid = f.get("fid", "")
            desc = f.get("description", "")
            plant_ch = max(f.get("plant_chapter", 1) or 1, 1)
            expected = f.get("expected_redeem", "待定") or "待定"
            age = current_chapter - plant_ch

            # 宏观悬念优先判定，符合条件的不计入严重超期
            if self._is_macro_foreshadow(desc, age):
                macro.append({
                    "fid": fid,
                    "description": desc,
                    "plant_chapter": plant_ch,
                    "expected_redeem": expected,
                    "age": age,
                })
                continue

            # 严重超期：沉睡超过20章
            if age > 20:
                overdue.append({
                    "fid": fid,
                    "description": desc,
                    "plant_chapter": plant_ch,
                    "expected_redeem": expected,
                    "age": age,
                })

            # 即将到期：expected_redeem 在当前章节+5章以内
            expected_clean = str(expected).strip()
            if expected_clean and expected_clean != "待定":
                range_match = re.search(r'第?(\d+)[~\-–到至]+(\d+)', expected_clean)
                if range_match:
                    end_ch = int(range_match.group(2))
                    if 0 < end_ch - current_chapter <= 5:
                        due_soon.append({
                            "fid": fid,
                            "description": desc,
                            "plant_chapter": plant_ch,
                            "expected_redeem": expected,
                        })
                else:
                    single_match = re.search(r'第?(\d+)', expected_clean)
                    if single_match:
                        target_ch = int(single_match.group(1))
                        if 0 < target_ch - current_chapter <= 5:
                            due_soon.append({
                                "fid": fid,
                                "description": desc,
                                "plant_chapter": plant_ch,
                                "expected_redeem": expected,
                            })

        # 按沉睡章数降序排列
        overdue.sort(key=lambda x: -x["age"])
        macro.sort(key=lambda x: -x["age"])

        total_active = len(active_rows)
        trend = recent_added - recent_redeemed

        return {
            "overdue": overdue,
            "due_soon": due_soon,
            "macro": macro,
            "recent_added": recent_added,
            "recent_redeemed": recent_redeemed,
            "total_active": total_active,
            "trend": trend,
        }

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
            all_uncompressed = conn.execute("""
                SELECT id, chapter_num, summary FROM summaries
                WHERE is_compressed=0
                ORDER BY chapter_num
            """).fetchall()
            latest_compressed = conn.execute("""
                SELECT chapter_num, summary FROM summaries
                WHERE is_compressed=1
                ORDER BY chapter_num DESC LIMIT 1
            """).fetchone()

        keep_recent = int(cfg("novel", "recent_summary_count", 5) or 5)
        keep_recent = max(1, keep_recent)
        if len(all_uncompressed) <= keep_recent:
            return

        to_compress = all_uncompressed[:-keep_recent]
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
                "chapter_nums": [r["chapter_num"] for r in batch],
                "text": compressed_text
            })

        combined_parts = []
        if latest_compressed and latest_compressed["summary"]:
            combined_parts.append(
                f"既有阶段摘要：{latest_compressed['summary']}"
            )
        for compressed in all_compressed_texts:
            combined_parts.append(
                f"第{compressed['first_ch']}-{compressed['last_ch']}章："
                f"{compressed['text']}"
            )
        combined_basis = "\n".join(combined_parts)

        if len(combined_parts) > 1:
            overall_text = call_api(
                system_prompt=(
                    "你是专业小说编辑，负责维护长期剧情记忆。\n"
                    "请把历史阶段摘要和新压缩摘要合并为一段350字以内的总阶段摘要。\n"
                    "必须保留：关键人物状态、主线进展、已揭示信息、未兑现伏笔。"
                    "直接输出摘要内容，不加前缀。"
                ),
                user_message=combined_basis,
                temperature=0.3,
                max_tokens=500,
            )
        else:
            overall_text = all_compressed_texts[0]["text"]

        with with_db_connection(self.novel_name) as conn:
            with DatabaseTransaction(conn):
                all_chapter_nums = [
                    ch for compressed in all_compressed_texts
                    for ch in compressed["chapter_nums"]
                ]
                if all_chapter_nums:
                    conn.execute(
                        f"DELETE FROM summaries WHERE is_compressed=1 "
                        f"AND chapter_num IN ({','.join('?' * len(all_chapter_nums))})",
                        all_chapter_nums
                    )
                for compressed in all_compressed_texts:
                    conn.execute(
                        f"UPDATE summaries SET is_compressed=1 "
                        f"WHERE id IN ({','.join('?' * len(compressed['ids']))})",
                        compressed["ids"]
                    )
                conn.execute(
                    "UPDATE summaries SET summary=? WHERE id=?",
                    (
                        f"[阶段摘要 第{to_compress[0]['chapter_num']}-"
                        f"{to_compress[-1]['chapter_num']}章] {overall_text}",
                        to_compress[-1]["id"],
                    )
                )

        overall_first = to_compress[0]["chapter_num"]
        overall_last = to_compress[-1]["chapter_num"]
        del all_uncompressed, to_compress, all_compressed_texts
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
                now = datetime.now()
                cur = conn.execute("""
                    UPDATE chapters
                    SET title=?,
                        content=?,
                        status=?,
                        plot_goal=?,
                        emotion_tag=?,
                        word_target=?,
                        updated_at=?
                    WHERE chapter_num=?
                """, (
                    title, content, status, plot_goal, emotion_tag,
                    word_target, now, chapter_num
                ))
                if cur.rowcount == 0:
                    conn.execute("""
                        INSERT INTO chapters
                        (chapter_num, title, content, status,
                         plot_goal, emotion_tag, word_target, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (chapter_num, title, content, status,
                          plot_goal, emotion_tag, word_target, now))

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
        包含：世界观、人物、活跃伏笔、近期摘要、章节号、本章伏笔提示、
              全书大纲、目标章数（用于进度感知和节奏控制）。

        优化Bug5: 合并静态数据（大纲、目标章数）为单次文件读取，
        减少对慢变数据的重复 IO。
        """
        # 静态数据：大纲和目标章数（文件读取，不走DB）
        outline = ""
        outline_path = self.data_dir / "master_outline.md"
        if outline_path.exists():
            try:
                outline = outline_path.read_text(encoding="utf-8").strip()
            except Exception:
                outline = ""

        target_chapters = 0
        target_path = self.data_dir / "target_chapters.txt"
        if target_path.exists():
            try:
                target_chapters = int(
                    target_path.read_text(encoding="utf-8").strip()
                )
            except Exception:
                target_chapters = 0

        # 优化Bug6: 合并 4 次独立 DB 连接为单次批量查询
        world_settings = ""
        characters = []
        active_foreshadowing = []
        recent_summaries = []
        from core.config_loader import get as cfg
        summary_count = cfg("novel", "recent_summary_count", 5)

        with with_db_connection(self.novel_name) as conn:
            # 世界观
            row = conn.execute(
                "SELECT content FROM world_settings WHERE id=1"
            ).fetchone()
            if row:
                world_settings = row["content"]

            # 人物
            rows = conn.execute("SELECT * FROM characters").fetchall()
            for r in rows:
                d = dict(r)
                try:
                    d["relationships"] = json.loads(d.get("relationships") or "{}")
                except Exception:
                    d["relationships"] = {}
                characters.append(d)

            # 活跃伏笔
            f_rows = conn.execute(
                "SELECT * FROM foreshadowing WHERE status='active'"
            ).fetchall()
            active_foreshadowing = [dict(r) for r in f_rows]

            # 近期摘要（压缩版 + 最新N条非压缩）
            compressed = conn.execute("""
                SELECT chapter_num, summary FROM summaries
                WHERE is_compressed=1
                ORDER BY chapter_num DESC LIMIT 1
            """).fetchone()
            recent = conn.execute("""
                SELECT chapter_num, summary FROM summaries
                WHERE is_compressed=0
                ORDER BY chapter_num DESC LIMIT ?
            """, (summary_count,)).fetchall()

            if compressed:
                recent_summaries.append(dict(compressed))
            recent_summaries.extend([dict(r) for r in reversed(recent)])

        # 伏笔提示（基于已加载的活跃伏笔，不再额外查 DB）
        foreshadow_hints = self._get_foreshadow_hints_from_list(
            active_foreshadowing, chapter_num
        )

        return {
            "world_settings":       world_settings,
            "characters":           characters,
            "active_foreshadowing": active_foreshadowing,
            "foreshadow_hints":     foreshadow_hints,
            "recent_summaries":     recent_summaries,
            "chapter_num":          chapter_num,
            "outline":              outline,
            "target_chapters":      target_chapters,
        }

    def _get_foreshadow_hints_from_list(self, active_list: list, chapter_num: int) -> list:
        """从已加载的伏笔列表计算提示（复用 get_foreshadow_hints 逻辑，避免重复查 DB）"""
        import re
        MAX_HINTS = 8
        URGENT_AGE = 20
        NORMAL_AGE = 10
        hints = []
        for f in active_list:
            desc        = f.get('description', '')
            expected    = f.get('expected_redeem', '待定')
            planted_at  = max(f.get('plant_chapter', 1) or 1, 1)
            age         = chapter_num - planted_at
            should_handle = False
            handle_type   = "可铺垫"
            priority      = 4
            expected_clean = str(expected).strip() if expected else ""
            if not expected_clean or expected_clean == "待定":
                should_handle = True
                if age >= URGENT_AGE:
                    handle_type, priority = "久悬未兑现", 2
                elif age >= NORMAL_AGE:
                    handle_type, priority = "待推进", 3
                else:
                    handle_type, priority = "可铺垫", 4
            else:
                range_match = re.search(r'第?(\d+)[~\-–到至]+(\d+)', expected_clean)
                if range_match:
                    s, e = int(range_match.group(1)), int(range_match.group(2))
                    if s <= chapter_num <= e:
                        should_handle, handle_type, priority = True, "应兑现", 0
                    elif chapter_num > e:
                        should_handle, handle_type, priority = True, "逾期未兑现", 0
                else:
                    single = re.search(r'第?(\d+)', expected_clean)
                    if single:
                        t = int(single.group(1))
                        if chapter_num > t:
                            should_handle, handle_type, priority = True, "逾期未兑现", 0
                        elif abs(t - chapter_num) <= 3:
                            should_handle = True
                            handle_type = "应兑现" if t <= chapter_num else "即将兑现"
                            priority = 0 if t <= chapter_num else 1
                    elif any(kw in expected_clean for kw in ['高潮', '结局', '终章', '决战']):
                        if age >= 5:
                            should_handle, handle_type, priority = True, "可推进", 5
            if should_handle:
                hint = f"[{handle_type}] {desc}"
                if expected_clean and expected_clean != "待定":
                    hint += f"（计划：{expected_clean}）"
                hints.append({"hint": hint, "handle_type": handle_type, "priority": priority, "age": age})
        hints.sort(key=lambda x: (x["priority"], -x["age"]))
        urgent  = [h for h in hints if h["priority"] <= 2]
        others  = [h for h in hints if h["priority"] >  2]
        selected = urgent + others[:max(0, MAX_HINTS - len(urgent))]
        return [h["hint"] for h in selected]

    def get_last_chapter_ending(self, chapter_num: int, chars: int = 400) -> str:
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
