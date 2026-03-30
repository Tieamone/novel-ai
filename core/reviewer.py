import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
from core.api_client import call_api
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg

REVIEW_PASS_TOTAL = 75
REVIEW_PASS_L1 = 30

REVIEWER_SYSTEM = """你是一位严格的网络小说责任编辑，职责是判断章节是否可发布。

必须执行三层评分并给出结构化归因：

【L1 逻辑与设定一致性】0-45分
- 人物行为是否符合既有人设（避免核心OOC）
- 时间线与因果是否自洽
- 是否存在硬设定冲突（世界规则/生死状态/关键事实）

【L2 伏笔与剧情承接】0-25分
- 历史未兑现伏笔是否被无视或误解
- 新增伏笔是否自然、是否服务后续
- 本章是否完成“目标导向推进”

【L3 可读性与网文节奏】0-30分
- 是否存在注水、重复表达
- 是否具备有效冲突推进/信息推进
- 结尾是否形成阅读驱动力

一票否决项（任一命中即不通过）：
1) 核心设定冲突（setting_conflict）
2) 重大时间线矛盾（timeline_break）
3) 主角或核心角色严重OOC（core_ooc）
4) 关键承诺伏笔被硬性遗忘且导致断裂（critical_payoff_missing）

通过条件（同时满足）：
- veto_items 为空
- score_total >= 75
- score_l1 >= 30

严格只输出JSON，不要解释，不要Markdown：
{
  "pass": true/false,
  "score_total": 0-100整数,
  "score_l1": 0-45整数,
  "score_l2": 0-25整数,
  "score_l3": 0-30整数,
  "veto_items": [
    {"code": "setting_conflict|timeline_break|core_ooc|critical_payoff_missing", "reason": "命中理由"}
  ],
  "l1_issues": ["问题描述"],
  "l2_issues": ["问题描述"],
  "l3_issues": ["问题描述"],
  "failure_attribution": {
    "primary_layer": "L1|L2|L3|none",
    "root_cause": "最关键失败原因，若通过写none",
    "severity": "high|medium|low|none"
  },
  "suggestions": "失败时给出可执行修订建议；通过写质量合格"
}"""


def _extract_json_obj(raw: str) -> dict:
    if not raw:
        return {}
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return {}
    try:
        obj = json.loads(match.group())
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _to_int(value, default=0, min_value=None, max_value=None) -> int:
    try:
        iv = int(float(value))
    except Exception:
        iv = default
    if min_value is not None:
        iv = max(min_value, iv)
    if max_value is not None:
        iv = min(max_value, iv)
    return iv


def _normalize_issue_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def _normalize_veto_items(value) -> list:
    valid_codes = {
        "setting_conflict",
        "timeline_break",
        "core_ooc",
        "critical_payoff_missing",
    }
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, dict):
            code = str(item.get("code", "")).strip()
            reason = str(item.get("reason", "")).strip()
        else:
            code = str(item).strip()
            reason = ""
        if not code:
            continue
        if code not in valid_codes:
            continue
        out.append({"code": code, "reason": reason or "命中一票否决项"})
    return out


def _review_error_result(message: str, issue: str) -> dict:
    return {
        "pass": False,
        "score_total": 0,
        "score_l1": 0,
        "score_l2": 0,
        "score_l3": 0,
        "score": 0,
        "veto_items": [],
        "veto_triggered": False,
        "veto_reasons": [],
        "l1_issues": [issue],
        "l2_issues": [],
        "l3_issues": [],
        "failure_attribution": {
            "primary_layer": "L1",
            "root_cause": issue,
            "severity": "high",
        },
        "suggestions": message,
        "review_error": True,
        "retry_hint": issue,
    }


def _infer_failure_layer(l1_issues: list, l2_issues: list, l3_issues: list) -> str:
    if l1_issues:
        return "L1"
    if l2_issues:
        return "L2"
    if l3_issues:
        return "L3"
    return "L3"


def _normalize_failure_attribution(value, passed: bool,
                                   l1_issues: list, l2_issues: list,
                                   l3_issues: list, veto_triggered: bool) -> dict:
    if isinstance(value, dict):
        primary = str(value.get("primary_layer", "")).strip() or ""
        root = str(value.get("root_cause", "")).strip()
        severity = str(value.get("severity", "")).strip().lower()
    else:
        primary, root, severity = "", "", ""

    valid_primary = {"L1", "L2", "L3", "none"}
    valid_severity = {"high", "medium", "low", "none"}

    if passed:
        return {
            "primary_layer": "none",
            "root_cause": "none",
            "severity": "none",
        }

    if primary not in valid_primary or primary == "none":
        primary = _infer_failure_layer(l1_issues, l2_issues, l3_issues)
    if not root:
        issues = l1_issues + l2_issues + l3_issues
        root = issues[0] if issues else "章节关键问题未明确，需要补充失败归因"
    if severity not in valid_severity or severity == "none":
        if veto_triggered or primary == "L1":
            severity = "high"
        elif primary == "L2":
            severity = "medium"
        else:
            severity = "low"
    return {
        "primary_layer": primary,
        "root_cause": root,
        "severity": severity,
    }


def _rule_pass(score_total: int, score_l1: int, veto_items: list) -> bool:
    return (not veto_items and
            score_total >= REVIEW_PASS_TOTAL and
            score_l1 >= REVIEW_PASS_L1)


def _normalize_review_result(raw_result: dict) -> dict:
    l1_issues = _normalize_issue_list(raw_result.get("l1_issues"))
    l2_issues = _normalize_issue_list(raw_result.get("l2_issues"))
    l3_issues = _normalize_issue_list(raw_result.get("l3_issues"))
    veto_items = _normalize_veto_items(raw_result.get("veto_items"))

    score_l1 = _to_int(
        raw_result.get("score_l1", raw_result.get("l1_score", 0)),
        default=0, min_value=0, max_value=45
    )
    score_l2 = _to_int(
        raw_result.get("score_l2", raw_result.get("l2_score", 0)),
        default=0, min_value=0, max_value=25
    )
    score_l3 = _to_int(
        raw_result.get("score_l3", raw_result.get("l3_score", 0)),
        default=0, min_value=0, max_value=30
    )

    total_raw = raw_result.get("score_total", raw_result.get("total_score"))
    if total_raw is None:
        legacy = raw_result.get("score")
        if legacy is None:
            score_total = score_l1 + score_l2 + score_l3
        else:
            score_total = _to_int(legacy, default=0, min_value=0)
            if score_total <= 10:
                score_total *= 10
    else:
        score_total = _to_int(total_raw, default=0, min_value=0, max_value=100)

    expected_total = score_l1 + score_l2 + score_l3
    if expected_total and abs(score_total - expected_total) > 20:
        score_total = expected_total

    model_pass = bool(raw_result.get("pass"))
    veto_triggered = bool(veto_items)
    final_pass = model_pass and _rule_pass(score_total, score_l1, veto_items)

    suggestions = str(raw_result.get("suggestions", "")).strip()
    if not suggestions:
        suggestions = "质量合格" if final_pass else "优先修复失败归因中的关键问题后再重试"

    failure_attr = _normalize_failure_attribution(
        raw_result.get("failure_attribution"),
        passed=final_pass,
        l1_issues=l1_issues,
        l2_issues=l2_issues,
        l3_issues=l3_issues,
        veto_triggered=veto_triggered,
    )

    veto_reasons = [f"{v['code']}：{v['reason']}" for v in veto_items]

    retry_lines = []
    if veto_reasons:
        retry_lines.append("一票否决项：")
        retry_lines.extend([f"- {x}" for x in veto_reasons])
    if failure_attr.get("primary_layer") != "none":
        retry_lines.append(
            f"失败归因：{failure_attr['primary_layer']} / "
            f"{failure_attr['severity']} / {failure_attr['root_cause']}"
        )
    all_issues = l1_issues + l2_issues + l3_issues
    if all_issues:
        retry_lines.append("问题清单：")
        retry_lines.extend([f"- {x}" for x in all_issues[:6]])
    retry_hint = "\n".join(retry_lines).strip()

    return {
        "pass": final_pass,
        "score_total": score_total,
        "score_l1": score_l1,
        "score_l2": score_l2,
        "score_l3": score_l3,
        "score": _to_int(round(score_total / 10), default=0, min_value=0, max_value=10),
        "veto_items": veto_items,
        "veto_triggered": veto_triggered,
        "veto_reasons": veto_reasons,
        "l1_issues": l1_issues,
        "l2_issues": l2_issues,
        "l3_issues": l3_issues,
        "failure_attribution": failure_attr,
        "suggestions": suggestions,
        "review_error": False,
        "retry_hint": retry_hint,
    }


def _build_retry_feedback(result: dict) -> str:
    if not result:
        return ""
    if result.get("retry_hint"):
        return str(result["retry_hint"]).strip()
    suggestions = str(result.get("suggestions", "")).strip()
    return suggestions


def _persist_review_result(mm: MemoryManager, chapter_num: int, result: dict):
    try:
        mm.update_chapter_review_result(chapter_num, result)
    except Exception as e:
        print(f"  [警告] 审稿结果写入数据库失败：{e}")


def build_review_prompt(ctx: dict, chapter_num: int,
                        content: str, plot_goal: str) -> str:
    chars = ctx.get("characters", [])
    chars_str = "\n".join([
        f"【{c.get('name', '未命名角色')}】"
        f"性格：{c.get('personality', '未记录')}｜"
        f"状态：{c.get('current_status', '未记录')}｜"
        f"关系：{json.dumps(c.get('relationships', {}), ensure_ascii=False)}"
        for c in chars
    ]) if chars else "（暂无人物设定）"

    foreshadow = ctx.get("active_foreshadowing", [])
    f_str = "\n".join(
        [f"- {f.get('fid', '未知ID')}: {f.get('description', '')}" for f in foreshadow]
    ) if foreshadow else "（暂无）"

    return f"""=== 审稿任务 ===
章节：第{chapter_num}章
本章目标：{plot_goal}

=== 人物设定 ===
{chars_str}

=== 未兑现伏笔 ===
{f_str}

=== 待审正文 ===
{content}

请先逐层评估再给最终JSON结论。"""


def review_chapter(novel_name: str, chapter_num: int,
                   content: str, plot_goal: str) -> dict:
    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)

    print(f"  正在审稿第{chapter_num}章...")
    prompt = build_review_prompt(ctx, chapter_num, content, plot_goal)

    raw = call_api(
        system_prompt=REVIEWER_SYSTEM,
        user_message=prompt,
        temperature=0.3,
        max_tokens=1400,
    )

    parsed = _extract_json_obj(raw)
    if not parsed:
        print("  [警告] 审稿返回格式异常，本轮判定为不通过")
        error_result = _review_error_result(
            message="审稿结果格式异常，请重试",
            issue="审稿结果格式异常，无法解析",
        )
        _persist_review_result(mm, chapter_num, error_result)
        return error_result

    result = _normalize_review_result(parsed)
    _persist_review_result(mm, chapter_num, result)

    status = "通过" if result.get("pass") else "不通过"
    print(
        f"  [审稿结果] {status} | 总分：{result['score_total']}/100 "
        f"(L1:{result['score_l1']}/45 L2:{result['score_l2']}/25 "
        f"L3:{result['score_l3']}/30)"
    )
    if result.get("veto_triggered"):
        print("  [一票否决] 已触发：")
        for item in result.get("veto_reasons", []):
            print(f"    - {item}")

    if not result.get("pass"):
        attr = result.get("failure_attribution", {})
        if attr and attr.get("primary_layer") and attr.get("primary_layer") != "none":
            print(
                "  [失败归因] "
                f"{attr.get('primary_layer')} / "
                f"{attr.get('severity', 'unknown')} / "
                f"{attr.get('root_cause', '未提供')}"
            )
        issues = (result.get("l1_issues", []) +
                  result.get("l2_issues", []) +
                  result.get("l3_issues", []))
        for issue in issues:
            print(f"    - {issue}")

    return result


def write_and_review(novel_name: str, chapter_num: int,
                     plot_goal: str, emotion_tag: str = "铺垫",
                     max_retry: int = None) -> str:
    from core.writer import write_chapter

    if max_retry is None:
        max_retry = cfg("novel", "max_retry", 3)

    mm = MemoryManager(novel_name)

    for attempt in range(max_retry):
        print(f"\n  第{attempt+1}次写作尝试...")
        content = write_chapter(novel_name, chapter_num,
                                plot_goal, emotion_tag)

        result = review_chapter(novel_name, chapter_num,
                                content, plot_goal)

        if result.get("pass"):
            print(f"  [OK] 第{chapter_num}章审稿通过！")
            mm.update_chapter_status(chapter_num, "approved")
            return content
        else:
            # 记录重试次数
            mm.increment_retry_count(chapter_num)

            if attempt < max_retry - 1:
                retry_feedback = _build_retry_feedback(result)
                if retry_feedback:
                    plot_goal = (
                        f"{plot_goal}\n\n上次问题（必须修复）：\n{retry_feedback}"
                    )
                print(f"  [重写] 准备第{attempt+2}次写作...")
            else:
                if result.get("review_error"):
                    print(f"  [错误] 审稿连续{max_retry}次异常，停止自动通过")
                    mm.update_chapter_status(chapter_num, "review_failed")
                    return ""
                print(f"  [警告] 连续{max_retry}次未通过，保留最后版本")
                mm.update_chapter_status(chapter_num, "force_approved")
                return content

    return ""
