"""Validate portable source paths and legacy ZIPs without accessing host files."""

from io import BytesIO
from pathlib import PurePosixPath
import re
import stat
import unicodedata
import zlib
from zipfile import BadZipFile, LargeZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from app.coding.schemas import CodingError


MAX_ARCHIVE_BYTES = 5 * 1024 * 1024
MAX_FILES = 150
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_PROJECTS = 10
# Limits also cover ignored entries, so a venv/ or binary ZIP bomb cannot bypass checks.
MAX_ARCHIVE_ENTRIES = 1500
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_MEMBER_BYTES = 5 * 1024 * 1024
EXCLUDED_DIRS = {".git", ".svn", ".hg", "venv", ".venv", "node_modules", "__pycache__", "dist", "build", ".idea", ".aws", ".ssh"}
EXTENSIONS = {".py", ".pyi", ".md", ".rst", ".txt", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".sql", ".xml", ".svg", ".csv"}
FILENAMES = {"dockerfile", "containerfile", "makefile", "license", "licence", "readme", ".gitignore", ".dockerignore", ".editorconfig", "pipfile"}
SECRET_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml", ".pypirc", ".npmrc", ".netrc"}
SECRET_STEMS = {"api_key", "api-key", "apikey", "secret", "secrets", "token", "access_token", "private_key", "private-key"}
RESERVED_WINDOWS = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)
# Browser-local coding supports more languages than the retired ZIP workflow.
# Keep this policy aligned with frontend/src/lib/local-files.js.
LOCAL_EXCLUDED_DIRS = EXCLUDED_DIRS | {".docker", ".kube", ".codex", ".agents", ".vscode", "data", ".next", ".nuxt"}
LOCAL_EXTENSIONS = EXTENSIONS | {".vue", ".svelte", ".scss", ".less", ".go", ".rs", ".java", ".kt", ".kts", ".c", ".h", ".cpp", ".hpp", ".cs", ".sh"}


def validate_path(path: str) -> str:
    """Return a portable relative source path or raise; no implicit normalization."""
    try:
        if isinstance(path, str):
            path.encode("utf-8")
    except UnicodeError:
        raise CodingError("项目路径包含无效 Unicode 字符", "CODING_UNSAFE_PATH") from None
    if (not isinstance(path, str) or not path or len(path) > 240 or path != unicodedata.normalize("NFC", path)
            or "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or any(char in path for char in ':*?"<>|') or path.startswith("/")):
        raise CodingError("项目中包含无效或不安全的文件路径", "CODING_UNSAFE_PATH")
    parts = path.split("/")
    if any(part in {"", ".", ".."} or len(part) > 100 or part.endswith((".", " ")) or RESERVED_WINDOWS.fullmatch(part) for part in parts):
        raise CodingError("项目文件路径不能包含上级目录、空路径或系统保留名称", "CODING_UNSAFE_PATH")
    return path


def allowed_source(path):
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    if any(part.lower() in EXCLUDED_DIRS or part.lower().startswith(".env") for part in parts[:-1]):
        return False
    if (name.startswith(".env") or name in SECRET_NAMES or PurePosixPath(name).stem in SECRET_STEMS
            or name.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))):
        return False
    return name in FILENAMES or PurePosixPath(name).suffix in EXTENSIONS


def validate_local_path(path: str) -> str:
    """Enforce the browser's local-file policy independently of ZIP restrictions."""
    validate_path(path)
    parts = path.lower().split("/")
    name = parts[-1]
    if (any(part in LOCAL_EXCLUDED_DIRS or part.startswith(".env") for part in parts)
            or name in SECRET_NAMES or re.sub(r"\.[^.]+$", "", name) in SECRET_STEMS
            or (name not in FILENAMES and "." + name.split(".")[-1] not in LOCAL_EXTENSIONS)):
        raise CodingError("该目录、敏感文件或非源码类型不允许访问", "CODING_FILE_BLOCKED")
    return path


def validate_files(files: dict[str, str]) -> dict[str, str]:
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise CodingError(f"项目最多包含 {MAX_FILES} 个源码文件", "CODING_FILE_LIMIT", 413)
    total, folded = 0, set()
    for path, content in files.items():
        validate_path(path)
        if not allowed_source(path):
            raise CodingError("该文件类型或凭证文件不能加入工作区", "CODING_FILE_BLOCKED")
        key = path.casefold()
        if key in folded:
            raise CodingError("项目中存在大小写冲突的文件名", "CODING_DUPLICATE_PATH")
        folded.add(key)
        if not isinstance(content, str) or "\x00" in content:
            raise CodingError("项目只能包含 UTF-8 文本源码", "CODING_BINARY_FILE")
        try:
            size = len(content.encode("utf-8"))
        except UnicodeError as error:
            raise CodingError("源码包含无效 Unicode 字符", "CODING_INVALID_ENCODING") from error
        if size > MAX_FILE_BYTES:
            raise CodingError("单个源码文件不能超过 128 KiB", "CODING_FILE_LIMIT", 413)
        total += size
    for path in folded:
        components = path.split("/")
        if any("/".join(components[:index]) in folded for index in range(1, len(components))):
            raise CodingError("文件名与目录名发生冲突", "CODING_DUPLICATE_PATH")
    if total > MAX_TOTAL_BYTES:
        raise CodingError("项目文本源码总量不能超过 2 MiB", "CODING_SIZE_LIMIT", 413)
    return files


def parse_project_zip(raw: bytes, filename: str) -> dict:
    if not filename.lower().endswith(".zip"):
        raise CodingError("请上传 ZIP 格式的 Python 项目", "CODING_ZIP_REQUIRED")
    if not raw or len(raw) > MAX_ARCHIVE_BYTES:
        raise CodingError("项目 ZIP 大小应在 1 字节至 5 MiB 之间", "CODING_ARCHIVE_LIMIT", 413)
    files, ignored, expanded, seen = {}, 0, 0, set()
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
                raise CodingError("压缩包为空或包含过多条目", "CODING_ARCHIVE_LIMIT", 413)
            validated = []
            for info in entries:
                # orig_filename exposes NULs that ZipInfo.filename silently truncates.
                if info.orig_filename != info.filename:
                    raise CodingError("压缩包路径包含无效字符", "CODING_UNSAFE_PATH")
                path = validate_path(info.filename[:-1] if info.is_dir() else info.filename)
                key = path.casefold()
                if key in seen:
                    raise CodingError("压缩包包含重复或大小写冲突的路径", "CODING_DUPLICATE_PATH")
                seen.add(key)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR} or (mode == stat.S_IFDIR and not info.is_dir()):
                    raise CodingError("压缩包不能包含符号链接或特殊文件", "CODING_UNSAFE_ARCHIVE")
                if info.flag_bits & 1 or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    raise CodingError("不支持加密压缩包或此压缩方式", "CODING_UNSAFE_ARCHIVE")
                expanded += info.file_size
                if (info.file_size > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES
                        or info.file_size > max(1024, info.compress_size) * 100):
                    raise CodingError("压缩包展开大小或压缩比例超出限制", "CODING_ARCHIVE_LIMIT", 413)
                validated.append((info, path))
            paths = [path for info, path in validated if not info.is_dir()]
            file_paths = {path.casefold() for path in paths}
            for _, path in validated:
                components = path.casefold().split("/")
                if any("/".join(components[:index]) in file_paths for index in range(1, len(components))):
                    raise CodingError("压缩包中的文件名与目录名冲突", "CODING_DUPLICATE_PATH")
            first_parts = {path.split("/")[0] for path in paths}
            root = next(iter(first_parts)) + "/" if len(first_parts) == 1 and all("/" in path for path in paths) else ""
            # Preserve an actual Python package root; only unwrap a container folder.
            if root and (root + "__init__.py").casefold() in file_paths:
                root = ""
            for info, path in validated:
                # Even excluded entries are read with a strict bound and CRC validation.
                with archive.open(info) as member:
                    data = member.read(MAX_MEMBER_BYTES + 1)
                if len(data) != info.file_size or len(data) > MAX_MEMBER_BYTES:
                    raise CodingError("压缩包成员大小异常", "CODING_UNSAFE_ARCHIVE")
                if info.is_dir():
                    continue
                original = path
                path = path[len(root):] if root else path
                if not allowed_source(original) or not allowed_source(path):
                    ignored += 1
                    continue
                if len(data) > MAX_FILE_BYTES:
                    raise CodingError("单个源码文件不能超过 128 KiB", "CODING_FILE_LIMIT", 413)
                try:
                    content = data.decode("utf-8-sig")
                except UnicodeDecodeError as error:
                    raise CodingError("源码必须使用 UTF-8 编码，请转换后重新上传", "CODING_INVALID_ENCODING") from error
                files[path] = content
            validate_files(files)
    except (BadZipFile, LargeZipFile, OSError, RuntimeError, NotImplementedError, EOFError, ValueError, zlib.error) as error:
        raise CodingError("ZIP 无法读取，请重新生成未加密的标准 ZIP 文件", "CODING_INVALID_ZIP") from error
    if not files or not any(path.lower().endswith(".py") for path in files):
        raise CodingError("此版本需要至少包含一个 .py 文件的 Python 项目", "CODING_PYTHON_REQUIRED", 422)
    name = PurePosixPath(filename.replace("\\", "/")).stem
    name = " ".join(name.split())[:80] or "Python 项目"
    warnings = [f"已排除 {ignored} 个依赖、凭证或非源码文件；导入前仍请检查源码内是否含有密钥。"] if ignored else []
    return {"name": name, "files": files, "warnings": warnings}


def export_project_zip(files: dict[str, str]) -> bytes:
    validate_files(files)
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            archive.writestr(path, content.encode("utf-8"))
    return output.getvalue()
