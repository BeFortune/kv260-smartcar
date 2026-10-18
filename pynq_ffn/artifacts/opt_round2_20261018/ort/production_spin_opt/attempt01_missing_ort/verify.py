"""Record local CPU/offline production policy checks with actual exit codes."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    before = json.loads((EVIDENCE / "before.json").read_text(encoding="utf8"))
    old_source = (EVIDENCE / "runtime_before.py").read_text(encoding="utf8")
    source = (ROOT / "ps/runtime.py").read_text(encoding="utf8")
    old_class = next(n for n in ast.parse(old_source).body if isinstance(n, ast.ClassDef) and n.name == "Runtime")
    new_class = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "Runtime")
    old_run = next(n for n in old_class.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    new_run = next(n for n in new_class.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    assert ast.dump(old_run) == ast.dump(new_run), "Runtime.run changed"
    for name in ("tools/ort_thread_study.py", "tests/test_ort_thread_study.py"):
        assert sha(ROOT / name) == before["files"][name], f"Frozen file changed: {name}"
    (EVIDENCE / "runtime.diff").write_text("".join(difflib.unified_diff(old_source.splitlines(True), source.splitlines(True), fromfile="before/ps/runtime.py", tofile="after/ps/runtime.py")), encoding="utf8")
    old_test = (EVIDENCE / "test_runtime_thread_policy_before.py").read_text(encoding="utf8")
    new_test = (ROOT / "tests/test_runtime_thread_policy.py").read_text(encoding="utf8")
    (EVIDENCE / "tests.diff").write_text("".join(difflib.unified_diff(old_test.splitlines(True), new_test.splitlines(True), fromfile="before/tests/test_runtime_thread_policy.py", tofile="after/tests/test_runtime_thread_policy.py")), encoding="utf8")
    stages = [
        ("syntax", [sys.executable, "-c", "import ast; from pathlib import Path; [ast.parse(Path(p).read_text(encoding='utf8'), filename=p) for p in ('ps/runtime.py', 'tests/test_runtime_thread_policy.py')]; print('AST syntax checks passed')"]),
        ("new_tests", [sys.executable, "-m", "pytest", "-q", "tests/test_runtime_thread_policy.py", "--junitxml=" + str(EVIDENCE / "new_tests.xml")]),
        ("old_tests", [sys.executable, "-m", "pytest", "-q", "tests", "--ignore=tests/test_runtime_thread_policy.py", "--junitxml=" + str(EVIDENCE / "old_tests.xml")]),
        ("all_tests", [sys.executable, "-m", "pytest", "-q", "tests", "--junitxml=" + str(EVIDENCE / "all_tests.xml")]),
    ]
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "execution_machine": "local Windows CPU", "shell": "Git Bash launching Python subprocesses", "cwd": str(ROOT),
        "python": sys.version, "python_executable": sys.executable, "platform": platform.platform(),
        "runtime_sha256_before_this_task": before["files"]["ps/runtime.py"],
        "runtime_sha256_after_this_task": sha(ROOT / "ps/runtime.py"),
        "test_sha256": sha(ROOT / "tests/test_runtime_thread_policy.py"),
        "frozen_study_sha256_unchanged": sha(ROOT / "tools/ort_thread_study.py"),
        "runtime_run_ast_unchanged": True,
        "starting_state": "spinning keyword and behavior already present; added requested type annotation and replaced bundle-dependent tests with synthetic bundle/mocked sessions",
        "scope": "CPU offline tests only; no PYNQ import in new tests, no device/serial/camera/control/hardware actions",
        "not_executed": ["remote optional validation", "board PL computation", "performance benchmark"],
        "stages": [],
    }
    for name, command in stages:
        started = datetime.now(timezone.utc)
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf8", errors="replace")
        (EVIDENCE / (name + ".log")).write_text("command: " + subprocess.list2cmdline(command) + "\ncwd: " + str(ROOT) + "\n" + result.stdout + "\nexit_code: " + str(result.returncode) + "\n", encoding="utf8")
        report["stages"].append({"name": name, "argv": command, "exit_code": result.returncode, "status": "已验证" if result.returncode == 0 else "失败", "elapsed_seconds": (datetime.now(timezone.utc) - started).total_seconds(), "log": str(EVIDENCE / (name + ".log"))})
        report["status"] = "已验证" if result.returncode == 0 else "失败"
        (EVIDENCE / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        print(name, "exit_code=" + str(result.returncode), flush=True)
        print(result.stdout[-1800:], flush=True)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
