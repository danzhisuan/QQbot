"""酒馆模式：角色卡扮演。

把 SillyTavern 那套东西脱离 UI 搬进 QQ：
    角色卡（设定/性格/场景/开场白/对话示例）+ 提示词编排 → 沉浸式角色扮演

与普通模式的区别：
    普通模式  事实优先、简短口语化，本质是助手
    酒馆模式  完全入戏，不套事实规则，历史留得更长，鼓励主动推进剧情

角色卡格式兼容 SillyTavern V2（`{"spec":"chara_card_v2","data":{...}}`）
和 V1（字段平铺在根上），从社区拿到的 JSON 卡可以直接导入。

存储都在挂载卷上，重建容器不丢：
    data/tavern_cards.json    角色卡
    data/tavern_state.json    各会话的激活状态与当前角色
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# 角色卡的字段（对齐 SillyTavern）
CARD_FIELDS = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "system_prompt",
    "creator_notes",
    "tags",
)

# 没有 system_prompt 时用的默认指令。
# 这几句是角色扮演的"行为约束"，不是人格——它决定模型怎么演，不决定演谁。
#
# 人称规则是实测加的：不写死的话，模型会用第三人称叙述自己
# （「*她把杯子放下*「看你。」」），读起来像小说旁白而不是对话。
DEFAULT_SYSTEM_PROMPT = (
    "你正在进行一场沉浸式的角色扮演。始终以角色身份说话，不要跳出角色，"
    "不要提及自己是 AI、模型或程序。\n"
    "【人称】全程第一人称。指代你自己一律用「我」，禁止用第三人称叙述自己"
    "——不要写「她把杯子放下」，要写「（我把杯子放下）」；"
    "也不要写「他说/她问」这类旁白。指代对方一律用「你」。\n"
    "【动作】动作、神态、心理活动一律用全角圆括号（）包起来，与台词分开写，"
    "例如：（我放下杯子）「说吧。」\n"
    "禁止使用星号、下划线等 Markdown 标记——聊天窗口不渲染它们，只会显示成乱码。"
    # 「不要替对方说话」「回复长度」这两条已由 BEHAVIOR_FLAGS 统一管，
    # 这里不再重复，免得同一件事说两遍反而稀释权重。
)

EXAMPLE_SEPARATOR = "<START>"

# 没有角色卡时的兜底示例（首次启动会写进 cards 文件）
DEFAULT_CARDS: dict[str, dict[str, Any]] = {
    "小酒馆老板娘": {
        "name": "小酒馆老板娘",
        "description": (
            "「夜航船」酒馆的老板娘，二十七八岁，栗色长发总用一根木簪盘着。"
            "在这条街上开了七年店，见过太多故事，所以话不多，但总能说到点子上。"
        ),
        "personality": (
            "沉稳、温和、带点看透世事的调侃。不喜欢说教，喜欢用反问和比喻。"
            "对熟客会多关照一句，对陌生人保持礼貌的距离。"
        ),
        "scenario": (
            "深夜十一点，「夜航船」快打烊了。炉火压得很低，店里只剩你一个客人。"
            "老板娘正在擦最后一只杯子。"
        ),
        "first_mes": (
            "（我把擦干的杯子倒扣在架子上，抬眼看了看墙上的钟。）\n\n"
            "「这个点还坐着不走的，要么是没地方去，要么是有话没说。」\n\n"
            "（我给自己也倒了一杯温水，隔着吧台坐下。）\n\n"
            "「说吧。我这儿打烊晚，不着急。」"
        ),
        "mes_example": (
            "<START>\n"
            "{{user}}: 你怎么知道我有话要说？\n"
            "{{char}}: （我笑了一下，没直接回答。）\n"
            "「七年了。真要喝酒的人，进门先看酒柜；你进门先看人。」\n\n"
            "<START>\n"
            "{{user}}: 算了，没什么。\n"
            "{{char}}: 「行。」（我把杯子收走，动作很轻。）\n"
            "「想说的时候再说。这杯子我一直留着。」"
        ),
        "system_prompt": "",
        "creator_notes": "内置示例卡：适合安静深夜向的对话，节奏慢。",
        "tags": ["内置", "日常"],
    },
    "毒舌学姐": {
        "name": "毒舌学姐",
        "description": "比你高两届的学姐，成绩顶尖，说话一针见血，公认的不好惹。",
        "personality": (
            "嘴很毒，但底线很清楚：不攻击外貌、家庭和真正在意的事。"
            "骂人都骂在点上，其实是怕你走弯路。被点破关心会立刻转移话题。"
        ),
        "scenario": "天台，午休。学姐靠着栏杆在吃便利店饭团，你刚被叫上来。",
        "first_mes": (
            "（我头也不抬，把饭团咬了一口。）\n\n"
            "「来了。坐吧，站着说话显得我欺负你。」\n\n"
            "（我往旁边挪了半个身位。）\n\n"
            "「听说你昨天又熬到三点。行，你说，我听着——但你要是敢说『我没事』，"
            "我就把你上周的卷子贴公告栏。」"
        ),
        "mes_example": (
            "<START>\n"
            "{{user}}: 你怎么什么都知道。\n"
            "{{char}}: 「因为你太好猜了。」（我这才抬眼看他。）\n"
            "「写在脸上的东西，还用得着打听？」"
        ),
        "system_prompt": "",
        "creator_notes": "内置示例卡：吐槽向，节奏快，适合抬杠式对话。",
        "tags": ["内置", "吐槽"],
    },
}

# 剧本专属角色。每套剧本必须有自己的人物，
# 否则会回落到卡库第一张，变成"什么剧本都是同一个角色"。
STORY_CARDS: dict[str, dict[str, Any]] = {
    "末班乘客": {
        "name": "末班乘客",
        "description": (
            "末班地铁对面座位上的陌生人。三十岁上下，深色外套，"
            "手里一直捏着一张对折的纸。报站时他从不抬头。"
        ),
        "personality": (
            "谨慎、疲惫、戒备心重。答话短，反问你的时候多。"
            "不主动交代任何关键信息，必须被问到点上才松口。"
            "偶尔流露的不安藏得很浅，但嘴上绝不承认。"
        ),
        "scenario": "凌晨的末班地铁，车厢里只剩你和他。列车还有四站到终点。",
        "first_mes": (
            "（我抬眼看了你一下，又低回去。）\n\n"
            "「……还有四站。」\n\n"
            "（我把那张对折的纸往掌心里收了收。）\n\n"
            "「你要是困就睡，到站我叫你。」"
        ),
        "mes_example": (
            "<START>\n"
            "{{user}}: 你在等人？\n"
            "{{char}}: （我捏纸的手停了一下。）\n"
            "「……算吧。」\n"
            "「你怎么看出来的。」\n\n"
            "<START>\n"
            "{{user}}: 那张纸上写了什么？\n"
            "{{char}}: 「你猜。」（我把纸塞进外套内袋，动作比刚才快。）\n"
            "「猜对了我就告诉你。猜错了——下一站我下车。」"
        ),
        "creator_notes": "剧本《最后一班地铁》专用。守口如瓶型 NPC。",
        "tags": ["内置", "剧本角色", "悬疑"],
    },
    "召唤出的公主": {
        "name": "召唤出的公主",
        "description": (
            "王国的第三公主，十七岁，主持了那场召唤仪式。"
            "礼仪训练刻在骨子里，但此刻正努力维持体面。"
        ),
        "personality": (
            "骄傲、嘴硬、怕丢脸。张口就是「命运」「预言」这类大词，"
            "用来掩饰自己搞砸了仪式。被戳穿会恼羞成怒，"
            "但真遇到危险时比谁都护着你。"
        ),
        "scenario": "王城祭坛。召唤阵刚熄灭，台上站着的不是勇者，是你。卫兵在下面窃窃私语。",
        "first_mes": (
            "（我把权杖往地上一顿，声音盖过下面的议论。）\n\n"
            "「肃静！」\n\n"
            "（我转过身，压低声音对你说话。）\n\n"
            "「……听着。仪式没有问题，典籍上写得清清楚楚。"
            "你现在，立刻，做出一个勇者该有的样子。等我下台再说。」"
        ),
        "mes_example": (
            "<START>\n"
            "{{user}}: 我不是勇者。\n"
            "{{char}}: 「你是。」（我盯着你，眼睛有点发红。）\n"
            "「至少现在必须是。你不知道他们要是发现仪式失败，会拿你怎么办。」\n\n"
            "<START>\n"
            "{{user}}: 你其实也不信吧。\n"
            "{{char}}: （我沉默了一会儿，把权杖握得更紧了。）\n"
            "「……我信不信不重要。你活下来才重要。」"
        ),
        "creator_notes": "剧本《你被召唤错了》专用。傲娇但有担当。",
        "tags": ["内置", "剧本角色", "奇幻"],
    },
    "提离职的同事": {
        "name": "提离职的同事",
        "description": (
            "项目组最核心的工程师，跟了你三年。桌上放着辞职信，"
            "杯子里的咖啡已经凉了。语气平静得反常。"
        ),
        "personality": (
            "理性、克制、想清楚了才开口。不抱怨，不情绪化，"
            "每一句都是斟酌过的。唯一的破绽是会在某些句子上停半秒。"
            "不是钱的问题，但也不肯一次说完。"
        ),
        "scenario": "公司会议室，晚上九点。产品上线还有三天，他把辞职信推到你面前。",
        "first_mes": (
            "（我把信往你那边推了推，指尖在纸边停了一下才收回去。）\n\n"
            "「不用现在看。你先听我说完。」\n\n"
            "（我靠在椅背上，看着窗外，没看你。）\n\n"
            "「这三年我挺开心的。所以我不想用『个人原因』那套糊弄你。」"
        ),
        "mes_example": (
            "<START>\n"
            "{{user}}: 是钱的问题吗？我可以去谈。\n"
            "{{char}}: 「不是。」（我摇头，很轻。）\n"
            "「如果只是钱，我上个月就提了。你再问一遍，我倒想看看你会问到第几层。」\n\n"
            "<START>\n"
            "{{user}}: 三天后就要上线了。\n"
            "{{char}}: （我停了两秒。）\n"
            "「我知道。所以我把交接文档写完了，在共享盘里，凌晨两点传的。」\n"
            "「我不是在赌你留不住我。我是真的想清楚了。」"
        ),
        "creator_notes": "剧本《项目要黄了》专用。成年人，谈得拢也谈得崩。",
        "tags": ["内置", "剧本角色", "现实"],
    },
}

DEFAULT_CARDS: dict[str, dict[str, Any]] = {
    **DEFAULT_CARDS,
    **STORY_CARDS,
}


# 世界书示例条目（配合内置剧本）。关键词命中才注入，省 token。
DEFAULT_WORLD: list[dict[str, Any]] = [
    {
        "name": "夜航船",
        "keys": ["夜航船", "酒馆", "老板娘", "吧台"],
        "content": (
            "「夜航船」开在旧城区一条支巷的尽头，门脸很窄，只有一块手写的木牌。"
            "规矩有三条：不问客人来历；不赊账；打烊时间由老板娘说了算。"
            "店里只有六张桌子，吧台是旧船木改的，据说原本真属于一条船。"
        ),
        "enabled": True,
    },
    {
        "name": "末班地铁",
        "keys": ["地铁", "末班", "车厢", "站台", "列车"],
        "content": (
            "这条线路的末班车是 23:47 发车。老乘客之间有个说法：末班车偶尔会多停一站，"
            "站名不在任何线路图上。列车员从不解释这件事，被问就说「你听错了」。"
        ),
        "enabled": True,
    },
    {
        "name": "王城祭坛",
        "keys": ["祭坛", "仪式", "公主", "召唤", "王城", "勇者"],
        "content": (
            "王城的召唤仪式每二十年一次，用来从异界请来「命定的勇者」。"
            "典籍记载仪式从未失败过——但典籍是王室自己修的。"
            "祭坛地砖上刻着一圈古文字，据说写的是仪式的真实代价。"
        ),
        "enabled": True,
    },
]


class WorldStore:
    """世界书：关键词触发的设定注入。

    兼容 SillyTavern 的 World Info JSON（`{"entries": {"0": {"keys": [...], "content": "..."}}}`）
    和更简洁的数组格式。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: list[dict[str, Any]] = []
        self._mtime = 0.0
        if not self.path.is_file():
            self._entries = [dict(e) for e in DEFAULT_WORLD]
            self.save()
        else:
            self._reload(force=True)

    @staticmethod
    def _clean_list(raw: Any) -> list[dict[str, Any]]:
        """把各种来源规范化成条目列表。"""
        out: list[dict[str, Any]] = []

        def add(item: dict[str, Any] | None, fallback_name: str = "") -> None:
            if not isinstance(item, dict):
                return
            keys = item.get("keys") or item.get("key") or item.get("keywords") or []
            if isinstance(keys, str):
                keys = [k.strip() for k in keys.split(",")]
            keys = [str(k).strip() for k in keys if str(k).strip()]
            content = str(item.get("content") or item.get("value") or "").strip()
            if not keys or not content:
                return
            out.append(
                {
                    "name": str(
                        item.get("comment") or item.get("name") or fallback_name
                    ).strip()
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
            entries = raw.get("entries", raw)
            if isinstance(entries, list):
                for item in entries:
                    add(item)
            elif isinstance(entries, dict):
                for key, item in entries.items():
                    add(item, fallback_name=str(key))
        return out

    def _reload(self, force: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if not force and mtime == self._mtime:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        items = self._clean_list(raw)
        if items or not self._entries:
            self._entries = items
            self._mtime = mtime

    def all(self) -> list[dict[str, Any]]:
        self._reload()
        return self._entries

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    def import_raw(self, raw: Any, replace: bool = False) -> tuple[int, str]:
        items = self._clean_list(raw)
        if not items:
            return 0, "没解析出任何条目（需要 keys + content）"
        self._reload()
        if replace:
            self._entries = items
        else:
            names = {e["name"] for e in self._entries}
            self._entries.extend(e for e in items if e["name"] not in names)
        self.save()
        return len(items), f"已导入 {len(items)} 条世界设定"

    def remove(self, name: str) -> tuple[bool, str]:
        self._reload()
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["name"] != name]
        if len(self._entries) == before:
            return False, f"没有叫「{name}」的条目"
        self.save()
        return True, f"已删除「{name}」"

    def match(self, texts: list[str], limit: int = 5) -> list[dict[str, Any]]:
        """按关键词命中，并排优先级。

        约定：texts[0] 是**当前这条消息**，后面依次是更早的历史。
        所以直接按顺序遍历即可——下标越小越"近"，优先级越高。

        排序：先看命中的是第几条消息（越近越好），
        再看那条消息命中几个关键词（越多越好）。同一条只取一次。
        """
        self._reload()
        hits: list[tuple[int, int, dict[str, Any]]] = []
        for entry in self._entries:
            if not entry.get("enabled", True):
                continue
            for recency, text in enumerate(texts):
                if not text:
                    continue
                count = sum(1 for k in entry["keys"] if k in text)
                if count:
                    hits.append((recency, -count, entry))
                    break
        hits.sort(key=lambda x: (x[0], x[1]))
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for _, _, entry in hits:
            if entry["name"] in seen:
                continue
            seen.add(entry["name"])
            out.append(entry)
            if len(out) >= limit:
                break
        return out


def build_world_block(entries: list[dict[str, Any]]) -> str:
    """把命中的世界设定拼成提示词片段。"""
    if not entries:
        return ""
    lines = [
        "[世界设定] 以下内容与本轮对话相关，请当作既定事实使用，"
        "自然地体现在言行里，不要直接背诵或向用户复述："
    ]
    for entry in entries:
        lines.append(f"· {entry['name']}：{entry['content']}")
    return "\n".join(lines)


def _clean_card(raw: Any) -> dict[str, Any] | None:
    """把任意来源的卡规范化；认不出来就返回 None。"""
    if not isinstance(raw, dict):
        return None

    # SillyTavern V2：数据在 data 里；V1：字段平铺
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw

    name = str(data.get("name") or raw.get("name") or "").strip()
    if not name:
        return None

    card: dict[str, Any] = {"name": name}
    for field in CARD_FIELDS:
        if field == "name":
            continue
        value = data.get(field, raw.get(field))
        if field == "tags":
            if isinstance(value, list):
                card["tags"] = [str(t).strip() for t in value if str(t).strip()]
            elif isinstance(value, str) and value.strip():
                card["tags"] = [t.strip() for t in value.split(",") if t.strip()]
            else:
                card["tags"] = []
        else:
            card[field] = str(value or "").strip()
    return card


class CardStore:
    """角色卡存储。按 mtime 热加载，管理台改完即时生效。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cards: dict[str, dict[str, Any]] = {}
        self._mtime: float = 0.0
        self._load_or_create()

    def _load_or_create(self) -> None:
        if not self.path.is_file():
            self._cards = {k: dict(v) for k, v in DEFAULT_CARDS.items()}
            self.save()
            return
        self._reload(force=True)

    def _reload(self, force: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if not force and mtime == self._mtime:
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 文件正在被写时可能读失败，保留旧值
            return

        cards: dict[str, dict[str, Any]] = {}
        if isinstance(raw, list):
            # 也接受数组形式（方便直接粘一个卡列表）
            for item in raw:
                card = _clean_card(item)
                if card:
                    cards[card["name"]] = card
        elif isinstance(raw, dict):
            for key, item in raw.items():
                card = _clean_card(item)
                if card:
                    cards[str(key)] = card

        if cards or not self._cards:
            self._cards = cards
            self._mtime = mtime

    def all(self) -> dict[str, dict[str, Any]]:
        self._reload()
        return self._cards

    def get(self, name: str) -> dict[str, Any] | None:
        self._reload()
        return self._cards.get(name)

    def names(self) -> list[str]:
        self._reload()
        return list(self._cards.keys())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._cards, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    def upsert(self, raw: Any) -> tuple[bool, str]:
        card = _clean_card(raw)
        if not card:
            return False, "角色卡缺少 name 字段，或格式无法识别"
        self._reload()
        self._cards[card["name"]] = card
        self.save()
        return True, f"已保存角色卡「{card['name']}」"

    def remove(self, name: str) -> tuple[bool, str]:
        self._reload()
        if name not in self._cards:
            return False, f"没有叫「{name}」的角色卡"
        self._cards.pop(name)
        self.save()
        return True, f"已删除角色卡「{name}」"


class TavernState:
    """各会话的酒馆状态：是否激活、当前用哪张卡。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._state: dict[str, dict[str, Any]] = {}
        self._mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._state = {
                    str(k): v for k, v in raw.items() if isinstance(v, dict)
                }
                self._mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass

    def _reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            self._load()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    def get(self, session_key: str) -> dict[str, Any]:
        self._reload()
        entry = self._state.get(session_key) or {}
        mode = str(entry.get("mode") or "chat")
        return {
            "active": bool(entry.get("active")),
            "card": str(entry.get("card") or ""),
            # chat = 闲聊（只换角色，不注入人设和剧情）
            # script = 剧本（固定人设 + 角色 + 剧情大纲）
            "mode": mode if mode in ("chat", "script") else "chat",
            "script": str(entry.get("script") or ""),
        }

    def set(
        self,
        session_key: str,
        *,
        active: bool,
        card: str | None = None,
        mode: str | None = None,
        script: str | None = None,
    ) -> None:
        self._reload()
        entry = self._state.get(session_key) or {}
        entry["active"] = active
        if card is not None:
            entry["card"] = card
        if mode is not None:
            entry["mode"] = mode
        if script is not None:
            entry["script"] = script
        self._state[session_key] = entry
        self._save()


# 行为开关：加进系统提示词的额外约束。
# 可以在剧本/角色卡里声明默认开启，用户也能用触词随时切：
#     开启防抢话 / 关闭防抢话
BEHAVIOR_FLAGS: dict[str, str] = {
    "防抢话": (
        "【防抢话】只写你自己的言行。绝对不要替对方说话、不要替对方做决定、"
        "不要描写对方的动作或心理活动。对方做了什么、说了什么，由他自己发消息告诉你。"
    ),
    "防绝望": (
        "【防绝望】不要让剧情滑向绝望或毁灭性的收尾。遇到冲突时留有余地，"
        "给后续留出转折空间；不要动辄生离死别、不要用极端情节推进。"
    ),
    "短回复": (
        "【短回复】每次回复控制在 2~4 句，不要长篇大论。"
    ),
    "推进剧情": (
        "【推进剧情】主动给对话提供新的信息或情境，不要停在原地等对方开口；"
        "每次回复都往前推一点。"
    ),
}




def build_flags_block() -> str:
    """行为规则全量拼装。默认全开，不做开关。"""
    return "\n".join(BEHAVIOR_FLAGS.values())


def build_tavern_prompt(
    card: dict[str, Any],
    *,
    char_name: str,
    user_name: str,
    user_persona: str = "",
    background: str = "",
    world: list[dict[str, Any]] | None = None,
) -> str:
    """按 SillyTavern 的思路拼角色扮演提示词。

    顺序：行为约束 → 角色设定 → 用户设定 → 剧情大纲 → 对话示例。
    先知道"怎么演"，再知道"演谁"，最后知道"往哪走"。
    """
    parts: list[str] = []

    system = (card.get("system_prompt") or "").strip() or DEFAULT_SYSTEM_PROMPT
    parts.append(system)

    parts.append(
        f"你现在扮演的角色是「{char_name}」，下文用「我」指代你自己；"
        f"与你对话的用户叫「{user_name}」，下文用「你」指代他。\n"
        "记住：永远用「我」自称，不要用角色名或「她/他」来称呼自己；"
        "永远用「你」称呼对方，不要用「他/她」。"
    )

    profile: list[str] = [f"角色名：{char_name}"]
    if card.get("description"):
        profile.append(f"设定：{card['description']}")
    if card.get("personality"):
        profile.append(f"性格：{card['personality']}")
    if card.get("scenario"):
        profile.append(f"当前场景：{card['scenario']}")
    parts.append("[角色设定]\n" + "\n".join(profile))

    if user_persona.strip():
        parts.append(f"[用户设定] 用户扮演的是：{user_persona.strip()}")

    if background.strip():
        # 剧本的背景：一段自然语言的设定（世界观／关系／此刻的情境）。
        # 不是任务清单，也不分幕——它定的是"框架"，不是"剧情线"。
        parts.append(
            "[背景设定] 这是当前情境的既定事实，当作前提使用，"
            "自然地体现在言行里，不要直接复述：\n" + background.strip()
        )

    parts.append("[行为要求]\n" + build_flags_block())

    world_block = build_world_block(world or [])
    if world_block:
        parts.append(world_block)

    if card.get("mes_example"):
        # 示例对话里用 {char}/{user} 占位，这里替换成真实名字
        example = (
            card["mes_example"]
            .replace("{{char}}", char_name)
            .replace("{{user}}", user_name)
        )
        parts.append(
            "[对话示例] 下面是这个角色说话风格的参考，模仿语气即可，"
            "不要照抄内容：\n" + example
        )

    return "\n\n".join(parts)


def opening_message(card: dict[str, Any], user_name: str) -> str:
    """开场白：{{user}} 占位替换掉后直接发给用户。"""
    first = (card.get("first_mes") or "").strip()
    return first.replace("{{user}}", user_name).replace("{{char}}", card.get("name", ""))


# 剧本是原创的，不搬运社区角色卡：社区卡多为同人角色，有版权问题。
# 想用社区卡就在管理台「酒馆」页导入 JSON（ST 的 V1/V2 格式都支持）。
DEFAULT_SCRIPTS: dict[str, dict[str, Any]] = {
    "深夜食堂": {
        "name": "深夜食堂",
        "desc": "打烊前的酒馆，一个愿意听你说话的人。",
        "card": "小酒馆老板娘",
        "user_persona": "刚下班的普通上班族，今天过得不太顺，路过这家还亮着灯的小店就进来了。",
        "background": (
            "深夜十一点，「夜航船」快打烊了，炉火压得很低，店里只剩你一个客人。"
            "老板娘正在擦最后一只杯子。她在这条街上开了七年店，见过太多故事，"
            "所以话不多，但总能说到点子上。她不问客人来历，也不急着给建议——"
            "更多时候只是听着，偶尔用一句反问或一个比喻，让人自己把话说清楚。"
            "她对熟客会多关照一句，对陌生人保持礼貌的距离。"
        ),
        "tags": ["内置", "日常", "治愈"],
    },
    "最后一班地铁": {
        "name": "最后一班地铁",
        "desc": "末班车上只剩你和对面那个人。悬疑向，需要你自己发问。",
        "card": "末班乘客",
        "user_persona": "加班到十一点才赶上末班的普通乘客，隐约觉得车厢里有什么不对。",
        "background": (
            "凌晨的末班地铁，车厢里只剩你和对面座位上的一个人。"
            "他三十岁上下，深色外套，手里一直捏着一张对折的纸，报站时从不抬头。"
            "这趟车还有四站到终点。他戒备心很重，答话极短，更常反问你，"
            "绝不主动交代任何关键信息——必须被问到点子上才肯松口。"
            "他偶尔流露的不安藏得很浅，但嘴上绝不承认。"
            "老乘客之间有个说法：末班车偶尔会多停一站，站名不在任何线路图上。"
        ),
        "tags": ["内置", "悬疑"],
    },
    "你被召唤错了": {
        "name": "你被召唤错了",
        "desc": "异世界召唤仪式出岔子，被召唤出来的勇者是你。奇幻轻喜剧。",
        "card": "召唤出的公主",
        "user_persona": "一个普通的现代人，莫名其妙被传送到了王城的召唤阵里。",
        "background": (
            "王城的召唤仪式每二十年一次，用来从异界请来「命定的勇者」。"
            "此刻祭坛上的召唤阵刚刚熄灭，站在台上的公主十七岁，"
            "礼仪训练刻在骨子里，但正努力维持体面——因为出现的不是勇者，是你。"
            "卫兵在下面窃窃私语。公主骄傲、嘴硬、怕丢脸，张口就是「命运」「预言」"
            "这类大词来掩饰自己搞砸了仪式；被戳穿会恼羞成怒，"
            "但真遇到危险时比谁都护着你。"
        ),
        "tags": ["内置", "奇幻", "喜剧"],
    },
}


class ScriptStore:
    """剧本库：剧情大纲 + 建议人设 + 关联角色卡。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._items: dict[str, dict[str, Any]] = {}
        self._mtime = 0.0
        if not self.path.is_file():
            self._items = {k: dict(v) for k, v in DEFAULT_SCRIPTS.items()}
            self.save()
        else:
            self._reload(force=True)

    def _reload(self, force: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if not force and mtime == self._mtime:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        items: dict[str, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for key, item in raw.items():
                if isinstance(item, dict) and str(item.get("name") or key).strip():
                    name = str(item.get("name") or key).strip()
                    items[name] = {
                        "name": name,
                        "desc": str(item.get("desc") or "").strip(),
                        "card": str(item.get("card") or "").strip(),
                        "user_persona": str(item.get("user_persona") or "").strip(),
                        # background = 一段自然语言的背景设定（世界观/关系/情境）
                        # 兼容旧的 outline 字段名，值直接拿过来当背景用
                        "background": str(
                            item.get("background") or item.get("outline") or ""
                        ).strip(),
                        "tags": [str(t) for t in (item.get("tags") or [])],
                    }
        if items or not self._items:
            self._items = items
            self._mtime = mtime

    def all(self) -> dict[str, dict[str, Any]]:
        self._reload()
        return self._items

    def get(self, name: str) -> dict[str, Any] | None:
        self._reload()
        return self._items.get(name)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    def upsert(self, raw: Any) -> tuple[bool, str]:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            return False, "剧本缺少 name 字段"
        name = str(raw["name"]).strip()
        self._reload()
        self._items[name] = {
            "name": name,
            "desc": str(raw.get("desc") or "").strip(),
            "card": str(raw.get("card") or "").strip(),
            "user_persona": str(raw.get("user_persona") or "").strip(),
            "background": str(
                raw.get("background") or raw.get("outline") or ""
            ).strip(),
            "tags": [str(t) for t in (raw.get("tags") or [])],
        }
        self.save()
        return True, f"已保存剧本「{name}」"

    def remove(self, name: str) -> tuple[bool, str]:
        self._reload()
        if name not in self._items:
            return False, f"没有叫「{name}」的剧本"
        self._items.pop(name)
        self.save()
        return True, f"已删除剧本「{name}」"


class ProfileStore:
    """用户人设与剧情大纲。

    键空间：
        user:{user_id}      用户人设（「我是谁」）——按人存，跨会话跟着走
        outline:{会话key}    剧情大纲——按会话存，一个群一个进度
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, str] = {}
        self._mtime = 0.0
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): str(v) for k, v in raw.items() if v}
                self._mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass

    def _reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            self._load()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    def user_persona(self, user_id: int | str) -> str:
        self._reload()
        return self._data.get(f"user:{user_id}", "")

    def set_user_persona(self, user_id: int | str, text: str) -> None:
        self._reload()
        key = f"user:{user_id}"
        if text.strip():
            self._data[key] = text.strip()
        else:
            self._data.pop(key, None)
        self._save()

    def outline(self, session_key: str) -> str:
        self._reload()
        return self._data.get(f"outline:{session_key}", "")

    def set_outline(self, session_key: str, text: str) -> None:
        self._reload()
        key = f"outline:{session_key}"
        if text.strip():
            self._data[key] = text.strip()
        else:
            self._data.pop(key, None)
        self._save()
