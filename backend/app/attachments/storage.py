"""仅使用服务器生成的 ID 定位文件；禁止将原始文件名用作路径。"""
from pathlib import Path
import re


class AttachmentStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, identifier, preview=False):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid attachment ID")
        path = self.root / (identifier + (".jpg" if preview else ".bin"))
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("Unsafe attachment path")
        return path

    def create(self, identifier):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.path(identifier).open("xb")

    def delete(self, identifier):
        for preview in (False, True):
            self.path(identifier, preview).unlink(missing_ok=True)
