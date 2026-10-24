"""Offline response-contract regression: python3 -I -B tests/test_response_contract.py."""

from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from unittest import TestCase, main
import importlib
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_planning_response_contract"

# Register namespace packages without executing plugin or package initializers.
for relative in ("", "core", "utils", "planner", "planner.generator"):
    name = PACKAGE + (f".{relative}" if relative else "")
    package = ModuleType(name)
    package.__path__ = [str(ROOT.joinpath(*relative.split("."))) if relative else str(ROOT)]
    sys.modules[name] = package

LLMInvalidResponseError = importlib.import_module(f"{PACKAGE}.core.exceptions").LLMInvalidResponseError
LLMResponseParser = importlib.import_module(f"{PACKAGE}.planner.generator.response_parser").LLMResponseParser
PromptBuilder = importlib.import_module(f"{PACKAGE}.planner.generator.prompt_builder").PromptBuilder
SchemaBuilder = importlib.import_module(f"{PACKAGE}.planner.generator.schema_builder").SchemaBuilder


class FixedClock:
    def get_now(self):
        return datetime(2026, 10, 24, 8, tzinfo=timezone.utc)


class ResponseContractTests(TestCase):
    def setUp(self):
        self.items = [{
            "name": "早餐",
            "description": "在旅馆吃早餐，继续昨天的旅程",
            "time_slot": "08:00",
            "duration_hours": 0.5,
            "goal_type": "meal",
            "priority": "high",
        }]
        self.response = json.dumps({"schedule_items": self.items}, ensure_ascii=False)

    def test_yesterday_object_response_still_succeeds(self):
        for response in (self.response, f"```json\n{self.response}\n```", f"```\n{self.response}\n```"):
            with self.subTest(response=response):
                self.assertEqual(LLMResponseParser.parse_schedule_response(response), self.items)

    def test_existing_control_character_cleanup_is_preserved(self):
        response = '{"schedule_items": [{"name": "早餐", "description": "第一行\n第二行"}]}'
        items = LLMResponseParser.parse_schedule_response(response)
        self.assertEqual(items[0]["description"], "第一行\n第二行")

    def test_today_array_response_reports_domain_error(self):
        response = json.dumps(self.items, ensure_ascii=False)
        for value in (response, f"```json\n{response}\n```"):
            with self.subTest(response=value):
                with self.assertRaisesRegex(LLMInvalidResponseError, "根节点必须是对象.*list") as caught:
                    LLMResponseParser.parse_schedule_response(value)
                self.assertTrue(caught.exception.response)
                self.assertLessEqual(len(caught.exception.response), 500)

    def test_json_parser_rejects_non_object_roots(self):
        for value in ([], self.items, ["schedule_items"], None, "schedule_items", 42, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(LLMInvalidResponseError, "根节点必须是对象"):
                    LLMResponseParser.parse_json_response(json.dumps(value))

    def test_direct_extraction_rejects_non_object_roots(self):
        for value in ([], self.items, ["schedule_items"], None, "schedule_items", 42, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(LLMInvalidResponseError, "根节点必须是对象"):
                    LLMResponseParser.extract_schedule_items(value)

    def test_missing_schedule_items_reports_domain_error(self):
        with self.assertRaisesRegex(LLMInvalidResponseError, "缺少必需的 'schedule_items'") as caught:
            LLMResponseParser.parse_schedule_response('{"other": []}')
        self.assertEqual(json.loads(caught.exception.response), {"other": []})

    def test_schedule_items_must_be_array(self):
        for value in (None, {}, "早餐", 1, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(LLMInvalidResponseError, "schedule_items必须是列表"):
                    LLMResponseParser.parse_schedule_response(json.dumps({"schedule_items": value}))

    def test_each_schedule_item_must_be_object(self):
        for value in (None, "早餐", 1, False, []):
            with self.subTest(value=value):
                response = json.dumps({"schedule_items": [self.items[0], value]})
                with self.assertRaisesRegex(LLMInvalidResponseError, r"schedule_items\[1\]必须是对象"):
                    LLMResponseParser.parse_schedule_response(response)

    def test_empty_array_remains_valid_for_extraction(self):
        self.assertEqual(LLMResponseParser.parse_schedule_response('{"schedule_items": []}'), [])

    def test_invalid_json_reports_domain_error(self):
        with self.assertRaisesRegex(LLMInvalidResponseError, "无法解析LLM响应为JSON"):
            LLMResponseParser.parse_schedule_response('{"schedule_items": [}')

    def test_valid_extraction_preserves_items(self):
        self.assertIs(LLMResponseParser.extract_schedule_items({"schedule_items": self.items}), self.items)

    def test_initial_and_retry_prompts_include_complete_root_schema(self):
        for detailed in (False, True):
            config = {
                "bot_profile": {"bot_name": "离线测试角色", "personality": "喜欢旅行"},
                "enable_detailed_description": detailed,
                "min_activities": 2,
                "max_activities": 4,
            }
            schema = SchemaBuilder(config).build_json_schema()
            before = json.dumps(schema, sort_keys=True)
            builder = PromptBuilder(config, FixedClock())
            context = "【10-23 周五】在卡帕多奇亚参观峡谷，入住格雷梅旅馆"
            prompts = (
                builder.build_schedule_prompt("daily", {}, schema, yesterday_context=context),
                builder.build_retry_prompt("daily", {}, schema, ["离线校验反馈"], yesterday_context=context),
            )
            for prompt in prompts:
                with self.subTest(detailed=detailed, retry="离线校验反馈" in prompt):
                    serialized = next(line.removeprefix("Schema: ") for line in prompt.splitlines() if line.startswith("Schema: "))
                    self.assertEqual(json.loads(serialized), schema)
                    self.assertEqual(json.loads(serialized)["type"], "object")
                    self.assertEqual(json.loads(serialized)["required"], ["schedule_items"])
                    self.assertIn("根节点必须是对象", prompt)
                    self.assertIn("禁止直接返回日程数组", prompt)
                    self.assertIn("【连续性要求】", prompt)
                    self.assertIn(context, prompt)
            self.assertEqual(json.dumps(schema, sort_keys=True), before)

    def test_prompt_without_schema_still_requires_object(self):
        builder = PromptBuilder({"bot_profile": {"bot_name": "离线测试", "personality": "喜欢旅行"}}, FixedClock())
        prompt = builder.build_schedule_prompt("daily", {})
        self.assertIn("根节点必须是对象", prompt)
        self.assertIn("禁止直接返回日程数组", prompt)

    def test_plugin_initialization_and_host_are_not_loaded(self):
        loaded = {name.removeprefix(f"{PACKAGE}.") for name in sys.modules if name.startswith(f"{PACKAGE}.")}
        self.assertEqual(loaded, {
            "core", "core.exceptions", "utils", "utils.timezone_manager", "planner", "planner.generator",
            "planner.generator.continuity_state", "planner.generator.prompt_builder",
            "planner.generator.response_parser", "planner.generator.schema_builder",
        })
        self.assertFalse(any(name == "src" or name.startswith("src.") for name in sys.modules))
        self.assertFalse(any(name.startswith("maibot_plugin_sdk") for name in sys.modules))


if __name__ == "__main__":
    main(verbosity=2)
