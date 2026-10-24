from pathlib import Path
from unittest.mock import AsyncMock, Mock

import asyncio
import importlib.util
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME = "schedule_contract_testpkg"
spec = importlib.util.spec_from_file_location(NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
module = importlib.util.module_from_spec(spec)
sys.modules[NAME] = module
spec.loader.exec_module(module)
InjectService = sys.modules[f"{NAME}.services.inject_service"].InjectService


def plugin(inject=True):
    instance = module.AutonomousPlanningPluginV4()
    instance.set_plugin_config({})
    service = InjectService(instance)
    service._context_cache = None
    service._inject_optimizer = None
    service._intent_classifier = None
    service._get_current_schedule = AsyncMock(return_value=("离线活动" if inject else "", "", [], "normal"))
    service._build_inject_text = Mock(return_value=("离线日程标记", True, None))
    service._compute_activity_aux = Mock(return_value=(20, ""))
    instance._inject_svc = service
    return instance


@pytest.mark.parametrize("payload_name", ["items", "messages"])
def test_planner_keeps_every_parameter(payload_name):
    p = plugin()
    payload = [{"role": "system", "content": "system"}, {"role": "user", "content": "当前消息"}]
    if payload_name == "items":
        payload = [{"item_type": "SystemMessageItem", "meta": {"item_id": "system"},
                    "parts": [{"type": "text", "text": "system"}]}]
    kwargs = {payload_name: payload, "session_id": "group-a", "item_schema_version": 1,
              "tool_definitions": [{"name": "reply"}], "selection_reason": "offline", "unknown_future_field": {"x": 1}}
    result = asyncio.run(p.handle_inject_schedule(**kwargs))["modified_kwargs"]
    for key, value in kwargs.items():
        if key != payload_name:
            assert result[key] == value
    assert len(result[payload_name]) == len(payload) + 1


@pytest.mark.parametrize("inject", [False, True])
def test_replyer_keeps_identity_and_all_bound_arguments(inject):
    p = plugin(inject)
    kwargs = {"session_id": "group-a", "attempt": 1, "reply_message_id": "owner-a",
              "extra_prompt": "既有身份凭证", "task_name": "replyer", "model_name": "offline-model",
              "reply_tool_args": {"target": "a"}, "unknown_future_field": [1, 2]}
    response = asyncio.run(p.handle_inject_replyer(**kwargs))
    result = response.get("modified_kwargs", kwargs)
    for key, value in kwargs.items():
        if key != "extra_prompt":
            assert result[key] == value
    assert result["extra_prompt"].startswith("既有身份凭证")
    assert ("离线活动" in result["extra_prompt"]) is inject


def test_retry_does_not_modify_existing_prompt():
    response = asyncio.run(plugin().handle_inject_replyer(
        session_id="group-a", attempt=2, extra_prompt="身份凭证", reply_message_id="owner-a",
    ))
    assert "modified_kwargs" not in response
