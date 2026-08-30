from __future__ import annotations

import re

WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def digits_only(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def format_abn(value: str | None) -> str:
    d = digits_only(value)
    if len(d) != 11:
        return value or ""
    return f"{d[0:2]} {d[2:5]} {d[5:8]} {d[8:11]}"


def is_valid_abn(value: str | None) -> bool:
    d = digits_only(value)
    if len(d) != 11:
        return False
    nums = [int(c) for c in d]
    nums[0] -= 1
    return sum(n * w for n, w in zip(nums, WEIGHTS)) % 89 == 0
