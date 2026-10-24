"""活动驱动的主动行为服务（v4.4 新增）。

两个职责合并在一个后台循环里：

1. **活动切换瞬间主动开口** —— 通过 ``ctx.maisaka.trigger_proactive`` 触发
   Maisaka 跑一轮主动对话；让 bot 在 12:00 切到午餐时自然在群里说一句
   "我去吃饭啦"。

2. **按 goal_type / priority 调节聊天频率** —— 通过
   ``ctx.frequency.set_adjust`` 把频率因子推给 heartflow，让 bot 在学习/工作时
   少说话、休息/娱乐时多说话。

两个能力都需要主程序提供（SDK v2.4+），如果 ctx 不可用会优雅降级。

设计要点：
    - 单独的 ``proactive_streams`` 白名单（默认空 = 完全禁用），避免误打扰
    - 每个 ``(stream_id, activity_name, date)`` 组合只主动触发一次
    - 频率因子按 goal_type 静态表映射，更新时记录避免重复 set_adjust
    - 后台循环 60 秒一次，启动延迟 15 秒等其他组件就绪
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple
import asyncio
import logging
import time

from ..planner.goal_manager import get_goal_manager
from ..utils.time_utils import parse_time_window
from ..utils.timezone_manager import TimezoneManager

if TYPE_CHECKING:
    from ..plugin import AutonomousPlanningPluginV4


logger = logging.getLogger(__name__)


# goal_type → 频率因子映射（1.0 = 主程序默认；<1 减少回复；>1 增加回复）
_FREQUENCY_FACTOR_BY_GOAL_TYPE: Dict[str, float] = {
    "study": 0.3,           # 学习：低频，不打扰
    "work": 0.3,            # 工作：低频
    "learn_topic": 0.5,     # 学习兴趣话题：偏低
    "sleep": 0.05,          # 睡觉：几乎不说话
    "exercise": 0.7,        # 运动：偏低
    "meal": 1.2,            # 吃饭：略高于平时
    "rest": 1.3,            # 休息：高频
    "daily_routine": 1.0,   # 日常作息：默认
    "entertainment": 1.5,   # 娱乐：高频
    "social_maintenance": 1.5,  # 社交：最高
}


# 主动发起的 intent 模板：按 goal_type 提供给 maisaka 当作主动开口的提示
_PROACTIVE_INTENT_TEMPLATES: Dict[str, str] = {
    "study": "Bot 刚开始学习/写代码，可以自然地在群里说一两句开始投入工作的话",
    "work": "Bot 刚开始工作，可以自然地说一两句进入状态的话",
    "meal": "Bot 刚开始吃饭，可以自然地在群里说一两句关于这顿饭的话",
    "rest": "Bot 刚开始休息，可以自然地说一两句放松/闲下来的话",
    "exercise": "Bot 刚开始运动，可以自然地在群里吼一两句锻炼的话",
    "entertainment": "Bot 刚开始娱乐/看剧/打游戏，可以自然地说一两句兴奋/期待的话",
    "social_maintenance": "Bot 现在心情比较social，可以主动找群友聊聊天",
    "daily_routine": "Bot 刚切换到日常活动，可以自然地说一句近况",
    "learn_topic": "Bot 开始钻研感兴趣的话题，可以自然地分享一点新发现",
}
_DEFAULT_PROACTIVE_INTENT = "Bot 切换到新活动了，可以自然地在群里说一两句近况"

# v4.4.5：约定（pending_commitment）来源的活动用强指令模板，让 Maisaka 不要陷入
# "自行决定是否回复"的工具调用循环。占位符会在 _trigger_proactive 中填充。
_COMMITMENT_INTENT_TEMPLATE = (
    "你之前答应过对方今天{time_hint}要在私聊里{title}（约定备注：{notes_hint}）。\n"
    "现在到点了，请用符合人设的语气**立即在私聊里发出**这条消息（例如叫醒、问候、"
    "提醒等），不要询问对方、不要等用户先开口、不要陷入工具调用循环。\n"
    "活动名：{activity_name}"
)


class ProactiveService:
    """活动驱动的主动行为服务（v4.4 新增）。"""

    # 活动开始后 ≤ 此分钟内才考虑触发主动发起（避免半天后突然冒一句）
    PROACTIVE_FRESH_WINDOW_MINUTES = 5
    # 后台循环周期（秒）
    LOOP_INTERVAL_SECONDS = 60.0
    # 启动延迟（秒），等其他组件就绪
    STARTUP_DELAY_SECONDS = 15.0
    # v4.4.1：proactive_streams 解析缓存有效期
    RESOLVE_CACHE_TTL_SUCCESS = 600.0  # 解析成功缓存 10 分钟
    RESOLVE_CACHE_TTL_FAILURE = 60.0   # 解析失败缓存 60 秒（短期避免反复查 + 允许群恢复重试）

    def __init__(self, plugin: "AutonomousPlanningPluginV4") -> None:
        """初始化 ProactiveService。

        Args:
            plugin: 当前插件实例
        """
        self._plugin = plugin
        self._stop_event: asyncio.Event = asyncio.Event()
        # 时区管理器（与 InjectService 用同一个时区）
        self._tz_manager: TimezoneManager = TimezoneManager(plugin.config.schedule.timezone)
        # 已触发的主动发起记录：{(stream_id, activity_name, date_str): trigger_time}
        # 同一个活动在同一天同一个 stream 只主动触发一次
        self._proactive_history: Dict[Tuple[str, str, str], float] = {}
        # 已应用的频率因子缓存：{stream_id: factor}（避免重复 set_adjust）
        self._current_factor: Dict[str, float] = {}
        # v4.4.1 新增：proactive_streams 解析缓存
        # 结构：{raw_entry: (session_id 或 None, expire_at)}
        # 解析成功缓存 10 分钟；失败缓存 60 秒（短期避免反复查 + 允许群恢复重试）
        self._resolved_cache: Dict[str, Tuple[Optional[str], float]] = {}
        logger.debug("ProactiveService 初始化（v4.4）")

    async def run_loop(self) -> None:
        """后台循环：60 秒检查一次活动状态，触发主动发起 + 频率调控。"""
        logger.info("🌟 活动驱动主动行为循环已启动（v4.4）")
        try:
            # 启动延迟：等待 InjectService 缓存预热 / chat_manager 就绪
            await asyncio.sleep(self.STARTUP_DELAY_SECONDS)
            while not self._stop_event.is_set():
                try:
                    await self._check_and_act()
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"主动检查异常: {exc}", exc_info=True)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.LOOP_INTERVAL_SECONDS,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.debug("主动行为循环被取消")
            raise
        finally:
            logger.info("🛑 主动行为循环已退出")

    async def stop(self) -> None:
        """通知循环停止。"""
        self._stop_event.set()

    # ------------------------------------------------------------
    # 主循环逻辑
    # ------------------------------------------------------------

    async def _check_and_act(self) -> None:
        """读当前活动 → 决定是否触发主动发起 / 调整频率。"""
        cfg = self._plugin.config.schedule
        # v4.4.1：兼容旧 ScheduleConfig 实例（hot-reload 过渡态）
        if not hasattr(cfg, "proactive_streams"):
            logger.debug("当前 config 无 proactive_streams 字段（hot-reload 过渡），跳过本轮")
            return
        raw_streams = cfg.proactive_streams
        if not raw_streams:
            # 白名单为空 = 主动行为完全禁用（默认安全）
            return

        # v4.4.1：把 qq:group:xxx / qq:private:xxx 等格式解析为真实 session_id
        proactive_streams = await self._resolve_proactive_streams(raw_streams)
        if not proactive_streams:
            # 所有条目都解析失败 → 跳过本轮
            return

        # 找当前正在进行的活动
        activity_info = await self._find_current_activity()
        if activity_info is None:
            return
        activity_name, goal_type, start_minutes, _end_minutes, activity_params = activity_info

        now = self._tz_manager.get_now()
        current_min = now.hour * 60 + now.minute
        date_str = now.strftime("%Y-%m-%d")

        # 1. 频率调控（每个 stream 独立）
        if cfg.enable_frequency_modulation:
            await self._apply_frequency(proactive_streams, goal_type)

        # 2. 主动发起判定（只在活动刚切换的前 5 分钟内才尝试）
        if not cfg.enable_proactive_trigger:
            return
        if (current_min - start_minutes) > self.PROACTIVE_FRESH_WINDOW_MINUTES:
            return  # 错过新鲜窗口

        for stream_id in proactive_streams:
            key = (stream_id, activity_name, date_str)
            if key in self._proactive_history:
                continue  # 同一天同一活动只触发一次
            await self._trigger_proactive(stream_id, activity_name, goal_type, activity_params)
            self._proactive_history[key] = time.time()

    # ------------------------------------------------------------
    # v4.4.1 新增：proactive_streams 格式解析
    # ------------------------------------------------------------

    async def _resolve_proactive_streams(self, raw_entries: list[str]) -> list[str]:
        """把 ``proactive_streams`` 配置项解析为真实 session_id 列表。

        支持三种格式（与 ``allowed_streams`` 一致）：
            - ``session:<id>``       → 直接取 ``<id>``
            - ``qq:group:<gid>``     → ``ctx.chat.get_stream_by_group_id`` 解析
            - ``qq:private:<uid>``   → ``ctx.chat.get_stream_by_user_id`` 解析
            - ``<id>``               → 当作 session_id 直接用（向后兼容）

        遵守 CLAUDE.md 会话 ID 规范：**不自行计算 session_id**，解析失败的条目直接
        跳过 + warn，不写入任何 fallback hash。

        Args:
            raw_entries: 配置文件中原始的 ``proactive_streams`` 列表。

        Returns:
            解析成功的 session_id 列表（按输入顺序，去重）。
        """
        resolved: list[str] = []
        seen: set[str] = set()
        now = time.time()
        ctx_obj = getattr(self._plugin, "ctx", None)

        for raw in raw_entries:
            entry = str(raw).strip()
            if not entry:
                continue

            # 命中缓存（含失败缓存）
            cached = self._resolved_cache.get(entry)
            if cached is not None and now < cached[1]:
                resolved_sid = cached[0]
                if resolved_sid and resolved_sid not in seen:
                    resolved.append(resolved_sid)
                    seen.add(resolved_sid)
                continue

            # 解析逻辑：按格式分支
            session_id: Optional[str] = None
            if entry.startswith("session:"):
                # session:<id> → 直接取
                session_id = entry[len("session:"):].strip() or None
            elif ":group:" in entry:
                # qq:group:<gid> → 用 chat_manager 解析
                session_id = await self._resolve_via_chat_capability(
                    entry, kind="group", ctx_obj=ctx_obj,
                )
            elif ":private:" in entry:
                # qq:private:<uid> → 用 chat_manager 解析
                session_id = await self._resolve_via_chat_capability(
                    entry, kind="private", ctx_obj=ctx_obj,
                )
            else:
                # 兜底：纯字符串直接当 session_id（向后兼容旧配置）
                session_id = entry

            # 写缓存
            ttl = self.RESOLVE_CACHE_TTL_SUCCESS if session_id else self.RESOLVE_CACHE_TTL_FAILURE
            self._resolved_cache[entry] = (session_id, now + ttl)

            if session_id and session_id not in seen:
                resolved.append(session_id)
                seen.add(session_id)

        return resolved

    async def _resolve_via_chat_capability(
        self,
        entry: str,
        kind: str,
        ctx_obj: Any,
    ) -> Optional[str]:
        """通过 ``ctx.chat`` 把 ``qq:group:<gid>`` / ``qq:private:<uid>`` 解析为 session_id。

        Args:
            entry: 原始配置条目（如 ``"qq:group:123456"``）。
            kind: ``"group"`` 或 ``"private"``。
            ctx_obj: ``self._plugin.ctx``，可能为 ``None``（SDK 不可用时）。

        Returns:
            真实 session_id；解析失败返回 ``None`` 并 warn。
        """
        if ctx_obj is None or not hasattr(ctx_obj, "chat"):
            logger.warning(f"无法解析 {entry}：ctx.chat 能力不可用（SDK < 2.4？）")
            return None

        parts = entry.split(":", 2)
        if len(parts) != 3:
            logger.warning(f"配置条目格式错误：{entry}（应为 <platform>:{kind}:<id>）")
            return None
        platform, _, target_id = parts
        platform = platform.strip() or "qq"
        target_id = target_id.strip()
        if not target_id:
            logger.warning(f"配置条目缺失 ID：{entry}")
            return None

        try:
            if kind == "group":
                result = await ctx_obj.chat.get_stream_by_group_id(target_id, platform)
            else:
                result = await ctx_obj.chat.get_stream_by_user_id(target_id, platform)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"解析 {entry} 异常: {exc}")
            return None

        # SDK 的 _normalize_capability_result 对 chat.get_stream_by_xxx 自动解包：
        # - Host 原始 `{"success": True, "stream": <dict>}` 被剥出 → 直接拿到 stream dict
        # - Host 找不到对应聊天流时 stream 字段为 None → SDK 透传 None 给我们
        # - 真正失败（capability_denied 等）返回原始 `{"success": False, "error": "..."}`
        #   （没有 stream key 时不被剥包）
        # 所以判断顺序必须是：先看是否含 success=False，再看是否 None，最后才取 session_id
        if isinstance(result, dict) and result.get("success") is False:
            logger.warning(f"解析 {entry} 失败: {result.get('error', '主程序拒绝')}")
            return None
        if result is None:
            logger.warning(f"解析 {entry} 失败：主程序未找到对应聊天流（可能 bot 与该 QQ 未建立过私聊 / 已退群）")
            return None
        if not isinstance(result, dict):
            logger.warning(f"解析 {entry} 失败：SDK 返回类型异常 {type(result).__name__}={result!r}")
            return None
        session_id = str(result.get("session_id") or "").strip()
        if not session_id:
            logger.warning(f"解析 {entry} 失败：返回 stream 无 session_id 字段（result={result!r}）")
            return None
        logger.info(f"解析成功 {entry} → session_id={session_id}")
        return session_id

    async def _find_current_activity(
        self,
    ) -> Optional[Tuple[str, str, int, int, Dict[str, Any]]]:
        """从 GoalManager 找当前正在进行的活动。

        Returns:
            ``(name, goal_type, start_minutes, end_minutes, parameters)`` —— 多返回的
            ``parameters`` dict 透传 goal.parameters 原始内容，供下游识别
            ``is_commitment`` / ``commitment_*`` 等 v4.4.5 新增字段。
            没找到时为 ``None``。
        """
        try:
            gm = get_goal_manager()
            goals = gm.get_active_goals(chat_id="global")
            if not goals:
                return None
            now = self._tz_manager.get_now()
            current_min = now.hour * 60 + now.minute

            for goal in goals:
                # 跳过 pending_commitment 等非日程活动
                if goal.goal_type == "pending_commitment":
                    continue
                tw = None
                if goal.parameters and "time_window" in goal.parameters:
                    tw = goal.parameters.get("time_window")
                elif goal.conditions:
                    tw = goal.conditions.get("time_window")
                if not tw:
                    continue
                start, end = parse_time_window(tw)
                if start is None or end is None:
                    continue
                # 跨夜归一化
                if end > 1440 and current_min < 720:
                    end -= 1440
                if start <= current_min < end:
                    return (
                        goal.name,
                        goal.goal_type or "daily_routine",
                        start,
                        end,
                        dict(goal.parameters) if goal.parameters else {},
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"查找当前活动失败: {exc}")
        return None

    # ------------------------------------------------------------
    # 频率调控
    # ------------------------------------------------------------

    async def _apply_frequency(self, streams: list[str], goal_type: str) -> None:
        """按 goal_type 映射频率因子并对每个 stream 应用。

        Args:
            streams: 目标聊天流 ID 列表
            goal_type: 当前活动类型
        """
        target_factor = _FREQUENCY_FACTOR_BY_GOAL_TYPE.get(goal_type, 1.0)
        ctx_obj = getattr(self._plugin, "ctx", None)
        if ctx_obj is None or not hasattr(ctx_obj, "frequency"):
            return  # SDK 未提供（v2.4 以下版本兼容）

        for stream_id in streams:
            # 同一 stream 因子不变则跳过，避免反复 set_adjust 刷日志
            if self._current_factor.get(stream_id) == target_factor:
                continue
            try:
                result = await ctx_obj.frequency.set_adjust(stream_id, target_factor)
                # SDK 返回 {"success": bool, ...}
                if isinstance(result, dict) and not result.get("success", False):
                    logger.debug(
                        f"频率调控失败: stream={stream_id}, factor={target_factor}, "
                        f"reason={result.get('error')}"
                    )
                    continue
                self._current_factor[stream_id] = target_factor
                logger.info(
                    f"🎚️ 频率调控: stream={stream_id}, "
                    f"goal_type={goal_type}, factor={target_factor}"
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"频率调控异常: stream={stream_id}, {exc}")

    # ------------------------------------------------------------
    # 主动发起
    # ------------------------------------------------------------

    async def _trigger_proactive(
        self,
        stream_id: str,
        activity_name: str,
        goal_type: str,
        activity_params: Dict[str, Any],
    ) -> None:
        """对单个聊天流触发一次主动开口。

        Args:
            stream_id: 目标聊天流 ID
            activity_name: 当前活动名（用于注入 intent prompt）
            goal_type: 当前活动类型（决定 intent 模板）
            activity_params: 当前活动的 goal.parameters。若含 ``is_commitment=True``
                则切换到强指令模板（v4.4.5）—— 普通模板里"自行决定是否回复"会让 LLM
                陷入工具调用循环不发消息，约定来源的活动必须强制发出。
        """
        ctx_obj = getattr(self._plugin, "ctx", None)
        if ctx_obj is None or not hasattr(ctx_obj, "maisaka"):
            return

        # v4.4.5：约定来源走强指令模板
        is_commitment = bool(activity_params.get("is_commitment"))
        if is_commitment:
            commit_title = str(activity_params.get("commitment_title") or activity_name).strip()
            commit_time = str(activity_params.get("commitment_time") or "").strip()
            commit_notes = str(activity_params.get("commitment_notes") or "").strip()
            intent_prompt = _COMMITMENT_INTENT_TEMPLATE.format(
                time_hint=f"{commit_time} " if commit_time else "",
                title=commit_title,
                notes_hint=commit_notes or "无",
                activity_name=activity_name,
            )
            reason = f"autonomous_planning_v4: 约定到期 → {commit_title}"
        else:
            template = _PROACTIVE_INTENT_TEMPLATES.get(goal_type, _DEFAULT_PROACTIVE_INTENT)
            intent_prompt = f"{template}（活动名：{activity_name}）"
            reason = f"autonomous_planning_v4: 活动切换 → {activity_name}"

        try:
            result = await ctx_obj.maisaka.trigger_proactive(
                stream_id=stream_id,
                intent=intent_prompt,
                reason=reason,
                priority="high" if is_commitment else "normal",
            )
            if isinstance(result, dict) and not result.get("success", False):
                logger.warning(
                    f"主动发起被拒: stream={stream_id}, activity={activity_name}, "
                    f"reason={result.get('error')}"
                )
                return
            logger.info(
                f"🚀 主动发起触发: stream={stream_id}, activity={activity_name}, "
                f"goal_type={goal_type}, commitment={is_commitment}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"主动发起异常: stream={stream_id}, {exc}")
