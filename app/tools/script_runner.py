"""通用脚本执行引擎（TOL-01）。

设计目标：
- 全异步：用 asyncio.create_subprocess_exec，不阻塞事件循环
- 严格超时：超时立即强制 kill，PRD 明确要求"超时脚本被强制 Kill"
- 跨平台：Windows 与 Linux 上都能正确杀子进程及其子孙进程
- 防注入：cmd 必须是 list，禁止 shell=True 字符串拼接

V1.0 阶段：此引擎**不作为 LLM 工具注册**。后续具体业务（如气象脚本调度、
RAG 文档预处理脚本）可以在自己的 @tool 装饰函数里调用 run_script，并对
命令做白名单 / 参数校验。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

# PRD 建议的默认超时
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# 是否为 Windows
_IS_WINDOWS = sys.platform == "win32"


@dataclass
class ScriptResult:
    """脚本执行结果。"""

    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool

    @property
    def success(self) -> bool:
        """是否成功结束（非超时 + 退出码 0）。"""
        return not self.timed_out and self.returncode == 0


async def run_script(
    cmd: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
    encoding: str = "utf-8",
) -> ScriptResult:
    """异步执行子进程脚本。

    Args:
        cmd: 命令及参数列表。例如 ["python", "scripts/parse.py", "--input", "a.csv"]。
            **必须是 list/tuple，禁止字符串**——这样能彻底规避 shell 注入。
        timeout: 最长执行秒数。超过即强制 kill 子进程（及其进程组）。
            默认 30s，对应 PRD TOL-01 验收要求。
        cwd: 工作目录。None 表示继承当前进程。
        env: 子进程环境变量。None 表示继承当前进程；传 dict 时**完全替换**。
        stdin_text: 通过 stdin 传给子进程的文本。
        encoding: stdout/stderr 解码字符集。

    Returns:
        ScriptResult，含 returncode / stdout / stderr / elapsed / timed_out。
        超时时 returncode = -1，timed_out = True，stderr 含超时说明。
    """
    if not cmd or isinstance(cmd, str):
        raise ValueError("cmd 必须是非空的 list/tuple，禁止传字符串（防 shell 注入）")
    cmd_list = list(cmd)

    # Windows 上用 CREATE_NEW_PROCESS_GROUP，便于后续 send CTRL_BREAK 或 kill；
    # Unix 上用 setsid 建立新进程组，超时时用 killpg 杀整组（含孙进程）。
    creationflags = 0
    preexec_fn = None
    if _IS_WINDOWS:
        # 0x00000200 = CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000200
    else:
        preexec_fn = os.setsid  # type: ignore[assignment]

    logger.info("subprocess 启动: cmd=%s cwd=%s timeout=%.1fs", cmd_list, cwd, timeout)
    t0 = time.perf_counter()

    proc = await asyncio.create_subprocess_exec(
        *cmd_list,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        creationflags=creationflags if _IS_WINDOWS else 0,
        preexec_fn=preexec_fn,
    )

    stdin_bytes = stdin_text.encode(encoding) if stdin_text is not None else None
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        # 强制 kill —— PRD 验收明确要求
        await _force_kill(proc)
        elapsed = time.perf_counter() - t0
        logger.warning(
            "subprocess 超时强制终止: cmd=%s pid=%s elapsed=%.2fs",
            cmd_list,
            proc.pid,
            elapsed,
        )
        # 尽量回收已有输出（kill 之后再 communicate 不再阻塞）
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            stdout_b, stderr_b = b"", b""

        msg = f"脚本执行超时（>{timeout:.1f}s），已被强制终止。"
        stderr_text = _decode(stderr_b, encoding)
        return ScriptResult(
            returncode=-1,
            stdout=_decode(stdout_b, encoding),
            stderr=(stderr_text + "\n" + msg) if stderr_text else msg,
            elapsed_seconds=elapsed,
            timed_out=True,
        )

    elapsed = time.perf_counter() - t0
    result = ScriptResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=_decode(stdout_b, encoding),
        stderr=_decode(stderr_b, encoding),
        elapsed_seconds=elapsed,
        timed_out=False,
    )
    logger.info(
        "subprocess 结束: cmd=%s returncode=%d elapsed=%.2fs stdout_len=%d stderr_len=%d",
        cmd_list,
        result.returncode,
        elapsed,
        len(result.stdout),
        len(result.stderr),
    )
    return result


# ────────────── 内部工具 ──────────────


async def _force_kill(proc: asyncio.subprocess.Process) -> None:
    """超时后强制 kill 子进程及其进程组/子孙。

    Linux/Mac: killpg(SIGKILL) 杀整个进程组（依赖创建时 setsid）。
    Windows:   proc.kill() —— Python 内部转 TerminateProcess。
              注：CREATE_NEW_PROCESS_GROUP 不会自动杀子孙，但 V1.0 阶段
              脚本一般是单进程，业务上够用。
    """
    if proc.returncode is not None:
        return  # 已结束

    try:
        if _IS_WINDOWS:
            proc.kill()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        # 进程在我们 kill 之前已经退出
        return
    except Exception as e:
        logger.error("强制 kill 失败: pid=%s err=%s", proc.pid, e)

    # 等待 OS 真正回收，避免僵尸进程
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        logger.error("subprocess kill 后仍未退出: pid=%s", proc.pid)


def _decode(b: bytes | None, encoding: str) -> str:
    """容错解码：先按 encoding，失败用 replace 兜底。"""
    if not b:
        return ""
    try:
        return b.decode(encoding)
    except UnicodeDecodeError:
        return b.decode(encoding, errors="replace")
