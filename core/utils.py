"""
公共工具函数模块
"""
import sqlite3
import time
from contextlib import contextmanager
from typing import Generator
from core.db import get_connection


@contextmanager
def with_db_connection(novel_name: str) -> Generator[sqlite3.Connection, None, None]:
    """
    数据库连接上下文管理器

    使用方式:
        with with_db_connection("小说名") as conn:
            conn.execute(...)
        # 连接自动关闭，即使发生异常

    Args:
        novel_name: 小说名称

    Yields:
        sqlite3.Connection: 数据库连接对象
    """
    conn = get_connection(novel_name)
    try:
        yield conn
    finally:
        conn.close()


def execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
    max_retries: int = 3,
    initial_delay: float = 0.1,
    backoff: float = 2.0,
) -> sqlite3.Cursor:
    """
    执行SQL并自动重试SQLite锁定错误。

    当SQLite返回"database is locked"时，按指数退避策略自动重试，
    而不是立即抛出异常。配合WAL模式和busy_timeout使用效果最佳。

    Args:
        conn: 数据库连接对象
        sql: SQL语句（支持参数化查询）
        params: 查询参数元组
        max_retries: 最大重试次数（默认3次）
        initial_delay: 首次重试等待时间（秒，默认0.1）
        backoff: 退避倍数（默认2.0，即0.1s→0.2s→0.4s）

    Returns:
        sqlite3.Cursor: 执行结果游标

    Raises:
        sqlite3.OperationalError: 重试耗尽后仍失败则抛出原始异常
        其他异常: 非锁定类异常直接抛出，不重试

    用法示例::

        with with_db_connection("小说名") as conn:
            cursor = execute_with_retry(
                conn,
                "UPDATE chapters SET status=? WHERE chapter_num=?",
                ("已审核", 5)
            )
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            last_exception = e
            error_msg = str(e).lower()
            if "locked" in error_msg or "database is locked" in error_msg:
                if attempt < max_retries - 1:
                    print(
                        f"  [提示] 数据库繁忙，"
                        f"{delay:.1f}秒后重试 "
                        f"({attempt + 1}/{max_retries})..."
                    )
                    time.sleep(delay)
                    delay *= backoff
                    continue
            raise

    raise last_exception


class DatabaseTransaction:
    """
    数据库事务管理器。
    使用 BEGIN IMMEDIATE 在事务开始时即获取写锁，
    避免延迟加锁导致的并发写入冲突。
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False