import sqlite3
import os
from pathlib import Path

_initialized_novels = set()
_wal_configured_paths = set()


def get_db_path(novel_name: str) -> str:
    from core.config_loader import get as cfg
    base = cfg("paths", "data_dir", "data")
    data_dir = Path(base) / novel_name
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "novel.db")


def get_connection(novel_name: str) -> sqlite3.Connection:
    db_path = get_db_path(novel_name)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if db_path not in _wal_configured_paths:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        _wal_configured_paths.add(db_path)
    return conn


def init_database(novel_name: str):
    conn = get_connection(novel_name)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS novel_info (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            genre TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            role TEXT,
            appearance TEXT,
            personality TEXT,
            secret TEXT,
            weakness TEXT,
            current_location TEXT,
            current_status TEXT,
            relationships TEXT,
            updated_chapter INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_num INTEGER NOT NULL UNIQUE,
            title TEXT,
            emotion_tag TEXT,
            plot_goal TEXT,
            word_target INTEGER DEFAULT 3000,
            content TEXT,
            summary TEXT,
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            review_score_total INTEGER,
            review_score_l1 INTEGER,
            review_score_l2 INTEGER,
            review_score_l3 INTEGER,
            review_veto_items TEXT,
            review_failure_attribution TEXT,
            review_updated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chapter_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_num INTEGER NOT NULL UNIQUE,
            plot_goal TEXT,
            emotion_tag TEXT DEFAULT '铺垫',
            status TEXT DEFAULT '待处理',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS foreshadowing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fid TEXT UNIQUE NOT NULL,
            plant_chapter INTEGER,
            description TEXT,
            expected_redeem TEXT,
            status TEXT DEFAULT 'active',
            redeemed_chapter INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_num INTEGER NOT NULL,
            summary TEXT NOT NULL,
            is_compressed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS world_settings (
            id INTEGER PRIMARY KEY,
            content TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_switch_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            switch_type TEXT NOT NULL,
            old_model TEXT,
            new_model TEXT,
            trigger_reason TEXT,
            failure_count INTEGER DEFAULT 0,
            chapter_num INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 索引
    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_chapter_num ON chapters(chapter_num)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_task_chapter_num ON chapter_tasks(chapter_num)"
        )
    except Exception:
        pass

    # 补列迁移（兼容旧库）
    _migrate(conn, cursor)

    conn.commit()
    conn.close()
    _initialized_novels.add(novel_name)
    print(f"[OK] 数据库初始化完成: data/{novel_name}/novel.db")


def ensure_database(novel_name: str):
    if novel_name in _initialized_novels:
        return
    init_database(novel_name)


def _migrate(conn, cursor):
    """安全补列，旧库缺列时自动添加"""
    existing = {
        row[1] for row in
        cursor.execute("PRAGMA table_info(chapters)").fetchall()
    }
    additions = {
        "plot_goal":   "ALTER TABLE chapters ADD COLUMN plot_goal TEXT",
        "emotion_tag": "ALTER TABLE chapters ADD COLUMN emotion_tag TEXT",
        "summary":     "ALTER TABLE chapters ADD COLUMN summary TEXT",
        "retry_count": "ALTER TABLE chapters ADD COLUMN retry_count INTEGER DEFAULT 0",
        "word_target": "ALTER TABLE chapters ADD COLUMN word_target INTEGER DEFAULT 3000",
        "review_score_total": "ALTER TABLE chapters ADD COLUMN review_score_total INTEGER",
        "review_score_l1": "ALTER TABLE chapters ADD COLUMN review_score_l1 INTEGER",
        "review_score_l2": "ALTER TABLE chapters ADD COLUMN review_score_l2 INTEGER",
        "review_score_l3": "ALTER TABLE chapters ADD COLUMN review_score_l3 INTEGER",
        "review_veto_items": "ALTER TABLE chapters ADD COLUMN review_veto_items TEXT",
        "review_failure_attribution": "ALTER TABLE chapters ADD COLUMN review_failure_attribution TEXT",
        "review_updated_at": "ALTER TABLE chapters ADD COLUMN review_updated_at TIMESTAMP",
    }
    for col, sql in additions.items():
        if col not in existing:
            try:
                cursor.execute(sql)
            except Exception:
                pass

    char_existing = {
        row[1] for row in
        cursor.execute("PRAGMA table_info(characters)").fetchall()
    }
    if "relationships" not in char_existing:
        try:
            cursor.execute(
                "ALTER TABLE characters ADD COLUMN relationships TEXT"
            )
        except Exception:
            pass

    task_existing = {
        row[1] for row in
        cursor.execute("PRAGMA table_info(chapter_tasks)").fetchall()
    }
    if "status" not in task_existing:
        try:
            cursor.execute(
                "ALTER TABLE chapter_tasks "
                "ADD COLUMN status TEXT DEFAULT '待处理'"
            )
        except Exception:
            pass

    try:
        cursor.execute(
            "UPDATE chapter_tasks SET status='待处理' WHERE status='pending'"
        )
    except Exception:
        pass

    conn.commit()


def clean_duplicate_chapters(novel_name: str):
    ensure_database(novel_name)
    conn = get_connection(novel_name)
    try:
        conn.execute("""
            DELETE FROM chapters WHERE id NOT IN (
                SELECT MAX(id) FROM chapters GROUP BY chapter_num
            )
        """)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_database("测试小说")
    print("数据库模块正常")
