"""script_runner 引擎单元测试（TOL-01 验收）。

覆盖：
- 正常执行：返回 0、stdout 内容正确
- stderr 输出
- 非零退出码
- 超时强制 kill（PRD 关键验收点）
- 命令不存在
- 安全：拒绝字符串形式的 cmd
- stdin 传入
"""

import sys

import pytest

from app.tools.script_runner import ScriptResult, run_script

# 跨平台 Python 可执行路径
PYEXE = sys.executable


@pytest.mark.asyncio
async def test_run_script_success():
    """正常脚本：returncode=0，stdout 含期望文本。"""
    r = await run_script([PYEXE, "-c", "print('hello world')"])
    assert isinstance(r, ScriptResult)
    assert r.returncode == 0
    assert r.success is True
    assert r.timed_out is False
    assert "hello world" in r.stdout
    assert r.elapsed_seconds > 0


@pytest.mark.asyncio
async def test_run_script_captures_stderr():
    """stderr 输出应被独立捕获。"""
    code = "import sys; sys.stderr.write('warn!')"
    r = await run_script([PYEXE, "-c", code])
    assert r.returncode == 0
    assert "warn!" in r.stderr


@pytest.mark.asyncio
async def test_run_script_nonzero_exit_code():
    """非零退出码：success=False，returncode 反映真实值。"""
    r = await run_script([PYEXE, "-c", "import sys; sys.exit(7)"])
    assert r.returncode == 7
    assert r.success is False
    assert r.timed_out is False


@pytest.mark.asyncio
async def test_run_script_timeout_force_kill():
    """TOL-01 关键验收：超时脚本必须被强制 Kill，并返回超时提示。"""
    # 让 Python 脚本 sleep 30 秒，但只给它 1 秒
    code = "import time; time.sleep(30)"
    r = await run_script([PYEXE, "-c", code], timeout=1.0)

    assert r.timed_out is True
    assert r.returncode == -1
    assert r.success is False
    # 必须包含超时提示
    assert "超时" in r.stderr or "timeout" in r.stderr.lower()
    # 实际耗时应远小于 sleep 时间，说明确实被强 kill 了
    assert r.elapsed_seconds < 5.0, f"超时未强制中断: elapsed={r.elapsed_seconds}"


@pytest.mark.asyncio
async def test_run_script_command_not_found():
    """不存在的命令应抛 FileNotFoundError（由 asyncio 子进程层产生）。"""
    with pytest.raises(FileNotFoundError):
        await run_script(["this_command_definitely_does_not_exist_12345"])


@pytest.mark.asyncio
async def test_run_script_rejects_string_cmd():
    """cmd 必须是 list/tuple，防 shell 注入。"""
    with pytest.raises(ValueError, match="cmd 必须是非空"):
        await run_script("echo hello")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_script_rejects_empty_cmd():
    """空列表也应被拒绝。"""
    with pytest.raises(ValueError):
        await run_script([])


@pytest.mark.asyncio
async def test_run_script_stdin_input():
    """通过 stdin 传文本，子进程能正确读到。"""
    code = "import sys; data = sys.stdin.read(); print(f'GOT:{data}')"
    r = await run_script([PYEXE, "-c", code], stdin_text="hello-stdin")
    assert r.returncode == 0
    assert "GOT:hello-stdin" in r.stdout


@pytest.mark.asyncio
async def test_run_script_env_isolation():
    """env 参数应能完全覆盖子进程环境变量。"""
    code = "import os; print(os.environ.get('TY_AGENT_TEST_VAR', 'MISSING'))"
    r = await run_script(
        [PYEXE, "-c", code],
        env={"TY_AGENT_TEST_VAR": "12345", "PATH": __import__("os").environ.get("PATH", "")},
    )
    assert r.returncode == 0
    assert "12345" in r.stdout
