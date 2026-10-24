"""事件处理器辅助模块

v4 中事件处理已搬迁到 plugin.py 的 @EventHandler / @HookHandler 装饰器外壳 +
services/ 业务实现层。此目录仅保留：

- exception_handler.py：异常处理工具函数（被 services 调用）
- inject/：智能注入子模块（被 InjectService 调用）
"""

__all__: list[str] = []
