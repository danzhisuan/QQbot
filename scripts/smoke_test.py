"""部署自检：确认 NoneBot 能加载适配器与全部插件。

在项目根目录执行：
    python scripts/smoke_test.py

返回码 0 表示通过；非 0 表示有插件加载失败，细节见输出。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter  # noqa: E402

EXPECTED_PLUGINS = {"echo", "llm_chat", "fortune", "webadmin"}


def main() -> int:
    nonebot.init()
    nonebot.get_driver().register_adapter(OneBotV11Adapter)
    nonebot.load_from_toml("pyproject.toml")

    loaded = sorted(plugin.name for plugin in nonebot.get_loaded_plugins())
    print(f"已加载插件: {loaded}")

    missing = EXPECTED_PLUGINS - set(loaded)
    if missing:
        print(f"错误: 以下插件未能加载 -> {sorted(missing)}", file=sys.stderr)
        return 1

    print("自检通过: 适配器与插件均已正常加载")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
