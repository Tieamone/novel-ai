import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
from core.api_client import call_reviewer_api, increment_failure_counter, reset_failure_counter, check_switch_needed
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg
from core.reader_reviewer import reader_review_chapter
from core.utils import with_db_connection, DatabaseTransaction, execute_with_retry

REVIEW_PASS_TOTAL = 75
REVIEW_PASS_L1 = 30

REVIEWER_SYSTEM = """你是一位经验丰富的网络小说责任编辑，职责是判断章节是否达到发布标准。

你深知好的网文应该具备哪些特质：
- 情节推进有逻辑，人物行为有动机
- 场景有具体的感官细节，而非空泛描述
- 对话有潜台词，不是说明书式的信息传递
- 情绪通过行为和生理反应展示，而不是直接贴标签
- 每章结束时，读者会忍不住往下翻

必须执行三层评分并给出结构化归因：

【L1 逻辑与设定一致性】0-45分
- 人物行为是否符合既有人设（避免核心OOC，即无原因的性格突变）
- 时间线与因果是否自洽（前后矛盾、逻辑跳跃）
- 是否存在硬设定冲突（世界规则/生死状态/关键已知事实）
- 场景连贯性：上一章的状态与本章开头是否衔接正常

【L2 伏笔与剧情承接】0-25分
- 历史未兑现伏笔是否被无视或误解
- 新增伏笔是否自然嵌入、服务后续
- 本章是否完成"目标导向推进"（任务卡目标是否达成）
- 章节内部因果是否完整：有起因、有经过、有结果或悬念

【L3 可读性与网文节奏】0-30分
- 是否存在注水或重复表达（换了个词说同一件事，扣3-5分）
- 是否具备有效冲突推进或信息推进
- 结尾是否形成阅读驱动力（读者想继续看）
- 是否存在比喻/修辞堆砌（平叙段每500字超过2处，或相邻段落出现结构相似比喻句，扣5-8分，code: metaphor_overload）
- 是否缺少实质性对话（全章少于2轮有来有回的对话，扣3-5分）
- 是否存在情绪标签化问题：直接用"她感到紧张/他心中涌起暖意"等方式陈述情绪，而非通过行为/生理反应展示（每处扣2-3分，code: emotion_labeling）
- 结尾是否使用模板化收束句（"才刚刚开始"/"还很长"/"无论前方"类，扣3-5分，code: cliche_ending）

一票否决项（任一命中即不通过，不受分数影响）：
1) 核心设定冲突（setting_conflict）：与已建立的世界规则或关键事实直接矛盾
2) 重大时间线矛盾（timeline_break）：事件顺序或时间跨度出现无法自圆其说的矛盾
3) 主角或核心角色严重OOC（core_ooc）：无任何触发事件的性格或立场突变
4) 关键承诺伏笔被硬性遗忘导致断裂（critical_payoff_missing）：读者已有强烈预期的伏笔被无视

通过条件（同时满足）：
- veto_items 为空
- score_total >= 75
- score_l1 >= 30

评分注意事项：
- L3分项问题可叠加扣分，但总分不得低于0
- 如果某层没有问题，该层应给出接近满分的评分，不要无故扣分
- 通过时 suggestions 写"质量合格"，不通过时给出可执行的修订建议

严格只输出JSON，不要解释，不要Markdown：
{
  "pass": true/false,
  "score_total": 0-100整数,
  "score_l1": 0-45整数,
  "score_l2": 0-25整数,
  "score_l3": 0-30整数,
  "veto_items": [
    {"code": "setting_conflict|timeline_break|core_ooc|critical_payoff_missing", "reason": "命中理由（需引用正文具体段落）"}
  ],
  "l1_issues": ["具体问题描述"],
  "l2_issues": ["具体问题描述"],
  "l3_issues": ["具体问题描述，注明是哪种类型（metaphor_overload/emotion_labeling/cliche_ending/注水/缺对话等）"],
  "failure_attribution": {
    "primary_layer": "L1|L2|L3|none",
    "root_cause": "最关键失败原因（一句话），若通过写none",
    "severity": "high|medium|low|none"
  },
  "suggestions": "失败时给出具体可执行的修订建议（指出需要改哪段、怎么改）；通过写质量合格"
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


def _is_transient_error(error):
    """判断是否为暂时性错误（可重试恢复）"""
    transient_keywords = ["timeout", "locked", "rate limit", "503", "502", "429"]
    return any(kw in str(error).lower() for kw in transient_keywords)


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
    if suggestions and suggestions != "质量合格":
        retry_lines.append(f"修订建议：{suggestions}")
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


def _truncate_for_review(text: str, max_len: int = 3000) -> str:
    """智能截断审稿内容：优先保留开头完整段落"""
    if len(text) <= max_len:
        return text

    # 策略1: 尝试按段落截断（保留完整段落）
    paragraphs = text.split('\n\n')
    result = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > max_len * 0.8:
            break
        result.append(para)
        current_len += len(para) + 2

    # 如果已收集足够内容（>70%），返回开头部分
    if current_len >= max_len * 0.7:
        return '\n\n'.join(result)

    # 策略2: 首尾保留
    half = max_len // 2
    return text[:half] + "\n\n[... 中间部分已省略 ...]\n\n" + text[-half:]


def build_review_prompt(ctx: dict, chapter_num: int,
                        content: str, plot_goal: str) -> str:
    chars = ctx.get("characters", [])
    chars_str = "\n".join([
        f"【{c.get('name', '未命名角色')}】"
        f"性格：{c.get('personality', '未记录')[:80]}｜"
        f"状态：{c.get('current_status', '未记录')[:60]}｜"
        f"位置：{c.get('current_location', '未记录')}｜"
        f"关系：{json.dumps(c.get('relationships', {}), ensure_ascii=False)[:100]}"
        for c in chars
    ]) if chars else "（暂无人物设定）"

    foreshadow = ctx.get("active_foreshadowing", [])
    f_str = "\n".join(
        [f"- [{f.get('fid', '?')}] {f.get('description', '')}" for f in foreshadow[:8]]
    ) if foreshadow else "（暂无）"

    recent = ctx.get("recent_summaries", [])
    recent_str = ""
    if recent:
        last = recent[-1]
        recent_str = f"\n上一章概要：{last.get('summary', '')[:200]}"

    return f"""=== 审稿任务 ===
章节：第{chapter_num}章
本章目标（任务卡）：{plot_goal}

=== 人物设定（核对OOC和状态连贯性）===
{chars_str}

=== 未兑现伏笔（核对是否被忽略）===
{f_str}

=== 前情概要（核对衔接）===
{recent_str or "（首章或无近期摘要）"}

=== 待审正文 ===
{content}

请逐层评估后给出最终JSON结论。评分时要公正：做得好的层不要无故扣分，有问题的层要指出具体段落。"""


def review_chapter(novel_name: str, chapter_num: int,
                   content: str, plot_goal: str) -> dict:
    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)

    print(f"  [责任编辑] 正在审稿第{chapter_num}章...")
    prompt = build_review_prompt(ctx, chapter_num, content, plot_goal)

    try:
        raw = call_reviewer_api(
            system_prompt=REVIEWER_SYSTEM,
            user_message=prompt,
            temperature=0.25,
            max_tokens=1600,
        )
    except Exception as e:
        if _is_transient_error(e):
            print(f"  [责任编辑] ⚠️ 审稿遇到暂时性问题: {str(e)[:100]}")
            print(f"  [责任编辑] 💡 建议稍后手动重新审稿此章节")
            mm.update_chapter_status(chapter_num, "pending_review")
            increment_failure_counter("reviewer")
            return _review_error_result(
                message=f"审稿遇到暂时性错误（{type(e).__name__}），请重试",
                issue="审稿API调用失败（暂时性）"
            )
        else:
            print(f"\n❌ [责任编辑] 审稿失败（严重错误）")
            print(f"   详情: {str(e)[:200]}")
            increment_failure_counter("reviewer")
            return _review_error_result(
                message="审稿服务不可用，请检查配置后重试",
                issue="审稿API调用失败（严重错误）"
            )

    parsed = _extract_json_obj(raw)
    if not parsed:
        print("  [责任编辑] 审稿返回格式异常，本轮判定为不通过")
        error_result = _review_error_result(
            message="审稿结果格式异常，请重试",
            issue="审稿结果格式异常，无法解析",
        )
        _persist_review_result(mm, chapter_num, error_result)
        increment_failure_counter("reviewer")
        return error_result

    result = _normalize_review_result(parsed)
    _persist_review_result(mm, chapter_num, result)

    status = "通过" if result.get("pass") else "不通过"
    print(
        f"  [责任编辑] {status} | 总分：{result['score_total']}/100 "
        f"(L1:{result['score_l1']}/45 L2:{result['score_l2']}/25 "
        f"L3:{result['score_l3']}/30)"
    )
    if result.get("veto_triggered"):
        print("  [责任编辑] 一票否决：")
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
        for issue in issues[:5]:
            print(f"    - {issue}")
        if result.get("suggestions") and result["suggestions"] != "质量合格":
            print(f"  [建议] {result['suggestions'][:200]}")
        increment_failure_counter("reviewer")
    else:
        reset_failure_counter("reviewer")

    return result


def _update_status_safe(novel_name: str, chapter_num: int, status: str):
    """
    安全更新章节状态（带重试的原子操作）。
    用于 write_and_review 中的状态流转，确保并发安全。
    """
    from datetime import datetime
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            execute_with_retry(conn, """
                UPDATE chapters SET status=?, updated_at=?
                WHERE chapter_num=?
            """, (status, datetime.now(), chapter_num))


def _increment_retry_safe(novel_name: str, chapter_num: int):
    """安全递增重试计数（带重试的原子操作）。"""
    with with_db_connection(novel_name) as conn:
        with DatabaseTransaction(conn):
            execute_with_retry(conn, """
                UPDATE chapters SET retry_count = retry_count + 1
                WHERE chapter_num=?
            """, (chapter_num,))




# ============================================================
# 场景重规划：当 veto 反复触发时，放弃原场景方案，生成新方案
# ============================================================

SCENE_REPLAN_SYSTEM = """你是一位资深网文策划编辑，专门解决剧情逻辑卡关问题。

当AI写手连续在同一章卡住时，你的任务是：
1. 找到让当前场景无法实现的根本矛盾
2. 提出一个不同的场景实现方案，绕开矛盾，同时完成相同的情节目标
3. 新方案必须与人物设定和已建立的世界观兼容

你输出的是一个修订后的情节目标描述（plot_goal），不是章节内容。
直接输出新的 plot_goal 文本，不要解释，不要JSON。"""


def _replan_scene_for_veto(novel_name: str, chapter_num: int,
                           plot_goal: str, veto_reasons: list,
                           suggestions: str) -> str:
    """
    当 veto 反复触发（core_ooc / timeline_break）时，
    调用 AI 重新规划场景方案，返回修订后的 plot_goal。
    这是"剧情层面的修复"，不是"写作层面的修复"。
    """
    from core.api_client import call_reviewer_api
    from core.memory_manager import MemoryManager

    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)

    chars = ctx.get("characters", [])
    chars_summary = "\n".join([
        f"{c.get('name')}（{c.get('role')}）：{c.get('personality', '')[:100]}"
        for c in chars[:6]
    ])

    summaries = ctx.get("recent_summaries", [])
    recent = "\n".join([
        f"第{s['chapter_num']}章：{s['summary']}" for s in summaries[-3:]
    ]) if summaries else "无近期摘要"

    veto_text = "\n".join([f"- {r}" for r in veto_reasons])
    print(f"  [场景重规划] 检测到反复 veto，正在重新规划第{chapter_num}章场景方案...")

    prompt = f"""以下章节的情节目标在三次写作尝试后仍因逻辑问题无法通过审核：

【原情节目标】
{plot_goal}

【反复触发的 Veto 原因】
{veto_text}

【审稿建议】
{suggestions}

【已确立的人物设定】
{chars_summary}

【近期剧情摘要】
{recent}

请提供一个修订后的情节目标，要求：
1. 完成相同的剧情推进方向（比如：主角到达目标地点、获得关键信息、与反派发生冲突）
2. 不要求任何角色做出与其设定相悖的行为
3. 给出一个具体的、不同于原方案的场景实现路径
4. 如果原方案中某角色的状态转变太突兀，提供一个更合理的触发机制

只输出修订后的情节目标文本（100-200字），不要任何解释。"""

    try:
        new_goal = call_reviewer_api(
            system_prompt=SCENE_REPLAN_SYSTEM,
            user_message=prompt,
            temperature=0.7,
            max_tokens=400,
        )
        new_goal = new_goal.strip()
        if new_goal and len(new_goal) > 30:
            print(f"  [场景重规划] 新方案已生成（{len(new_goal)}字）")
            return new_goal
        else:
            print(f"  [场景重规划] 生成结果过短，保留原方案")
            return plot_goal
    except Exception as e:
        print(f"  [场景重规划] 失败：{e}，保留原方案")
        return plot_goal

def write_and_review(novel_name: str, chapter_num: int,
                     plot_goal: str, emotion_tag: str = "铺垫",
                     max_retry: int = None) -> str:
    """
    写作 → 审稿（责任编辑 + 读者视角）→ 保存 的完整流程。

    重试策略（修复版）：
    ─────────────────────────────────────────────────────────
    • L1 一票否决（core_ooc / timeline_break 等逻辑崩塌）：
        第1次失败 → 附带审稿建议重写（写法层面修复）
        第2次起   → 调用 _replan_scene_for_veto 重规划场景方案
                    （改变"怎么到达目标"，不改变目标本身）
    • L3 不通过（文笔/AI痕迹问题）：
        附带具体问题清单重写，每次重置为干净的反馈，不累积
    • 读者视角不通过：
        只用读者视角问题作为反馈，不混入责任编辑结果
    ─────────────────────────────────────────────────────────
    """
    from core.writer import write_chapter
    from core.api_client import get_failure_stats, check_switch_needed

    if max_retry is None:
        max_retry = cfg("novel", "max_retry", 3)

    mm = MemoryManager(novel_name)
    content = None

    MAX_REVIEW_RETRIES = 3
    review_retry_count = 0

    # 原始 plot_goal 保存，重规划时基于原始目标
    original_plot_goal = plot_goal
    current_plot_goal = plot_goal

    # veto 连续触发计数（用于决定何时切换到场景重规划模式）
    consecutive_veto_count = 0

    try:
        _update_status_safe(novel_name, chapter_num, "writing")
    except Exception as e:
        print(f"  [警告] 状态标记 writing 失败（非致命）：{e}")

    for attempt in range(max_retry):
        print(f"\n  第{attempt+1}次写作尝试...")

        # ── 写作 ──────────────────────────────────────────────
        try:
            content = write_chapter(novel_name, chapter_num,
                                    current_plot_goal, emotion_tag)
            reset_failure_counter("author")
        except Exception as e:
            print(f"  [错误] 写作调用失败：{e}")
            increment_failure_counter("author")
            if check_switch_needed("author"):
                print(f"\n{'='*60}")
                print(f"  [提示] 作者模型连续失败，建议切换模型")
                print(f"{'='*60}")
                from core.api_client import _select_single_model
                print("\n是否切换作者模型？(y/n，默认y)")
                choice = input().strip().lower() or "y"
                if choice == "y":
                    author_choice = _select_single_model("作者模型", default="1")
                    from core.api_client import set_author_model
                    set_author_model(author_choice["model"], author_choice["provider"])
                    reset_failure_counter("author")
            continue

        # ── 责任编辑审稿 ──────────────────────────────────────
        result = review_chapter(novel_name, chapter_num,
                                content, current_plot_goal)

        # 审稿模型异常（JSON 解析失败等）
        if result.get("review_error"):
            review_retry_count += 1
            issue_msg = result.get("issue") or result.get("retry_hint", "未知错误")
            if review_retry_count <= MAX_REVIEW_RETRIES:
                print(f"\n  [重试审稿] 责任编辑返回异常（{issue_msg}），"
                      f"第{review_retry_count}/{MAX_REVIEW_RETRIES}次重试...")
                increment_failure_counter("reviewer")
                if check_switch_needed("reviewer"):
                    print(f"\n{'='*60}")
                    print(f"  [提示] 审稿模型连续异常，建议切换模型")
                    print(f"{'='*60}")
                    from core.api_client import _select_single_model
                    print("\n是否切换审稿模型？(y/n，默认y)")
                    choice = input().strip().lower() or "y"
                    if choice == "y":
                        reviewer_choice = _select_single_model("审稿模型", default="1")
                        from core.api_client import set_reviewer_model
                        set_reviewer_model(reviewer_choice["model"], reviewer_choice["provider"])
                        reset_failure_counter("reviewer")
                continue
            else:
                print(f"\n  [错误] 审稿连续{MAX_REVIEW_RETRIES}次异常，降级处理")

        # ── 责任编辑不通过 ───────────────────────────────────
        if not result.get("pass"):
            score = result.get("score_total", 0)
            veto_triggered = result.get("veto_triggered", False)
            veto_items = result.get("veto_items", [])
            veto_codes = [v.get("code", "") for v in veto_items]
            suggestions = result.get("suggestions", "")

            print(f"\n  [重写] 责任编辑不通过（{score}/100），跳过读者视角")

            try:
                _increment_retry_safe(novel_name, chapter_num)
            except Exception as e:
                print(f"  [警告] 重试计数更新失败（非致命）：{e}")
                mm.increment_retry_count(chapter_num)

            if attempt < max_retry - 1:
                is_logic_veto = veto_triggered and any(
                    c in veto_codes for c in
                    ("core_ooc", "timeline_break", "setting_conflict",
                     "plot_collapse", "worldview_break")
                )

                if is_logic_veto:
                    consecutive_veto_count += 1
                    veto_reasons = [
                        f"{v.get('code', '?')}：{v.get('reason', '')}"
                        for v in veto_items
                    ]
                    print(f"  [失败类型] L1逻辑崩塌（veto×{consecutive_veto_count}）：{', '.join(veto_codes)}")

                    if consecutive_veto_count >= 2:
                        # 第2次及以后的 L1 veto → 重规划场景方案
                        new_goal = _replan_scene_for_veto(
                            novel_name, chapter_num,
                            original_plot_goal, veto_reasons, suggestions
                        )
                        current_plot_goal = new_goal
                        print(f"  [重写] 已切换至新场景方案，准备第{attempt+2}次写作...")
                    else:
                        # 第1次 L1 veto → 附带详细审稿建议重写（写法层面）
                        veto_text = "\n".join([f"- {r}" for r in veto_reasons])
                        current_plot_goal = (
                            f"{original_plot_goal}\n\n"
                            f"【上次审稿一票否决原因，本次必须从根本上修复】\n"
                            f"{veto_text}\n\n"
                            f"【审稿建议（必须执行）】\n{suggestions}"
                        )
                        print(f"  [重写] 已附详细修复要求，准备第{attempt+2}次写作...")
                else:
                    # L3 文笔/AI痕迹问题 → 附具体问题清单，每次重置（不累积）
                    consecutive_veto_count = 0
                    retry_feedback = _build_retry_feedback(result)
                    if retry_feedback:
                        current_plot_goal = (
                            f"{original_plot_goal}\n\n"
                            f"【上次写作文笔问题，本次必须修复（以下问题逐一解决）】\n"
                            f"{retry_feedback}"
                        )
                    else:
                        current_plot_goal = original_plot_goal
                    print(f"  [重写] 已附文笔修复要求，准备第{attempt+2}次写作...")

            else:
                # 所有重试耗尽
                _handle_all_retries_failed(novel_name, chapter_num, content, mm, max_retry)
                return content or ""

            continue

        # ── 责任编辑通过 → 读者视角审稿 ─────────────────────
        consecutive_veto_count = 0
        reader_result = reader_review_chapter(novel_name, chapter_num, content)

        if reader_result.get("pass"):
            print(f"\n  [OK] 第{chapter_num}章双重审核通过！")
            review_retry_count = 0
            try:
                _update_status_safe(novel_name, chapter_num, "已审核")
            except Exception as e:
                print(f"  [警告] 状态更新失败：{e}")
                mm.update_chapter_status(chapter_num, "已审核")
            return content

        # 读者视角不通过
        score_r = reader_result.get("score_total", 0)
        print(f"\n  [重写] 读者视角不通过（{score_r}/100）")

        try:
            _increment_retry_safe(novel_name, chapter_num)
        except Exception as e:
            print(f"  [警告] 重试计数更新失败（非致命）：{e}")
            mm.increment_retry_count(chapter_num)

        if attempt < max_retry - 1:
            # Bug修复7: 只用读者视角的问题，不混入已通过的责任编辑结果
            reader_issues = reader_result.get("issues", [])
            reader_suggestions = str(reader_result.get("suggestions", "")).strip()
            parts = [f"- {i}" for i in reader_issues if i]
            if reader_suggestions and reader_suggestions not in ("", "质量合格"):
                parts.append(f"- 建议：{reader_suggestions}")
            if parts:
                current_plot_goal = (
                    f"{original_plot_goal}\n\n"
                    f"【读者视角反馈（本次必须修复）】\n"
                    + "\n".join(parts)
                )
            else:
                current_plot_goal = original_plot_goal
            print(f"  [重写] 已附读者视角修复要求，准备第{attempt+2}次写作...")
        else:
            _handle_all_retries_failed(novel_name, chapter_num, content, mm, max_retry)
            return content or ""

    # 循环正常结束（理论上不应到达此处）
    if content:
        try:
            _update_status_safe(novel_name, chapter_num, "强制通过")
        except Exception:
            pass
    return content or ""


def _handle_all_retries_failed(novel_name: str, chapter_num: int,
                                content: str, mm, max_retry: int):
    """所有重试耗尽时的统一处理：询问切换模型，强制通过当前内容"""
    print(f"\n{'='*60}")
    print(f"  [提示] 连续{max_retry}次未通过！建议切换更强的模型后重新生成")
    print(f"{'='*60}")

    from core.api_client import _select_single_model
    print("\n是否现在切换模型？(y/n，默认n)")
    choice = input().strip().lower() or "n"
    if choice == "y":
        model_choice = _select_single_model("写作模型", default="1")
        from core.api_client import set_author_model, set_reviewer_model
        from core.api_client import reset_failure_counter as rfc
        set_author_model(model_choice["model"], model_choice["provider"])
        set_reviewer_model(model_choice["model"], model_choice["provider"])
        rfc("author")
        rfc("reviewer")
        print(f"\n  [OK] 已切换至 {model_choice['name']}")
        print(f"  请在主菜单中选择「恢复review_failed」重新生成第{chapter_num}章")

    if content:
        print(f"  [警告] 保留最后版本（强制通过）")
        try:
            _update_status_safe(novel_name, chapter_num, "强制通过")
        except Exception as e:
            print(f"  [警告] 状态更新失败：{e}")
            mm.update_chapter_status(chapter_num, "强制通过")