"""
Code Scanner — AST-based static analysis + pattern matching
for submitted Python scripts before execution.
"""

import ast
import re
from typing import List, Dict


BANNED_IMPORTS = [
    "os.system",
    "subprocess",
    "socket",
    "ftplib",
    "telnetlib",
    "paramiko",
    "fabric",
    "ctypes",
    "pickle",
    "shelve",
    "multiprocessing",
    "threading",
    "shutil",
    "pathlib",
    "glob",
    "importlib",
    "pkgutil",
    "pty",
    "tty",
    "termios",
    "signal",
    "mmap",
]


BANNED_CALLS = [
    "os.system",
    "os.popen",
    "os.spawn",
    "os.exec",
    "os.fork",
    "os.kill",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.environ",
    "os.getenv",
    "builtins.eval",
    "builtins.exec",
    "builtins.compile",
    "builtins.__import__",
]


BANNED_BUILTINS = [
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "breakpoint",
]


BANNED_PATTERNS = [
    (r"os\.environ", "accessing environment variables"),
    (r"os\.system\s*\(", "shell command execution"),
    (r"subprocess\.", "subprocess execution"),
    (r"__import__\s*\(", "dynamic import"),
    (r"eval\s*\(", "eval() call"),
    (r"exec\s*\(", "exec() call"),
    (r"open\s*\(.*['\"]w['\"]", "writing to filesystem"),
    (r"open\s*\(.*['\"]a['\"]", "appending to filesystem"),
    (r"base64\.b64decode", "base64 decoded payload"),
    (r"chr\s*\(\s*\d+\s*\)\s*\+", "character code obfuscation"),
    (r"\\x[0-9a-fA-F]{2}", "hex encoded obfuscation"),
    (r"while\s+True.*os\.fork", "fork bomb pattern"),
    (r"lambda.*lambda.*lambda", "deeply nested lambda obfuscation"),
    (r"getattr\s*\(.*['\"]system['\"]", "getattr shell access"),
    (r"globals\s*\(\s*\)", "globals() access"),
    (r"locals\s*\(\s*\)", "locals() access"),
    (r"vars\s*\(\s*\)", "vars() access"),
    (r"\bsocket\b", "raw socket access"),
    (r"requests\.get\s*\(.*http", "outbound HTTP request"),
    (r"requests\.post\s*\(.*http", "outbound HTTP POST"),
    (r"urllib\.request", "outbound URL request"),
    (r"http\.client", "raw HTTP client"),
    (r"cryptominer|xmrig|monero|bitcoin.*mine", "crypto mining keywords"),
]


ALLOWED_IMPORTS = {
    
    "math", "cmath", "decimal", "fractions", "random", "statistics",
    "numpy", "scipy", "sympy",
    
    "pandas", "csv", "json", "re", "string", "textwrap",
    
    "torch", "tensorflow", "keras", "sklearn", "sklearn.linear_model",
    "sklearn.ensemble", "sklearn.metrics", "sklearn.preprocessing",
    "sklearn.model_selection", "xgboost", "lightgbm", "transformers",
    "datasets", "tokenizers", "accelerate", "diffusers",
    
    "matplotlib", "matplotlib.pyplot", "seaborn", "plotly",
    
    "time", "datetime", "itertools", "functools", "collections",
    "typing", "dataclasses", "enum", "abc", "copy", "pprint",
    "hashlib", "hmac", "uuid", "struct", "io", "sys",
    
    "PIL", "PIL.Image", "cv2", "imageio", "skimage",
    
    "nltk", "spacy", "gensim",
    
    "pathlib.Path",
    
    "pprint", "traceback", "logging", "warnings",
}


class ScanResult:
    def __init__(self):
        self.passed = True
        self.violations: List[Dict] = []
        self.warnings: List[Dict] = []

    def add_violation(self, line: int, message: str, severity: str = "error"):
        self.passed = False
        self.violations.append({
            "line": line,
            "message": message,
            "severity": severity
        })

    def add_warning(self, line: int, message: str):
        self.warnings.append({
            "line": line,
            "message": message,
            "severity": "warning"
        })


def scan_imports(tree: ast.AST, result: ScanResult):
    """Check all imports against allowed list and banned list."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                full_module = alias.name
                if full_module in BANNED_IMPORTS or module in BANNED_IMPORTS:
                    result.add_violation(
                        node.lineno,
                        f"Banned import: '{full_module}'"
                    )
                elif module not in ALLOWED_IMPORTS and full_module not in ALLOWED_IMPORTS:
                    result.add_violation(
                        node.lineno,
                        f"Import not in allowlist: '{full_module}' — add it to requirements.txt and ensure it is a trusted package"
                    )

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            base_module = module.split(".")[0]
            if module in BANNED_IMPORTS or base_module in BANNED_IMPORTS:
                result.add_violation(
                    node.lineno,
                    f"Banned import: 'from {module}'"
                )
            elif base_module not in ALLOWED_IMPORTS and module not in ALLOWED_IMPORTS:
                result.add_violation(
                    node.lineno,
                    f"Import not in allowlist: 'from {module}' — ensure it is a trusted package"
                )


def scan_function_calls(tree: ast.AST, result: ScanResult):
    """Check for banned built-in calls and dangerous attribute access."""
    for node in ast.walk(tree):
        
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in BANNED_BUILTINS:
                    result.add_violation(
                        node.lineno,
                        f"Banned function call: '{node.func.id}()'"
                    )
            
            elif isinstance(node.func, ast.Attribute):
                call_str = f"{getattr(node.func.value, 'id', '')}."                           f"{node.func.attr}"
                for banned in BANNED_CALLS:
                    if call_str in banned or banned.endswith(node.func.attr):
                        result.add_violation(
                            node.lineno,
                            f"Banned call: '{call_str}'"
                        )


def scan_patterns(code: str, result: ScanResult):
    """Regex-based pattern scanning on raw source code."""
    lines = code.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  
        for pattern, description in BANNED_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                result.add_violation(
                    i,
                    f"Suspicious pattern detected: {description}"
                )
                break  


def scan_complexity(tree: ast.AST, result: ScanResult):
    """Warn about suspiciously complex or obfuscated structures."""
    for node in ast.walk(tree):
        
        if isinstance(node, ast.Lambda):
            depth = sum(
                1 for child in ast.walk(node)
                if isinstance(child, ast.Lambda)
            )
            if depth > 2:
                result.add_violation(
                    node.lineno,
                    "Deeply nested lambdas — possible obfuscation"
                )
        


def scan_code(code: str) -> ScanResult:
    """
    Main entry point. Runs all scan passes on submitted code.
    Returns a ScanResult with passed=True if safe, False if violations found.
    """
    result = ScanResult()

    
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        result.add_violation(
            e.lineno or 0,
            f"Syntax error: {e.msg}"
        )
        return result

    
    scan_imports(tree, result)

    
    scan_function_calls(tree, result)

    
    scan_patterns(code, result)

    
    scan_complexity(tree, result)

    return result


def format_scan_result(result: ScanResult) -> str:
    """Format scan result as a human readable string for the submitter UI."""
    if result.passed:
        return "✓ Code scan passed — no security violations found"

    lines = ["✗ Code scan failed — job rejected\n"]
    for v in result.violations:
        lines.append(f"  Line {v['line']}: {v['message']}")
    if result.warnings:
        lines.append("\nWarnings:")
        for w in result.warnings:
            lines.append(f"  Line {w['line']}: {w['message']}")
    return "\n".join(lines)
