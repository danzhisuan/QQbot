"""从 chub.ai 搜索并导入社区角色卡。

设计立场：**只做工具，不预置内容。**
    社区角色卡是具体作者的作品。把一批卡抓下来打包进项目，等于替作者再分发，
    而且无法逐张确认授权。所以这里只提供「搜索 + 按你的选择导入」的能力：
    卡进的是你自己的服务器，选哪张由你决定。

数据来源：
    搜索  https://api.chub.ai/search?search=<关键词>
    取卡  https://avatars.charhub.io/avatars/<fullPath>/chara_card_v2.png
          （SillyTavern 把角色卡 JSON 以 base64 塞在 PNG 的 tEXt 块里）
"""

from __future__ import annotations

import base64
import json
import struct
from typing import Any

import httpx

SEARCH_URL = "https://api.chub.ai/search"
AVATAR_URL = "https://avatars.charhub.io/avatars/{path}/chara_card_v2.png"
LOREBOOK_URL = "https://api.chub.ai/api/lorebooks/{path}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

LOREBOOK_PREFIX = "lorebooks/"


async def search(
    query: str, limit: int = 8, *, lorebooks_only: bool = False
) -> list[dict[str, Any]]:
    """按关键词搜卡，返回按星标数排序的结果。

    chub 的搜索结果是「角色卡 + 世界书」混在一起的，
    世界书的 fullPath 以 `lorebooks/` 开头，靠这个区分。
    """
    params = {
        "search": query,
        "limit": str(max(limit * 3, 20) if lorebooks_only else limit),
        "page": "1",
        "sort": "star_count",
        "venus": "true",
        "nsfw": "false",
    }
    if lorebooks_only:
        params["namespace"] = "lorebooks"
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}) as client:
        resp = await client.get(SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    nodes = ((data.get("data") or {}).get("nodes")) or []
    results = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        path = str(node.get("fullPath") or "").strip()
        if not path:
            continue
        is_lore = path.startswith(LOREBOOK_PREFIX)
        if lorebooks_only and not is_lore:
            continue
        if not lorebooks_only and is_lore:
            continue  # 普通搜角色时，别把世界书混进来
        results.append(
            {
                "path": path,
                "name": str(node.get("name") or path).strip(),
                "desc": str(node.get("description") or "").strip(),
                "stars": int(node.get("starCount") or 0),
                "tokens": int(node.get("nTokens") or 0),
                "topics": [
                    str(t) for t in (node.get("topics") or []) if isinstance(t, str)
                ][:4],
            }
        )
    results.sort(key=lambda r: -r["stars"])
    return results[:limit]


def parse_png_card(data: bytes) -> dict[str, Any] | None:
    """从 PNG 的 tEXt 块里取出角色卡 JSON。

    SillyTavern 的卡是 PNG 图片 + 一个关键字为 chara 的 tEXt 块，
    值是 base64 编码的 JSON（V2 格式）。
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if ctype == b"tEXt":
            key, _, value = body.partition(b"\x00")
            if key.lower() in (b"chara", b"ccv3"):
                try:
                    raw = base64.b64decode(value)
                    parsed = json.loads(raw.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(parsed, dict):
                    return parsed
        if ctype == b"IEND":
            break
        pos += 12 + length
    return None


async def fetch_card(path: str) -> tuple[dict[str, Any] | None, str]:
    """按 fullPath 取卡。返回 (卡数据, 错误信息)。"""
    url = AVATAR_URL.format(path=path)
    try:
        async with httpx.AsyncClient(
            timeout=40, headers={"User-Agent": UA}, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
    except Exception as exc:  # noqa: BLE001
        return None, f"下载失败：{exc}"

    card = parse_png_card(data)
    if card is None:
        return None, "这个文件里没有找到角色卡数据（可能作者只传了图片）"
    return card, ""


async def fetch_lorebook(path: str) -> tuple[list[dict[str, Any]] | None, str]:
    """取世界书条目。

    内容不在 PNG 里（PNG 只有名字和描述），要打 ?full=true 才拿得到 definition。
    返回可直接交给 WorldStore.import_raw 的条目列表。
    """
    # 搜索结果里的 fullPath 是 `lorebooks/<id>/<slug>`，
    # 但取内容的端点已经含 lorebooks 段，必须把前缀去掉，否则 404。
    clean = path.strip().lstrip("/")
    if clean.startswith(LOREBOOK_PREFIX):
        clean = clean[len(LOREBOOK_PREFIX):]
    url = LOREBOOK_URL.format(path=clean)
    try:
        async with httpx.AsyncClient(
            timeout=60, headers={"User-Agent": UA}, follow_redirects=True
        ) as client:
            resp = await client.get(url, params={"full": "true"})
            resp.raise_for_status()
            node = (resp.json() or {}).get("node") or {}
    except Exception as exc:  # noqa: BLE001
        return None, f"下载失败：{exc}"

    definition = node.get("definition")
    if not isinstance(definition, dict):
        return None, "作者没有公开条目内容"

    # 条目在 embedded_lorebook.entries 里；有些书直接放在 entries。
    raw = None
    embedded = definition.get("embedded_lorebook")
    if isinstance(embedded, dict):
        raw = embedded.get("entries", embedded)
    if not raw:
        raw = definition.get("entries", definition)
    entries: list[dict[str, Any]] = []

    def add(item: Any, fallback: str = "") -> None:
        if not isinstance(item, dict):
            return
        keys = item.get("keys") or item.get("key") or []
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split(",")]
        keys = [str(k).strip() for k in keys if str(k).strip()]
        content = str(item.get("content") or "").strip()
        if not keys or not content:
            return
        entries.append(
            {
                "name": str(item.get("comment") or item.get("name") or fallback).strip()
                or keys[0],
                "keys": keys,
                "content": content,
                "enabled": not bool(item.get("disable") or item.get("disabled")),
            }
        )

    if isinstance(raw, list):
        for item in raw:
            add(item)
    elif isinstance(raw, dict):
        for key, item in raw.items():
            add(item, fallback=str(key))

    if not entries:
        return None, "条目是空的（可能全部被作者停用了）"
    return entries, ""
