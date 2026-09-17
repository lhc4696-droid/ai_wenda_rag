r"""项目根目录执行：.\venv\Scripts\python.exe backend\migrate_db.py。"""

from app.config import get_settings
from app.migrations import initialize_database
import argparse
from datetime import datetime, timezone
import sqlite3
import uuid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化或无损迁移知言 AI 数据库")
    parser.add_argument("--backup", action="store_true", help="升级前通过 SQLite 在线备份接口保存独立副本")
    args = parser.parse_args()
    database_path = get_settings().auth_db_path
    if args.backup and database_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = database_path.with_name(f"{database_path.stem}.backup-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3")
        source = sqlite3.connect(database_path)
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        print(f"Backup saved: {backup_path}")
    initialize_database(database_path)
    print(f"Database schema upgraded: {database_path}")
