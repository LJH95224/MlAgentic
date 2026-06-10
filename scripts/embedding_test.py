"""Embedding 模型独立测试脚本（不依赖 Milvus / Neo4j / Agent）。

用途：
  - 排查 EMBEDDING_MODEL / EMBEDDING_API_KEY / EMBEDDING_API_BASE 配置是否正确
  - 验证 SiliconFlow / DashScope 等厂商返回的实际向量维度
  - 验证 dimensions 参数是否被厂商支持
  - 出问题时直接看清楚错误来自哪里（厂商前缀缺失 / 鉴权 / 网络 / 模型不存在）

运行方式：
  conda activate geo_agent
  cd TyAgent
  python scripts/embedding_test.py
"""

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import litellm

from app.core.config import get_settings


def _redact(s: str | None) -> str:
    """API key 脱敏显示。"""
    if not s:
        return "(空)"
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"


async def test_single(text: str = "今天北京天气晴朗") -> None:
    """单条文本 embedding。"""
    settings = get_settings()

    kwargs = {
        "model": settings.embedding_model,
        "input": [text],
    }
    if settings.embedding_api_key:
        kwargs["api_key"] = settings.embedding_api_key
    if settings.embedding_api_base:
        kwargs["api_base"] = settings.embedding_api_base
    # 注意：先不传 dimensions，看看模型默认输出多少维
    # 若想测试 dimensions 是否生效，取消下一行注释
    # kwargs["dimensions"] = settings.embedding_dimension

    print(f"\n[用例 1] 单条文本 embedding（不传 dimensions）")
    print(f"  请求文本: {text!r}")

    try:
        resp = await litellm.aembedding(**kwargs)
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")
        return

    # 兼容 Pydantic 对象
    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    data = resp_dict.get("data", [])
    if not data:
        print(f"  ❌ 响应中无 data 字段: {resp_dict}")
        return

    vec = data[0]["embedding"]
    dim = len(vec)
    usage = resp_dict.get("usage", {})

    print(f"  ✅ 成功")
    print(f"  返回维度: {dim}")
    print(f"  Token 使用: {usage}")
    print(f"  向量前 5 维: {vec[:5]}")


async def test_dimensions_param(target_dim: int) -> None:
    """带 dimensions 参数的 embedding，验证厂商是否支持自定义输出维度。

    注意：LiteLLM 的 openai/ 路由对此参数有特殊限制（"Setting dimensions is not
    supported for OpenAI text-embedding-3 and later models"）—— 即使底层实际是
    SiliconFlow 也会被拒绝。所以这个测试**预期会失败**，仅用于确认 LiteLLM 行为。
    生产代码（app/rag/embedding.py）不传此参数，靠返回值维度校验保证一致。
    """
    settings = get_settings()

    kwargs = {
        "model": settings.embedding_model,
        "input": ["测试自定义维度"],
        "dimensions": target_dim,
    }
    if settings.embedding_api_key:
        kwargs["api_key"] = settings.embedding_api_key
    if settings.embedding_api_base:
        kwargs["api_base"] = settings.embedding_api_base

    print(f"\n[用例 2] 单条文本 embedding（请求 dimensions={target_dim}）")

    try:
        resp = await litellm.aembedding(**kwargs)
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")
        return

    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    vec = resp_dict["data"][0]["embedding"]
    actual_dim = len(vec)

    if actual_dim == target_dim:
        print(f"  ✅ 厂商支持 dimensions 参数：返回 {actual_dim} 维（与请求一致）")
    else:
        print(f"  ⚠️  厂商**不**支持 dimensions 参数：请求 {target_dim}，实际返回 {actual_dim}")
        print(f"     → .env 中 EMBEDDING_DIMENSION 应改为 {actual_dim}")
        print(f"     → 同时需重建 Milvus Collection（vector dim 已固化）")


async def test_batch() -> None:
    """批量 embedding 测试，验证多文本一次调用的能力。"""
    settings = get_settings()

    texts = [
        "台风的形成需要温暖的海面",
        "数值天气预报依赖大气运动方程组",
        "ECMWF 是欧洲中期天气预报中心",
    ]

    kwargs = {
        "model": settings.embedding_model,
        "input": texts,
    }
    if settings.embedding_api_key:
        kwargs["api_key"] = settings.embedding_api_key
    if settings.embedding_api_base:
        kwargs["api_base"] = settings.embedding_api_base

    print(f"\n[用例 3] 批量 embedding（{len(texts)} 条）")

    try:
        resp = await litellm.aembedding(**kwargs)
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")
        return

    resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else resp
    data = resp_dict.get("data", [])

    print(f"  ✅ 返回 {len(data)} 条向量（期望 {len(texts)}）")
    if len(data) == len(texts):
        for i, item in enumerate(data):
            print(f"    [{i}] 维度={len(item['embedding'])}, index={item.get('index')}")


async def main() -> None:
    settings = get_settings()

    print("=" * 60)
    print("Embedding 模型独立测试")
    print("=" * 60)
    print(f"EMBEDDING_MODEL:     {settings.embedding_model!r}")
    print(f"EMBEDDING_API_BASE:  {settings.embedding_api_base!r}")
    print(f"EMBEDDING_API_KEY:   {_redact(settings.embedding_api_key)}")
    print(f"EMBEDDING_DIMENSION: {settings.embedding_dimension}（.env 期望值）")

    if not settings.embedding_model:
        print("\n❌ EMBEDDING_MODEL 未配置，无法继续。请检查 .env")
        return

    # 用例 1：不传 dimensions，看默认返回多少维
    await test_single()

    # 用例 2：带 dimensions 参数，验证厂商是否支持
    await test_dimensions_param(settings.embedding_dimension)

    # 用例 3：批量
    await test_batch()

    print("\n" + "=" * 60)
    print("测试完成。如果上面看到维度不一致的警告，请按提示调整 .env")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
