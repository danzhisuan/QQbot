"""LLM 聊天插件：把群聊/私聊消息接到任意 OpenAI 兼容接口。

行为：
    - 群聊：被 @ 时才回复（避免刷屏，也降低被风控的概率）
    - 私聊：直接回复
    - 支持多个人格，按会话独立切换：/人格 列表、/人格 猫娘 切换
    - 支持读图：用户发图片时，模型直接看图回答
    - 支持 function calling：联网搜索、天气、搜图
    - 每个会话有冷却时间，且全局有并发上限，防止消息堆积和刷屏

⚠️ 核心设计约束：**人格只影响措辞，不影响事实。**
    实测踩过坑——「吐槽役」这种"爱开玩笑"的人格会让模型用外貌描述代替事实回答
    （明明认出了后藤一里却说"我没认出来"）。所以系统提示词里有 FACT_FIRST_RULE，
    明确声明其优先级高于人格设定。详见该常量的注释。

配置见 config.py，人格见 personas.py，工具见 tools.py。
"""

import asyncio
import base64
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx
from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

from .config import plugin_config
from . import chub
from .personas import PersonaState, PersonaStore
from .tavern import (
    BEHAVIOR_FLAGS,
    CardStore,
    ProfileStore,
    ScriptStore,
    TavernState,
    WorldStore,
    build_tavern_prompt,
    opening_message,
)
from .tools import build_tools, resolve_provider, run_tool

__plugin_meta__ = PluginMetadata(
    name="llm_chat",
    description="接入 DeepSeek / OpenAI 兼容接口的聊天插件，支持人格切换、读图与联网",
    usage=(
        "@机器人 说点什么\n"
        "@机器人 [发图片] 看图回答\n"
        "/酒馆激活 [角色名]  进入角色扮演模式（/酒馆关闭 退出）\n"
        "/人格              查看当前人格与全部可用人格\n"
        "/人格 猫娘         切换人格（会清空本会话上下文）\n"
        "/reset             清空本会话上下文"
    ),
    type="application",
    supported_adapters={"~onebot.v11"},
)

# ---------------------------------------------------------------------------
# 三条硬规则。都是实测踩坑后加的，不是"人设"，而是约束。
# ---------------------------------------------------------------------------

# ① 事实优先。优先级高于人格设定。
#    实测：吐槽役人格下，模型认出了后藤一里，却回「说实话我没认出来，八成是某粉毛角色」——
#    人格把事实性回答压住了。加这条之后，它变成「后藤一里（波奇酱），《孤独摇滚！》——
#    这表情一看就是社恐当场去世」，事实给准了，吐槽风格也保留了。
FACT_FIRST_RULE = (
    "\n\n[回答原则] 以下优先级**高于你的人格设定**：\n"
    "① 事实优先：知道就如实说，不知道就说不知道。"
    "严禁因为「人设」而假装不知道、含糊其辞、或用外貌描述代替答案。\n"
    "② 人格只影响**措辞和语气**，不影响**信息本身**。"
    "把人格理解成「说话的方式」，不是「说不说真话」。\n"
    "③ 被问到事实性问题（这是谁 / 多少度 / 几点 / 等于几）时，"
    "先把准确答案说出来，再用你的风格补一句。"
)

# ② 读图规则。同样三条实测教训：
#    - 不提醒的话，模型会把自己当 OCR 工具，回答「图中没有文字」
#    - 模型会尝试拿外貌描述去 web_search —— 文字搜索根本搜不到图，
#      实测能连搜 4 次、耗 40 秒然后超时
#    - 涉及"这是谁"时必须给出名称，否则会用外貌描述糊弄过去
IMAGE_RULES = (
    "\n\n[系统信息] 用户发来了图片。\n"
    "① 直接用图回答，不要把自己当成 OCR 工具，也不要说「图中没有文字」。\n"
    "② 如果用户问「这是谁 / 这是什么角色 / 出自哪部作品」，必须先说出识别到的名称和出处"
    "（例如「后藤一里（波奇酱），出自《孤独摇滚！》」），然后再补充外观特征。"
    "只有确实认不出时才说不认识，不许用外貌描述代替回答。\n"
    "③ 辨认图片内容时不要调用 web_search：拿文字描述去搜图是搜不到的，"
    "只会白白浪费时间并导致超时。直接凭你已有的知识回答。"
)

# 只发图、没带文字时用的兜底提示。
#
# ⚠️ 刻意写得极短。实测（百炼 qwen3-vl-235b，同一张图各跑多次）：
#     问「这是谁」                        → ✅ 2/2 正确
#     问「请说明这是什么…如果是角色就说出名称和出处…」→ ❌ 编出别的角色名
#     带 system 消息（哪怕只有一句人设）   → ❌ 编出别的角色名
#   这个模型一旦收到指令性文字，就不再真正看图，转而按套路编答案。
#   所以识图一律走极简提问，见 _ask_llm 里的 minimal 分支。
IMAGE_ONLY_PROMPT = "这是谁？这是什么？"

chat = on_message(rule=to_me(), priority=90, block=False)
persona_cmd = on_command("人格", aliases={"persona"}, priority=5, block=True)
tavern_cmd = on_command("酒馆", aliases={"tavern"}, priority=5, block=True)


async def _pending_for_me(event: MessageEvent) -> bool:
    """只在「等这个人发新建内容」时才命中，避免抢走正常聊天。"""
    entry = _pending.get(_session_key(event))
    if not entry:
        return False
    if time.monotonic() - entry.get("at", 0) > PENDING_TTL:
        _pending.pop(_session_key(event), None)
        return False
    return str(entry.get("user_id")) == str(event.user_id)


# 向导的第二步：优先级高于普通聊天，且只在待输入状态下命中
new_input = on_message(rule=_pending_for_me, priority=1, block=True)
reset = on_command("reset", aliases={"清空对话"}, priority=5, block=True)

_personas = PersonaStore(plugin_config.llm_persona_file)
_persona_state = PersonaState(plugin_config.llm_persona_state_file)

# 酒馆：角色卡 + 剧本 + 各会话激活状态 + 用户人设/剧情大纲
_tavern_cards = CardStore(plugin_config.llm_tavern_cards_file)
_tavern_state = TavernState(plugin_config.llm_tavern_state_file)
_tavern_scripts = ScriptStore(plugin_config.llm_tavern_scripts_file)
_tavern_profile = ProfileStore(plugin_config.llm_tavern_profile_file)
_tavern_world = WorldStore(plugin_config.llm_tavern_world_file)

# 最近一次社区搜索的结果（按会话存），供 /酒馆导入 <序号> 使用
_last_search: dict[str, list[dict[str, Any]]] = {}

# 向导状态：/酒馆新建 选了类型后，等用户把内容发过来。
# key=会话, value={kind, step, user_id, at, draft}
_pending: dict[str, dict[str, Any]] = {}
PENDING_TTL = 300  # 5 分钟没发内容就作废

# 向导可新建的类型
NEW_KINDS: dict[str, str] = {
    "1": "persona",
    "2": "outline",
    "3": "world",
    "4": "card",
}
DEL_ALIAS = {
    "5": "del", "删": "del", "删除": "del",
}
KIND_LABEL = {
    "persona": "人设（我是谁）",
    "outline": "剧情大纲",
    "world": "世界书条目",
    "card": "角色卡",
}
KIND_ALIAS = {
    "1": "persona", "人设": "persona", "我是谁": "persona",
    "2": "outline", "大纲": "outline", "剧情": "outline",
    "3": "world", "世界书": "world", "设定": "world",
    "4": "card", "角色卡": "card", "角色": "card",
}

# 保留的历史条数取两者较大的：酒馆模式要更长才连贯，
# 实际用多少由 _ask_llm 按当前模式切片决定。
_history: dict[str, deque[dict[str, str]]] = defaultdict(
    lambda: deque(
        maxlen=max(
            plugin_config.llm_max_history, plugin_config.llm_tavern_max_history, 1
        )
    )
)
_last_reply_at: dict[str, float] = {}
_semaphore = asyncio.Semaphore(max(plugin_config.llm_max_concurrency, 1))

# 每个会话最近一次收到的图片 (data URI 列表, 收到时刻)。
# 用户经常把图片和问题分成两条消息发（先发图，隔几秒再问「这是谁」），
# 不做处理的话，问问题那条消息里根本没有图，模型只能照着历史里的
# 「[发了一张图片]」和上一条的元素描述复述——表现就是「只会分析元素、认不出内容」。
_recent_image: dict[str, tuple[list[str], float]] = {}

# 会话数量上限，超过时清理最久未活跃的，防止内存无界增长
_MAX_SESSIONS = 2000


def _session_key(event: MessageEvent) -> str:
    """群聊按群维度共享上下文，私聊按用户维度。"""
    group_id = getattr(event, "group_id", None)
    return f"group:{group_id}" if group_id else f"private:{event.user_id}"


def _current_persona_name(session_key: str) -> str:
    """取会话当前人格；若人格已被删掉，自动回落到默认。"""
    name = _persona_state.get(session_key, plugin_config.llm_persona_default)
    if _personas.get(name) is None:
        name = plugin_config.llm_persona_default
    if _personas.get(name) is None:
        # 连默认人格都没了（文件被改坏），退化到第一个可用人格
        available = _personas.all()
        name = next(iter(available), plugin_config.llm_persona_default)
    return name


def _prune_sessions() -> None:
    # 过期的粘性图片顺手清掉，避免长时间运行后缓存里堆满 data URI
    cutoff = time.monotonic() - max(plugin_config.llm_sticky_image_seconds, 1) * 2
    for key in [k for k, (_, at) in _recent_image.items() if at < cutoff]:
        _recent_image.pop(key, None)

    if len(_history) <= _MAX_SESSIONS:
        return
    stale = sorted(_last_reply_at.items(), key=lambda kv: kv[1])[: len(_history) - _MAX_SESSIONS]
    for key, _ in stale:
        _history.pop(key, None)
        _last_reply_at.pop(key, None)
        _recent_image.pop(key, None)


def _build_system_prompt(
    persona: dict[str, Any], persona_name: str, has_images: bool
) -> str:
    """组装系统提示词：人格设定 + 硬规则 + 客观信息。"""
    parts: list[str] = []

    base = (persona.get("prompt") or plugin_config.llm_system_prompt or "").strip()
    if base:
        # 人格提示词里可以用 {model} / {persona} 占位符
        base = base.replace("{model}", plugin_config.llm_model).replace(
            "{persona}", persona_name
        )
        parts.append(base)

    # 紧跟在人格后面，让它明确是"对人设的约束"
    parts.append(FACT_FIRST_RULE.strip())

    if plugin_config.llm_inject_date:
        parts.append(f"[系统信息] 今天的日期是 {time.strftime('%Y-%m-%d')}。")

    if plugin_config.llm_reveal_model:
        # 模型自己并不知道这些信息，不告诉它就只能含糊其辞
        parts.append(
            f"[系统信息] 你实际由 {plugin_config.llm_model} 模型驱动，"
            f"通过 {plugin_config.llm_api_base} 接入。"
            "如果用户问你是谁、用什么模型、什么版本，直接如实回答，不要含糊其辞或推脱。"
        )

    if has_images:
        parts.append(IMAGE_RULES.strip())

    return "\n\n".join(parts)


def _image_info(data: bytes) -> str:
    """从字节流头部读出格式和尺寸，不依赖 Pillow。

    用来诊断「模型到底收到了多大的图」——QQ 会对聊天图片做有损压缩，
    分辨率太低会直接影响识别率。
    """
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return f"PNG {w}x{h}"
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5 : i + 7], "big")
                    w = int.from_bytes(data[i + 7 : i + 9], "big")
                    return f"JPEG {w}x{h}"
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
            return "JPEG ?x?"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w = int.from_bytes(data[6:8], "little")
            h = int.from_bytes(data[8:10], "little")
            return f"GIF {w}x{h}"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "WebP"
    except Exception:  # noqa: BLE001
        pass
    return "未知格式"


async def _collect_images(event: MessageEvent) -> list[str]:
    """把消息里的图片下载下来转成 data URI，喂给支持视觉的模型。

    为什么不直接把 QQ 的图片 URL 交给模型：那些链接带时效 token，
    而且供应商侧不一定能访问到腾讯的 CDN。自己下载再 base64 最稳。

    每次下载都会记录字节数和分辨率——识别率不对时先看这条日志。
    """
    if not plugin_config.llm_vision_enabled:
        return []

    uris: list[str] = []
    for segment in event.message:
        if segment.type != "image":
            continue
        if len(uris) >= max(plugin_config.llm_max_images, 0):
            logger.info(
                f"llm_chat: 图片数量超过上限（{plugin_config.llm_max_images} 张），其余已忽略"
            )
            break
        url = str(segment.data.get("url") or "").strip()
        if not url:
            continue
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            data = resp.content
            if not data:
                continue

            mime = (resp.headers.get("content-type") or "").split(";")[0].strip() or "?"
            logger.info(
                f"llm_chat: 收到图片 {len(data)} 字节 / {mime} / {_image_info(data)}"
            )
            logger.info(f"llm_chat: 图片地址 {url}")

            # 调试用：把原图存下来，方便在服务器上直接拿去试不同模型/预处理
            if plugin_config.llm_debug_save_images:
                try:
                    dump = Path(plugin_config.llm_persona_file).parent / "received"
                    dump.mkdir(parents=True, exist_ok=True)
                    ext = {"image/png": "png", "image/gif": "gif"}.get(mime, "jpg")
                    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{len(data)}.{ext}"
                    path = dump / name
                    path.write_bytes(data)
                    logger.info(f"llm_chat: 已保存原图到 {path}")
                except OSError as exc:
                    logger.warning(f"llm_chat: 保存图片失败：{exc}")

            if len(data) > plugin_config.llm_max_image_bytes:
                # 不能静默丢弃：模型收不到图却仍会硬答，结果更糟
                logger.warning(
                    f"llm_chat: 图片超过上限已跳过（{len(data)} > "
                    f"{plugin_config.llm_max_image_bytes}），模型这次看不到这张图。"
                    "可调大 LLM_MAX_IMAGE_BYTES。"
                )
                continue

            if not mime.startswith("image/"):
                mime = "image/jpeg"
            uris.append(f"data:{mime};base64,{base64.b64encode(data).decode()}")
        except Exception as exc:  # noqa: BLE001 - 单张图失败不影响其他
            logger.warning(f"llm_chat: 下载图片失败（{url[:60]}…）：{exc}")

    return uris


async def _ask_llm(
    session_key: str,
    text: str,
    persona: dict[str, Any],
    persona_name: str,
    input_images: list[str] | None = None,
    user_name: str = "用户",
    user_id: str = "",
) -> tuple[str, list[str]]:
    """向模型提问，必要时自动执行联网搜索。返回 (回复文本, 待发送的图片URL)。

    input_images 是**用户发来的图**（已转成 data URI），与函数内部
    工具搜到的 found_images 是两回事。

    会话若处于酒馆模式，则走角色卡提示词、跳过事实优先规则、放宽历史长度。
    """
    history = _history[session_key]

    # ---- 酒馆模式 ----
    tav = _tavern_state.get(session_key)
    tav_card = _tavern_cards.get(tav["card"]) if tav["active"] else None
    if tav["active"] and not tav_card:
        # 卡被删了，自动退出，避免一直演一个不存在的角色
        _tavern_state.set(session_key, active=False)
        logger.warning(f"llm_chat: 酒馆角色「{tav['card']}」已不存在，已自动退出酒馆模式")
        tav = {"active": False, "card": ""}

    model_name = persona.get("model") or plugin_config.llm_model
    # 带图时可以用单独的视觉模型 + 单独的接口（供应商可不同）
    has_images = bool(input_images)
    api_base = plugin_config.llm_api_base
    api_key = plugin_config.llm_api_key
    if has_images:
        if plugin_config.llm_vision_model:
            model_name = plugin_config.llm_vision_model
        if plugin_config.llm_vision_api_base:
            api_base = plugin_config.llm_vision_api_base
        if plugin_config.llm_vision_api_key:
            api_key = plugin_config.llm_vision_api_key

    # 极简模式：带图时只发「用户这一条消息 + 图片」，不带 system 提示词、不带历史。
    # 实测百炼 qwen3-vl 收到任何指令性文字就不看图了（见 IMAGE_ONLY_PROMPT 注释）。
    # 代价：识图这一轮不会体现人格语气，也接不上之前的对话上下文。
    minimal = has_images and plugin_config.llm_vision_minimal_prompt

    temperature = persona.get("temperature", plugin_config.llm_temperature)

    messages: list[dict[str, Any]] = []
    if not minimal:
        if tav_card:
            # 闲聊模式：只用角色卡，不注入背景（纯聊天）
            # 剧本模式：角色 + 背景设定 + 你的身份 + 行为开关
            is_script = tav.get("mode") == "script"
            script_def = (
                _tavern_scripts.get(tav.get("script") or "") if is_script else None
            ) or {}
            bg = str(script_def.get("background") or "") if is_script else ""
            # 用户另外写的剧情方向，附在剧本背景后面
            own = _tavern_profile.outline(session_key) if is_script else ""
            if own and own not in bg:
                bg = (bg + "\n\n[用户补充的剧情方向]\n" + own).strip() if bg else own
            messages.append(
                {
                    "role": "system",
                    "content": build_tavern_prompt(
                        tav_card,
                        char_name=tav_card["name"],
                        user_name=user_name,
                        user_persona=(
                            _tavern_profile.user_persona(user_id) if is_script else ""
                        ),
                        background=bg,
                        # 世界书两种模式都生效：它是"背景事实"，不是"剧情走向"
                        world=_tavern_world.match(
                            [text]
                            + [str(m.get("content") or "") for m in list(history)[-4:]],
                            plugin_config.llm_tavern_world_limit,
                        ),
                    ),
                }
            )
            model_name = plugin_config.llm_tavern_model or model_name
            temperature = plugin_config.llm_tavern_temperature
        else:
            messages.append(
                {
                    "role": "system",
                    "content": _build_system_prompt(persona, persona_name, has_images),
                }
            )
        # 酒馆要更长的上下文才连贯，普通模式短一点省 token
        limit = (
            plugin_config.llm_tavern_max_history
            if tav_card
            else plugin_config.llm_max_history
        )
        messages.extend(list(history)[-max(limit, 1) :])

    # 带图时 content 要用多模态数组；不带图就用普通字符串（兼容性最好）
    if has_images:
        user_content: Any = [{"type": "text", "text": text or IMAGE_ONLY_PROMPT}]
        user_content += [
            {"type": "image_url", "image_url": {"url": uri}} for uri in input_images
        ]
    else:
        user_content = text
    messages.append({"role": "user", "content": user_content})

    url = api_base.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # 极简模式下连工具也不给：实测带上 tools 参数同样会让它不认真看图，
    # 转而去调 image_search（拿外貌描述搜图，必然搜不到）。
    tools = (
        build_tools()
        if (plugin_config.search_enabled and not minimal)
        else None
    )
    provider = resolve_provider(plugin_config.search_provider, plugin_config.bocha_api_key)
    # 工具搜到的、要发给用户的图片；注意与入参 input_images 区分开
    found_images: list[str] = []

    answer = ""
    async with httpx.AsyncClient(timeout=plugin_config.llm_timeout) as client:
        # 工具调用循环：模型可能连续几轮调用工具，最多 search_max_rounds 轮
        for _ in range(max(plugin_config.search_max_rounds, 1)):
            payload: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            message = resp.json()["choices"][0]["message"]

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                answer = (message.get("content") or "").strip()
                break

            # 把模型这轮的 tool_calls 原样回填，再逐条塞入工具结果
            messages.append(message)
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                logger.info(f"llm_chat: 调用工具 {name} args={fn.get('arguments')}")
                result = await run_tool(
                    name,
                    fn.get("arguments", "{}"),
                    provider=provider,
                    api_key=plugin_config.bocha_api_key,
                    count=plugin_config.search_count,
                    max_images=plugin_config.search_max_images,
                    timeout=plugin_config.search_timeout,
                )
                found_images.extend(result.images)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result.content,
                    }
                )
        else:
            # 循环用尽仍在调工具，兜底再要一次纯文本回答
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            answer = (resp.json()["choices"][0]["message"].get("content") or "").strip()

    if not answer:
        answer = "……我一时语塞了。"

    # 仅在成功后写入历史，失败不会污染上下文
    # 历史里只存文本：图片本身不进上下文，省 token，也不会撑爆上下文窗口
    history.append({"role": "user", "content": text or "[发了一张图片]"})
    history.append({"role": "assistant", "content": answer})

    # 去重并限量（这里处理的是工具搜到的图，不是用户发来的图）
    deduped: list[str] = []
    seen: set[str] = set()
    for u in found_images:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return answer, deduped[: max(plugin_config.search_max_images, 0)]


@chat.handle()
async def handle_chat(bot: Bot, event: MessageEvent):
    if not plugin_config.llm_api_key:
        # 没配 Key 就静默退出，避免每来一条消息都报错
        logger.warning("llm_chat: 未配置 LLM_API_KEY，跳过应答")
        return

    text = event.message.extract_plain_text().strip()

    # 图片单独收集（QQ 的 [CQ:image] 段不在 plaintext 里）
    input_images = await _collect_images(event)

    session_key = _session_key(event)

    # 图片先记进会话缓存：即使这条因为冷却被忽略，图也要留着给后面的追问用
    if input_images:
        _recent_image[session_key] = (input_images, time.monotonic())
    elif text and plugin_config.llm_sticky_image_seconds > 0:
        # 本条没带图，但可能是「先发图、再问这是什么」的追问。
        # 不沿用的话，模型看到的只有历史里那句「[发了一张图片]」和元素描述，
        # 只能照着复述，表现就是「只会分析元素、认不出内容」。
        cached = _recent_image.get(session_key)
        if cached and time.monotonic() - cached[1] <= plugin_config.llm_sticky_image_seconds:
            input_images = cached[0]
            logger.info(
                f"llm_chat: 本条无图片，沿用 {time.monotonic() - cached[1]:.0f} 秒前的那张"
                "（用户把图片和问题分了两条发）"
            )

    # 只发了个表情之类既没文字也没图的消息，不处理
    if not text and not input_images:
        return

    now = time.monotonic()

    if now - _last_reply_at.get(session_key, 0.0) < plugin_config.llm_cooldown_seconds:
        return  # 冷却中，静默忽略

    if _semaphore.locked():
        return  # 并发已满，丢弃而不是排队

    persona_name = _current_persona_name(session_key)
    persona = _personas.get(persona_name) or {}

    async with _semaphore:
        _last_reply_at[session_key] = time.monotonic()
        _prune_sessions()
        try:
            reply, found_images = await _ask_llm(
                session_key,
                text,
                persona,
                persona_name,
                input_images,
                user_name=_user_name(event),
                user_id=str(event.user_id),
            )
        except httpx.TimeoutException:
            logger.warning("llm_chat: 请求 LLM 超时")
            await chat.finish("我想得太久了，等下再问我吧～")
        except httpx.HTTPStatusError as exc:
            logger.warning(f"llm_chat: LLM 返回错误状态 {exc.response.status_code}")
            await chat.finish("我的大脑暂时连不上了，稍后再试。")
        except Exception as exc:  # noqa: BLE001 - 兜底，任何异常都不该让机器人崩掉
            logger.exception(f"llm_chat: 调用 LLM 失败: {exc}")
            await chat.finish("我这边出了点问题，稍后再试。")

    # 先发文字，再发搜索到的图片（图片直接由 NapCat 拉取 URL，无需下载到本地）
    await chat.send(reply)
    for image_url in found_images:
        try:
            await chat.send(MessageSegment.image(image_url))
        except Exception as exc:  # noqa: BLE001 - 单张图失败不影响其他图
            logger.warning(f"llm_chat: 发送图片失败 {image_url}: {exc}")


async def _can_switch_persona(bot: Bot, event: MessageEvent) -> bool:
    """谁能切换人格：配置放开 / 机器人管理员 / 群主与群管理员 / 私聊本人。"""
    if plugin_config.llm_persona_open:
        return True
    if await SUPERUSER(bot, event):
        return True

    group_id = getattr(event, "group_id", None)
    if not group_id:
        return True  # 私聊只有本人在，允许

    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=event.user_id)
        return info.get("role") in ("owner", "admin")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"llm_chat: 查询群成员身份失败，按无权限处理: {exc}")
        return False


@persona_cmd.handle()
async def handle_persona(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    session_key = _session_key(event)
    available = _personas.all()
    current = _current_persona_name(session_key)
    name = args.extract_plain_text().strip()

    # 不带参数：列出人格
    if not name:
        lines = [f"当前人格：{current}", "", f"可用人格（共 {len(available)} 个）："]
        for persona_name, cfg in available.items():
            mark = "▸" if persona_name == current else " "
            desc = cfg.get("description") or ""
            lines.append(f"{mark} {persona_name}" + (f" —— {desc}" if desc else ""))
        lines += ["", "切换：/人格 名字"]
        await persona_cmd.finish("\n".join(lines))

    # 支持用「默认」这个关键词快速回到默认人格
    if name in ("默认", "default"):
        name = plugin_config.llm_persona_default

    if name not in available:
        await persona_cmd.finish(f"没有叫「{name}」的人格。发送 /人格 看看有哪些可用的。")

    if not await _can_switch_persona(bot, event):
        await persona_cmd.finish("切换人格需要机器人管理员，或本群群主/管理员的权限。")

    _persona_state.set(session_key, name)
    # 换人格时清空上下文，避免上一个人格的对话风格串味
    _history.pop(session_key, None)
    desc = available[name].get("description") or ""
    suffix = f"（{desc}）" if desc else ""
    await persona_cmd.finish(f"已切换为「{name}」人格{suffix}，本会话上下文已清空。")


@new_input.handle()
async def handle_new_input(bot: Bot, event: MessageEvent):
    """向导第二步：接收用户发来的内容，或删除时选的序号。"""
    session_key = _session_key(event)
    entry = _pending.get(session_key)
    if not entry:
        return
    text = event.message.extract_plain_text().strip()
    if not text:
        return
    user_id = str(event.user_id)
    kind = entry.get("kind", "")

    if text in ("取消", "算了", "cancel", "退出"):
        _pending.pop(session_key, None)
        await new_input.finish("已取消。")

    # ---------- 选类型 ----------
    if kind == "pick":
        picked = _resolve_kind(text)
        if not picked:
            await new_input.finish(
                "没认出是哪个类型。回复 1~4，或直接发「人设」「大纲」「世界书」「角色卡」。\n"
                "发「取消」退出。"
            )
        action = entry.get("action", "new")
        if action == "new":
            _pending[session_key] = {
                "kind": picked,
                "step": 0,
                "user_id": event.user_id,
                "at": time.monotonic(),
                "draft": {},
            }
            await new_input.finish(_ask_kind(picked, session_key, user_id))

        # 删除：先列可删项
        items = _deletable(picked, session_key, user_id)
        if not items:
            _pending.pop(session_key, None)
            await new_input.finish(f"没有可删的{KIND_LABEL[picked]}。")
        lines = [f"🗑 可删除的{KIND_LABEL[picked]}：", ""]
        lines += [f"  {i}. {name}" for i, (name, _) in enumerate(items, 1)]
        lines += ["", "回复序号删除，或发「取消」。"]
        _pending[session_key] = {
            "kind": "del",
            "target": picked,
            "items": items,
            "user_id": event.user_id,
            "at": time.monotonic(),
            "draft": {},
            "step": 0,
        }
        await new_input.finish("\n".join(lines))

    # ---------- 删除流程：等序号 ----------
    if kind == "del":
        target = entry.get("target", "")
        items = entry.get("items") or []
        if not text.isdigit() or not (1 <= int(text) <= len(items)):
            await new_input.finish(f"请回复 1~{len(items)} 的序号，或发「取消」。")
        _, value = items[int(text) - 1]
        if target == "persona":
            _tavern_profile.set_user_persona(user_id, "")
            msg = "✅ 已删除我的人设"
        elif target == "outline":
            _tavern_profile.set_outline(session_key, "")
            msg = "✅ 已删除本会话的剧情大纲"
        elif target == "world":
            ok, detail = _tavern_world.remove(value)
            msg = ("✅ " if ok else "❌ ") + detail
        elif target == "card":
            ok, detail = _tavern_cards.remove(value)
            msg = ("✅ " if ok else "❌ ") + detail
        else:
            msg = "❌ 未知类型"
        _pending.pop(session_key, None)
        await new_input.finish(msg)

    # ---------- 新建流程 ----------
    if kind == "persona":
        _tavern_profile.set_user_persona(user_id, text)
        _pending.pop(session_key, None)
        await new_input.finish(f"✅ 人设已保存：\n{text}")

    if kind == "outline":
        _tavern_profile.set_outline(session_key, text)
        _pending.pop(session_key, None)
        n = len(text.splitlines())
        await new_input.finish(f"✅ 剧情大纲已保存（{n} 行）。想删就发 /酒馆删除。")

    if kind == "world":
        if entry.get("step", 0) == 0:
            entry["draft"]["keys"] = text
            entry["step"] = 1
            entry["at"] = time.monotonic()
            await new_input.finish(
                "第二步：把**设定内容**发给我。\n"
                "（命中关键词时要注入的事实性设定，写多长都行）"
            )
        keys = entry["draft"].get("keys", "")
        name = keys.replace("，", ",").split(",")[0].strip() or "未命名"
        count, msg = _tavern_world.import_raw(
            [{"name": name, "keys": keys, "content": text}], replace=False
        )
        _pending.pop(session_key, None)
        if not count:
            await new_input.finish(f"❌ {msg}")
        await new_input.finish(f"✅ {msg}\n条目名：{name}\n触发词：{keys}")

    if kind == "card":
        if entry.get("step", 0) == 0:
            entry["draft"]["name"] = text
            entry["step"] = 1
            entry["at"] = time.monotonic()
            await new_input.finish(
                "第二步：把这个角色的**设定**发给我。\n"
                "（外貌、身份、背景。性格和开场白之后可以在管理台补）"
            )
        name = entry["draft"]["name"]
        ok, msg = _tavern_cards.upsert({"name": name, "description": text})
        _pending.pop(session_key, None)
        if not ok:
            await new_input.finish(f"❌ {msg}")
        await new_input.finish(
            f"✅ {msg}\n\n想补开场白/性格，去管理台「酒馆」页编辑；"
            f"想删就发 /酒馆删除。"
        )

    # ---------- 搜索社区卡：收到关键词 ----------
    if kind == "search":
        _pending.pop(session_key, None)
        try:
            results = await chub.search(text, limit=8)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"llm_chat: chub 搜索失败：{exc}")
            await new_input.finish(f"搜索失败了：{exc}")
        if not results:
            await new_input.finish(f"没搜到「{text}」。换个关键词试试（英文结果更多）。")
        _last_search[session_key] = results
        results = await _localize(results)
        _last_search[session_key] = results
        items = _search_items(results)
        await new_input.finish(
            _set_pick(session_key, event, items, "import",
                      f"🔍 「{text}」找到 {len(results)} 张（按星标排序），选一个导入")
        )

    # ---------- 搜索社区世界书 ----------
    if kind == "search_world":
        _pending.pop(session_key, None)
        try:
            results = await chub.search(text, limit=8, lorebooks_only=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"llm_chat: lorebook 搜索失败：{exc}")
            await new_input.finish(f"搜索失败了：{exc}")
        if not results:
            await new_input.finish(
                f"没搜到「{text}」相关的世界书。\n社区里世界书比角色卡少，换个更宽的关键词试试。"
            )
        results = await _localize(results)
        items = _search_items(results, with_tokens=True)
        await new_input.finish(
            _set_pick(session_key, event, items, "import_world",
                      f"📖 「{text}」找到 {len(results)} 部世界书，选一部导入")
        )

    # ---------- 二级菜单：选了某一项 ----------
    if kind == "select":
        action = entry.get("action", "")
        items = entry.get("items") or []
        picked = _pick_index(text, len(items))
        if picked is None:
            await new_input.finish(f"请回复 1~{len(items)} 的序号，或发「取消」。")
        label, value = items[picked]

        if action in ("open_chat", "open"):
            _pending.pop(session_key, None)
            _tavern_state.set(session_key, active=True, card=value,
                              mode="chat", script="")
            _history.pop(session_key, None)
            card = _tavern_cards.all().get(value) or {}
            opening = opening_message(card, _user_name(event))
            await new_input.send(f"💬 闲聊模式 · 角色「{value}」")
            if opening:
                await new_input.finish(opening)
            await new_input.finish("直接说话就行，/酒馆 看全部指令。")

        if action == "open_script":
            _pending.pop(session_key, None)
            if not await _can_switch_persona(bot, event):
                await new_input.finish("开剧本需要机器人管理员，或本群群主/管理员的权限。")
            item = _tavern_scripts.get(value) or {}
            cards_all = _tavern_cards.all()
            target = item.get("card") or ""
            if target not in cards_all:
                await new_input.finish(
                    f"剧本「{value}」需要角色卡「{target}」，你卡库里没有。\n"
                    f"可以 /酒馆搜 找一张导入。"
                )
            # 剧本模式：角色 + 人设 + 大纲 一起定死
            _tavern_profile.set_user_persona(user_id, item.get("user_persona") or "")
            _tavern_profile.set_outline(session_key, item.get("outline") or "")
            _tavern_state.set(session_key, active=True, card=target,
                              mode="script", script=value)
            _history.pop(session_key, None)
            card = cards_all[target]
            opening = opening_message(card, _user_name(event))
            lines = [f"🎬 剧本模式 · 《{value}》", f"角色：{target}"]
            if item.get("user_persona"):
                lines.append(f"你的身份：{item['user_persona'][:50]}")
            if item.get("outline"):
                lines.append(f"剧情：{len(item['outline'].splitlines())} 幕已载入")
            lines.append("")
            if opening:
                lines.append(opening)
            await new_input.finish("\n".join(lines))

        if action == "view_script":
            _pending.pop(session_key, None)
            await new_input.finish(
                _script_detail(value, _tavern_cards.all())
                + f"\n\n想开演就发 /酒馆剧本 选《{value}》。"
            )

        if action == "view_world":
            _pending.pop(session_key, None)
            e = next((x for x in _tavern_world.all() if x["name"] == value), None)
            if not e:
                await new_input.finish("这条设定已经不存在了。")
            await new_input.finish(
                f"📖 {e['name']}\n关键词：{'、'.join(e['keys'])}\n\n{e['content']}"
            )

        if action == "import_world":
            _pending.pop(session_key, None)
            if not await _can_switch_persona(bot, event):
                await new_input.finish("导入需要机器人管理员，或本群群主/管理员的权限。")
            await new_input.send(f"⏳ 正在下载「{label}」，世界书可能很大，稍等…")
            entries, err = await chub.fetch_lorebook(value)
            if entries is None:
                await new_input.finish(f"❌ 导入失败：{err}")
            # 社区世界书动辄几百条、几万 token。全导进来会撑爆上下文，
            # 只取前面的（作者一般把最重要的放前面）。
            capped = entries[:60]
            count, msg = _tavern_world.import_raw(capped, replace=False)
            extra = f"（原书共 {len(entries)} 条，已取前 {len(capped)} 条）" if len(entries) > len(capped) else ""
            await new_input.finish(
                f"✅ {msg}{extra}\n\n"
                "聊到关键词时才会注入，不命中不占 token。\n"
                "/酒馆世界书 可以查看和删除。"
            )

        if action == "import":
            _pending.pop(session_key, None)
            if not await _can_switch_persona(bot, event):
                await new_input.finish("导入需要机器人管理员，或本群群主/管理员的权限。")
            await new_input.send(f"⏳ 正在导入「{label}」，稍等…")
            card, err = await chub.fetch_card(value)
            if card is None:
                await new_input.finish(f"❌ 导入失败：{err}")
            ok, msg = _tavern_cards.upsert(card)
            if not ok:
                await new_input.finish(f"❌ {msg}")
            name = (card.get("data") or card).get("name") or label
            await new_input.finish(f"✅ {msg}\n\n用 /酒馆开 就能选到它了。")

        _pending.pop(session_key, None)
        await new_input.finish("这一项没法处理，已退出。")

    # ---------- 只发图 / 其他 ----------
    _pending.pop(session_key, None)
    await new_input.finish("没识别到要做什么，已退出向导。/酒馆 重新开始。")


def _pick_index(text: str, total: int) -> int | None:
    """从回复里取序号，容忍「1」「1.」「第2个」这些写法。"""
    for ch in text:
        if ch.isdigit():
            idx = int(ch)
            if 1 <= idx <= total:
                return idx - 1
    return None


@reset.handle()
async def handle_reset(bot: Bot, event: MessageEvent):
    _history.pop(_session_key(event), None)
    await reset.finish("已清空本会话的上下文")


def _user_name(event: MessageEvent) -> str:
    """取用户的显示名，用于角色扮演里称呼他。"""
    sender = getattr(event, "sender", None)
    if sender is not None:
        name = (getattr(sender, "card", "") or getattr(sender, "nickname", "")).strip()
        if name:
            return name
    return f"用户{event.user_id}"


def _resolve_kind(text: str) -> str:
    """把用户回复解析成类型。容忍「1」「人设」「1.人设」这些写法。"""
    t = text.strip().lstrip("０-９0123456789.、，,。 ").strip()
    for alias, kind in KIND_ALIAS.items():
        if alias and alias in t:
            return kind
    # 纯数字：取第一个数字
    for ch in text:
        if ch in NEW_KINDS:
            return NEW_KINDS[ch]
    return ""


# 常见标签的本地译名。社区卡就那么几十个高频标签，
# 用字典比调模型快得多，也不用花钱。
TAG_ZH = {
    "Games": "游戏", "Game": "游戏", "Anime": "动漫", "Manga": "漫画",
    "Game Characters": "游戏角色", "Anime Game Characters": "动漫游戏角色",
    "Anime Characters": "动漫角色", "Original Characters": "原创角色", "OC": "原创角色",
    "Female": "女性", "Male": "男性", "Multiple": "多角色", "Non-binary": "非二元",
    "Fantasy": "奇幻", "Sci-fi": "科幻", "Science Fiction": "科幻",
    "Historical": "历史", "Modern": "现代", "Cyberpunk": "赛博朋克",
    "Horror": "恐怖", "Mystery": "悬疑", "Thriller": "惊悚",
    "Romance": "恋爱", "Comedy": "喜剧", "Drama": "剧情", "Slice of Life": "日常",
    "Action": "动作", "Adventure": "冒险", "Supernatural": "超自然",
    "RPG": "RPG", "Scenario": "剧情向", "TAVERN": "酒馆", "ROOT": "基础设定",
    "Politics": "政治", "Military": "军事", "School": "校园",
    "Image Generating": "配图生成", "Helpers": "辅助工具", "Language Model": "语言模型",
    "Vtuber": "虚拟主播", "Pokemon": "宝可梦", "Furry": "兽人",
    "Arknights": "明日方舟", "Genshin Impact": "原神",
}

# 明显是占位符的简介，翻译了也没意义
PLACEHOLDER_DESCS = {
    "creator's notes go here.",
    "creator's notes go here",
    "no description",
    "todo",
}


def _tag_zh(tag: str) -> str:
    return TAG_ZH.get(tag, tag)


def _desc_useful(text: str) -> bool:
    """判断简介是不是模板占位符（chub 建卡时默认带一句，作者常忘了改）。"""
    t = (text or "").strip().lower().rstrip(".")
    return bool(t) and t not in {p.rstrip(".") for p in PLACEHOLDER_DESCS}


async def _translate_lines(texts: list[str]) -> list[str]:
    """把一批英文简介翻成中文。

    一次调用翻全部，比逐条翻省得多。行数对不上就原样返回，
    避免翻译错位把 A 的简介贴到 B 上。
    """
    if not any(t.strip() for t in texts):
        return texts
    numbered = "\n".join(f"{i + 1}. {t.strip()}" for i, t in enumerate(texts))
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            resp = await client.post(
                plugin_config.llm_api_base.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {plugin_config.llm_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": plugin_config.llm_model,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是翻译器。把用户给的编号列表逐条译成简体中文。"
                                "必须保持相同的条数和编号，每行一条，只输出译文，"
                                "不要解释、不要合并、不要增删。原文已是中文的照抄。"
                            ),
                        },
                        {"role": "user", "content": numbered},
                    ],
                },
            )
            resp.raise_for_status()
            out = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"llm_chat: 简介翻译失败，改用原文：{exc}")
        return texts

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if len(lines) != len(texts):
        logger.warning(
            f"llm_chat: 翻译行数不匹配（{len(lines)} vs {len(texts)}），改用原文"
        )
        return texts

    result = []
    for i, orig in enumerate(texts):
        translated = __import__("re").sub(r"^\s*\d+\s*[.、)）:：]\s*", "", lines[i]).strip()
        result.append(translated or orig)
    return result


def _plain(text: str) -> str:
    """去掉 Markdown 标记并压成一行。QQ 不渲染 Markdown，留着只会显示成星号。"""
    import re as _re

    t = (text or "").replace("\r", " ")
    t = _re.sub(r"^\s*[-*+]\s+", "", t, flags=_re.M)  # 列表符号
    t = _re.sub(r"^#{1,6}\s*", "", t, flags=_re.M)  # 标题
    t = t.replace("**", "").replace("__", "").replace("*", "").replace("`", "")
    t = _re.sub(r"\s+", " ", t)
    # 折叠后行内的列表符会留在中间，换成顿号；
    # 但「：，」这种（原文是「**包括：** - xxx」）要还原成「：」
    t = _re.sub(r"\s[-–—•]\s", "，", t)
    t = _re.sub(r"([：:])，", r"\1", t)
    t = _re.sub(r"，{2,}", "，", t)
    return t.strip()


def _search_items(results: list[dict[str, Any]], *, with_tokens: bool = False) -> list[tuple[str, str]]:
    """把搜索结果转成编号菜单项。简介已在上层翻译过。"""
    items = []
    for r in results:
        label = f"⭐{r['stars']}　{r['name']}"
        if with_tokens:
            tok = f"{r['tokens'] // 1000}K" if r.get("tokens") else "?"
            label += f"　约 {tok} token"
        if r.get("desc_zh"):
            label += "\n     " + _plain(r["desc_zh"])[:70]
        if r.get("topics"):
            label += "\n     🏷 " + "、".join(_tag_zh(t) for t in r["topics"][:5])
        items.append((label, r["path"]))
    return items


async def _localize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给搜索结果补上中文简介 desc_zh。"""
    texts = []
    for r in results:
        d = r.get("desc") or ""
        texts.append(d if _desc_useful(d) else "")
    translated = await _translate_lines(texts)
    for r, t in zip(results, translated, strict=False):
        r["desc_zh"] = t if _desc_useful(r.get("desc") or "") else ""
    return results


def _script_detail(name: str, cards: dict[str, Any]) -> str:
    """剧本详情：只看，不改变任何状态。"""
    item = _tavern_scripts.get(name) or {}
    lines = [f"🎬 剧本：{name}", ""]
    if item.get("desc"):
        lines += [item["desc"], ""]
    card_name = item.get("card") or ""
    lines.append(
        f"使用角色：{card_name or '（不限）'}"
        + ("" if card_name in cards else "　⚠️ 你的卡库里没有这张")
    )
    if item.get("user_persona"):
        lines += ["", "建议人设（我是谁）：", f"  {item['user_persona']}"]
    if item.get("outline"):
        lines += ["", "剧情大纲："]
        lines += [f"  {n}. {t}" for n, t in enumerate(item["outline"].splitlines(), 1)]
    lines += ["", "/酒馆开　　选角色开始　　/酒馆套用　用它的人设和大纲"]
    return "\n".join(lines)


def _set_pick(
    session_key: str, event: MessageEvent, items: list[tuple[str, str]],
    action: str, title: str, hint: str = "回复序号",
) -> str:
    """把一组选项存进待选状态，返回编号菜单。

    统一走「列出来 → 回序号」的二级菜单：
    一级命令不带参数，用户不用记名字，也不会因为打错名字失败。
    """
    lines = [title, ""]
    lines += [f"  {i}. {label}" for i, (label, _) in enumerate(items, 1)]
    lines += ["", f"{hint}，或发「取消」退出。"]
    _pending[session_key] = {
        "kind": "select",
        "action": action,
        "items": items,
        "user_id": event.user_id,
        "at": time.monotonic(),
        "draft": {},
        "step": 0,
    }
    return "\n".join(lines)


def _ask_kind(kind: str, session_key: str, user_id: str) -> str:
    """向导提示语：告诉用户下一步该发什么。"""
    if kind == "persona":
        return (
            "🙋 好。把「你是谁」发给我，直接发一段话就行。\n"
            "例如：一个刚加完班的程序员，今天被领导骂了。\n\n"
            "（发「取消」可以退出）"
        )
    if kind == "outline":
        return (
            "📜 好。把剧情走向发给我，可以分多行写分幕。\n"
            "例如：\n第一幕：主角推门进来\n第二幕：两人吵起来\n第三幕：和解\n\n"
            "（发「取消」可以退出）"
        )
    if kind == "world":
        return (
            "📖 世界书条目分两步。\n\n"
            "第一步：把**关键词**发给我，逗号分隔。\n"
            "例如：罗德岛, 博士, 感染者\n\n"
            "（命中这些词时，这条设定才会被注入对话）"
        )
    if kind == "card":
        return (
            "🎭 角色卡分两步。\n\n"
            "第一步：把**角色名**发给我。\n"
            "例如：小酒馆老板娘"
        )
    return ""


def _deletable(kind: str, session_key: str, user_id: str) -> list[tuple[str, str]]:
    """列出可删项，返回 [(显示名, 定位用的值)]。"""
    if kind == "persona":
        text = _tavern_profile.user_persona(user_id)
        return [(text[:40] or "（空）", "persona")] if text else []
    if kind == "outline":
        text = _tavern_profile.outline(session_key)
        if not text:
            return []
        first = text.splitlines()[0][:36] if text.splitlines() else ""
        return [(f"{len(text.splitlines())} 幕 —— {first}", "outline")]
    if kind == "world":
        return [(e["name"], e["name"]) for e in _tavern_world.all()]
    if kind == "card":
        return [(name, name) for name in _tavern_cards.all()]
    return []


def _tavern_menu(session_key: str, user_id: str) -> str:
    """总菜单：状态一眼可见，指令按用途分组，尽量短。"""
    state = _tavern_state.get(session_key)
    cards = _tavern_cards.all()
    scripts = _tavern_scripts.all()
    world = _tavern_world.all()
    persona_text = _tavern_profile.user_persona(user_id)
    outline_text = _tavern_profile.outline(session_key)

    lines = ["🍺 酒馆"]

    # ---- 状态行：一眼看清在哪个模式 ----
    if state["active"] and state["card"]:
        if state.get("mode") == "script":
            lines.append(f"🎬 剧本模式《{state.get('script') or '—'}》")
            lines.append(f"角色：{state['card']}")
        else:
            lines.append("💬 闲聊模式")
            lines.append(f"角色：{state['card']}")
    else:
        lines.append("○ 未开启")
    lines.append("")

    # ---- 两个模式 + 开关，放最前面 ----
    lines += [
        "/酒馆闲聊　　选个角色，纯聊天",
        "/酒馆剧本　　选套剧本，按设定开演",
        "/酒馆关　　　退出",
        "",
        "/酒馆角色　　看当前角色卡",
        "/酒馆列表　　角色卡一览",
        "",
        "/酒馆新建　　新建人设／大纲／世界书／角色卡",
        "/酒馆删除　　删除自建的内容",
        "",
        "/酒馆搜　　　从社区找角色",
        "/酒馆搜剧情　从社区找世界观设定",
        "/酒馆世界书　世界观设定一览",
    ]
    if state.get("mode") == "script":
        lines += ["", "/酒馆人设　/酒馆大纲　　查看本剧本的人设与剧情"]
    return "\n".join(lines)


async def _enter_tavern(
    bot: Bot, event: MessageEvent, session_key: str, target: str, cards: dict
) -> None:
    """进入酒馆模式并发开场白。切角色会清空上下文，避免上个角色串味。"""
    if not await _can_switch_persona(bot, event):
        await tavern_cmd.finish("切换酒馆角色需要机器人管理员，或本群群主/管理员的权限。")

    _tavern_state.set(session_key, active=True, card=target)
    _history.pop(session_key, None)

    card = cards[target]
    opening = opening_message(card, _user_name(event))
    await tavern_cmd.send(f"🍺 已进入酒馆模式，你现在是「{target}」。")
    if opening:
        await tavern_cmd.finish(opening)
    await tavern_cmd.finish("直接说话就行，/酒馆 可以看全部指令。")


@tavern_cmd.handle()
async def handle_tavern(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    session_key = _session_key(event)
    user_id = str(event.user_id)
    cards = _tavern_cards.all()
    scripts = _tavern_scripts.all()
    state = _tavern_state.get(session_key)
    raw = args.extract_plain_text().strip()
    parts = raw.split(maxsplit=1)
    action = parts[0] if parts else ""
    argument = parts[1].strip() if len(parts) > 1 else ""

    # ---- 无参数：总菜单 ----
    if not raw:
        await tavern_cmd.finish(_tavern_menu(session_key, user_id))

    # ---- 状态 ----
    if action in ("状态", "status"):
        persona_text = _tavern_profile.user_persona(user_id)
        outline_text = _tavern_profile.outline(session_key)
        lines = ["🍺 酒馆状态", "─────────────"]
        if state["active"] and state["card"]:
            lines.append(f"模式：扮演中 —— 「{state['card']}」")
        else:
            lines.append("模式：未激活（普通助手）")
        lines.append(f"我的人设：{persona_text or '（未设置）'}")
        lines.append("")
        lines.append("剧情大纲：")
        lines.append(outline_text or "（未设置）")
        await tavern_cmd.finish("\n".join(lines))

    # ---- 关闭 ----
    if action in ("关闭", "关", "退出", "off", "结束"):
        _tavern_state.set(session_key, active=False)
        _history.pop(session_key, None)
        await tavern_cmd.finish("已退出酒馆模式，恢复普通助手。本会话上下文已清空。")

    # ---- 角色卡列表 ----
    if action in ("列表", "角色", "list"):
        if not cards:
            await tavern_cmd.finish("还没有角色卡，去管理台「酒馆」页添加，或用 /酒馆剧本 套用剧本。")
        lines = [f"🍺 角色卡（共 {len(cards)} 张）", "─────────────"]
        for name, card in cards.items():
            mark = "▸" if name == state["card"] and state["active"] else " "
            desc = (card.get("creator_notes") or card.get("description") or "")[:30]
            lines.append(f"{mark} {name}" + (f"\n    {desc}" if desc else ""))
        lines += ["", "/酒馆激活 角色名   进入扮演"]
        await tavern_cmd.finish("\n".join(lines))

    # ---- 世界书：列出来选 ----
    if action in ("世界书", "设定", "world"):
        entries = _tavern_world.all()
        if not entries:
            await tavern_cmd.finish("世界书是空的。用 /酒馆新建 选「世界书条目」加一条。")
        items = []
        for e in entries:
            flag = "" if e.get("enabled", True) else "（已停用）"
            items.append((f"{e['name']}{flag}", e["name"]))
        await tavern_cmd.finish(
            _set_pick(session_key, event, items, "view_world",
                      f"📖 世界书（{len(entries)} 条，命中关键词才注入）")
        )

    # ---- 搜索社区卡：先问关键词 ----
    if action in ("搜", "搜索", "search", "找"):
        _pending[session_key] = {
            "kind": "search", "user_id": event.user_id,
            "at": time.monotonic(), "draft": {}, "step": 0,
        }
        await tavern_cmd.finish(
            "🔍 把**关键词**发给我。\n"
            "例如：arknights　明日方舟　Amiya\n\n"
            "（社区卡大多是英文名，英文关键词结果更多）"
        )

    # ---- 搜剧情/世界书（社区 lorebook） ----
    if action in ("搜剧情", "剧情搜索", "搜设定", "剧情"):
        _pending[session_key] = {
            "kind": "search_world", "user_id": event.user_id,
            "at": time.monotonic(), "draft": {}, "step": 0,
        }
        await tavern_cmd.finish(
            "📖 把**关键词**发给我，我去社区搜世界观／剧情设定。\n"
            "例如：arknights　dragon age　cyberpunk\n\n"
            "（导入后会变成世界书条目，聊到关键词时自动生效）"
        )

    # ---- 导入：列出上次搜索结果 ----
    if action in ("导入", "import"):
        cached = _last_search.get(session_key) or []
        if not cached:
            await tavern_cmd.finish("还没搜过。先发 /酒馆搜 找角色。")
        items = _search_items(cached)
        await tavern_cmd.finish(
            _set_pick(session_key, event, items, "import",
                      f"📥 上次搜到的 {len(cached)} 条，选一个导入")
        )

    # ---- 剧本：选一套，按剧本背景开演 ----
    if action in ("剧本", "script"):
        if not scripts:
            await tavern_cmd.finish("剧本库是空的。")
        items = []
        for name, item in scripts.items():
            label = name
            if item.get("desc"):
                label += "\n     " + item["desc"][:50]
            bg = str(item.get("background") or "")
            label += f"\n     角色：{item.get('card') or '不限'}"
            if bg:
                label += "\n     " + bg[:46].replace("\n", " ") + "…"
            items.append((label, name))
        await tavern_cmd.finish(
            _set_pick(session_key, event, items, "open_script",
                      f"🎬 剧本模式 —— 选一套（{len(scripts)} 套）\n"
                      "会固定人设和角色，按剧本背景开始。")
        )

    # ---- 查看当前角色 ----
    if action in ("角色", "role", "当前角色"):
        if not state["active"] or not state["card"]:
            await tavern_cmd.finish("现在不在扮演中。用 /酒馆开 [角色] 开始。")
        card = cards.get(state["card"])
        if not card:
            await tavern_cmd.finish(f"角色卡「{state['card']}」已不存在。")
        lines = [f"🎭 当前角色：{state['card']}", ""]
        for label, key in [
            ("设定", "description"),
            ("性格", "personality"),
            ("场景", "scenario"),
        ]:
            if card.get(key):
                lines += [f"【{label}】{card[key]}", ""]
        if card.get("tags"):
            lines.append("标签：" + "、".join(card["tags"]))
        await tavern_cmd.finish("\n".join(lines))

    # ---- 删除/停用某个角色的分支走这里（保留原有“直接给角色名”行为） ----
        return


    # ---- 套用剧本自带的人设与大纲（可选） ----
    if action in ("套用", "用剧本", "apply"):
        if not argument or argument not in scripts:
            await tavern_cmd.finish("用法：/酒馆套用 剧本名（会填入该剧本自带的人设和大纲）")
        item = scripts[argument]
        filled = []
        if item.get("user_persona"):
            _tavern_profile.set_user_persona(user_id, item["user_persona"])
            filled.append(f"人设：{item['user_persona']}")
        if item.get("outline"):
            _tavern_profile.set_outline(session_key, item["outline"])
            n = len(item["outline"].splitlines())
            filled.append(f"大纲：{n} 幕已载入")
        if not filled:
            await tavern_cmd.finish(f"剧本「{argument}」没有自带的人设或大纲。")
        await tavern_cmd.finish(
            "✅ 已套用剧本自带设定\n" + "\n".join(f"  {f}" for f in filled)
            + "\n\n想改就用 /酒馆人设 和 /酒馆大纲 覆盖，想清空用 /酒馆清空人设 /酒馆清空大纲。"
        )

    # ---- 人设：只看 ----
    if action in ("人设", "persona", "我是谁"):
        current = _tavern_profile.user_persona(user_id)
        await tavern_cmd.finish(
            f"🙋 我的人设：\n{current or '（还没设）'}\n\n"
            "新建/修改：发送 /酒馆新建 选「人设」\n"
            "删除：发送 /酒馆删除 选「人设」"
        )

    # ---- 大纲：只看 ----
    if action in ("大纲", "outline", "剧情"):
        current = _tavern_profile.outline(session_key)
        if current:
            lines = ["📜 当前剧情大纲：", ""]
            lines += [f"  {n}. {t}" for n, t in enumerate(current.splitlines(), 1)]
        else:
            lines = ["📜 当前剧情大纲：（还没设）"]
        lines += ["", "新建/修改：发送 /酒馆新建 选「剧情大纲」"]
        await tavern_cmd.finish("\n".join(lines))

    # ---- 新建向导：第一步选类型 ----
    if action in ("新建", "加", "添加", "create", "new"):
        # 关键：弹菜单的同时就进入"等选择"状态，
        # 否则用户回复的「1」「人设」没人接，会被普通 AI 聊天抢走。
        _pending[session_key] = {
            "kind": "pick",
            "action": "new",
            "user_id": event.user_id,
            "at": time.monotonic(),
            "draft": {},
            "step": 0,
        }
        await tavern_cmd.finish(
            "✏️ 要新建什么？\n\n"
            "  1. 人设（我是谁）\n"
            "  2. 剧情大纲\n"
            "  3. 世界书条目\n"
            "  4. 角色卡\n\n"
            "回复序号或名称，例如发「1」或「人设」。\n"
            "（发「取消」可以退出）"
        )

    # ---- 删除向导：第一步选类型 ----
    if action in ("删除", "删", "del", "delete"):
        _pending[session_key] = {
            "kind": "pick",
            "action": "del",
            "user_id": event.user_id,
            "at": time.monotonic(),
            "draft": {},
            "step": 0,
        }
        await tavern_cmd.finish(
            "🗑 要删除什么？\n\n"
            "  1. 人设（我是谁）\n"
            "  2. 剧情大纲\n"
            "  3. 世界书条目\n"
            "  4. 角色卡\n\n"
            "回复序号或名称，我会列出可删的内容。\n"
            "（发「取消」可以退出）"
        )

    # ---- 激活 ----
    # ---- 闲聊：选角色，纯聊天 ----
    if action in ("激活", "开", "开启", "on", "开始", "enter", "闲聊", "聊天"):
        if not cards:
            await tavern_cmd.finish("还没有角色卡。用 /酒馆搜 从社区导入。")
        # 只列名字。社区卡自带的 creator_notes 常是作者名之类，
        # 跟在卡名后面会让人以为是一张叫「黍 琛紫枫」的卡，反而看不清。
        items = [(name, name) for name in cards]
        await tavern_cmd.finish(
            _set_pick(session_key, event, items, "open_chat",
                      f"💬 闲聊模式 —— 选一个角色（{len(cards)} 张）\n"
                      "只带角色设定，不套剧情，随便聊。")
        )

    # ---- 直接给角色名 ----
    if raw in cards:
        await _enter_tavern(bot, event, session_key, raw, cards)

    await tavern_cmd.finish(
        f"看不懂「{raw}」。发送 /酒馆 打开菜单，或 /酒馆激活 直接开始。"
    )
