"""日志初始化。

V1.0 仅做最基础的 stdlib logging 配置，后续可替换为 structlog / loguru。
"""

import logging
import sys


def setup_logging(debug: bool = False) -> None:
    """初始化根 logger。重复调用安全。"""
    level = logging.DEBUG if debug else logging.INFO

    root = logging.getLogger()
    # 防止 uvicorn / pytest 重复挂载 handler
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)

    # 降低过于啰嗦的第三方日志
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
