r"""从项目根目录运行：.\venv\Scripts\python.exe backend\init_db.py"""

from app.auth.store import AuthStore
from app.config import get_settings


def main():
    path = get_settings().auth_db_path
    AuthStore(path).initialize()
    print(f"Database initialized: {path}")


if __name__ == "__main__":
    main()
