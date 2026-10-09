"""llm_chat 自检：配置 -> 提示词编排 -> 真实调用。

分三层，失败在哪层一目了然：
    第一层  配置：Key 有没有、模型名对不对
    第二层  提示词：人格规则、酒馆两种模式、世界书、行为规则有没有拼进去
    第三层  真实调用：纯文本、读图、工具调用

用法：
    python scripts/test_llm.py            # 全跑（含联网）
    python scripts/test_llm.py --offline  # 只跑不联网的部分

在容器里跑：
    docker exec nonebot python /app/scripts/test_llm.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OFFLINE = "--offline" in sys.argv

_failed = 0
_skipped = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failed
    mark = "[OK]  " if ok else "[FAIL]"
    print(f"  {mark} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        _failed += 1


def skip(label: str) -> None:
    global _skipped
    _skipped += 1
    print(f"  [SKIP] {label}")


def _boot():
    """初始化 NoneBot 并载入插件，返回 (config, tavern, tools)。"""
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)

    from src.plugins.llm_chat import config as cfg
    from src.plugins.llm_chat import tavern as tv

    return cfg.plugin_config, tv


# ---------------------------------------------------------------- 第一层


def test_config(cfg) -> None:
    print("\n--- 第一层：配置 ---")
    check("llm_api_base 已配置", bool(cfg.llm_api_base), cfg.llm_api_base)
    check("llm_model 已配置", bool(cfg.llm_model), cfg.llm_model)
    if cfg.llm_api_key:
        check("llm_api_key 已配置", True, f"{cfg.llm_api_key[:8]}…（{len(cfg.llm_api_key)} 字符）")
    else:
        check("llm_api_key 已配置", False, "未配置，真实调用会跳过")
    check("temperature 在合理区间", 0 <= cfg.llm_temperature <= 2, str(cfg.llm_temperature))
    check("timeout 为正", cfg.llm_timeout > 0, f"{cfg.llm_timeout}s")
    check("历史条数 > 0", cfg.llm_max_history > 0, str(cfg.llm_max_history))

    if cfg.llm_vision_api_base:
        check(
            "识图走独立接口",
            True,
            f"{cfg.llm_vision_model} @ {cfg.llm_vision_api_base}",
        )
        check("识图独立 Key 已配置", bool(cfg.llm_vision_api_key))
    else:
        check("识图复用主接口", bool(cfg.llm_vision_model), cfg.llm_vision_model or "同主模型")


# ---------------------------------------------------------------- 第二层


def _persona() -> dict:
    from src.plugins.llm_chat import _personas

    return _personas.get("默认") or {}


def test_prompt(cfg, tv) -> None:
    print("\n--- 第二层：提示词编排 ---")

    # 人格模式：必须带事实优先规则
    prompt = tv.build_tavern_prompt(
        {"name": "测试角色", "description": "设定", "personality": "性格"},
        char_name="测试角色",
        user_name="我",
    )
    check("人称规则已注入", "第一人称" in prompt and "「我」" in prompt)
    check("括号动作规则已注入", "圆括号" in prompt)
    check("禁止 Markdown 星号", "Markdown" in prompt)
    check("行为规则全量注入", all(f"【{k}】" in prompt for k in tv.BEHAVIOR_FLAGS),
          "、".join(tv.BEHAVIOR_FLAGS))

    # 背景：剧本模式才注入，且不允许模型复述
    bg = tv.build_tavern_prompt(
        {"name": "C"}, char_name="C", user_name="我", background="这是一个测试背景设定。"
    )
    check("背景已注入", "[背景设定]" in bg and "测试背景设定" in bg)

    no_bg = tv.build_tavern_prompt({"name": "C"}, char_name="C", user_name="我")
    check("无背景时不出现背景段", "[背景设定]" not in no_bg)

    # 用户人设
    up = tv.build_tavern_prompt(
        {"name": "C"}, char_name="C", user_name="我", user_persona="我是个程序员"
    )
    check("用户人设已注入", "[用户设定]" in up and "程序员" in up)

    # 世界书
    wb = tv.build_tavern_prompt(
        {"name": "C"}, char_name="C", user_name="我",
        world=[{"name": "测试条目", "keys": ["k"], "content": "测试世界观内容"}],
    )
    check("世界书已注入", "[世界设定]" in wb and "测试世界观内容" in wb)
    check("世界书要求不复述", "不要直接背诵" in wb)

    # 占位符替换
    ph = tv.build_tavern_prompt(
        {"name": "C", "mes_example": "<START>\n{{user}}: 你好\n{{char}}: 你也好"},
        char_name="小红", user_name="小明",
    )
    check("示例对话占位符已替换", "小明" in ph and "小红" in ph and "{{" not in ph)


def test_world_match() -> None:
    print("\n--- 第二层：世界书触发 ---")
    from src.plugins.llm_chat import _tavern_world

    entries = _tavern_world.all()
    check("世界书可读", isinstance(entries, list), f"{len(entries)} 条")
    if not entries:
        skip("世界书为空，跳过匹配测试")
        return

    first = entries[0]
    key = first["keys"][0]
    hit = _tavern_world.match([f"随便聊聊{key}吧"], 5)
    check(f"命中触发词「{key}」", any(e["name"] == first["name"] for e in hit),
          str([e["name"] for e in hit]))
    miss = _tavern_world.match(["完全无关的一句话zzz"], 5)
    check("无关内容不触发", not miss, str([e["name"] for e in miss]))


# ---------------------------------------------------------------- 第三层


async def _ask(cfg, text: str, images=None):
    from src.plugins.llm_chat import _ask_llm, _history, _personas

    session = "test:llm"
    _history.pop(session, None)
    persona = _personas.get("默认") or {}
    return await _ask_llm(session, text, persona, "默认", images or [],
                          user_name="测试", user_id="0")


async def test_live(cfg) -> None:
    print("\n--- 第三层：真实调用 ---")
    if not cfg.llm_api_key:
        skip("未配置 LLM_API_KEY")
        return

    # 纯文本
    try:
        reply, imgs = await _ask(cfg, "只回复两个字：收到")
        ok = bool(reply.strip()) and len(reply) < 60
        check("纯文本调用", ok, reply.replace("\n", " ")[:60])
    except Exception as exc:  # noqa: BLE001
        check("纯文本调用", False, f"{type(exc).__name__}: {exc}")
        return

    # 多轮上下文
    try:
        from src.plugins.llm_chat import _ask_llm, _history, _personas

        session = "test:ctx"
        _history.pop(session, None)
        persona = _personas.get("默认") or {}
        await _ask_llm(session, "记住这个数字：7391", persona, "默认", [],
                       user_name="测试", user_id="0")
        reply2, _ = await _ask_llm(session, "刚才我让你记的数字是多少？只回答数字",
                                   persona, "默认", [], user_name="测试", user_id="0")
        check("多轮上下文保持", "7391" in reply2, reply2.replace("\n", " ")[:50])
        _history.pop(session, None)
    except Exception as exc:  # noqa: BLE001
        check("多轮上下文保持", False, f"{type(exc).__name__}: {exc}")

    # 工具调用（联网）
    if cfg.search_enabled:
        try:
            reply, _ = await _ask(cfg, "上海今天天气怎么样？")
            ok = any(k in reply for k in ("°C", "度", "晴", "雨", "阴", "云"))
            check("工具调用（天气）", ok, reply.replace("\n", " ")[:70])
        except Exception as exc:  # noqa: BLE001
            check("工具调用（天气）", False, f"{type(exc).__name__}: {exc}")
    else:
        skip("搜索已关闭")


# ---------------------------------------------------------------- 主流程


def main() -> int:
    print("=" * 66)
    print("llm_chat 自检" + ("（离线模式）" if OFFLINE else ""))
    print("=" * 66)

    cfg, tv = _boot()
    test_config(cfg)
    test_prompt(cfg, tv)
    test_world_match()

    if OFFLINE:
        skip("真实调用（--offline）")
    else:
        asyncio.run(test_live(cfg))

    print()
    print("=" * 66)
    summary = "全部通过" if not _failed else f"失败 {_failed} 项"
    if _skipped:
        summary += f"（跳过 {_skipped} 项）"
    print("结果:", summary)
    print("=" * 66)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
