"""日志配置：集中管理日志格式和级别。"""
import logging


def setup_logging(level: int = logging.INFO) -> None:
    """配置全局日志：输出到控制台，格式带时间/级别/模块名。

    在应用启动时调用一次即可，所有模块的 logger 都会使用此配置。
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
