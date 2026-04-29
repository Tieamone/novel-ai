import json
import re
from core.api_client import call_reviewer_api, increment_failure_counter, reset_failure_counter, check_switch_needed
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg
from core.reader_reviewer import reader_review_chapter
from core.utils import with_db_connection, DatabaseTransaction, execute_with_retry, \
    extract_json_obj, to_int, is_transient_error

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

→ AI痕迹检测（最高优先级，每类每处扣2-4分）：
- 情绪标签直陈（emotion_labeling）：直接写"她感到紧张/他心中涌起/她不禁感动/他意识到"，而非通过行为或生理反应表达
- 高频烂俗动作（cliche_action）：深吸一口气平复情绪、握紧拳头下定决心、瞳孔收缩察觉危险、喉咙发紧、脑海浮现
- 模板收束句（cliche_ending）："这才刚开始/路还很长/无论前方有多少困难/故事远未结束"，每出现一次扣3-5分
- 心理陈述结尾（psych_ending）：以"她决定了/他知道该怎么做了"等直接心理陈述收章，扣3分
- 对称句式滥用（parallel_abuse）：连续出现"虽然…但依然/一方面…另一方面"超过2次，扣2-3分
- 句子节奏单调（rhythm_flat）：全章句子长度高度均匀，缺乏长短句交错的节奏变化，扣2-3分

→ 质量检测：
- 注水或重复表达：换了个词说同一件事（扣3-5分）
- 有效推进：是否有冲突升级、信息新增或关系变化（无推进扣5分）
- 结尾驱动力：读者读完想不想翻下一章
- 比喻堆砌（metaphor_overload）：平叙段每500字超过2处比喻/拟人（扣5-8分）
- 缺实质性对话：全章少于2轮有来有回的对话（扣3-5分）

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
  "l3_issues": ["具体问题描述，注明类型（emotion_labeling/cliche_action/cliche_ending/psych_ending/parallel_abuse/rhythm_flat/metaphor_overload/注水/缺对话等），并引用原文具体句子"],
  "failure_attribution": {
    "primary_layer": "L1|L2|L3|none",
    "root_cause": "最关键失败原因（一句话），若通过写none",
    "severity": "high|medium|low|none"
  },
  "suggestions": "失败时给出具体可执行的修订建议（指出需要改哪段、怎么改）；通过写质量合格"
}"""


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

    score_l1 = to_int(
        raw_result.get("score_l1", raw_result.get("l1_score", 0)),
        default=0, min_value=0, max_value=45
    )
    score_l2 = to_int(
        raw_result.get("score_l2", raw_result.get("l2_score", 0)),
        default=0, min_value=0, max_value=25
    )
    score_l3 = to_int(
        raw_result.get("score_l3", raw_result.get("l3_score", 0)),
        default=0, min_value=0, max_value=30
    )

    total_raw = raw_result.get("score_total", raw_result.get("total_score"))
    if total_raw is None:
        legacy = raw_result.get("score")
        if legacy is None:
            score_total = score_l1 + score_l2 + score_l3
        else:
            score_total = to_int(legacy, default=0, min_value=0)
            if score_total <= 10:
                score_total *= 10
    else:
        score_total = to_int(total_raw, default=0, min_value=0, max_value=100)

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
        "score": to_int(round(score_total / 10), default=0, min_value=0, max_value=10),
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
    content = _truncate_for_review(content)
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
        if is_transient_error(e):
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

    parsed = extract_json_obj(raw)
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


def write_and_review(novel_name: str, chapter_num: int,
                     plot_goal: str, emotion_tag: str = "铺垫",
                     max_retry: int = None) -> str:
    """
    写作→审稿（责任编辑+读者视角）→保存 的完整流程。

    并发安全保障：
    - 入口标记状态为 'writing'，标识正在处理中
    - 所有数据库状态变更使用 execute_with_retry 防锁定失败
    - 异常路径保证状态回退到确定性的终态（approved/force_approved/review_failed）
    - 绝不会停留在中间不一致状态

    冲突检测：
    - 连续 ≥2 次因相同 veto_code 失败且层均为 L1/L2 时，触发任务卡冲突菜单
    - 每章最多允许一次任务卡重写，防止无限循环
    """
    from core.writer import write_chapter
    from core.api_client import get_failure_stats, check_switch_needed, get_switch_history

    if max_retry is None:
        max_retry = cfg("novel", "max_retry", 3)

    mm = MemoryManager(novel_name)
    content = None

    MAX_REVIEW_RETRIES = 3
    review_retry_count = 0

    try:
        _update_status_safe(novel_name, chapter_num, "writing")
    except Exception as e:
        print(f"  [警告] 状态标记 writing 失败（非致命）：{e}")

    # ── 冲突检测变量 ──────────────────────────────────────────────
    _original_plot_goal = plot_goal   # 保留原始干净目标（不含累积反馈）
    _original_emotion_tag = emotion_tag
    _veto_code_counter: dict = {}     # {veto_code: 命中次数}
    _failure_layers: list = []        # 每次失败的 primary_layer
    _task_rewrite_done = False        # 每章最多重写一次任务卡
    _rewrite_requested = False        # 内层 for 循环通知外层需要重试
    # ─────────────────────────────────────────────────────────────

    # 外层 while：正常流程只走一次；任务卡重写后最多再走一次
    while True:
        _rewrite_requested = False

        for attempt in range(max_retry):
            print(f"\n  第{attempt+1}次写作尝试...")

            try:
                content = write_chapter(novel_name, chapter_num,
                                        plot_goal, emotion_tag)
                reset_failure_counter("author")
            except Exception as e:
                print(f"  [错误] 写作调用失败：{e}")
                increment_failure_counter("author")
                if check_switch_needed("author"):
                    print(f"\n{'='*60}")
                    print(f"  [提示] 作者模型连续失败，建议切换模型")
                    print(f"{'='*60}")
                    from core.api_client import select_all_models_interactive, _select_single_model
                    print("\n是否切换作者模型？(y/n，默认y)")
                    choice = input().strip().lower() or "y"
                    if choice == "y":
                        print("\n【选择新的作者模型】")
                        author_choice = _select_single_model("作者模型", default="1")
                        from core.api_client import set_author_model
                        set_author_model(author_choice["model"], author_choice["provider"])
                        reset_failure_counter("author")
                continue

            result = review_chapter(novel_name, chapter_num,
                                    content, plot_goal)

            if result.get("review_error"):
                review_retry_count += 1
                issue_msg = result.get('issue') or result.get('retry_hint', '未知错误')
                if review_retry_count <= MAX_REVIEW_RETRIES:
                    print(f"\n  [重试审稿] 责任编辑返回异常（{issue_msg}），第{review_retry_count}/{MAX_REVIEW_RETRIES}次重试...")
                    increment_failure_counter("reviewer")
                    if check_switch_needed("reviewer"):
                        print(f"\n{'='*60}")
                        print(f"  [提示] 审稿模型连续异常，建议切换模型")
                        print(f"{'='*60}")
                        from core.api_client import select_all_models_interactive, _select_single_model
                        print("\n是否切换审稿模型？(y/n，默认y)")
                        choice = input().strip().lower() or "y"
                        if choice == "y":
                            print("\n【选择新的审稿模型】")
                            reviewer_choice = _select_single_model("审稿模型", default="1")
                            from core.api_client import set_reviewer_model
                            set_reviewer_model(reviewer_choice["model"], reviewer_choice["provider"])
                            reset_failure_counter("reviewer")
                    continue
                else:
                    print(f"\n  [错误] 审稿连续{MAX_REVIEW_RETRIES}次异常，降级为不通过处理")
                    pass

            if not result.get("pass"):
                print(f"\n  [重写] 责任编辑不通过（{result.get('score_total', 0)}/100），跳过读者视角评估")
                try:
                    _increment_retry_safe(novel_name, chapter_num)
                except Exception as e:
                    print(f"  [警告] 重试计数更新失败（非致命）：{e}")
                    mm.increment_retry_count(chapter_num)

                # ── 冲突检测：收集本次失败数据 ────────────────────────
                for veto_item in result.get("veto_items", []):
                    code = veto_item.get("code", "unknown")
                    _veto_code_counter[code] = _veto_code_counter.get(code, 0) + 1
                _attr = result.get("failure_attribution", {})
                if isinstance(_attr, dict):
                    _layer = _attr.get("primary_layer", "")
                    if _layer:
                        _failure_layers.append(_layer)

                repeated_vetos = [
                    code for code, cnt in _veto_code_counter.items() if cnt >= 2
                ]
                _all_structural = bool(_failure_layers) and all(
                    l in ("L1", "L2") for l in _failure_layers
                )
                _conflict_detected = (
                    len(repeated_vetos) > 0
                    and _all_structural
                    and attempt >= 1
                    and not _task_rewrite_done
                )
                # ─────────────────────────────────────────────────────

                # ── 冲突菜单 ──────────────────────────────────────────
                if _conflict_detected:
                    _veto_desc = "、".join(repeated_vetos)
                    print(f"\n{'='*60}")
                    print(f"  [冲突检测] 连续{attempt+1}次因相同原因失败：{_veto_desc}")
                    print(f"  系统判断：当前任务卡目标可能与故事设定存在冲突")
                    print(f"{'='*60}")
                    print("\n  处理方式：")
                    print("  1. 基于大纲自动重写本章任务卡，然后重新写作（推荐）")
                    print("  2. 手动输入新的情节目标")
                    print("  3. 继续原任务卡重试（切换模型）")
                    print("  4. 强制通过当前版本")
                    conflict_choice = input("\n  请选择（默认1）：").strip() or "1"

                    if conflict_choice == "1":
                        print("\n  [任务卡重写] 正在基于大纲生成新目标...")
                        from core.planner import rewrite_task_for_chapter
                        veto_descs = [
                            f"{item.get('code','')}: {item.get('description','')}"
                            for item in result.get("veto_items", [])
                            if item.get("code") in repeated_vetos
                        ]
                        new_task = rewrite_task_for_chapter(
                            novel_name, chapter_num,
                            veto_reasons=veto_descs,
                            current_goal=_original_plot_goal,
                            current_emotion_tag=_original_emotion_tag,
                        )
                        plot_goal    = new_task["plot_goal"]
                        emotion_tag  = new_task["emotion_tag"]
                        _original_plot_goal   = plot_goal
                        _original_emotion_tag = emotion_tag
                        _veto_code_counter.clear()
                        _failure_layers.clear()
                        _task_rewrite_done = True
                        _rewrite_requested = True
                        print(f"  [OK] 新任务卡：{plot_goal}")
                        print(f"       情绪标签：{emotion_tag}")
                        break  # 跳出 for 循环，外层 while 重新开始

                    elif conflict_choice == "2":
                        print("\n  请输入新的情节目标（40-80字，直接回车取消）：")
                        manual_goal = input("  > ").strip()
                        if manual_goal and len(manual_goal) >= 10:
                            plot_goal   = manual_goal
                            _original_plot_goal   = plot_goal
                            _veto_code_counter.clear()
                            _failure_layers.clear()
                            _task_rewrite_done = True
                            _rewrite_requested = True
                            # 同步写库
                            try:
                                from core.utils import with_db_connection as _wdb
                                with _wdb(novel_name) as _conn:
                                    _row = _conn.execute(
                                        "SELECT original_plot_goal, rewrite_count "
                                        "FROM chapter_tasks WHERE chapter_num=?",
                                        (chapter_num,)
                                    ).fetchone()
                                    _orig = (_row["original_plot_goal"] or _original_plot_goal) if _row else _original_plot_goal
                                    _rc   = ((_row["rewrite_count"] or 0) + 1) if _row else 1
                                    _conn.execute(
                                        "UPDATE chapter_tasks SET plot_goal=?, "
                                        "original_plot_goal=?, rewrite_count=? "
                                        "WHERE chapter_num=?",
                                        (plot_goal, _orig, _rc, chapter_num)
                                    )
                                    _conn.commit()
                            except Exception as _e:
                                print(f"  [警告] 任务卡更新失败（非致命）：{_e}")
                            print(f"  [OK] 已更新任务卡目标")
                            break  # 跳出 for 循环，外层 while 重新开始
                        else:
                            print("  [提示] 输入无效，继续原任务卡重试")
                            # 不 break，继续 for 循环的正常重试逻辑

                    elif conflict_choice == "4":
                        print(f"  [强制通过] 保留当前版本")
                        try:
                            _update_status_safe(novel_name, chapter_num, "强制通过")
                        except Exception as e:
                            print(f"  [警告] 状态更新失败：{e}")
                            mm.update_chapter_status(chapter_num, "强制通过")
                        return content or ""

                    # choice == "3"：跳过冲突菜单，走正常重试逻辑（下方）
                # ─────────────────────────────────────────────────────

                # 若刚才选了 1 或 2 并 break，这里不会执行
                if _rewrite_requested:
                    break

                if attempt < max_retry - 1:
                    retry_feedback = _build_retry_feedback(result)
                    if retry_feedback:
                        plot_goal = (
                            f"{_original_plot_goal}\n\n"
                            f"【上次写作问题（责任编辑），本次必须修复】\n{retry_feedback}"
                        )
                    print(f"  [重写] 准备第{attempt+2}次写作，已附上修复要求...")
                    continue
                else:
                    if result.get("review_error"):
                        print(f"  [错误] 审稿连续{max_retry}次异常，停止")
                        try:
                            _update_status_safe(novel_name, chapter_num, "审稿失败")
                        except Exception as e:
                            print(f"  [警告] 状态更新失败：{e}，尝试直接写入...")
                            mm.update_chapter_status(chapter_num, "审稿失败")
                        return ""

                    print(f"\n{'='*60}")
                    print(f"  [提示] 连续{max_retry}次未通过！")
                    print(f"  建议切换到更强的模型后重新生成此章节")
                    print(f"{'='*60}")

                    from core.api_client import select_all_models_interactive, _select_single_model
                    print("\n是否切换模型？(y/n，默认y)")
                    choice = input().strip().lower() or "y"
                    if choice == "y":
                        print("\n【选择新的写作/审稿模型】")
                        model_choice = _select_single_model("综合模型", default="1")
                        from core.api_client import set_author_model, set_reviewer_model
                        set_author_model(model_choice["model"], model_choice["provider"])
                        set_reviewer_model(model_choice["model"], model_choice["provider"])
                        reset_failure_counter("author")
                        reset_failure_counter("reviewer")
                        print(f"\n  [OK] 已切换至 {model_choice['name']}")
                        print(f"  请在章节菜单选择「恢复审稿失败章节」重新生成第{chapter_num}章")

                    print(f"  [警告] 保留最后版本（强制通过）")
                    try:
                        _update_status_safe(novel_name, chapter_num, "强制通过")
                    except Exception as e:
                        print(f"  [警告] 状态更新失败：{e}，尝试直接写入...")
                        mm.update_chapter_status(chapter_num, "强制通过")
                    return content or ""

                continue  # for 循环正常推进

            # ── 责任编辑通过，进入读者视角 ───────────────────────────
            reader_result = reader_review_chapter(novel_name, chapter_num, content)

            if reader_result.get("pass"):
                print(f"\n  [OK] 第{chapter_num}章双重审核通过！")
                review_retry_count = 0
                try:
                    _update_status_safe(novel_name, chapter_num, "已审核")
                except Exception as e:
                    print(f"  [警告] 状态更新失败：{e}，尝试直接写入...")
                    mm.update_chapter_status(chapter_num, "已审核")
                return content
            else:
                print(f"\n  [重写] 读者视角不通过（{reader_result.get('score_total', 0)}/100）")
                try:
                    _increment_retry_safe(novel_name, chapter_num)
                except Exception as e:
                    print(f"  [警告] 重试计数更新失败（非致命）：{e}")
                    mm.increment_retry_count(chapter_num)

                if attempt < max_retry - 1:
                    reader_issues = reader_result.get("issues", [])
                    reader_suggestions = reader_result.get("suggestions", "")
                    reader_feedback_parts = [f"- {i}" for i in reader_issues if i]
                    if reader_suggestions and reader_suggestions not in ("", "质量合格"):
                        reader_feedback_parts.append(f"- 建议：{reader_suggestions}")
                    if reader_feedback_parts:
                        plot_goal = (
                            f"{_original_plot_goal}\n\n"
                            f"【上次写作问题（读者视角），本次必须修复）】\n"
                            + "\n".join(reader_feedback_parts)
                        )
                    print(f"  [重写] 准备第{attempt+2}次写作，已附上修复要求...")
                else:
                    print(f"\n{'='*60}")
                    print(f"  [提示] 连续{max_retry}次未通过！")
                    print(f"  建议切换到更强的模型后重新生成此章节")
                    print(f"{'='*60}")

                    from core.api_client import select_all_models_interactive, _select_single_model
                    print("\n是否切换模型？(y/n，默认y)")
                    choice = input().strip().lower() or "y"
                    if choice == "y":
                        print("\n【选择新的写作/审稿模型】")
                        model_choice = _select_single_model("综合模型", default="1")
                        from core.api_client import set_author_model, set_reviewer_model
                        set_author_model(model_choice["model"], model_choice["provider"])
                        set_reviewer_model(model_choice["model"], model_choice["provider"])
                        reset_failure_counter("author")
                        reset_failure_counter("reviewer")
                        print(f"\n  [OK] 已切换至 {model_choice['name']}")
                        print(f"  请在章节菜单选择「恢复审稿失败章节」重新生成第{chapter_num}章")

                    print(f"  [警告] 保留最后版本（强制通过）")
                    try:
                        _update_status_safe(novel_name, chapter_num, "强制通过")
                    except Exception as e:
                        print(f"  [警告] 状态更新失败：{e}，尝试直接写入...")
                        mm.update_chapter_status(chapter_num, "强制通过")
                    return content or ""

        # for 循环结束后：若收到重写请求且未超次，继续 while
        if _rewrite_requested:
            continue  # 回到 while True，重新 for attempt in range(max_retry)
        else:
            break  # 正常退出 while

    if check_switch_needed("author") or check_switch_needed("reviewer"):
        print(f"\n{'='*60}")
        print(f"  [提示] 检测到多次失败，建议检查模型配置")
        fail_stats = get_failure_stats()
        print(f"  失败统计：作者={fail_stats['author_failures']} 审核={fail_stats['reviewer_failures']} 读者视角={fail_stats['reader_reviewer_failures']}")
        print(f"{'='*60}")

    return content or ""