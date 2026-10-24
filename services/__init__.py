"""自主规划插件 v4 - Service 层

业务实现层，与组件装饰器外壳解耦：
- plugin.py 的 ``@Tool / @Command / @EventHandler / @HookHandler`` 装饰器
  只做 SDK 注册和参数转发
- services/*.py 承载真实业务逻辑（数据访问 / API 调用 / 后台循环）
"""

from .cleanup_service import CleanupService
from .command_service import CommandService
from .inject_service import InjectService
from .proactive_service import ProactiveService
from .tools_service import ToolsService

__all__ = [
    "CleanupService",
    "CommandService",
    "InjectService",
    "ProactiveService",
    "ToolsService",
]
