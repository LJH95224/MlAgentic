"""会话标题 / 摘要异步任务（V1.5 PRD §3.4 TASK-04 / TASK-05 + SES-07 / SES-08）。

【架构约定】（与 S3.2 ingest_task 一致 - dev_plan S3.5）：
- Celery @task 同步 def 壳；核心 async def _main；体内只调一次 asyncio.run
- PG 连接走 task_resources（每任务现建现断 NullPool）
- LLM 走独立配置项 SESSION_TITLE_MODEL / SESSION_SUMMARY_MODEL（缺省回退主对话模型）

【两个任务】：
- generate_session_title_task(session_id):
    触发：/chat/stream 流末尾判 title is None AND message_count==2 时 .delay()
    Prompt：把首条 user + 首条 assistant 拼接，要求 LLM 输出不超过 20 字中文标题
    幂等：任务里再判一次 title is None，避免并发触发覆盖手动标题
- generate_session_summary_task(session_id):
    触发：POST /sessions/{id}/summarize endpoint 主动调
    Prompt：把全量 messages 拼接，要求 LLM 输出 200 字以内中文摘要
    超长：拼接后 > 32k tokens 直接返 failed（dev_plan S4 决策：拼接 + 超长报 failed）

【SES-04 / 列表排序的兼容】：
- 写 title / summary 时**不要 touch updated_at**——避免异步任务完成时把会话顶到
  列表第一位，违反"按用户活跃度排序"的用户预期（dev_plan S1 沉淀）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

import litellm
from sqlalchemy import asc, select, update

from app.core.config import get_settings
from app.models.message import ChatMessage
from app.models.session import ChatSession
from app.tasks._resources import task_resources
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


# ──────────────── 常量 ────────────────


# 标题长度上限（字符）。略低于 PRD SES-07 的 20 字硬上限，给容错。
TITLE_MAX_CHARS = 20

# 摘要长度上限（字符）。略低于 PRD SES-08 的 200 字。
SUMMARY_MAX_CHARS = 200

# 摘要拼接 messages 时按 tokens 估算的上限（粗估：中文 1 字 ≈ 1.5 token）。
# 超过此阈值视为 LLM context 装不下，任务 failed 返回。
# 32k 是 deepseek-v4-flash 的 context；保守取 28k 留 prompt + 输出空间。
SUMMARY_INPUT_CHAR_LIMIT = 28_000


# ──────────────── Prompt ────────────────


# 严格按 PRD §3.1 SES-07 模板
TITLE_SYSTEM_PROMPT = """你是一个对话标题生成器。根据以下对话内容，生成一个简洁的中文标题，
要求：不超过 20 字，概括核心议题，不要加引号或标点符号结尾。"""

SUMMARY_SYSTEM_PROMPT = """你是一个对话摘要生成器。请把以下完整对话内容总结成中文摘要，
要求：不超过 200 字，覆盖核心议题、关键结论、未决问题，不要加引号或 markdown 格式。"""


# ──────────────── LLM 调用工具 ────────────────


def _resolve_kwargs(model_override: str | None) -> dict[str, Any]:
    """拼装 litellm 调用参数；model 优先用 override（SESSION_TITLE_MODEL 等），
    缺省回退 LITELLM_MODEL；其它配置（key/base/timeout）始终用 LITELLM_*。

    复用 chat client 的厂商前缀推断逻辑（与 app.kg.ner 一致）。
    """
    settings = get_settings()
    model = model_override or settings.litellm_model
    if not model:
        raise ValueError(
            "SESSION_TITLE_MODEL / SESSION_SUMMARY_MODEL 都未配置且 LITELLM_MODEL 也为空"
        )

    # 厂商前缀自动补全
    if "/" not in model and settings.litellm_api_base:
        if "deepseek.com" in settings.litellm_api_base:
            model = f"deepseek/{model}"
        elif "dashscope.aliyuncs.com" in settings.litellm_api_base:
            model = f"dashscope/{model}"
        elif "open.bigmodel.cn" in settings.litellm_api_base:
            model = f"zhipu/{model}"

    kwargs: dict[str, Any] = {
        "model": model,
        "timeout": settings.litellm_timeout,
        "num_retries": settings.litellm_num_retries,
    }
    if settings.litellm_api_key:
        kwargs["api_key"] = settings.litellm_api_key
    if settings.litellm_api_base:
        kwargs["api_base"] = settings.litellm_api_base
    return kwargs


def _clean_title(raw: str) -> str:
    """LLM 输出清洗：去引号 / 去 markdown / 去首尾标点 / 截 20 字。"""
    s = raw.strip()
    # 去 ```...``` 围栏
    s = re.sub(r"^```[^\n]*\n?|\n?```$", "", s).strip()
    # 去首尾引号（中英文 + 全/半角；用集合避免字面字符串重复）
    quote_chars = "\"'“”‘’「」『』"
    s = s.strip().strip(quote_chars)
    # 去末尾标点（PRD 要求不带标点结尾）
    s = re.sub(r"[。，、！？!?\.,;：:]+$", "", s)
    # 多余空白
    s = re.sub(r"\s+", " ", s).strip()
    # 硬截 20 字（中文字符按 char 计）
    return s[:TITLE_MAX_CHARS]


def _clean_summary(raw: str) -> str:
    """摘要清洗：去 markdown 围栏 + 多空白 + 截 200 字。"""
    s = raw.strip()
    s = re.sub(r"^```[^\n]*\n?|\n?```$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s[:SUMMARY_MAX_CHARS]


# ──────────────── 标题任务 ────────────────


async def _generate_title_main(session_id: str) -> dict:
    """生成首轮对话标题。

    步骤：
    1. 加载 session；若 title 已非空（用户已手动设置）→ 直接跳过
    2. 加载首条 user + 首条 assistant 消息（按 created_at 正序取前两条非 system）
    3. 调 LLM，清洗 → 写回 session.title（不 touch updated_at）
    """
    settings = get_settings()
    sid = uuid.UUID(session_id)

    async with task_resources() as resources:
        async with resources.db() as session:
            sess_row = (
                await session.execute(
                    select(ChatSession).where(ChatSession.id == sid)
                )
            ).scalar_one_or_none()
            if sess_row is None:
                logger.warning("标题任务: session_id=%s 不存在", session_id)
                return {"status": "skipped", "reason": "session_not_found"}
            if sess_row.title:
                logger.info(
                    "标题任务: session_id=%s 已有手动标题=%r，跳过覆盖",
                    session_id,
                    sess_row.title,
                )
                return {"status": "skipped", "reason": "title_already_set"}

            # 取首条 user + 首条 assistant
            msgs = list(
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(
                            ChatMessage.session_id == sid,
                            ChatMessage.role.in_(("user", "assistant")),
                        )
                        .order_by(asc(ChatMessage.created_at), asc(ChatMessage.id))
                        .limit(2)
                    )
                ).scalars().all()
            )
            if len(msgs) < 2:
                logger.info(
                    "标题任务: session_id=%s 消息不够(%d<2)，跳过", session_id, len(msgs)
                )
                return {"status": "skipped", "reason": "not_enough_messages"}

        # ── LLM 调用 ──
        user_first = msgs[0].content or ""
        ai_first = msgs[1].content or ""
        prompt_body = (
            f"【用户】{user_first}\n\n【AI】{ai_first}"
        )[:4000]  # 给 LLM 输入硬上限，标题生成不需要全文

        kwargs = _resolve_kwargs(settings.session_title_model)
        kwargs["messages"] = [
            {"role": "system", "content": TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_body},
        ]

        logger.info("标题任务: session_id=%s 调 LLM model=%s", session_id, kwargs["model"])
        resp = await litellm.acompletion(**kwargs)
        if hasattr(resp, "model_dump"):
            resp_dict = resp.model_dump()
        else:
            resp_dict = resp
        raw = resp_dict["choices"][0]["message"]["content"]
        title = _clean_title(raw)
        if not title:
            logger.warning(
                "标题任务: session_id=%s LLM 返回空 / 清洗后为空 raw=%r", session_id, raw[:200]
            )
            return {"status": "skipped", "reason": "empty_title"}

        # ── 写回 PG（不 touch updated_at）──
        async with resources.db() as session:
            await session.execute(
                update(ChatSession)
                .where(ChatSession.id == sid, ChatSession.title.is_(None))
                .values(title=title)
            )
            await session.commit()

        logger.info(
            "标题任务: session_id=%s ✓ 标题=%r", session_id, title
        )
        return {"status": "completed", "title": title}


# ──────────────── 摘要任务 ────────────────


async def _generate_summary_main(session_id: str) -> dict:
    """生成会话摘要。

    步骤：
    1. 加载 session；不存在 → skipped
    2. 拼接全量 user/assistant 消息（按 created_at 正序）
    3. 字符长度超 SUMMARY_INPUT_CHAR_LIMIT → status=failed（dev_plan S4 决策）
    4. 调 LLM，清洗 → 写回 session.summary + summarized_at（不 touch updated_at）
    """
    settings = get_settings()
    sid = uuid.UUID(session_id)

    async with task_resources() as resources:
        async with resources.db() as session:
            sess_row = (
                await session.execute(
                    select(ChatSession).where(ChatSession.id == sid)
                )
            ).scalar_one_or_none()
            if sess_row is None:
                logger.warning("摘要任务: session_id=%s 不存在", session_id)
                return {"status": "skipped", "reason": "session_not_found"}

            # 取全量 user/assistant（tool / system 不计入摘要）
            msgs = list(
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(
                            ChatMessage.session_id == sid,
                            ChatMessage.role.in_(("user", "assistant")),
                        )
                        .order_by(asc(ChatMessage.created_at), asc(ChatMessage.id))
                    )
                ).scalars().all()
            )

        if not msgs:
            logger.info("摘要任务: session_id=%s 无消息，跳过", session_id)
            return {"status": "skipped", "reason": "no_messages"}

        # 拼接 + 超长保护
        parts = [
            f"【{m.role}】{m.content or ''}"
            for m in msgs
            if m.content and m.content.strip()
        ]
        body = "\n\n".join(parts)
        if len(body) > SUMMARY_INPUT_CHAR_LIMIT:
            logger.error(
                "摘要任务: session_id=%s 拼接内容超长 chars=%d limit=%d → failed",
                session_id,
                len(body),
                SUMMARY_INPUT_CHAR_LIMIT,
            )
            return {
                "status": "failed",
                "reason": "content_too_long",
                "content_chars": len(body),
                "limit": SUMMARY_INPUT_CHAR_LIMIT,
            }

        # ── LLM 调用 ──
        kwargs = _resolve_kwargs(settings.session_summary_model)
        kwargs["messages"] = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": body},
        ]

        logger.info(
            "摘要任务: session_id=%s 调 LLM model=%s chars=%d",
            session_id,
            kwargs["model"],
            len(body),
        )
        resp = await litellm.acompletion(**kwargs)
        if hasattr(resp, "model_dump"):
            resp_dict = resp.model_dump()
        else:
            resp_dict = resp
        raw = resp_dict["choices"][0]["message"]["content"]
        summary = _clean_summary(raw)
        if not summary:
            logger.warning(
                "摘要任务: session_id=%s LLM 返回空 / 清洗后为空 raw=%r",
                session_id,
                raw[:200],
            )
            return {"status": "skipped", "reason": "empty_summary"}

        # ── 写回 PG（含 summarized_at，但不 touch updated_at）──
        now = datetime.now(timezone.utc)
        async with resources.db() as session:
            await session.execute(
                update(ChatSession)
                .where(ChatSession.id == sid)
                .values(summary=summary, summarized_at=now)
            )
            await session.commit()

        logger.info(
            "摘要任务: session_id=%s ✓ summary_chars=%d", session_id, len(summary)
        )
        return {
            "status": "completed",
            "summary_chars": len(summary),
            "summarized_at": now.isoformat(),
        }


# ──────────────── Celery 任务入口（同步壳） ────────────────


@celery_app.task(name="app.tasks.session_task.generate_session_title_task")
def generate_session_title_task(session_id: str) -> dict:
    """SES-07 / TASK-04：首轮对话后异步生成会话标题。"""
    logger.info("[title] task 开始 session_id=%s", session_id)
    try:
        return asyncio.run(_generate_title_main(session_id))
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=10)
        logger.error("[title] task 失败 session_id=%s err=%s", session_id, exc)
        # 标题任务失败不阻断会话（保持 title=NULL），仅返错误信息
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": tb}


@celery_app.task(name="app.tasks.session_task.generate_session_summary_task")
def generate_session_summary_task(session_id: str) -> dict:
    """SES-08 / TASK-05：主动触发会话摘要生成。"""
    logger.info("[summary] task 开始 session_id=%s", session_id)
    try:
        return asyncio.run(_generate_summary_main(session_id))
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=10)
        logger.error("[summary] task 失败 session_id=%s err=%s", session_id, exc)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": tb}


__all__ = [
    "generate_session_title_task",
    "generate_session_summary_task",
    "TITLE_MAX_CHARS",
    "SUMMARY_MAX_CHARS",
    "SUMMARY_INPUT_CHAR_LIMIT",
    "_clean_title",
    "_clean_summary",
]
