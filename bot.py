"""机器人启动入口。

本地运行：
    python bot.py

Docker 运行：
    由 docker-compose 调用 `python bot.py`（见 Dockerfile 的 CMD）。

第三方插件：
    从 Web 管理台安装的插件装在挂载卷里（默认 data/pylibs），模块名记在
    data/extra_plugins.json，这里在启动时加载。这样重建容器也不会丢。

配置来源（按优先级）：
    1. 容器/进程的环境变量
    2. 当前工作目录下的 .env
    3. 当前工作目录下的 .env.{ENVIRONMENT}
"""

import json
import os
import sys
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# ---------------------------------------------------------------- 第三方插件目录
# pip install --target 会把整个依赖树都装进来（连 nonebot2、pydantic 的副本都有），
# 所以这里必须 **append 到 sys.path 末尾**而不是插到最前：
# 让镜像自带的核心包始终优先，否则插件带进来的版本会把框架本身顶掉。
# 必须无条件 append，不能判断目录是否存在：
# 首次启动时这个目录还没被创建（管理台还没装过插件），
# 一旦跳过，后面通过管理台装的插件就 import 不到了，热加载必然失败。
# Python 对不存在的 sys.path 条目是安全的，会自动忽略。
_EXTRA_LIBS = Path(os.environ.get("EXTRA_LIBS_DIR") or "data/pylibs").resolve()
sys.path.append(str(_EXTRA_LIBS))

# 初始化 NoneBot，自动读取 .env / .env.{ENVIRONMENT}
nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 从 pyproject.toml 的 [tool.nonebot] 加载适配器与插件目录
nonebot.load_from_toml("pyproject.toml")


# ---------------------------------------------------------------- 第三方插件
def _load_extra_plugins() -> None:
    """加载 Web 管理台装进来的第三方插件。单个失败不影响其他。"""
    path = Path(os.environ.get("EXTRA_PLUGINS_FILE") or "data/extra_plugins.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.warning(f"读取第三方插件清单失败（{path}）：{exc}")
        return

    if not isinstance(raw, list):
        nonebot.logger.warning(f"第三方插件清单格式不对（应为数组）：{path}")
        return

    loaded = 0
    for name in raw:
        try:
            if nonebot.load_plugin(str(name)):
                loaded += 1
                nonebot.logger.info(f"已加载第三方插件: {name}")
        except Exception as exc:  # noqa: BLE001 - 单个插件失败不影响其他插件
            nonebot.logger.warning(f"第三方插件 {name} 加载失败：{exc}")

    if raw:
        nonebot.logger.info(f"第三方插件：{loaded}/{len(raw)} 个加载成功")


_load_extra_plugins()

if __name__ == "__main__":
    nonebot.run()
