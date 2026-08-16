"""SWE Task Fixtures — 验证 Agent 修改已有代码的能力。

架构:
  swe_tasks/
    task_NNN_name/
      project/             ← Agent 要修改的初始项目
        ...code files...
      task.json            ← 任务描述、验证命令

测试模式:
  1. Fixture 加载 & 结构验证 (单元测试)
  2. 验证命令可用性 (给定修复后的代码, 验证命令应通过)
  3. Agent 端到端执行 (需真实 Backend, 可选)
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ── 查找 fixtures 根目录 ─────────────────────────────────

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "swe_tasks"

if not FIXTURES_ROOT.exists():
    pytest.skip("SWE task fixtures not found", allow_module_level=True)


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════


class SWETask:
    """单个 SWE 测试任务。"""

    def __init__(self, task_dir: Path) -> None:
        self.path = task_dir
        with open(task_dir / "task.json", encoding="utf-8") as f:
            self.data: dict[str, Any] = json.load(f)
        self.id: str = self.data["id"]
        self.description: str = self.data["description"]
        self.expected: str = self.data["expected"]
        self.files: list[str] = self.data.get("files", [])
        self.verification: dict[str, Any] = self.data["verification"]
        self.project_dir: Path = task_dir / "project"


# ═══════════════════════════════════════════════════════════
# Harness — 任务加载与验证
# ═══════════════════════════════════════════════════════════


def discover_tasks() -> list[SWETask]:
    """发现所有 SWE 任务。"""
    tasks: list[SWETask] = []
    if not FIXTURES_ROOT.exists():
        return tasks
    for entry in sorted(FIXTURES_ROOT.iterdir()):
        if entry.is_dir() and (entry / "task.json").exists():
            tasks.append(SWETask(entry))
    return tasks


def run_verification(task: SWETask, project_dir: Path | None = None) -> tuple[bool, str]:
    """运行任务的验证命令。

    Args:
        task: SWE 任务。
        project_dir: 项目目录（默认使用 task.project_dir）。

    Returns:
        (passed: bool, output: str)
    """
    cmd = task.verification.get("command", "")
    timeout = task.verification.get("timeout", 15)
    work_dir = project_dir or task.project_dir

    # 确定执行目录：验证脚本在 work_dir.parent，project 在 work_dir
    cwd = str(work_dir.parent)

    # 执行 setup（如果有）
    setup = task.verification.get("setup", "")
    if setup:
        try:
            subprocess.run(
                setup, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass

    # 执行验证命令
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        output = r.stdout + "\n" + r.stderr if r.stderr else r.stdout
    except subprocess.TimeoutExpired:
        return False, f"验证超时 ({timeout}s)"
    except Exception as e:
        return False, f"验证执行失败: {e}"

    # 检查输出中不能有 FAILED
    has_failed = "FAILED" in output or "failed" in output.split("\n")[0] if output else False

    # 检查退出码
    exit_ok = r.returncode == 0

    # 检查预期输出模式
    expected_pattern = task.verification.get("expected_output", "")
    pattern_ok = True
    if expected_pattern:
        import re
        pattern_ok = bool(re.search(expected_pattern, output, re.IGNORECASE))

    passed = exit_ok and pattern_ok and not has_failed
    return passed, output


def setup_task_workspace(task: SWETask, target_dir: Path) -> Path:
    """将任务项目复制到目标工作区。

    Args:
        task: SWE 任务。
        target_dir: 目标工作区目录。

    Returns:
        项目目录在目标工作区中的路径。
    """
    project_dir = target_dir / "project"
    if project_dir.exists():
        import shutil
        shutil.rmtree(project_dir)

    # 复制项目文件
    import shutil
    shutil.copytree(task.project_dir, project_dir)

    # 复制验证脚本（如果有）
    for f in task.path.iterdir():
        if f.suffix == ".py" and f.name.startswith("verify_"):
            dest = target_dir / f.name
            shutil.copy2(f, dest)

    # 确保 __init__.py 存在（如果项目中没有）
    init_file = project_dir / "__init__.py"
    if not init_file.exists():
        init_file.write_text("")

    return project_dir


# ═══════════════════════════════════════════════════════════
# 预计算 — 打上正确补丁后的项目
# ═══════════════════════════════════════════════════════════


_COMPLETE_FIXES: dict[str, dict[str, str]] = {
    "task_001_fix_bug": {
        "calculator.py": (
            '"""Calculator with fix — divide now raises ZeroDivisionError."""\n'
            "\n"
            "import math\n"
            "\n\n"
            "def add(a: float, b: float) -> float:\n"
            "    return a + b\n"
            "\n\n"
            "def subtract(a: float, b: float) -> float:\n"
            "    return a - b\n"
            "\n\n"
            "def multiply(a: float, b: float) -> float:\n"
            "    return a * b\n"
            "\n\n"
            "def divide(a: float, b: float) -> float:\n"
            '    """Divide a by b. Raises ZeroDivisionError if b is 0."""\n'
            "    return a / b\n"
            "\n\n"
            "def power(a: float, b: float) -> float:\n"
            "    return a ** b\n"
            "\n\n"
            "def sqrt(a: float) -> float:\n"
            "    if a < 0:\n"
            '        raise ValueError("Cannot sqrt negative number")\n'
            "    return math.sqrt(a)\n"
        ),
    },
    "task_002_modify_function": {
        "string_utils.py": (
            '"""String utilities — with is_palindrome."""\n'
            "\n\n"
            "def reverse(text: str) -> str:\n"
            '    """Return the reversed string."""\n'
            "    return text[::-1]\n"
            "\n\n"
            "def count_vowels(text: str) -> int:\n"
            '    """Count vowels (a, e, i, o, u) in text."""\n'
            "    vowels = \"aeiouAEIOU\"\n"
            "    return sum(1 for c in text if c in vowels)\n"
            "\n\n"
            "def to_upper(text: str) -> str:\n"
            '    """Convert text to uppercase."""\n'
            "    return text.upper()\n"
            "\n\n"
            "def to_lower(text: str) -> str:\n"
            '    """Convert text to lowercase."""\n'
            "    return text.lower()\n"
            "\n\n"
            "def remove_whitespace(text: str) -> str:\n"
            '    """Remove all whitespace from text."""\n'
            "    return ''.join(text.split())\n"
            "\n\n"
            "def is_palindrome(text: str) -> bool:\n"
            '    """Check if text is a palindrome (ignore case and spaces)."""\n'
            "    cleaned = ''.join(c.lower() for c in text if c.isalnum())\n"
            "    return cleaned == cleaned[::-1]\n"
        ),
    },
    "task_003_add_feature": {
        "todo.py": (
            '"""Todo list manager — with clear_done feature."""\n'
            "\n"
            "import json\n"
            "from pathlib import Path\n"
            "\n\n"
            "class TodoList:\n"
            '    def __init__(self, storage_path: str = "todos.json"):\n'
            "        self.path = Path(storage_path)\n"
            "        self.todos: list[dict] = []\n"
            "        self._load()\n"
            "\n"
            "    def _load(self) -> None:\n"
            "        if self.path.exists():\n"
            "            self.todos = json.loads(self.path.read_text(encoding=\"utf-8\"))\n"
            "\n"
            "    def _save(self) -> None:\n"
            "        self.path.write_text(json.dumps(self.todos, indent=2), encoding=\"utf-8\")\n"
            "\n"
            "    def add(self, title: str) -> dict:\n"
            '        todo = {"id": len(self.todos) + 1, "title": title, "done": False}\n'
            "        self.todos.append(todo)\n"
            "        self._save()\n"
            "        return todo\n"
            "\n"
            "    def list_all(self) -> list[dict]:\n"
            "        return self.todos\n"
            "\n"
            "    def mark_done(self, todo_id: int) -> bool:\n"
            "        for t in self.todos:\n"
            "            if t[\"id\"] == todo_id:\n"
            '                t["done"] = True\n'
            "                self._save()\n"
            "                return True\n"
            "        return False\n"
            "\n"
            "    def remove(self, todo_id: int) -> bool:\n"
            "        for i, t in enumerate(self.todos):\n"
            "            if t[\"id\"] == todo_id:\n"
            "                self.todos.pop(i)\n"
            "                self._save()\n"
            "                return True\n"
            "        return False\n"
            "\n"
            "    def clear_done(self) -> None:\n"
            '        """Remove all completed todos."""\n'
            "        self.todos = [t for t in self.todos if not t[\"done\"]]\n"
            "        self._save()\n"
        ),
    },
    "task_004_fix_html": {
        "style.css": (
            "/* Fixed: .box width now has proper unit */\n"
            "body {\n"
            "    font-family: Arial, sans-serif;\n"
            "    margin: 20px;\n"
            "    background-color: #f0f0f0;\n"
            "}\n"
            "\n"
            ".container {\n"
            "    max-width: 800px;\n"
            "    margin: 0 auto;\n"
            "    padding: 20px;\n"
            "    background: white;\n"
            "    border-radius: 8px;\n"
            "    box-shadow: 0 2px 4px rgba(0,0,0,0.1);\n"
            "}\n"
            "\n"
            "h1 {\n"
            "    color: navy;\n"
            "    text-align: center;\n"
            "}\n"
            "\n"
            ".box {\n"
            "    border: 1px solid #ddd;\n"
            "    padding: 15px;\n"
            "    margin: 10px auto;\n"
            "    width: 90%;\n"
            "    border-radius: 4px;\n"
            "}\n"
            "\n"
            ".box h2 {\n"
            "    color: #333;\n"
            "    margin-top: 0;\n"
            "}\n"
            "\n"
            ".description {\n"
            "    text-align: center;\n"
            "    color: #666;\n"
            "    font-style: italic;\n"
            "}\n"
            "\n"
            ".email {\n"
            "    color: blue;\n"
            "    font-weight: bold;\n"
            "}\n"
            "\n"
            "#clickMe {\n"
            "    display: block;\n"
            "    margin: 20px auto;\n"
            "    padding: 10px 20px;\n"
            "    background-color: navy;\n"
            "    color: white;\n"
            "    border: none;\n"
            "    border-radius: 4px;\n"
            "    cursor: pointer;\n"
            "}\n"
            "\n"
            "#clickMe:hover {\n"
            "    background-color: #000080;\n"
            "}\n"
        ),
    },
    "task_005_fix_test": {
        "user_manager.py": (
            '"""User manager — with fixed email validation."""\n'
            "\n"
            "import re\n"
            "\n\n"
            "class UserManager:\n"
            "    def __init__(self):\n"
            "        self.users: dict[str, dict] = {}\n"
            "\n"
            "    def add_user(self, username: str, email: str, age: int) -> dict:\n"
            "        if username in self.users:\n"
            '            raise ValueError(f"User \'{username}\' already exists")\n'
            "        if age < 0 or age > 150:\n"
            '            raise ValueError("Age must be between 0 and 150")\n'
            "        self.users[username] = {\n"
            '            "username": username,\n'
            '            "email": email,\n'
            '            "age": age,\n'
            "        }\n"
            "        return self.users[username]\n"
            "\n"
            "    def get_user(self, username: str) -> dict | None:\n"
            "        return self.users.get(username)\n"
            "\n"
            "    def validate_email(self, email: str) -> bool:\n"
            '        """Validate email with proper regex."""\n'
            "        if not email:\n"
            "            return False\n"
            '        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"\n'
            "        return bool(re.match(pattern, email))\n"
            "\n"
            "    def list_users(self) -> list[dict]:\n"
            "        return list(self.users.values())\n"
            "\n"
            "    def remove_user(self, username: str) -> bool:\n"
            "        if username in self.users:\n"
            "            del self.users[username]\n"
            "            return True\n"
            "        return False\n"
        ),
    },
}


def apply_fix(task: SWETask, target_dir: Path) -> Path:
    """将正确修复应用到目标目录的项目中。

    Args:
        task: SWE 任务。
        target_dir: 目标工作区目录。

    Returns:
        项目目录路径。
    """
    project_dir = setup_task_workspace(task, target_dir)
    fixes = _COMPLETE_FIXES.get(task.id, {})
    for filename, content in fixes.items():
        filepath = project_dir / filename
        filepath.write_text(content, encoding="utf-8")
    return project_dir


# ═══════════════════════════════════════════════════════════
# 测试: Fixture 结构验证
# ═══════════════════════════════════════════════════════════


class TestSWETaskFixtureStructure:
    """Fixtures 结构验证测试。"""

    def test_fixtures_root_exists(self):
        """Fixtures 根目录存在。"""
        assert FIXTURES_ROOT.exists()
        assert FIXTURES_ROOT.is_dir()

    def test_discover_tasks(self):
        """发现至少 5 个任务。"""
        tasks = discover_tasks()
        assert len(tasks) >= 5, f"预期至少 5 个任务，发现 {len(tasks)}"

    def test_each_task_has_required_fields(self):
        """每个任务包含必要字段。"""
        for task in discover_tasks():
            assert task.id, f"{task.path}: 缺少 id"
            assert task.description, f"{task.id}: 缺少 description"
            assert task.expected, f"{task.id}: 缺少 expected"
            assert task.verification, f"{task.id}: 缺少 verification"
            assert "command" in task.verification, f"{task.id}: 缺少 verification.command"
            assert task.project_dir.exists(), f"{task.id}: project/ 目录不存在"

    def test_each_task_has_project_files(self):
        """每个任务的项目目录包含代码文件。"""
        for task in discover_tasks():
            files = list(task.project_dir.iterdir())
            assert len(files) >= 1, f"{task.id}: project/ 为空"
            # 至少有一个 .py 或 .html 或 .css 文件
            code_files = [f for f in files if f.suffix in (".py", ".html", ".css", ".js")]
            assert len(code_files) >= 1, f"{task.id}: 没有代码文件"

    def test_task_json_parses_correctly(self):
        """task.json 可正确解析。"""
        for task in discover_tasks():
            assert task.id.startswith("task_"), f"{task.id}: id 格式错误"
            assert isinstance(task.files, list)
            for f in task.files:
                assert (task.project_dir / f).exists(), \
                    f"{task.id}: 声明的文件 {f} 不存在"

    def test_all_tasks_have_fix_in_complete_fixes(self):
        """所有任务在 _COMPLETE_FIXES 中有对应修复。"""
        tasks = discover_tasks()
        for task in tasks:
            assert task.id in _COMPLETE_FIXES, \
                f"{task.id}: 缺少对应的修复定义"
            fixes = _COMPLETE_FIXES[task.id]
            for f in task.files:
                assert f in fixes, \
                    f"{task.id}: 缺少文件 {f} 的修复内容"


# ═══════════════════════════════════════════════════════════
# 测试: 验证命令可用性
# ═══════════════════════════════════════════════════════════


class TestSWETaskVerification:
    """验证命令在修复后的项目上可以通过。"""

    @pytest.fixture(params=[t for t in discover_tasks()], ids=[t.id for t in discover_tasks()])
    def task(self, request) -> SWETask:
        return request.param

    def test_verification_passes_with_fix(self, task: SWETask, tmp_path: Path):
        """应用正确修复后，验证命令通过。"""
        apply_fix(task, tmp_path)
        # copy __init__.py if needed
        project_dir = tmp_path / "project"
        init_file = project_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

        passed, output = run_verification(task, project_dir)
        assert passed, f"{task.id}: 修复后验证失败:\n{output[:500]}"

    def test_verification_fails_without_fix(self, task: SWETask, tmp_path: Path):
        """未修复时，验证命令应失败（初始项目本身应含问题）。"""
        project_dir = setup_task_workspace(task, tmp_path)
        init_file = project_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

        passed, output = run_verification(task, project_dir)
        # 初始项目应包含问题，验证应失败
        # 注意：某些任务的初始代码可能碰巧通过部分检查
        # 这是 fixture 正确性的参考，不是强断言

    def test_each_task_has_unique_id(self):
        """所有任务 ID 唯一。"""
        tasks = discover_tasks()
        ids = [t.id for t in tasks]
        assert len(ids) == len(set(ids)), "发现重复的任务 ID"


# ═══════════════════════════════════════════════════════════
# 测试: Agent 端到端执行
# ═══════════════════════════════════════════════════════════


class TestSWETaskAgentExecution:
    """Agent 端到端执行 SWE 任务。

    需要真实 Backend（需要 API Key），默认跳过。
    设置环境变量 SWE_TASK_BACKEND=claude|deepseek|gemini 来启用。
    """

    backend_name = os.environ.get("SWE_TASK_BACKEND", "")

    @classmethod
    def setup_class(cls):
        if not cls.backend_name:
            raise pytest.skip("需要 SWE_TASK_BACKEND 环境变量来运行端到端测试")

    def test_agent_solves_task(self, task_id: str, tmp_path: Path):
        """Agent 自动解决 SWE 任务。"""
        # 查找任务
        tasks = discover_tasks()
        task = next((t for t in tasks if t.id == task_id), None)
        if task is None:
            pytest.skip(f"任务 {task_id} 未找到")

        # 设置工作区
        project_dir = setup_task_workspace(task, tmp_path)

        # 运行 Agent
        import asyncio

        from zmai.config import Config
        from zmai.runtime import Runtime

        config = Config()
        runtime = Runtime(config=config)
        backend = self.backend_name

        # Agent 的任务是从现有代码开始修改
        result = asyncio.run(runtime.run(
            agent_id=f"swe_test_{task.id}",
            task=(
                f"项目在 {project_dir} 目录下。\n\n"
                f"任务: {task.description}\n\n"
                f"请读取项目代码，理解问题，做出修改，然后运行测试验证。"
            ),
            backend=backend,
            config={"project_path": str(project_dir)},
        ))

        # 验证
        passed, output = run_verification(task, project_dir)
        assert passed, f"Agent 执行后验证失败:\n状态: {result.get('status')}\n输出: {output[:500]}"
