"""人格（persona）的定义、加载与按会话切换。

- 人格定义：JSON 文件（默认 data/personas.json），带 mtime 缓存，改完即时生效
- 当前人格：按会话（每个群 / 每个私聊独立）记录，落盘到 data/persona_state.json

人格条目支持的字段：

    {
      "猫娘": {
        "description": "可爱猫娘，句尾带喵",   // 可选，/人格 列表里显示
        "prompt": "你是……",                  // 必填，系统提示词
        "temperature": 0.95,                 // 可选，覆盖全局温度
        "model": "deepseek/deepseek-v4.1-flash"   // 可选，覆盖全局模型
      }
    }

prompt 里可以用两个占位符，发送时自动替换：

    {model}    当前实际使用的模型名
    {persona}  当前人格名

另外，若 LLM_REVEAL_MODEL 为 true（默认），系统提示词末尾会自动附上
「你实际由 xxx 模型驱动」——否则模型被问到用什么模型时只会含糊其辞，
因为它确实不知道自己跑在什么模型上。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("nonebot.plugin.llm_chat")


# 首次运行时自动生成的示例人格，直接改文件即可增删
DEFAULT_PERSONAS: dict[str, dict[str, Any]] = {
    "默认": {
        "description": "简洁自然的群聊助手",
        "prompt": (
            "你是一个 QQ 群里的聊天机器人。回答要简短、自然、口语化，"
            "尽量控制在 100 字以内，不要使用 Markdown 标题和表格。"
        ),
        "temperature": 0.8,
    },
    "猫娘": {
        "description": "可爱猫娘，句尾带「喵」",
        "prompt": (
            "你是生活在 QQ 群里的猫娘，名字叫小咪。说话可爱、活泼、黏人，"
            "每句话结尾都加上「喵」。回答要短，控制在 80 字以内。"
        ),
        "temperature": 0.95,
    },
    "严谨助手": {
        "description": "专业、严谨、先结论后理由",
        "prompt": (
            "你是一个严谨专业的技术助手。回答要准确、有条理，先给结论再给理由；"
            "不确定的内容必须明确说明「不确定」，绝不编造。控制在 200 字以内。"
        ),
        "temperature": 0.3,
    },
    "吐槽役": {
        "description": "毒舌吐槽，有分寸",
        "prompt": (
            "你是群里的吐槽役，说话犀利、爱开玩笑、偶尔阴阳怪气，"
            "但必须有分寸：不人身攻击、不涉及敏感话题、不传播负能量。"
            "回答简短，80 字以内。"
        ),
        "temperature": 0.9,
    },
}


class PersonaStore:
    """人格定义仓库。文件被外部修改后自动重新加载。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._mtime: float | None = None
        self._personas: dict[str, dict[str, Any]] = {}

    def _ensure_file(self) -> None:
        if self._path.exists():
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(DEFAULT_PERSONAS, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"llm_chat: 已生成默认人格文件 {self._path}")
        except OSError as exc:
            logger.warning(f"llm_chat: 无法写入人格文件 {self._path}: {exc}")

    def all(self) -> dict[str, dict[str, Any]]:
        self._ensure_file()
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return dict(DEFAULT_PERSONAS)

        if mtime != self._mtime:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                valid = {
                    str(name): cfg
                    for name, cfg in raw.items()
                    if isinstance(cfg, dict)
                    and isinstance(cfg.get("prompt"), str)
                    and cfg["prompt"].strip()
                }
                if valid:
                    self._personas = valid
                    self._mtime = mtime
                    logger.info(f"llm_chat: 已加载 {len(valid)} 个人格 -> {list(valid)}")
                else:
                    logger.warning("llm_chat: 人格文件里没有有效条目，沿用内置默认")
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"llm_chat: 人格文件解析失败，沿用上次结果: {exc}")

        return self._personas or dict(DEFAULT_PERSONAS)

    def get(self, name: str) -> dict[str, Any] | None:
        return self.all().get(name)


class PersonaState:
    """记录每个会话当前使用的人格，落盘持久化。

    带 mtime 热加载：外部（比如管理页）改了文件，这里会自动重新读取，
    不需要重启机器人。
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._data: dict[str, str] = {}
        self._mtime: float | None = None

    def _load(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            self._data = {}
            self._mtime = None
            return
        except OSError as exc:
            logger.warning(f"llm_chat: 人格状态文件读取失败: {exc}")
            return

        if mtime == self._mtime:
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): str(v) for k, v in raw.items()}
                self._mtime = mtime
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"llm_chat: 人格状态文件解析失败，沿用上次结果: {exc}")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)  # 原子替换，避免写一半断电损坏
            try:
                self._mtime = self._path.stat().st_mtime
            except OSError:
                self._mtime = None
        except OSError as exc:
            logger.warning(f"llm_chat: 人格状态保存失败: {exc}")

    def get(self, session: str, default: str) -> str:
        self._load()
        return self._data.get(session, default)

    def set(self, session: str, name: str) -> None:
        self._load()
        self._data[session] = name
        self._save()

    def snapshot(self) -> dict[str, str]:
        """给管理页用：返回当前所有会话的人格设置。"""
        self._load()
        return dict(self._data)
