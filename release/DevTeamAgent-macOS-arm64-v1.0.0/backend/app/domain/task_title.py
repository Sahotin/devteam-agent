from __future__ import annotations

import re


def build_task_title(requirement: str) -> str:
    """从完整需求中生成稳定、可读的短标题，不丢失原始需求。"""
    plain = re.sub(r"[*_`#]+", "", requirement)
    plain = re.sub(r"\s+", " ", plain).strip(" 。；;，,")
    lowered = plain.lower()

    invisible = any(token in plain for token in ("没有出现", "没有显示", "未出现", "未显示", "打不开"))
    if invisible and any(token in plain for token in ("游戏画面", "页面", "浏览器")):
        return "修复项目页面无法显示问题"
    if "serving http on" in lowered and "8080" in lowered:
        return "排查本地服务启动与访问问题"
    if any(token in plain for token in ("启动失败", "无法启动", "启动异常")):
        return "修复项目启动异常"

    first_clause = re.split(r"[。！？!?；;\n]", plain, maxsplit=1)[0].strip()
    if len(first_clause) <= 42:
        return first_clause or "研发任务"
    return f"{first_clause[:40].rstrip()}…"
