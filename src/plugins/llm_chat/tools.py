"""联网搜索工具：把搜索结果接成大模型的 function calling 工具。

支持两个后端，用 SEARCH_PROVIDER 选择（默认 auto）：

    bing   免费，无需 Key。文字走 Bing 的 RSS 输出（结构化 XML，不是脆弱的
           HTML 抓取），图片走 Bing 图片搜索。
           —— 注意：cn.bing.com 的 RSS 里 title/link 是全局的，description
           才是该条结果的摘要。

    bocha  博查 API，需 Key，中文搜索质量更好，一次请求同时返回网页和图片。

    auto   有 BOCHA_API_KEY 就用 bocha，否则用 bing。

接口文档：https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import unescape
from typing import Any

import httpx

logger = logging.getLogger("nonebot.plugin.llm_chat")

BOCHA_ENDPOINT = "https://api.bocha.cn/v1/web-search"
BING_SEARCH = "https://cn.bing.com/search"
BING_IMAGES = "https://cn.bing.com/images/search"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class ToolResult:
    """工具执行结果。

    content: 喂回给模型看的文本
    images:  机器人要直接发给用户的图片 URL
    """

    content: str
    images: list[str] = field(default_factory=list)


WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "联网搜索最新的网页信息。凡是涉及实时信息、新闻、股价、赛事、"
            "不认识的名词、你不确定的事实，都应该先调用它，不要凭记忆回答。"
            "搜索结果会以标题+摘要+链接的形式返回给你。"
            "注意：查天气请用 get_weather（搜索引擎给不出具体数值）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "搜索关键词。必须提取核心词，不要直接传用户的原句——"
                        "搜索引擎做的是字面匹配，长句会搜出毫不相关的东西。"
                        "例如用户问「今天上海天气怎么样」就用「上海天气」，"
                        "而不是「今天上海天气怎么样」。多个关键词用空格分隔。"
                    ),
                }
            },
            "required": ["query"],
        },
    },
}

IMAGE_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "image_search",
        "description": (
            "搜索图片并直接发送到当前聊天。当用户明确要图片时使用，"
            "例如「找几张猫的图」「发个表情包」「看看长什么样」。"
            "注意：只想要文字答案时请不要调用这个，用 web_search。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "图片关键词，用名词短语即可，例如「橘猫」「星空壁纸」「雪山」",
                }
            },
            "required": ["query"],
        },
    },
}


WEATHER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "查询指定城市的实时天气和今日预报，返回精确的气温、体感、湿度、"
            "风速、降水概率等数值。**凡是问天气、气温、下不下雨、穿什么衣服，"
            "必须用这个工具，不要用 web_search** —— 搜索引擎只返回网页标题和摘要，"
            "拿不到具体数值，会答不上来。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，中英文都行，例如「上海」「北京」「Tokyo」",
                }
            },
            "required": ["city"],
        },
    },
}

# WMO 天气代码 → 中文描述（Open-Meteo 用的就是这套编码）
WMO_CODES: dict[int, str] = {
    0: "晴", 1: "晴间多云", 2: "局部多云", 3: "阴",
    45: "有雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def build_tools() -> list[dict[str, Any]]:
    return [WEB_SEARCH_TOOL, IMAGE_SEARCH_TOOL, WEATHER_TOOL]


def _clean(text: Any) -> str:
    """去掉 HTML 标签并做实体解码，摘要里常带 <b> 之类。"""
    raw = unescape(str(text or ""))
    return re.sub(r"<[^>]+>", "", raw).strip()


# ---------------------------------------------------------------- 博查后端


def _bocha_pages(data: dict[str, Any], limit: int) -> str:
    pages = (data.get("data", {}).get("webPages", {}) or {}).get("value", []) or []
    if not pages:
        return "（没有搜到相关结果）"
    lines: list[str] = []
    for i, page in enumerate(pages[:limit], start=1):
        lines.append(f"[{i}] {_clean(page.get('name'))}")
        date = (page.get("datePublished") or "")[:10]
        if date:
            lines.append(f"    发布于 {date}")
        text = _clean(page.get("summary") or page.get("snippet"))
        if text:
            lines.append(f"    {text}")
        url = (page.get("url") or "").strip()
        if url:
            lines.append(f"    来源 {url}")
    return "\n".join(lines)


def _bocha_images(data: dict[str, Any], limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for item in (data.get("data", {}).get("images", {}) or {}).get("value", []) or []:
        url = item.get("contentUrl") or item.get("thumbnailUrl")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls


async def _fetch_bocha(
    query: str, *, api_key: str, count: int, timeout: float
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"query": query, "count": max(1, min(count, 50)),
               "summary": True, "freshness": "noLimit"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(BOCHA_ENDPOINT, headers=headers, json=payload)
        if resp.status_code == 401:
            raise RuntimeError("博查 API Key 无效")
        if resp.status_code == 403:
            raise RuntimeError("博查余额不足")
        if resp.status_code == 429:
            raise RuntimeError("博查请求频率超限")
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------- Bing 后端


def _parse_bing_rss(content: bytes, limit: int) -> str:
    """解析 Bing 的 RSS 输出。用字节解析，让 ElementTree 自己按声明编码处理。"""
    root = ET.fromstring(content)
    items = root.findall(".//item")
    if not items:
        return "（没有搜到相关结果）"
    lines: list[str] = []
    for i, item in enumerate(items[:limit], start=1):
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        desc = _clean(item.findtext("description"))
        pub = _clean(item.findtext("pubDate"))
        lines.append(f"[{i}] {title}")
        if pub:
            lines.append(f"    时间 {pub}")
        if desc:
            lines.append(f"    {desc}")
        if link:
            lines.append(f"    来源 {link}")
    return "\n".join(lines)


def _parse_bing_images(html: str, limit: int) -> list[str]:
    """从 Bing 图片搜索页里提取原图地址（murl 字段）。

    两段式：先只取带图片扩展名的（最可靠）；如果一个都没有，
    再退而接受全部候选——有些 CDN 的图片地址不带扩展名。
    """
    candidates: list[str] = []
    seen: set[str] = set()
    # Bing 把 JSON 放在 m 属性里，引号被 HTML 转义过；两种写法都兼容
    for pattern in (r'murl&quot;:&quot;(https?://[^&]+?)&quot;', r'"murl":"(https?://[^"]+?)"'):
        for url in re.findall(pattern, html):
            url = unescape(url)
            if url in seen or not url.startswith("http"):
                continue
            seen.add(url)
            candidates.append(url)
        if candidates:
            break

    strict = [
        u
        for u in candidates
        if re.search(r"\.(jpg|jpeg|png|gif|webp|bmp|avif)(\?|$)", u, re.I)
    ]
    return (strict or candidates)[:limit]


async def _fetch_bing_text(query: str, *, count: int, timeout: float) -> str:
    # ⚠️ Accept 头必须显式指定为 application/rss+xml。
    #    用默认的 */* 时 Bing 会返回 200 + 一个空 feed（约 900 字节、0 个 item），
    #    看起来完全正常却是空结果。实测踩过这个坑，别去掉这个头。
    headers = {"Accept": "application/rss+xml", "User-Agent": UA}
    params = {"q": query, "format": "rss"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(BING_SEARCH, params=params, headers=headers)
        resp.raise_for_status()
        return _parse_bing_rss(resp.content, count)


async def _fetch_bing_images(query: str, *, limit: int, timeout: float) -> list[str]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(
            BING_IMAGES,
            params={"q": query, "form": "HDRSC2", "first": "1"},
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh"},
        )
        resp.raise_for_status()
        html = resp.content.decode("utf-8", errors="replace")
        return _parse_bing_images(html, limit)


# ---------------------------------------------------------------- 天气
#
# 为什么单独做而不是靠搜索：搜索引擎只返回标题+摘要+链接，拿不到页面里的
# 具体数值。用户问「今天多少度」时，模型只能干瞪眼说「我没拿到数据」。
# Open-Meteo 免费、无需 Key、直出数值，一个城市 1-2 秒。


async def _geocode(city: str, timeout: float) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            GEOCODE_URL,
            params={"name": city, "count": 1, "language": "zh", "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results") or []
    return results[0] if results else None


async def _weather_report(city: str, timeout: float) -> str:
    place = await _geocode(city, timeout)
    if not place:
        return (
            f"没找到叫「{city}」的城市。请只传城市名（例如「上海」），"
            "不要带「天气」「今天」这些词。"
        )

    lat = place.get("latitude")
    lon = place.get("longitude")
    label = "".join(
        str(x) for x in (place.get("country"), place.get("admin1"), place.get("name")) if x
    ) or city

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,weather_code,wind_speed_10m"
                ),
                "daily": (
                    "temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,weather_code,sunrise,sunset"
                ),
                "timezone": "Asia/Shanghai",
                "forecast_days": 2,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    today = dates[0] if dates else ""

    def g(key: str) -> Any:
        vals = daily.get(key) or []
        return vals[0] if vals else None

    def g2(key: str) -> Any:
        vals = daily.get(key) or []
        return vals[1] if len(vals) > 1 else None

    code = int(cur.get("weather_code") or 0)
    today_code = int(g("weather_code") or 0)
    condition = WMO_CODES.get(code, f"未知({code})")
    today_condition = WMO_CODES.get(today_code, f"未知({today_code})")

    lines = [
        f"【{label}】实况与今明预报",
        f"观测时间：{cur.get('time', '—')}",
        f"当前：{condition}，{cur.get('temperature_2m')}°C"
        f"（体感 {cur.get('apparent_temperature')}°C）",
        f"湿度：{cur.get('relative_humidity_2m')}%　"
        f"风速：{cur.get('wind_speed_10m')} km/h　"
        f"降水量：{cur.get('precipitation')} mm",
        f"今日（{today}）：{today_condition}，"
        f"{g('temperature_2m_min')}°C ~ {g('temperature_2m_max')}°C，"
        f"降水概率 {g('precipitation_probability_max')}%",
    ]

    # 用户经常问「明天」——接口本来就取了 2 天，顺手带上，免得模型说查不到
    if len(dates) > 1:
        tomorrow_code = int(g2("weather_code") or 0)
        lines.append(
            f"明日（{dates[1]}）："
            f"{WMO_CODES.get(tomorrow_code, f'未知({tomorrow_code})')}，"
            f"{g2('temperature_2m_min')}°C ~ {g2('temperature_2m_max')}°C，"
            f"降水概率 {g2('precipitation_probability_max')}%"
        )

    lines += [
        f"日出 {g('sunrise')}　日落 {g('sunset')}",
        "以上是精确数值，可直接回答用户，不要再调用搜索。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 统一入口


def resolve_provider(configured: str, bocha_api_key: str) -> str:
    """把 auto 解析成具体后端名。"""
    name = (configured or "auto").strip().lower()
    if name == "auto":
        return "bocha" if bocha_api_key else "bing"
    return name


async def run_tool(
    name: str,
    arguments: str | dict[str, Any],
    *,
    provider: str,
    api_key: str,
    count: int,
    max_images: int,
    timeout: float,
) -> ToolResult:
    """执行工具调用。任何异常都转成给模型看的文本，不让机器人崩掉。"""
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return ToolResult(content="工具参数解析失败，请换个说法再试。")
    else:
        args = arguments or {}

    # 先判工具名，再校验参数，避免"未知工具"被参数错误掩盖
    if name not in ("web_search", "image_search", "get_weather"):
        return ToolResult(content=f"未知工具 {name}")

    # 天气走独立接口，参数是 city 不是 query，所以要在通用校验之前处理
    if name == "get_weather":
        city = str(args.get("city") or "").strip()
        if not city:
            return ToolResult(content="缺少城市名。")
        try:
            return ToolResult(content=await _weather_report(city, timeout))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"llm_chat: 天气查询失败 city={city!r}: {exc}")
            return ToolResult(
                content=f"天气查询失败了（{exc}），请告诉用户稍后再试，或直接说你查不到。"
            )

    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(content="缺少搜索关键词。")
    if provider == "bocha" and not api_key:
        return ToolResult(content="搜索服务未配置，无法联网查询。请直接说明你不确定。")

    try:
        if name == "image_search":
            if provider == "bocha":
                data = await _fetch_bocha(query, api_key=api_key, count=count, timeout=timeout)
                images = _bocha_images(data, max_images)
            else:
                images = await _fetch_bing_images(query, limit=max_images, timeout=timeout)

            if not images:
                return ToolResult(content=f"没有搜到「{query}」的图片，可以换个关键词试试。")
            return ToolResult(
                content=f"已找到 {len(images)} 张「{query}」的图片，正在发送给用户。"
                "请用一句话简短回应，不要再重复图片链接或使用 Markdown。",
                images=images,
            )

        if provider == "bocha":
            data = await _fetch_bocha(query, api_key=api_key, count=count, timeout=timeout)
            body = _bocha_pages(data, count)
        else:
            body = await _fetch_bing_text(query, count=count, timeout=timeout)
        return ToolResult(content=f"关键词「{query}」的搜索结果：\n{body}")

    except Exception as exc:  # noqa: BLE001 - 网络类错误一律降级成文本
        logger.warning(f"llm_chat: 搜索失败 provider={provider} query={query!r}: {exc}")
        return ToolResult(
            content=f"搜索失败了（{exc}），请告诉用户稍后再试，或直接说你不确定。"
        )
