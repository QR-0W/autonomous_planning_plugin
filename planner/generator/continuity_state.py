"""Schedule continuity helpers.

The schedule generator intentionally keeps the LLM as the creative component,
but continuity itself must not be prompt-only.  This module extracts a compact
state from recent schedule summaries and validates whether newly generated
items still carry that state forward.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Sequence


TRAVEL_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "旅行", "旅程", "游览", "参观", "景区", "景点", "旅店", "酒店", "民宿",
    "退房", "入住", "机场", "车站", "火车", "高铁", "航班", "飞机", "大巴",
    "轮渡", "打车", "转场", "赶路", "路线", "行李", "出发", "抵达",
    "到达", "前往", "返回", "回到", "离开", "飞往", "坐车", "热气球",
    "峡谷", "山谷", "博物馆", "教堂", "古城", "老城", "海滩", "港口",
    "码头", "市集", "观景",
)

TRANSFER_KEYWORDS: tuple[str, ...] = (
    "退房", "收拾行李", "整理行李", "出发", "抵达", "到达", "前往",
    "离开", "转场", "换城市", "坐车", "高铁", "火车", "航班", "飞机",
    "机场", "车站", "大巴", "轮渡", "入住", "check-in", "checkin",
)

ROUTINE_WORDS: tuple[str, ...] = (
    "睡觉", "起床", "洗漱", "早餐", "午餐", "晚餐", "午休", "夜聊",
    "睡前", "活动", "日程", "今天", "明天", "当天", "照片", "朋友",
)

# A small known-location list is only an anchor recognizer, not a route table.
# Regex extraction below still handles arbitrary Chinese city/place suffixes.
KNOWN_LOCATIONS: tuple[str, ...] = (
    "卡帕多奇亚", "格雷梅", "安塔利亚", "伊斯坦布尔", "开塞利", "伊兹密尔",
    "棉花堡", "费特希耶", "博德鲁姆", "阿拉恰特", "特洛伊", "以弗所",
    "北京", "上海", "广州", "深圳", "杭州", "苏州", "南京", "成都",
    "重庆", "西安", "武汉", "长沙", "厦门", "青岛", "大理", "丽江",
    "昆明", "拉萨", "敦煌", "乌鲁木齐", "香港", "澳门", "台北",
    "东京", "大阪", "京都", "首尔", "釜山", "曼谷", "清迈", "新加坡",
    "巴黎", "伦敦", "罗马", "米兰", "柏林", "慕尼黑", "纽约", "洛杉矶",
)

PLACE_SUFFIX_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z·\-]{1,15}"
    r"(?:市|城|镇|村|岛|港|湾|谷|山|湖|寺|馆|宫|堡|海滩|机场|车站|酒店|民宿|"
    r"老城|古城|景区|博物馆|公园|教堂|峡谷|山谷|洞穴|市集|码头|港口|营地|"
    r"草原|沙漠|雪山|高原|古镇|小镇))"
)

TRAVEL_VERB_LOCATION_RE = re.compile(
    r"(?:在|到|去|赴|抵达|到达|前往|返回|回到|离开|入住|游览|参观|"
    r"逛|转场到|转去|飞往|坐车去)"
    r"([\u4e00-\u9fa5A-Za-z·\-]{2,16})"
)

DAY_HEADER_RE = re.compile(r"^【[^】]+】")


@dataclass(frozen=True)
class ContinuityState:
    """Compact state extracted from recent schedule text."""

    has_recent_context: bool
    has_travel_context: bool
    recent_locations: List[str]
    latest_locations: List[str]

    @property
    def has_strong_continuity(self) -> bool:
        """Whether generated schedules should be required to preserve context."""
        return self.has_recent_context and (
            self.has_travel_context or bool(self.latest_locations)
        )


def _unique_in_order(values: Iterable[str], *, limit: int = 12) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _trim_location_candidate(candidate: str) -> str:
    text = str(candidate or "").strip(" \t\r\n，。；、:：()（）[]【】'\"")
    for known in KNOWN_LOCATIONS:
        if known in text:
            return known

    # Verb regexes often capture the following action ("卡帕多奇亚看清晨...").
    # Cut at common action particles while keeping at least two chars.
    cut_tokens = (
        "看", "吃", "喝", "拍", "整理", "准备", "慢慢", "附近", "的",
        "里", "上", "和", "把", "先", "继续", "体验", "散步",
    )
    for token in cut_tokens:
        idx = text.find(token)
        if idx >= 2:
            text = text[:idx]
            break
    return text.strip(" \t\r\n，。；、:：()（）[]【】'\"")


def _is_location_candidate(candidate: str) -> bool:
    if len(candidate) < 2 or len(candidate) > 16:
        return False
    if candidate in ROUTINE_WORDS:
        return False
    if any(word in candidate for word in ROUTINE_WORDS):
        # Known locations such as "伊斯坦布尔机场" are accepted by suffix/known checks
        # before generic routine words can discard them.
        if not any(known in candidate for known in KNOWN_LOCATIONS):
            if not any(candidate.endswith(suffix) for suffix in ("机场", "车站", "酒店", "民宿")):
                return False
    return True


def extract_locations(text: str, *, limit: int = 12) -> List[str]:
    """Extract likely city/place anchors from Chinese schedule prose.

    This is deliberately heuristic: it only supplies continuity anchors for
    prompt construction and semantic validation, not geocoding.
    """
    source = str(text or "")
    candidates: List[str] = []

    for known in KNOWN_LOCATIONS:
        if known in source:
            candidates.append(known)

    for match in PLACE_SUFFIX_RE.finditer(source):
        candidates.append(match.group(1))

    for match in TRAVEL_VERB_LOCATION_RE.finditer(source):
        candidates.append(_trim_location_candidate(match.group(1)))

    return _unique_in_order(
        (candidate for candidate in candidates if _is_location_candidate(candidate)),
        limit=limit,
    )


def _split_day_sections(summary: str) -> List[str]:
    sections: List[List[str]] = []
    current: List[str] = []
    saw_day_header = False
    for raw_line in str(summary or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if DAY_HEADER_RE.match(line):
            if current and saw_day_header:
                sections.append(current)
            current = [line]
            saw_day_header = True
        elif saw_day_header and current:
            current.append(line)
        elif not saw_day_header:
            # Keep header-less summaries usable, but ignore the leading label
            # once a real dated section appears below it.
            current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section) for section in sections]


def extract_continuity_state(summary: str) -> ContinuityState:
    """Extract continuity/travel state from recent schedule summary."""
    source = str(summary or "")
    has_recent_context = bool(source.strip()) and not any(
        marker in source for marker in ("没有具体日程记录", "普通的日子", "记不太清")
    )
    if not has_recent_context:
        return ContinuityState(False, False, [], [])

    sections = _split_day_sections(source) or [source]
    day_locations = [extract_locations(section) for section in sections]
    latest_locations = day_locations[0] if day_locations else []
    recent_locations = _unique_in_order(
        (location for locations in day_locations for location in locations),
        limit=12,
    )

    has_travel_context = any(keyword in source for keyword in TRAVEL_CONTEXT_KEYWORDS)
    if len(recent_locations) >= 2:
        has_travel_context = True

    return ContinuityState(
        has_recent_context=has_recent_context,
        has_travel_context=has_travel_context,
        recent_locations=recent_locations,
        latest_locations=latest_locations,
    )


def build_continuity_prompt_section(recent_summary: str) -> str:
    """Build a deterministic continuity section for the schedule prompt."""
    state = extract_continuity_state(recent_summary)
    if not state.has_strong_continuity:
        return ""

    lines = ["", "【连续旅行/地点演化要求】"]
    if state.recent_locations:
        lines.append(f"- 已识别最近地点/路线锚点：{' → '.join(state.recent_locations[:8])}。")
    if state.latest_locations:
        lines.append(f"- 最近一天最直接的承接点：{'、'.join(state.latest_locations[:4])}。")

    lines.extend([
        "- 今天必须继承最近地点、路线、未收尾旅程或长期项目；不能只换活动名却丢掉近期主线。",
        "- 如果今天换城市/区域，必须写出退房或收拾行李、交通、抵达、入住/休整等现实过渡；禁止瞬移式换地点。",
        "- 如果今天不换地点，也要安排当地新的具体体验、收尾或恢复体力，而不是停在泛泛日常里空转。",
    ])

    return "\n".join(lines) + "\n"


def _flatten_items(items: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for item in items:
        parts.append(str(item.get("name", "") or ""))
        parts.append(str(item.get("description", "") or ""))
    return "\n".join(parts)


def find_continuity_violations(
    items: Sequence[Dict[str, Any]],
    recent_summary: str,
) -> List[str]:
    """Return blocking continuity violations for generated schedule items."""
    state = extract_continuity_state(recent_summary)
    if not state.has_strong_continuity:
        return []

    generated_text = _flatten_items(items)
    generated_locations = extract_locations(generated_text)
    generated_location_set = set(generated_locations)
    recent_location_set = set(state.recent_locations)
    latest_anchors = state.latest_locations[:5] or state.recent_locations[:5]

    has_recent_anchor = any(anchor and anchor in generated_text for anchor in latest_anchors)
    has_known_recent_location = bool(generated_location_set & recent_location_set)
    has_transfer = any(keyword in generated_text for keyword in TRANSFER_KEYWORDS)

    violations: List[str] = []
    if state.has_travel_context and not (has_recent_anchor or has_known_recent_location or has_transfer):
        anchor_text = "、".join(latest_anchors[:4]) or "近期地点/旅程"
        violations.append(
            "连续性硬约束：近期日程显示旅行/地点主线"
            f"（{anchor_text}），但新日程没有承接任何最近地点、当地体验或现实转场。"
        )

    return violations
