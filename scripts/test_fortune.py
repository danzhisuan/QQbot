"""今日运势自检：确定性、差异性、格式、分布。

用法：
    docker exec nonebot python /app/scripts/test_fortune.py
    python scripts/test_fortune.py          （本地，无需 httpx）
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

# Windows 控制台默认 GBK，打不出 emoji，强制切 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

CORE_PATH = Path(__file__).resolve().parent.parent / "src/plugins/fortune/core.py"
spec = importlib.util.spec_from_file_location("fortune_core", CORE_PATH)
core = importlib.util.module_from_spec(spec)
sys.modules["fortune_core"] = core
spec.loader.exec_module(core)

_failed = False


def check(label: str, cond: bool, extra: str = "") -> None:
    global _failed
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        _failed = True


DAY = date(2026, 10, 6)
OTHER_DAY = date(2026, 10, 7)


def test_determinism() -> None:
    uid = 100000000
    a = core.build_fortune(uid, "阿伟", DAY)
    b = core.build_fortune(uid, "阿伟", DAY)
    check("同人同日结果完全一致", a == b)

    # 隔一天必须变
    c = core.build_fortune(uid, "阿伟", OTHER_DAY)
    check("跨天结果变化", a != c)

    # 不同用户结果不同
    d = core.build_fortune(123456, "阿伟", DAY)
    check("不同用户结果不同", a != d)


def test_format() -> None:
    text = core.build_fortune(100000000, "阿伟", DAY)
    for field in ("今日运势", "宜：", "忌：", "幸运数字：", "幸运颜色：", "阿伟", "10月6日"):
        check(f"包含「{field}」", field in text)
    check("不使用 Markdown", "**" not in text and "##" not in text)
    check("行数合理", 7 <= len(text.splitlines()) <= 12, f"{len(text.splitlines())} 行")


def test_fields_consistency() -> None:
    """等级与幸运指数、宜忌数量、星星数都要自洽。"""
    bad_cases = []
    for uid in range(1, 400):
        data = core.draw(uid, DAY)
        lo, hi = core.SCORE_RANGE[str(data["level"])]
        if not (lo <= int(data["score"]) <= hi):  # type: ignore[arg-type]
            bad_cases.append(("score", uid, data))
        if len(data["good"]) != 2 or len(data["bad"]) != 2:  # type: ignore[arg-type]
            bad_cases.append(("count", uid, data))
        if len(set(data["good"])) != 2 or len(set(data["bad"])) != 2:  # type: ignore[arg-type]
            bad_cases.append(("dup", uid, data))
        if not (1 <= int(data["number"]) <= 99):  # type: ignore[arg-type]
            bad_cases.append(("number", uid, data))
    check("400 个样本字段全部自洽", not bad_cases, str(bad_cases[:2]))


def test_distribution() -> None:
    """抽 20000 次，验证权重分布和「凶」的比例。"""
    total = 20000
    counter: dict[str, int] = {}
    for uid in range(total):
        level = str(core.draw(uid, DAY)["level"])
        counter[level] = counter.get(level, 0) + 1

    expected = dict(core.LEVELS)
    print("     分布:", {k: f"{v / total:.1%}" for k, v in
                        sorted(counter.items(), key=lambda kv: -kv[1])})

    ok = True
    for level, weight in expected.items():
        got = counter.get(level, 0) / total
        want = weight / 100
        if abs(got - want) > 0.02:  # 允许 2 个百分点误差
            ok = False
            print(f"      偏差过大: {level} 期望 {want:.1%} 实际 {got:.1%}")
    check("权重分布符合预期（±2%）", ok)
    check("「凶」低于 8%", counter.get("凶", 0) / total < 0.08)


def test_sample_output() -> None:
    print("\n--- 示例输出 ---")
    print(core.build_fortune(100000000, "阿伟", DAY))
    print()


def main() -> int:
    test_determinism()
    test_format()
    test_fields_consistency()
    test_distribution()
    test_sample_output()
    print("结果:", "全部通过" if not _failed else "存在失败")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
