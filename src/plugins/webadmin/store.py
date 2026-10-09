"""NoneBot 插件商店：拉取官方 registry、搜索、安装、卸载。

数据源：https://registry.nonebot.dev/plugins.json（约 940 个插件，560KB）

安装策略（关键取舍）：
    容器以 uid 1001 运行，site-packages 是 root 的、不可写，所以只能
    `pip install --target <挂载卷目录>`。但 pip 会把**整个依赖树**都复制进去
    （连 nonebot2、pydantic 都有副本），因此 bot.py 里必须把这个目录
    **append 到 sys.path 末尾**，让镜像自带的核心包优先——否则插件带进来的
    旧版本会把框架本身顶掉，整个机器人崩溃。

    挂载卷的好处：重建容器不会丢，也不需要在镜像里预装任何东西。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REGISTRY_URL = "https://registry.nonebot.dev/plugins.json"
CACHE_TTL = 3600  # registry 缓存 1 小时
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

_registry_mem: dict[str, Any] = {"data": None, "at": 0.0}


def libs_dir() -> Path:
    import os

    return Path(os.environ.get("EXTRA_LIBS_DIR") or "data/pylibs").resolve()


def plugins_file() -> Path:
    import os

    return Path(
        os.environ.get("EXTRA_PLUGINS_FILE") or "data/extra_plugins.json"
    ).resolve()


def registry_cache_file() -> Path:
    return plugins_file().parent / "registry_cache.json"


# ---------------------------------------------------------------- registry


def _normalize(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        module = str(item.get("module_name") or "").strip()
        project = str(item.get("project_link") or "").strip()
        if not module or not project:
            continue
        tags = []
        for t in item.get("tags") or []:
            if isinstance(t, dict):
                label = str(t.get("label") or "").strip()
            else:
                label = str(t).strip()
            if label:
                tags.append(label)
        out.append(
            {
                "module_name": module,
                "project_link": project,
                "name": str(item.get("name") or module).strip(),
                "desc": str(item.get("desc") or "").strip(),
                "author": str(item.get("author") or "").strip(),
                "homepage": str(item.get("homepage") or "").strip(),
                "version": str(item.get("version") or "").strip(),
                "tags": tags,
                "is_official": bool(item.get("is_official")),
                "valid": bool(item.get("valid", True)),
                "time": str(item.get("time") or ""),
            }
        )
    return out


async def get_registry(force: bool = False) -> list[dict[str, Any]]:
    """取插件列表：内存缓存 → 磁盘缓存 → 网络。"""
    now = time.time()
    if not force and _registry_mem["data"] and now - _registry_mem["at"] < CACHE_TTL:
        return _registry_mem["data"]

    cache = registry_cache_file()
    if not force and cache.is_file():
        try:
            age = now - cache.stat().st_mtime
            if age < CACHE_TTL:
                data = _normalize(json.loads(cache.read_text(encoding="utf-8")))
                if data:
                    _registry_mem.update(data=data, at=now)
                    return data
        except Exception:  # noqa: BLE001
            pass

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(REGISTRY_URL)
            resp.raise_for_status()
            data = _normalize(resp.json())
        if data:
            _registry_mem.update(data=data, at=now)
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                pass
            return data
    except Exception as exc:  # noqa: BLE001
        # 网络失败时退回磁盘缓存（哪怕过期），总比什么都没有强
        if cache.is_file():
            try:
                data = _normalize(json.loads(cache.read_text(encoding="utf-8")))
                if data:
                    _registry_mem.update(data=data, at=now)
                    return data
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError(f"插件列表获取失败：{exc}") from exc

    return []


def search(
    registry: list[dict[str, Any]],
    installed: dict[str, Any],
    *,
    query: str = "",
    tag: str = "",
    official_only: bool = False,
    page: int = 0,
    size: int = 24,
) -> dict[str, Any]:
    q = (query or "").strip().lower()
    results = []
    for item in registry:
        if official_only and not item["is_official"]:
            continue
        if tag and tag not in item["tags"]:
            continue
        if q:
            haystack = " ".join(
                [
                    item["name"],
                    item["desc"],
                    item["module_name"],
                    item["project_link"],
                    item["author"],
                    " ".join(item["tags"]),
                ]
            ).lower()
            if q not in haystack:
                continue
        results.append(item)

    # 已装的排前面，官方插件次之
    results.sort(
        key=lambda x: (
            0 if x["module_name"] in installed else 1,
            0 if x["is_official"] else 1,
            x["name"],
        )
    )

    total = len(results)
    start = max(page, 0) * size
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": results[start : start + size],
        "tags": _top_tags(registry),
    }


def _top_tags(registry: list[dict[str, Any]], limit: int = 24) -> list[str]:
    counter: dict[str, int] = {}
    for item in registry:
        for t in item["tags"]:
            counter[t] = counter.get(t, 0) + 1
    return [t for t, _ in sorted(counter.items(), key=lambda kv: -kv[1])[:limit]]


# ---------------------------------------------------------------- 已安装状态


def read_installed() -> list[str]:
    try:
        raw = json.loads(plugins_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw]


def write_installed(names: list[str]) -> None:
    path = plugins_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def installed_detail(registry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """已装插件：合并 registry 元信息，并检查模块是否真的在卷里。"""
    index = {item["module_name"]: item for item in registry}
    root = libs_dir()
    out = []
    for name in read_installed():
        item = index.get(name)
        present = (root / name).exists() or (root / f"{name}.py").exists()
        out.append(
            {
                "module_name": name,
                "name": (item or {}).get("name", name),
                "desc": (item or {}).get("desc", ""),
                "version": (item or {}).get("version", ""),
                "homepage": (item or {}).get("homepage", ""),
                "project_link": (item or {}).get("project_link", ""),
                "files_present": present,
                "in_registry": item is not None,
            }
        )
    return out


# ---------------------------------------------------------------- 安装 / 卸载


async def pip_install(project_link: str, timeout: float = 300.0) -> tuple[bool, str]:
    root = libs_dir()
    root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--target",
        str(root),
        "--upgrade",
        "-i",
        PIP_INDEX,
        project_link,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return False, f"安装超时（>{timeout:.0f}s）"
    except Exception as exc:  # noqa: BLE001
        return False, f"启动 pip 失败：{exc}"

    output = (stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode == 0:
        tail = [
            line
            for line in output.splitlines()
            if line.startswith("Successfully installed")
        ]
        return True, (tail[-1] if tail else "安装完成")
    tail = "\n".join(output.strip().splitlines()[-6:])
    return False, f"pip 退出码 {proc.returncode}：{tail}"


def delete_package(module_name: str) -> tuple[bool, str]:
    """删除插件文件（含 dist-info）。只允许删 libs 目录内的东西。"""
    root = libs_dir()
    if not root.is_dir():
        return False, "插件目录不存在"

    targets: list[Path] = []
    pkg = root / module_name
    if pkg.exists():
        targets.append(pkg)
    single = root / f"{module_name}.py"
    if single.exists():
        targets.append(single)

    # 顶层包名通常是 module_name 的第一段
    top = module_name.split(".")[0]
    for dist in root.glob(f"{top.replace('_', '-')}*.dist-info"):
        targets.append(dist)
    for dist in root.glob(f"{top}*.dist-info"):
        targets.append(dist)

    if not targets:
        return False, f"在 {root} 里找不到 {module_name} 的文件"

    removed = []
    for path in targets:
        resolved = path.resolve()
        # 安全校验：必须真的在 libs 目录内
        if not str(resolved).startswith(str(root) + "/") and resolved != root:
            continue
        try:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
            removed.append(path.name)
        except OSError as exc:
            return False, f"删除 {path.name} 失败：{exc}"

    return bool(removed), "已删除：" + "、".join(removed[:8])


def try_load_plugin(module_name: str) -> tuple[bool, str]:
    """尝试热加载插件，不重启进程。"""
    import importlib
    import nonebot

    # 刚装进来的包在 import 缓存里还不存在，先让它失效一次
    importlib.invalidate_caches()
    if str(libs_dir()) not in sys.path:
        sys.path.append(str(libs_dir()))

    try:
        plugin = nonebot.load_plugin(module_name)
        if plugin:
            return True, f"已热加载：{plugin.name}"
        return False, "已安装，但 NoneBot 没能加载它（可能模块名不对，重启后生效）"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
