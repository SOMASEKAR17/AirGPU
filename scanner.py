import ast
import re
from typing import List, Dict

DANGEROUS_CALL_PATTERNS = [
    ("eval", "dynamic code evaluation via eval()"),
    ("exec", "dynamic code execution via exec()"),
    ("compile", "dynamic code compilation"),
    ("__import__", "dynamic import — possible obfuscation"),
    ("breakpoint", "debugger breakpoint — not allowed in submitted jobs"),
]

DANGEROUS_ATTRIBUTE_CALLS = [
    ("os", "system", "shell command execution via os.system()"),
    ("os", "popen", "shell pipe via os.popen()"),
    ("os", "execv", "process replacement via os.execv()"),
    ("os", "execve", "process replacement via os.execve()"),
    ("os", "execvp", "process replacement via os.execvp()"),
    ("os", "spawnl", "process spawning via os.spawnl()"),
    ("os", "spawnle", "process spawning via os.spawnle()"),
    ("os", "spawnv", "process spawning"),
    ("os", "fork", "process forking — fork bomb risk"),
    ("os", "forkpty", "process forking"),
    ("os", "kill", "process kill signal"),
    ("os", "killpg", "process group kill signal"),
    ("subprocess", "call", "shell subprocess execution"),
    ("subprocess", "run", "shell subprocess execution"),
    ("subprocess", "Popen", "shell subprocess with Popen"),
    ("subprocess", "check_output", "shell subprocess check_output"),
    ("subprocess", "check_call", "shell subprocess check_call"),
    ("subprocess", "getoutput", "shell subprocess getoutput"),
    ("subprocess", "getstatusoutput", "shell subprocess getstatusoutput"),
    ("ctypes", "CDLL", "loading native library via ctypes"),
    ("ctypes", "cdll", "loading native library via ctypes"),
    ("ctypes", "windll", "loading Windows DLL"),
    ("socket", "connect", "raw network connection"),
    ("socket", "bind", "raw socket bind"),
    ("urllib", "urlopen", "outbound URL request"),
    ("requests", "get", "outbound HTTP GET request"),
    ("requests", "post", "outbound HTTP POST request"),
    ("requests", "put", "outbound HTTP PUT request"),
    ("requests", "delete", "outbound HTTP DELETE request"),
    ("httpx", "get", "outbound HTTP request via httpx"),
    ("httpx", "post", "outbound HTTP request via httpx"),
    ("aiohttp", "get", "outbound HTTP request via aiohttp"),
]

DANGEROUS_STRING_PATTERNS = [
    (r"rm\s+-rf\s*/", "rm -rf shell command in string literal"),
    (r"curl\s+https?://", "curl outbound request in string literal"),
    (r"wget\s+https?://", "wget outbound request in string literal"),
    (r"nc\s+-[a-z]*e", "netcat reverse shell pattern"),
    (r"bash\s+-i\s+>&", "bash reverse shell pattern"),
    (r"/etc/passwd", "reading /etc/passwd"),
    (r"/etc/shadow", "reading /etc/shadow"),
    (r"\.ssh/id_rsa", "reading SSH private key"),
    (r"chmod\s+[0-9]+\s+/", "chmod on system path"),
    (r"base64\s+-d", "base64 decode in shell string"),
    (r"python\s+-c\s+['\"]", "embedded python -c execution"),
    (r"xmrig|cryptominer|stratum\+tcp", "crypto mining reference"),
]

SUSPICIOUS_OPEN_PATHS = [
    "/etc/", "/proc/", "/sys/", "/dev/", "/root/",
    "~/.ssh", "/var/", "/usr/", "/bin/", "/sbin/",
]

class ScanResult:
    def __init__(self):
        self.passed = True
        self.violations: List[Dict] = []
        self.warnings: List[Dict] = []
        self.risk_score: int = 0

    def add_violation(self, line: int, message: str, risk: int = 10):
        self.passed = False
        self.risk_score += risk
        self.violations.append({"line": line, "message": message})

    def add_warning(self, line: int, message: str, risk: int = 3):
        self.risk_score += risk
        self.warnings.append({"line": line, "message": message})

def _get_call_name(node: ast.Call):
    if isinstance(node.func, ast.Name):
        return node.func.id, None, node.func.id
    if isinstance(node.func, ast.Attribute):
        obj = None
        if isinstance(node.func.value, ast.Name):
            obj = node.func.value.id
        return obj, node.func.attr, f"{obj}.{node.func.attr}"
    return None, None, None

def scan_dangerous_calls(tree: ast.AST, result: ScanResult):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        obj, attr, full = _get_call_name(node)

        if obj is None and attr is None and full:
            for fname, desc in DANGEROUS_CALL_PATTERNS:
                if full == fname:
                    result.add_violation(node.lineno, f"Dangerous call: {desc}", risk=15)

        if obj and attr:
            for dobj, dattr, desc in DANGEROUS_ATTRIBUTE_CALLS:
                if obj == dobj and attr == dattr:
                    result.add_violation(node.lineno, f"Dangerous call: {desc}", risk=15)

def scan_open_calls(tree: ast.AST, result: ScanResult):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        obj, attr, full = _get_call_name(node)
        is_open = (full == "open") or (attr == "open")
        if not is_open:
            continue
        if node.args:
            first = node.args[0]
            path_str = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                path_str = first.value
            if path_str:
                for bad_path in SUSPICIOUS_OPEN_PATHS:
                    if path_str.startswith(bad_path):
                        result.add_violation(
                            node.lineno,
                            f"Reading from restricted system path: {path_str}",
                            risk=20
                        )
                if len(node.args) >= 2 or any(kw.arg == "mode" for kw in node.keywords):
                    mode_val = None
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        mode_val = node.args[1].value
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode_val = kw.value.value
                    if mode_val and ("w" in str(mode_val) or "a" in str(mode_val)):
                        if path_str and not path_str.startswith("/app"):
                            result.add_warning(
                                node.lineno,
                                f"Writing to path outside /app: {path_str}"
                            )

def scan_string_literals(tree: ast.AST, result: ScanResult):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for pattern, desc in DANGEROUS_STRING_PATTERNS:
                if re.search(pattern, node.value, re.IGNORECASE):
                    result.add_violation(
                        node.lineno,
                        f"Suspicious string literal: {desc}",
                        risk=12
                    )

def scan_obfuscation(tree: ast.AST, result: ScanResult):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if len(val) > 200 and re.match(r'^[A-Za-z0-9+/=]+$', val.strip()):
                result.add_warning(
                    node.lineno,
                    "Long base64-like string — possible encoded payload"
                )
            if len(val) > 100 and all(ord(c) > 127 for c in val[:20] if c.strip()):
                result.add_warning(
                    node.lineno,
                    "Non-ASCII heavy string — possible obfuscated content"
                )

        if isinstance(node, ast.Lambda):
            nested = sum(1 for child in ast.walk(node) if isinstance(child, ast.Lambda))
            if nested > 3:
                result.add_warning(node.lineno, "Deeply nested lambdas — possible obfuscation")

def scan_environment_access(tree: ast.AST, result: ScanResult):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        obj, attr, full = _get_call_name(node)
        if (obj == "os" and attr == "environ") or full == "os.environ":
            result.add_warning(node.lineno, "Reading environment variables — may access secrets")
        if obj == "os" and attr == "getenv":
            result.add_warning(node.lineno, "Reading environment variable via os.getenv()")

def scan_code(code: str) -> ScanResult:
    result = ScanResult()
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        result.add_violation(e.lineno or 0, f"Syntax error: {e.msg}", risk=5)
        return result

    scan_dangerous_calls(tree, result)
    scan_open_calls(tree, result)
    scan_string_literals(tree, result)
    scan_obfuscation(tree, result)
    scan_environment_access(tree, result)

    return result

def format_scan_result(result: ScanResult) -> str:
    if result.passed and not result.warnings:
        return "✓ Code scan passed — no security issues found"
    if result.passed:
        lines = [f"⚠ Code scan passed with {len(result.warnings)} warning(s)\n"]
        for w in result.warnings:
            lines.append(f"  Line {w['line']}: {w['message']}")
        return "\n".join(lines)
    lines = [f"✗ Code scan failed — {len(result.violations)} violation(s) found\n"]
    for v in result.violations:
        lines.append(f"  Line {v['line']}: {v['message']}")
    if result.warnings:
        lines.append(f"\nWarnings ({len(result.warnings)}):")
        for w in result.warnings:
            lines.append(f"  Line {w['line']}: {w['message']}")
    return "\n".join(lines)
