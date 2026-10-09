"""Web 管理台：挂在 NoneBot 自己的 FastAPI 上，通过 SSH 隧道访问。

    http://127.0.0.1:18080/admin

设计取舍（为什么不用社区的现成 WebUI）：
    那些方案是给「裸机部署」设计的——在线装插件、改 .env 都是改本机文件。
    而本项目是 Docker 部署，容器里改的东西重建就没了。所以这里只做
    **能真正持久化** 的事：人格文件走挂载卷（改完即时生效），
    重启则直接退出进程让 Docker 的 restart 策略拉起。

鉴权：WEBADMIN_TOKEN，未设置时回落到 ONEBOT_ACCESS_TOKEN。两者都为空则整个页面关闭。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, Cookie, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from nonebot import get_app, get_bots, get_loaded_plugins, logger
from nonebot.plugin import PluginMetadata

from .page import LOGIN_HTML, PAGE_HTML
from . import store
from ..llm_chat.config import plugin_config
from ..llm_chat.tavern import CardStore, TavernState, WorldStore

# 与管理台共用同一份存储（llm_chat 里也会实例化，但两者都按 mtime 热加载，
# 谁改了另一方下次读取就能看到）
_tavern_cards = CardStore(plugin_config.llm_tavern_cards_file)
_tavern_state = TavernState(plugin_config.llm_tavern_state_file)
_tavern_world = WorldStore(plugin_config.llm_tavern_world_file)

__plugin_meta__ = PluginMetadata(
    name="webadmin",
    description="Web 管理台：运行状态、人格管理、配置检查、日志、重启",
    usage="浏览器打开 http://<host>:8080/admin",
    type="application",
    supported_adapters={"~onebot.v11"},
)

# ---------------------------------------------------------------- 基础工具

_STARTED = time.time()
_STARTED_STR = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
_LOG_BUFFER: deque[str] = deque(maxlen=600)

# 会话 Cookie 名
_COOKIE = "botadmin"

_PERSONA_FILE = Path(os.environ.get("LLM_PERSONA_FILE") or "data/personas.json")
_PERSONA_STATE_FILE = Path(
    os.environ.get("LLM_PERSONA_STATE_FILE") or "data/persona_state.json"
)
_TOOLS_PATH = Path(__file__).resolve().parent.parent / "llm_chat" / "tools.py"


def _token() -> str:
    """管理 Token：优先 WEBADMIN_TOKEN，否则用 OneBot 的 token 兜底。"""
    return (
        os.environ.get("WEBADMIN_TOKEN", "").strip()
        or os.environ.get("ONEBOT_ACCESS_TOKEN", "").strip()
    )


def _mask(value: str) -> str:
    if not value:
        return "（未设置）"
    if len(value) <= 10:
        return value[:2] + "***"
    return value[:8] + "…" + value[-4:]


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}天{h}小时{m}分"
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def _read_memory() -> str:
    """从 /proc/self/status 读当前进程的物理内存占用（Linux 容器内可用）。"""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                kb = int(line.split()[1])
                return f"{kb / 1024:.1f} MB"
    except Exception:  # noqa: BLE001
        pass
    return "未知"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _llm_cfg() -> dict[str, str]:
    return {
        "base": os.environ.get("LLM_API_BASE", "").strip(),
        "key": os.environ.get("LLM_API_KEY", "").strip(),
        "model": os.environ.get("LLM_MODEL", "").strip(),
    }


def _search_cfg() -> dict[str, str]:
    bocha = os.environ.get("BOCHA_API_KEY", "").strip()
    provider = (os.environ.get("SEARCH_PROVIDER", "") or "auto").strip().lower()
    if provider == "auto":
        provider = "bocha" if bocha else "bing"
    return {
        "enabled": os.environ.get("SEARCH_ENABLED", "true").strip().lower(),
        "provider": provider,
        "bocha": bocha,
    }


def _load_tools_module():
    """按文件路径加载搜索工具模块，避免依赖插件加载顺序。"""
    spec = importlib.util.spec_from_file_location("webadmin_tools", _TOOLS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("找不到 tools.py")
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["webadmin_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 日志采集

def _log_sink(message: Any) -> None:
    _LOG_BUFFER.append(str(message).rstrip("\n"))


logger.add(
    _log_sink,
    level="INFO",
    format="{time:HH:mm:ss} | {level: <7} | {message}",
    enqueue=False,
)
logger.info("webadmin: 管理台已挂载，日志采集已开启")


# ---------------------------------------------------------------- 路由

app = get_app()
_router_prefix = "/admin"


def _authed(token_cookie: str | None, token_query: str | None = None) -> bool:
    expected = _token()
    if not expected:
        return False
    return (token_query or "") == expected or (token_cookie or "") == expected


@app.get(f"{_router_prefix}", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(request: Request, token: str | None = Query(default=None)):
    if not _token():
        return HTMLResponse(
            "<h3 style='font-family:sans-serif;padding:40px'>管理台未启用："
            "请在 .env 设置 WEBADMIN_TOKEN 或 ONEBOT_ACCESS_TOKEN</h3>",
            status_code=503,
        )
    cookie = request.cookies.get(_COOKIE)
    if token and token == _token():
        resp = RedirectResponse(url=_router_prefix, status_code=302)
        resp.set_cookie(
            _COOKIE, token, httponly=True, samesite="lax", max_age=30 * 86400
        )
        return resp
    if _authed(cookie):
        return HTMLResponse(PAGE_HTML)
    return HTMLResponse(LOGIN_HTML)


@app.post(f"{_router_prefix}/api/login", include_in_schema=False)
async def admin_login(payload: dict = Body(...)):
    if payload.get("token", "") != _token():
        raise HTTPException(status_code=401, detail="Token 不正确")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        _COOKIE, payload["token"], httponly=True, samesite="lax", max_age=30 * 86400
    )
    return resp


def _guard(cookie: str | None) -> None:
    if not _authed(cookie):
        raise HTTPException(status_code=401, detail="未登录")


@app.post(f"{_router_prefix}/api/logout", include_in_schema=False)
async def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_COOKIE)
    return resp


@app.get(f"{_router_prefix}/api/status")
async def api_status(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)

    bots = sorted(get_bots().keys())
    plugins = []
    for plugin in get_loaded_plugins():
        meta = plugin.metadata
        plugins.append(
            {
                "name": plugin.name,
                "description": (meta.description if meta else "") or "",
            }
        )

    personas = _read_json(_PERSONA_FILE, {})
    search = _search_cfg()
    llm = _llm_cfg()
    search_ready = search["enabled"] == "true" and (
        search["provider"] == "bing" or bool(search["bocha"])
    )

    return {
        "name": "QQ Bot 管理台",
        "started_at": _STARTED_STR,
        "uptime": _fmt_uptime(time.time() - _STARTED),
        "memory": _read_memory(),
        "bots": bots,
        "plugins": [p["name"] for p in plugins],
        "plugin_detail": plugins,
        "persona_count": len(personas) if isinstance(personas, dict) else 0,
        "search_provider": search["provider"],
        "search_ready": search_ready,
        "llm_ready": bool(llm["key"] and llm["base"]),
    }


@app.get(f"{_router_prefix}/api/personas")
async def api_personas(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    personas = _read_json(_PERSONA_FILE, {})
    sessions = _read_json(_PERSONA_STATE_FILE, {})
    if not isinstance(personas, dict):
        personas = {}
    if not isinstance(sessions, dict):
        sessions = {}
    return {"personas": personas, "sessions": sessions}


@app.post(f"{_router_prefix}/api/personas")
async def api_save_personas(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    _guard(botadmin)
    incoming = payload.get("personas")
    if not isinstance(incoming, dict) or not incoming:
        raise HTTPException(status_code=400, detail="人格文件不能为空")

    clean: dict[str, Any] = {}
    for name, cfg in incoming.items():
        name = str(name).strip()
        if not name or not isinstance(cfg, dict):
            continue
        prompt = str(cfg.get("prompt") or "").strip()
        if not prompt:
            continue  # 没有提示词的人格直接丢掉，避免机器人读到时崩
        item: dict[str, Any] = {"prompt": prompt}
        desc = str(cfg.get("description") or "").strip()
        if desc:
            item["description"] = desc
        temp = cfg.get("temperature")
        if isinstance(temp, (int, float)):
            item["temperature"] = float(temp)
        clean[name] = item

    if not clean:
        raise HTTPException(status_code=400, detail="没有有效人格（提示词不能为空）")

    _write_json(_PERSONA_FILE, clean)
    logger.info(f"webadmin: 人格文件已更新，共 {len(clean)} 个人格")
    return {"ok": True, "message": f"已保存 {len(clean)} 个人格，立即生效"}


@app.post(f"{_router_prefix}/api/session")
async def api_set_session(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    _guard(botadmin)
    session = str(payload.get("session") or "").strip()
    persona = str(payload.get("persona") or "").strip()
    if not session:
        raise HTTPException(status_code=400, detail="缺少会话")

    state = _read_json(_PERSONA_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    if persona:
        personas = _read_json(_PERSONA_FILE, {})
        if not isinstance(personas, dict) or persona not in personas:
            raise HTTPException(status_code=400, detail=f"人格「{persona}」不存在")
        state[session] = persona
    else:
        state.pop(session, None)

    _write_json(_PERSONA_STATE_FILE, state)
    logger.info(f"webadmin: 会话 {session} 人格设为 {persona or '（跟随默认）'}")
    return {"ok": True}


@app.get(f"{_router_prefix}/api/config")
async def api_config(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    llm = _llm_cfg()
    search = _search_cfg()
    items = [
        {"k": "LLM_API_BASE", "v": llm["base"] or "（未设置）"},
        {"k": "LLM_MODEL", "v": llm["model"] or "（未设置）"},
        {"k": "LLM_API_KEY", "v": _mask(llm["key"])},
        {"k": "SEARCH_ENABLED", "v": search["enabled"]},
        {"k": "SEARCH_PROVIDER", "v": search["provider"]},
        {"k": "BOCHA_API_KEY", "v": _mask(search["bocha"])},
        {"k": "ONEBOT_ACCESS_TOKEN", "v": _mask(os.environ.get("ONEBOT_ACCESS_TOKEN", ""))},
        {"k": "SUPERUSERS", "v": os.environ.get("SUPERUSERS", "（未设置）")},
        {"k": "COMMAND_START", "v": os.environ.get("COMMAND_START", "（默认）")},
        {"k": "HOST:PORT", "v": f"{os.environ.get('HOST')}:{os.environ.get('PORT')}"},
        {"k": "人格文件", "v": str(_PERSONA_FILE)},
        {"k": "会话人格文件", "v": str(_PERSONA_STATE_FILE)},
    ]
    return {"items": items}


@app.post(f"{_router_prefix}/api/test/llm")
async def api_test_llm(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    llm = _llm_cfg()
    if not (llm["base"] and llm["key"] and llm["model"]):
        return {"ok": False, "message": "LLM 配置不完整（base / key / model 缺一不可）"}
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                llm["base"].rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {llm['key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": llm["model"],
                    "messages": [{"role": "user", "content": "只回复两个字：正常"}],
                    "temperature": 0,
                },
            )
        cost = time.monotonic() - t0
        if r.status_code != 200:
            return {"ok": False, "message": f"HTTP {r.status_code}：{r.text[:160]}"}
        content = (r.json()["choices"][0]["message"].get("content") or "").strip()
        return {
            "ok": True,
            "message": f"{llm['model']} 正常，{cost:.1f}s，回复：{content[:40] or '(空)'}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


@app.post(f"{_router_prefix}/api/test/search")
async def api_test_search(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    search = _search_cfg()
    if search["enabled"] != "true":
        return {"ok": False, "message": "联网搜索已关闭（SEARCH_ENABLED=false）"}
    try:
        tools = _load_tools_module()
        result = await tools.run_tool(
            "web_search",
            json.dumps({"query": "上海天气"}),
            provider=search["provider"],
            api_key=search["bocha"],
            count=3,
            max_images=1,
            timeout=20,
        )
        body = result.content.replace("\n", " ⏎ ")[:180]
        return {"ok": "搜索结果" in result.content, "message": f"{search['provider']}：{body}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


@app.get(f"{_router_prefix}/api/logs")
async def api_logs(
    lines: int = Query(default=300, ge=1, le=600), botadmin: str | None = Cookie(default=None)
):
    _guard(botadmin)
    data = list(_LOG_BUFFER)[-lines:]
    return {"lines": data or ["（还没有日志）"], "total": len(_LOG_BUFFER)}


@app.post(f"{_router_prefix}/api/restart")
async def api_restart(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    logger.warning("webadmin: 收到重启指令，进程即将退出，由 Docker 拉起新容器")

    async def _exit_later() -> None:
        await asyncio.sleep(0.8)  # 留出时间把 HTTP 响应发回去
        os._exit(1)  # 非 0 退出码，配合 restart: unless-stopped 自动重启

    asyncio.create_task(_exit_later())
    return {"ok": True, "message": "重启中"}


# ---------------------------------------------------------------- 插件商店


@app.get(f"{_router_prefix}/api/store")
async def api_store(
    q: str = Query(default=""),
    tag: str = Query(default=""),
    official: bool = Query(default=False),
    page: int = Query(default=0, ge=0),
    botadmin: str | None = Cookie(default=None),
):
    _guard(botadmin)
    try:
        registry = await store.get_registry()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    installed = store.read_installed()
    result = store.search(
        registry,
        {name: True for name in installed},
        query=q,
        tag=tag,
        official_only=official,
        page=page,
    )
    result["installed"] = installed
    result["total_registry"] = len(registry)
    return result


@app.get(f"{_router_prefix}/api/store/installed")
async def api_store_installed(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    try:
        registry = await store.get_registry()
    except Exception:  # noqa: BLE001 - registry 拿不到也要能看已装列表
        registry = []
    return {
        "items": store.installed_detail(registry),
        "libs_dir": str(store.libs_dir()),
        "plugins_file": str(store.plugins_file()),
    }


@app.post(f"{_router_prefix}/api/store/refresh")
async def api_store_refresh(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    try:
        registry = await store.get_registry(force=True)
        return {"ok": True, "message": f"已刷新，共 {len(registry)} 个插件"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}


@app.post(f"{_router_prefix}/api/store/install")
async def api_store_install(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    _guard(botadmin)
    module_name = str(payload.get("module_name") or "").strip()
    project_link = str(payload.get("project_link") or "").strip()
    if not module_name or not project_link:
        raise HTTPException(status_code=400, detail="缺少 module_name 或 project_link")
    # 防止把参数拼进命令时被注入
    if not module_name.replace("-", "_").replace(".", "_").isidentifier():
        raise HTTPException(status_code=400, detail="module_name 不合法")
    if not project_link.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise HTTPException(status_code=400, detail="project_link 不合法")

    names = store.read_installed()
    logger.info(f"webadmin: 开始安装插件 {project_link}（{module_name}）")

    ok, message = await store.pip_install(project_link)
    if not ok:
        logger.warning(f"webadmin: 安装 {project_link} 失败：{message}")
        return {"ok": False, "message": message}

    if module_name not in names:
        names.append(module_name)
        store.write_installed(names)

    loaded, load_msg = store.try_load_plugin(module_name)
    logger.info(f"webadmin: 插件 {module_name} 安装完成（{load_msg}）")
    return {
        "ok": True,
        "message": f"{message}；{load_msg}",
        "needs_restart": not loaded,
        "loaded": loaded,
    }


@app.post(f"{_router_prefix}/api/store/uninstall")
async def api_store_uninstall(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    _guard(botadmin)
    module_name = str(payload.get("module_name") or "").strip()
    delete_files = bool(payload.get("delete_files", True))
    if not module_name:
        raise HTTPException(status_code=400, detail="缺少 module_name")

    names = [n for n in store.read_installed() if n != module_name]
    store.write_installed(names)

    msg = f"{module_name} 已停用"
    if delete_files:
        ok, detail = store.delete_package(module_name)
        msg += f"；{detail}" if ok else f"；文件未删除（{detail}）"

    logger.info(f"webadmin: 卸载插件 {module_name} —— {msg}")
    # 已加载的插件无法真正卸载，必须重启进程
    return {"ok": True, "message": msg, "needs_restart": True}


@app.post(f"{_router_prefix}/api/tavern/world")
async def api_world_save(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    """新增/修改一条世界设定。"""
    _guard(botadmin)
    name = str(payload.get("name") or "").strip()
    keys = payload.get("keys") or []
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.replace("，", ",").split(",")]
    keys = [str(k).strip() for k in keys if str(k).strip()]
    content = str(payload.get("content") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="条目名必填")
    if not keys:
        raise HTTPException(status_code=400, detail="至少填一个关键词，否则永远不会被触发")
    if not content:
        raise HTTPException(status_code=400, detail="设定内容不能为空")

    entries = [e for e in _tavern_world.all() if e["name"] != name]
    entries.append(
        {
            "name": name,
            "keys": keys,
            "content": content,
            "enabled": bool(payload.get("enabled", True)),
        }
    )
    ok, message = _tavern_world.import_raw(entries, replace=True)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    logger.info(f"webadmin: 已保存世界设定「{name}」")
    return {"ok": True, "message": f"已保存世界设定「{name}」"}


@app.post(f"{_router_prefix}/api/tavern/world/delete")
async def api_world_delete(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    _guard(botadmin)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少 name")
    ok, message = _tavern_world.remove(name)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    logger.info(f"webadmin: {message}")
    return {"ok": True, "message": message}


@app.post(f"{_router_prefix}/api/tavern/world/import")
async def api_world_import(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    """导入世界书。兼容 SillyTavern 的 World Info JSON（entries 字典或数组）。"""
    _guard(botadmin)
    raw = payload.get("world") if "world" in payload else payload
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    replace = bool(payload.get("replace", False))
    count, message = _tavern_world.import_raw(entries if entries is not None else raw, replace)
    if not count:
        raise HTTPException(status_code=400, detail=message)
    logger.info(f"webadmin: 世界书导入 {count} 条")
    return {"ok": True, "message": message, "count": count}


# ---------------------------------------------------------------- 酒馆


@app.get(f"{_router_prefix}/api/tavern")
async def api_tavern(botadmin: str | None = Cookie(default=None)):
    _guard(botadmin)
    cards = _tavern_cards.all()
    # 哪些会话正在酒馆模式
    active = {
        key: entry.get("card", "")
        for key, entry in _tavern_state._state.items()  # noqa: SLF001 - 只读展示
        if entry.get("active")
    }
    return {
        "cards": [
            {"key": name, **{k: v for k, v in card.items() if k != "name"}}
            for name, card in cards.items()
        ],
        "active": active,
        "cards_file": str(_tavern_cards.path),
        "world": _tavern_world.all(),
        "world_file": str(_tavern_world.path),
    }


@app.post(f"{_router_prefix}/api/tavern/card")
async def api_tavern_save(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    """新增/修改一张角色卡。兼容直接粘贴 SillyTavern 的 V1/V2 JSON。"""
    _guard(botadmin)
    # 允许把整张 ST 卡原样贴进来
    raw = payload.get("card") if isinstance(payload.get("card"), dict) else payload
    ok, message = _tavern_cards.upsert(raw)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    logger.info(f"webadmin: {message}")
    return {"ok": True, "message": message}


@app.post(f"{_router_prefix}/api/tavern/delete")
async def api_tavern_delete(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    _guard(botadmin)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少 name")
    ok, message = _tavern_cards.remove(name)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    logger.info(f"webadmin: {message}")
    return {"ok": True, "message": message}


@app.post(f"{_router_prefix}/api/tavern/import")
async def api_tavern_import(
    payload: dict = Body(...), botadmin: str | None = Cookie(default=None)
):
    """批量导入：接受 ST 卡数组，或 {"cards": [...]}。"""
    _guard(botadmin)
    items = payload.get("cards") if isinstance(payload.get("cards"), list) else payload
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="请提供角色卡数组")

    saved, failed = 0, []
    for item in items:
        ok, message = _tavern_cards.upsert(item)
        if ok:
            saved += 1
        else:
            failed.append(message)
    logger.info(f"webadmin: 批量导入角色卡，成功 {saved} 张，失败 {len(failed)} 张")
    return {
        "ok": saved > 0,
        "message": f"导入完成：成功 {saved} 张"
        + (f"，失败 {len(failed)} 张（{failed[0]}）" if failed else ""),
        "saved": saved,
    }
