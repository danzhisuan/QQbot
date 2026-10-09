"""连通性自检插件。

用途：验证「NapCat -> NoneBot」链路是否打通。链路一旦通了，这个插件就一定会回复。
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="echo",
    description="连通性自检：复读你发的内容",
    usage="/echo 内容\n/ping",
    type="application",
    supported_adapters={"~onebot.v11"},
)

echo = on_command("echo", aliases={"复读"}, priority=10, block=True)
ping = on_command("ping", priority=10, block=True)


@echo.handle()
async def handle_echo(args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    await echo.finish(text or "用法：/echo 内容")


@ping.handle()
async def handle_ping():
    await ping.finish("pong")
