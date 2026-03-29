import sqlite3
import os
from pathlib import Path


def get_db_path(novel_name: str) -> str:
    data_dir = Path("data") / novel_name
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "novel.db")


def get_connection(novel_name: str) -> sqlite3.Connection:
    db_path = get_db_path(novel_name)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
            status TEXT DEFAULT 'pending',
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
        CREATE TABLE IF NOT EXISTS tone_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            locked INTEGER DEFAULT 0
        )
    """)

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

    conn.commit()
    conn.close()
    print(f"[OK] 数据库初始化完成: data/{novel_name}/novel.db")


def clean_duplicate_chapters(novel_name: str):
    conn = get_connection(novel_name)
    conn.execute("""
        DELETE FROM chapters WHERE id NOT IN (
            SELECT MAX(id) FROM chapters GROUP BY chapter_num
        )
    """)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_database("测试小说")
    print("数据库模块正常")