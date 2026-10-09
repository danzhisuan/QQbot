"""今日运势插件（命令层）。

逻辑在 core.py 里，这里只负责接命令、处理 @某人、回复。

    /运势           看自己的
    /运势 @某人      看别人的（同一天谁查都一样）
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.plugin import PluginMetadata

from .core import build_fortune

__plugin_meta__ = PluginMetadata(
    name="fortune",
    description="今日运势：按「用户 + 日期」确定性生成，同一天结果固定",
    usage="/运势 查看今日运势\n/运势 @某人 看 TA 的运势",
    type="application",
    supported_adapters={"~onebot.v11"},
)

fortune = on_command(
    "运势", aliases={"今日运势", "jrrp", "人品"}, priority=5, block=True
)


@fortune.handle()
async def handle_fortune(bot: Bot, event: MessageEvent):
    # /运势 @某人 —— 取消息里第一个有效的 @
    for segment in event.message:
        if segment.type != "at":
            continue
        qq = str(segment.data.get("qq", "")).strip()
        if not qq.isdigit():
            continue
        target_id = int(qq)
        name = str(segment.data.get("name", "") or "").strip()
        if not name:
            # 有些客户端不带昵称，去查一下，查不到就用 QQ 号兜底
            try:
                info = await bot.get_stranger_info(user_id=target_id)
                name = (info.get("nickname") or "").strip() or str(target_id)
            except Exception:  # noqa: BLE001 - 查不到不影响出结果
                name = str(target_id)
        await fortune.finish(build_fortune(target_id, name))

    # 默认看自己的，优先用群名片
    own_name = ""
    sender = getattr(event, "sender", None)
    if sender is not None:
        own_name = (sender.card or sender.nickname or "").strip()
    await fortune.finish(build_fortune(event.user_id, own_name))
