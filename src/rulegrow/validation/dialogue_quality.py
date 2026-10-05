"""Dialogue quality evaluation (PRD section 5).

Deterministic heuristics always run; an optional independent model review
(cfg.reviewer_enabled) treats input/reply as data with a fixed rubric.
"""

from __future__ import annotations

import re

from ..lint.diagnostics import Diagnostic

GENERIC_MARKERS = (
    "我不知道", "无法回答", "请再说一遍", "你说什么",
)


def evaluate_reply(user_input: str, reply: str, reviewer=None) -> Diagnostic:
    diag = Diagnostic(stage="quality")

    if len(reply) < 2:
        diag.add("Q001", "回复内容过短，缺乏实际内容")
    if len(reply) > 2000:
        diag.add("Q002", "回复过长，不符合对话场景")

    # relevance: short replies must share some lexical overlap with the input
    # (bigram or character); longer replies are presumed contentful
    # (regression/guard tests cover behaviour)
    if len(reply) < 30 and not (_has_overlap(user_input, reply, 2) or _has_overlap(user_input, reply, 1)):
        diag.add("Q003", "回复与输入无明显关联，疑似万能回复")

    # pure echo of input is not a real reply
    if reply.strip() == user_input.strip():
        diag.add("Q004", "回复只是原样复述输入")

    # generic filler that would match almost anything
    for marker in GENERIC_MARKERS:
        if marker in reply and len(reply) < 12:
            diag.add("Q005", f"疑似万能兜底回复：「{reply}」")

    if reviewer is not None and diag.ok:
        passed, reason = reviewer(user_input, reply)
        if not passed:
            diag.add("Q006", f"语义评审未通过：{reason}")

    return diag


def _has_overlap(user_input: str, reply: str, n: int = 2) -> bool:
    """Check for n-gram overlap between input and reply (CJK-friendly)."""
    input_ngrams = _ngrams(user_input, n)
    if not input_ngrams:
        return True  # nothing to compare against; let other checks decide
    return bool(_ngrams(reply, n) & input_ngrams)


def _ngrams(text: str, n: int) -> set[str]:
    cleaned = re.sub(r"[，。！？、\s]", "", text)
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + n] for i in range(len(cleaned) - n + 1)}
