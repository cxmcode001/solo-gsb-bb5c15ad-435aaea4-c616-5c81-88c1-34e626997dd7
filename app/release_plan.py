"""多权威节点联合发布规划（不落库）。

在当日 YYYYMMDD00..99 候选区间内，按从小到大寻找首个相对每个节点
当前 serial 都属于 RFC 1982 有效前进（advance）的候选值；若当日区间
内不存在共同候选，则汇总各节点对整个候选区间的阻断统计
（equal / regression / incomparable），由 API 层转换为 HTTP 409。
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from .rfc1982 import (
    MAX_SERIAL,
    REL_ADVANCE,
    REL_EQUAL,
    REL_INCOMPARABLE,
    REL_REGRESSION,
    classify,
    forward_distance,
)
from .suggest import ERR_OUT_OF_RANGE, NN_MAX, daily_base

# ReleasePlanError.code 取值（用于 409 响应中的 reason 字段）
ERR_NO_COMMON_CANDIDATE = "no_common_candidate"  # 当日区间内无共同候选

#: 阻断关系（非有效前进）——候选值命中任一即对该节点不可发布
_BLOCKING_RELATIONS = (REL_EQUAL, REL_REGRESSION, REL_INCOMPARABLE)


class ReleasePlanError(Exception):
    """无法形成发布规划（对应 HTTP 409 Conflict）。

    :param code: 机器可读的原因代码（no_common_candidate / date_out_of_range）
    :param blockers: 各节点对候选区间的阻断摘要（date_out_of_range 时为空）
    """

    def __init__(
        self,
        message: str,
        code: str,
        blockers: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.blockers = blockers or []


def _blocking_summary(nodes: Sequence[tuple[str, int]], candidates: range) -> list[dict]:
    """统计每个节点的当前 serial 对整个候选区间的阻断关系分布。"""
    blockers: list[dict] = []
    for name, current in nodes:
        counts = {relation: 0 for relation in _BLOCKING_RELATIONS}
        for candidate in candidates:
            relation = classify(current, candidate)
            if relation in counts:
                counts[relation] += 1
        blockers.append(
            {
                "name": name,
                "current": current,
                "equal": counts[REL_EQUAL],
                "regression": counts[REL_REGRESSION],
                "incomparable": counts[REL_INCOMPARABLE],
            }
        )
    return blockers


def build_release_plan(
    zone: str,
    nodes: Sequence[tuple[str, int]],
    today: date,
) -> dict:
    """为多权威节点选择共同的下一 serial，返回发布规划（不写库）。

    候选区间为当日 YYYYMMDD00..99，按从小到大取首个相对每个节点当前
    serial 均为 RFC 1982 有效前进的值；无共同候选时抛出 ReleasePlanError
    并携带逐节点阻断摘要。
    """
    base = daily_base(today)
    if base + NN_MAX > MAX_SERIAL:
        raise ReleasePlanError(
            "当前日期超出 32 位序列号可表达范围", code=ERR_OUT_OF_RANGE
        )
    candidates = range(base, base + NN_MAX + 1)

    chosen: int | None = None
    for candidate in candidates:
        if all(classify(current, candidate) == REL_ADVANCE for _, current in nodes):
            chosen = candidate
            break

    if chosen is None:
        raise ReleasePlanError(
            f"当日区间 {base}-{base + NN_MAX} 内不存在相对所有节点"
            "均为有效前进的候选 serial",
            code=ERR_NO_COMMON_CANDIDATE,
            blockers=_blocking_summary(nodes, candidates),
        )

    return {
        "zone": zone,
        "date": today.strftime("%Y%m%d"),
        "serial": chosen,
        "nodes": [
            {
                "name": name,
                "current": current,
                "relation": classify(current, chosen),
                "distance": forward_distance(current, chosen),
            }
            for name, current in nodes
        ],
        "note": "候选 serial 相对每个节点的当前值均为 RFC 1982 意义下的有效前进",
    }
