"""
verify_goals.py — Standalone verification of get_schedule_goals with real DB.

Usage:
    cd /home/qr0w/MaiBot/plugins/xuqian13_autonomous_planning_plugin
    /home/qr0w/MaiBot/.venv/bin/python tests/verify_goals.py
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PKG_NAME = "_maibot_plugin_xuqian13_autonomous_planning_plugin_v4"


def main():
    # ── Load plugin -----------------------------------------------------------
    spec = importlib.util.spec_from_file_location(
        PKG_NAME, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    plugin_mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG_NAME] = plugin_mod
    spec.loader.exec_module(plugin_mod)

    def imp(rel: str):
        return importlib.import_module(f"{PKG_NAME}.{rel}")

    gm_mod = imp("planner.goal_manager")
    tz_mod = imp("utils.timezone_manager")

    tz = tz_mod.TimezoneManager("Asia/Shanghai")
    now = tz.get_now()

    gm = gm_mod.GoalManager(data_dir=str(PLUGIN_DIR / "data"))

    all_ok = True

    # ── Today ----------------------------------------------------------------
    today_str = now.strftime("%Y-%m-%d")
    today_goals = gm.get_schedule_goals(chat_id="global", date_str=today_str)
    if len(today_goals) == 13:
        print(f"[OK]   get_schedule_goals('{today_str}') = {len(today_goals)} items")
    else:
        print(f"[FAIL] get_schedule_goals('{today_str}') = {len(today_goals)}, expected 13")
        all_ok = False

    # ── Check each item has time_window ---------------------------------------
    for g in today_goals:
        params = g.parameters or {}
        if "time_window" not in params:
            print(f"[FAIL] Goal '{g.name}' missing time_window in parameters")
            all_ok = False

    # ── Print today's schedule -----------------------------------------------
    print(f"\nToday's schedule ({today_str}, {len(today_goals)} items):")
    for i, g in enumerate(today_goals, 1):
        tw = (g.parameters or {}).get("time_window", [0, 0])
        start_h, start_m = divmod(int(tw[0]), 60) if tw else (0, 0)
        end_h, end_m = divmod(int(tw[1]), 60) if len(tw) > 1 else (0, 0)
        print(f"  {i:2d}. {start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}  {g.name}")
        if g.description:
            desc_short = g.description[:60]
            print(f"      {desc_short}")

    # ── All non-pending -------------------------------------------------------
    pending = [g for g in gm.get_all_goals(chat_id="global") if g.goal_type == "pending_commitment"]
    for g in today_goals:
        if g.goal_type == "pending_commitment":
            print(f"[FAIL] Pending commitment leaked into schedule goals: {g.name}")
            all_ok = False
    if not pending:
        print("[OK]   No pending commitments in DB (clean)")
    else:
        print(f"[INFO] {len(pending)} pending commitments in DB (not in schedule)")

    # ── Past days ------------------------------------------------------------
    for offset in [1, 2, 3]:
        day_str = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        goals = gm.get_schedule_goals(chat_id="global", date_str=day_str)
        if len(goals) == 13:
            print(f"[OK]   get_schedule_goals('{day_str}') = {len(goals)} items")
        else:
            print(f"[WARN] get_schedule_goals('{day_str}') = {len(goals)}, expected 13")
            # Not a hard failure — could be boundary

    # ── Total count ----------------------------------------------------------
    total = len(gm.get_all_goals(chat_id="global"))
    print(f"\nTotal goals in DB: {total}")

    # ── Result ---------------------------------------------------------------
    print()
    if all_ok:
        print("ALL GOALS VERIFICATION PASSED")
        return 0
    else:
        print("SOME CHECKS FAILED — see above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
