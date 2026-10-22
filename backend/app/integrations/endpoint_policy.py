"""Pure endpoint validation; no settings, credentials, DNS or HTTP side effects."""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


_BLOCKED_SUFFIXES = (
    "localhost", "local", "internal", "lan", "home", "test", "invalid",
    "example", "onion", "arpa",
)
_TRANSITION_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16",
))


def _public_ip(value: str) -> bool:
    try:
        if "%" in value:
            return False
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        return False
    if not address.is_global or any((address.is_private, address.is_loopback,
            address.is_link_local, address.is_multicast, address.is_reserved,
            address.is_unspecified)):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or any(address in network for network in _TRANSITION_NETWORKS):
            return False
    return True


def normalize_base_url(value: str) -> str:
    """Normalize a base endpoint without I/O or echoing potentially secret URLs."""
    if not isinstance(value, str):
        raise ValueError("Base URL 必须是 HTTPS 地址")
    value = value.strip()
    if not value or len(value) > 2048 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Base URL 为空、过长或包含不允许的字符")
    if any(char in value for char in ("\\", "%", "?", "#")):
        raise ValueError("Base URL 不能包含反斜杠、编码转义、查询参数或片段")
    try:
        parsed = urlsplit(value)
        hostname, port = parsed.hostname, parsed.port
        if parsed.scheme.lower() != "https" or not hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
            raise ValueError()
        hostname = hostname.encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        raise ValueError("Base URL 必须是有效的 HTTPS 地址，且不能包含账号或密码") from None

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if (len(labels) < 2 or len(hostname) > 253
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or not re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]{2,59}", labels[-1])
                or any(hostname == suffix or hostname.endswith("." + suffix) for suffix in _BLOCKED_SUFFIXES)):
            raise ValueError("Base URL 只允许公开服务域名，不能使用本机或内网地址") from None
        authority = hostname
    else:
        if not _public_ip(str(address)):
            raise ValueError("Base URL 不能指向本机、内网或保留 IP 地址")
        authority = "[" + address.compressed + "]" if address.version == 6 else address.compressed

    path = parsed.path.rstrip("/")
    if (not re.fullmatch(r"[A-Za-z0-9._~/-]*", path) or "//" in path
            or any(part in (".", "..") for part in path.split("/"))):
        raise ValueError("Base URL 路径包含不支持的字符或目录跳转")
    if path.lower().endswith("/chat/completions"):
        raise ValueError("请填写接口基础地址（例如以 /v1 结尾），不要包含 /chat/completions")
    if port is not None and port != 443:
        authority += ":" + str(port)
    return urlunsplit(("https", authority, path, "", ""))


def normalize_proxy_settings(proxy_url: str, base_urls: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Operator-only, loopback HTTP CONNECT proxy and exact trusted model bases.

    Proxy-side DNS is a deliberate trust boundary: the operator must trust both
    the proxy and these destinations. This is NOT public-IP pinning via a proxy.
    Never construct these options from an HTTP request or user model settings.
    """
    if not isinstance(proxy_url, str) or not isinstance(base_urls, tuple):
        raise ValueError("自定义模型代理配置格式错误")
    proxy_url = proxy_url.strip()
    if not proxy_url and not base_urls:
        return "", ()
    if not proxy_url or not base_urls:
        raise ValueError("CUSTOM_MODEL_PROXY_URL 与 CUSTOM_MODEL_PROXY_BASE_URLS 必须同时填写或同时留空")
    try:
        if (len(proxy_url) > 256 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in proxy_url)
                or any(c in proxy_url for c in ("\\", "%", "?", "#"))):
            raise ValueError()
        parsed = urlsplit(proxy_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.port is None or not 1 <= parsed.port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError()
        host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
        proxy = f"http://{host}:{parsed.port}"
    except (ValueError, UnicodeError):
        raise ValueError("CUSTOM_MODEL_PROXY_URL 仅支持本机 HTTP/Mixed 代理，如 http://127.0.0.1:7897；不支持凭据、路径、SOCKS 或远程代理") from None
    try:
        if len(base_urls) > 32:
            raise ValueError()
        bases = tuple(dict.fromkeys(normalize_base_url(item) for item in base_urls))
    except (ValueError, TypeError):
        raise ValueError("CUSTOM_MODEL_PROXY_BASE_URLS 须为最多 32 个完整公网 HTTPS 基础地址，以英文逗号分隔；不支持通配符、凭据或 /chat/completions") from None
    return proxy, bases
