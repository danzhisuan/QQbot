"""联网搜索工具模块自检。

用法：
    容器内：  docker exec nonebot python /app/scripts/test_tools.py
    本地：    python scripts/test_tools.py          （需要已装 httpx）
    跳过联网：加 --offline

默认会真的发一次搜索请求（Bing 免费，不消耗任何额度）来验证端到端可用。
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

TOOLS_PATH = Path(__file__).resolve().parent.parent / "src/plugins/llm_chat/tools.py"

spec = importlib.util.spec_from_file_location("llm_tools", TOOLS_PATH)
tools_mod = importlib.util.module_from_spec(spec)
sys.modules["llm_tools"] = tools_mod  # dataclass 需要能从 sys.modules 找到本模块
spec.loader.exec_module(tools_mod)

OFFLINE = "--offline" in sys.argv
_failed = False


def check(label: str, cond: bool, extra: str = "") -> None:
    global _failed
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        _failed = True


def run(name: str, args: str, provider: str = "bing", key: str = ""):
    return asyncio.run(
        tools_mod.run_tool(
            name, args, provider=provider, api_key=key,
            count=5, max_images=3, timeout=20,
        )
    )


def test_schema() -> None:
    tools = tools_mod.build_tools()
    names = [t["function"]["name"] for t in tools]
    check("工具数量为 3", len(tools) == 3, str(names))
    check("包含 get_weather", "get_weather" in names)
    for tool in tools:
        fn = tool["function"]
        check(f"schema 完整: {fn['name']}",
              bool(fn["name"] and fn["description"] and fn["parameters"]["required"]))


def test_provider_resolve() -> None:
    r = tools_mod.resolve_provider
    check("auto 无 key -> bing", r("auto", "") == "bing")
    check("auto 有 key -> bocha", r("auto", "k") == "bocha")
    check("显式 bing 生效", r("bing", "k") == "bing")
    check("大小写与空格容错", r(" Bocha ", "") == "bocha")


def test_bing_parsers() -> None:
    # 注意：bytes 字面量不能含非 ASCII，所以先写字符串再 encode
    rss = """<?xml version="1.0" encoding="utf-8" ?>
    <rss version="2.0"><channel>
      <item>
        <title>上海天气</title>
        <link>https://weather.example.com/sh</link>
        <description>今天&lt;b&gt;多云&lt;/b&gt; 转晴</description>
        <pubDate>Mon, 06 Oct 2026 10:00:00 GMT</pubDate>
      </item>
      <item><title>第二条</title><link>https://b.com</link><description>描述二</description></item>
    </channel></rss>""".encode("utf-8")
    text = tools_mod._parse_bing_rss(rss, 5)
    check("RSS 解析出标题", "上海天气" in text, text.splitlines()[0][:50])
    check("RSS 去掉了 HTML 标签", "<b>" not in text and "多云" in text)
    check("RSS 含链接与时间", "weather.example.com" in text and "2026" in text)
    check("空 RSS 不炸", "没有搜到" in tools_mod._parse_bing_rss(b"<rss><channel/></rss>", 5))

    html = (
        '<a m=\'{"murl":"https://pic.example.com/a.jpg","turl":"https://t/1.jpg"}\'></a>'
        '<a m=\'{"murl":"https://pic.example.com/b.png"}\'></a>'
        '<a m=\'{"murl":"https://pic.example.com/a.jpg"}\'></a>'
        '<a m=\'{"murl":"https://not-an-image.example.com/page"}\'></a>'
    )
    imgs = tools_mod._parse_bing_images(html, 10)
    check("图片提取去重", imgs == ["https://pic.example.com/a.jpg", "https://pic.example.com/b.png"],
          str(imgs))


def test_fallbacks() -> None:
    check("未知工具容错", "未知工具" in run("nope", "{}").content)
    check("空关键词容错", "缺少搜索关键词" in run("web_search", '{"query":""}').content)
    check("坏 JSON 容错", "解析失败" in run("web_search", "not-json").content)
    r = run("web_search", '{"query":"x"}', provider="bocha", key="")
    check("bocha 无 key 时降级", "未配置" in r.content)


def test_live() -> None:
    print("\n--- 真实联网测试（全部免费、无需 Key） ---")
    r = run("web_search", '{"query":"上海天气"}')
    ok = "搜索结果" in r.content and "http" in r.content
    check("web_search 真实可用", ok, (r.content[:120].replace("\n", " | ")))

    r = run("image_search", '{"query":"橘猫"}')
    ok = len(r.images) > 0
    check("image_search 真实可用", ok, f"取到 {len(r.images)} 张，例如 {r.images[0][:70] if r.images else '-'}")

    # 天气：必须有精确数值，不能只给网页链接
    r = run("get_weather", '{"city":"上海"}')
    has_numbers = "当前：" in r.content and "°C" in r.content and "今日（" in r.content
    check("get_weather 返回精确数值", has_numbers,
          r.content.splitlines()[2] if len(r.content.splitlines()) > 2 else r.content[:80])
    check("get_weather 含明日预报", "明日（" in r.content)

    # 容错
    r = run("get_weather", '{"city":""}')
    check("天气空城市容错", "缺少城市名" in r.content)
    r = run("get_weather", '{"city":"qqqzzz不存在的城"}')
    check("天气查不到城市容错", "没找到" in r.content)


def main() -> int:
    test_schema()
    test_provider_resolve()
    test_bing_parsers()
    test_fallbacks()
    if not OFFLINE:
        test_live()
    else:
        print("\n（已跳过联网测试）")

    print()
    print("结果:", "全部通过" if not _failed else "存在失败")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
