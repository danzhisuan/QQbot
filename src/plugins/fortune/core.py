"""今日运势的纯逻辑：不依赖 NoneBot，方便单独测试。

确定性设计：同一个人在同一天，无论查多少次、谁来查，结果都完全一样。
种子 = sha256("fortune:{user_id}:{yyyy-mm-dd}")，跨天自动变化。
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta, timezone

# 固定用北京时间判断"今天"，避免服务器时区导致凌晨换运
CST = timezone(timedelta(hours=8))

# (等级, 权重) —— 权重总和 100。凶刻意压到 4%，别让人扫兴
LEVELS: list[tuple[str, int]] = [
    ("大吉", 6),
    ("中吉", 15),
    ("小吉", 22),
    ("吉", 24),
    ("末吉", 18),
    ("平", 11),
    ("凶", 4),
]

# 等级 -> 幸运指数区间，让两者观感一致（大吉不该配 60 分）
SCORE_RANGE: dict[str, tuple[int, int]] = {
    "大吉": (92, 100),
    "中吉": (84, 91),
    "小吉": (74, 83),
    "吉": (66, 73),
    "末吉": (58, 65),
    "平": (50, 57),
    "凶": (30, 49),
}

LEVEL_EMOJI: dict[str, str] = {
    "大吉": "🌟",
    "中吉": "✨",
    "小吉": "🍀",
    "吉": "🙂",
    "末吉": "🍃",
    "平": "😐",
    "凶": "🌧️",
}

GOOD = [
    "摸鱼", "早睡", "喝奶茶", "主动找人聊天", "整理桌面", "出门散步",
    "把拖了很久的小事做掉", "给喜欢的人发消息", "清购物车", "听老歌",
    "吃顿好的", "撸猫", "看番", "打游戏", "复盘总结", "换个头像",
    "拍张照", "写点东西", "多喝水", "晒被子",
]

BAD = [
    "熬夜", "内卷", "冲动消费", "开会", "改需求", "借钱给别人",
    "立 flag", "深夜 emo", "空腹喝咖啡", "跟人抬杠", "拖到最后一天",
    "相信「下次一定」", "抄近道", "在群里吵架", "翻旧账",
    "边走路边看手机", "把话说太满",
]

COLORS = [
    "藕荷色", "雾霾蓝", "奶油白", "脏橘", "抹茶绿", "高级灰",
    "蜜桃粉", "墨黑", "焦糖色", "天青色", "柠黄", "丁香紫",
]

ADVICE_GOOD = [
    "今天适合把拖了很久的小事做掉，成就感会意外地高。",
    "有想法就说出口，今天你的表达比平时更有说服力。",
    "适合做一次小决定，别拖到明天——明天你会更犹豫。",
    "今天的好运藏在细节里，慢一点反而更快。",
    "适合和很久没联系的人聊两句，可能会有意外收获。",
    "状态在线，把最难的那件事放在上午做。",
]

ADVICE_MID = [
    "今天适合稳一点，把手上没做完的先收尾。",
    "别急着开启新计划，先把桌面的东西清一清。",
    "运气不温不火，按部就班就是最优解。",
    "今天适合少说多做，安静地把事推进一点。",
    "有点小波折但不碍事，别往心里去就行。",
]

ADVICE_BAD = [
    "今天不太顺，少做重大决定，早点休息。",
    "运气偏低，别硬刚，把节奏放慢一点。",
    "今天容易踩坑，凡事多确认一遍再动手。",
    "状态一般，允许自己摸一天鱼也没关系。",
]


def today() -> date:
    return datetime.now(CST).date()


def make_rng(user_id: int, day: date) -> random.Random:
    """用 用户ID + 日期 作种子，保证同一天结果完全可复现。"""
    digest = hashlib.sha256(f"fortune:{user_id}:{day.isoformat()}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def draw(user_id: int, day: date) -> dict[str, object]:
    """抽出当天的运势数据（纯数据，方便测试和复用）。"""
    rng = make_rng(user_id, day)

    names, weights = zip(*LEVELS)
    level = rng.choices(names, weights=weights, k=1)[0]
    lo, hi = SCORE_RANGE[level]
    score = rng.randint(lo, hi)

    good = rng.sample(GOOD, 2)
    bad = rng.sample(BAD, 2)

    if level in ("大吉", "中吉"):
        advice = rng.choice(ADVICE_GOOD)
    elif level == "凶":
        advice = rng.choice(ADVICE_BAD)
    elif level == "平":
        advice = rng.choice(ADVICE_MID + ADVICE_BAD)
    else:
        advice = rng.choice(ADVICE_MID)

    return {
        "level": level,
        "score": score,
        "good": good,
        "bad": bad,
        "number": rng.randint(1, 99),
        "color": rng.choice(COLORS),
        "advice": advice,
    }


def render(data: dict[str, object], nickname: str = "", day: date | None = None) -> str:
    """把运势数据渲染成适合 QQ 的纯文本（不用 Markdown 表格）。"""
    day = day or today()
    level = str(data["level"])
    score = int(data["score"])  # type: ignore[arg-type]
    stars = "★" * round(score / 20) + "☆" * (5 - round(score / 20))

    head = f"{LEVEL_EMOJI[level]} 今日运势"
    if nickname:
        head += f" · {nickname}"
    head += f" · {day.month}月{day.day}日"

    return "\n".join(
        [
            head,
            "",
            f"【{level}】{stars} {score}%",
            f"宜：{'、'.join(data['good'])}",  # type: ignore[arg-type]
            f"忌：{'、'.join(data['bad'])}",  # type: ignore[arg-type]
            f"幸运数字：{data['number']}",
            f"幸运颜色：{data['color']}",
            "",
            str(data["advice"]),
        ]
    )


def build_fortune(user_id: int, nickname: str = "", day: date | None = None) -> str:
    """一步到位：抽签 + 渲染。"""
    day = day or today()
    return render(draw(user_id, day), nickname, day)
