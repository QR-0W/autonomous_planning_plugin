"""Timezone Manager Module.

This module provides centralized timezone handling functionality,
eliminating code duplication across multiple modules.
"""

from __future__ import annotations

import logging
from datetime import datetime, tzinfo
from typing import Optional


logger = logging.getLogger(__name__)


class TimezoneManager:
    """时区管理器 - 集中管理时区处理，避免重复代码

    解析优先级：
        1. Python 3.9+ 标准库 ``zoneinfo``（无第三方依赖，IANA tzdata）
        2. 第三方 ``pytz``（向后兼容）
        3. 系统本地时区（兜底，可能不准确）

    Example:
        >>> tz_manager = TimezoneManager("Asia/Shanghai")
        >>> now = tz_manager.get_now()
        >>> print(now.strftime("%Y-%m-%d %H:%M:%S %Z"))
    """

    def __init__(self, timezone_str: str = "Asia/Shanghai") -> None:
        """初始化时区管理器。

        Args:
            timezone_str: 时区字符串（如 ``Asia/Shanghai``、``UTC``）。
        """
        self.timezone_str: str = timezone_str
        self._tz: Optional[tzinfo] = self._init_timezone()

    def _init_timezone(self) -> Optional[tzinfo]:
        """按优先级解析时区对象。

        Returns:
            tzinfo 对象；全部失败时返回 ``None`` 由调用方降级到系统时间。
        """
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(self.timezone_str)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f"zoneinfo 加载 {self.timezone_str} 失败: {exc}，尝试 pytz 降级")

        try:
            import pytz
            return pytz.timezone(self.timezone_str)
        except ImportError:
            logger.warning("zoneinfo 与 pytz 均不可用，将使用系统时区")
        except Exception as exc:
            logger.warning(f"时区初始化失败: {exc}，将使用系统时区")
        return None

    def get_now(self) -> datetime:
        """获取配置时区下的当前时间；解析失败时回退系统时间。

        Returns:
            带 tzinfo 的当前时间；若时区不可用则返回 naive ``datetime.now()``。
        """
        if self._tz is not None:
            return datetime.now(self._tz)
        return datetime.now()
