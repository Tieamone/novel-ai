from core.outline_manager import format_chapter_foreshadow
import json
import re
from datetime import datetime
from core.interaction import ask as input, say as print, check_cancelled

_SWITCH_CHOICES = (("y", "切换模型"), ("n", "保留当前模型"))
_RETRY_CHOICES = (("1", "继续局部修订"), ("2", "完全重写"), ("3", "切换模型重新生成"))
from core.api_client import call_reviewer_api, call_cross_checker_api, increment_failure_counter, reset_failure_counter, check_switch_needed
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg
from core.reader_reviewer import reader_review_chapter
from core.utils import with_db_connection, DatabaseTransaction, execute_with_retry, \
    extract_json_obj, to_int, is_transient_error

REVIEW_PASS_TOTAL = 75
REVIEW_PASS_L1 = 30

REVIEWER_SYSTEM = """你是一位经验丰富的网络小说责任编辑，职责是判断章节是否达到发布标准。

先按事件核对任务卡的章节边界，再评分。任务完成后的一两句自然反应、收束不算越界，任务卡的“定格”“立即停笔”也按事件边界理解。开始新的实质行动、产生下个事件的结果或提前兑现后续剧情才算越界，必须在 veto_items 写入 past_chapter_goal，逐字引用第一处越界正文，pass 为 false。
例如任务卡写“约好次日验收就停”，正文写“次日，他把样品摆上验收台”已经进入下一场，哪怕还没验收完也要否决。正文只写“明早带样品过来”仍在本章范围。
字数不足本身不扣分。短章须真实呈现完成本章任务所必需的行动、选择和结果。只有几句任务复述、梗概或遗漏关键因果时，指出具体缺失内容，不能要求凑字数。

你深知好的网文应该具备哪些特质：
- 情节推进有逻辑，人物行为有动机
- 场景信息足以理解行动，无效环境、感官和重复心理描写应删减
- 删除后仍能理解事件、冲突和选择的细节应省略。普通流程概述结果不算梗概；只有关键冲突、决定或因果缺失才要求展开。
- 不要求镜头感、氛围铺垫、痛感比喻或台词间的小动作，不因删减后变短或较前文简洁而扣分。指出冗余原句并建议直接删除，不换一种描写凑回篇幅。
- 对话有潜台词，不是说明书式的信息传递
- 情绪表达简洁清楚，允许直述，不要求转换成生理反应
- 章节停在任务目标达成或人物作出选择的地方，允许自然收束

必须执行三层评分并给出结构化归因：

【L1 逻辑与设定一致性】0-45分
- 人物行为是否符合既有人设（避免核心OOC，即无原因的性格突变）
- 时间线与因果是否自洽（前后矛盾、逻辑跳跃）
- 是否存在硬设定冲突（世界规则/生死状态/关键已知事实）
- 场景连贯性：上一章的状态与本章开头是否衔接正常

【L2 伏笔与剧情承接】0-25分
- 只核对本章安排，不因历史伏笔未处理扣分，无伏笔任务是正常情况
- 是否擅自新增需要后续解释的秘密；即使自然，也要求删除或改成普通事实
- 本章是否完成"目标导向推进"（任务卡目标是否达成）
- 章节内部因果是否完整：有起因、有经过、有结果或悬念

【L3 可读性与网文节奏】0-30分

→ AI痕迹检测（最高优先级，每类每处扣2-4分）：
- 情绪标签直陈（emotion_labeling）：反复解释同一种情绪且没有新增信息；简洁直述情绪或认知不扣分
- 高频烂俗动作（cliche_action）：深吸一口气平复情绪、握紧拳头下定决心、瞳孔收缩察觉危险、喉咙发紧、脑海浮现
- 模板收束句（cliche_ending）："这才刚开始/路还很长/无论前方有多少困难/故事远未结束"，每出现一次扣3-5分
- 心理陈述结尾（psych_ending）：没有具体行动内容的空泛表态扣3分；明确下一步选择不扣分
- 先总结后展开（summary_then_expand）：段落以概括性总结句开头，再展开细节（如"总的来说…"/"简单来说…"/"概括一下…"），扣2-3分
- 三段式收尾（triple_ending）：结尾连续三段呈现"冲突→反思→升华"固定结构（如"虽然困难…但他明白了…从此以后…"），扣3-5分
- 强行正能量升华（forced_positive）：结尾强行转向积极/励志/哲理，与前文情绪基调不符（如"虽然失败了，但他收获了成长"），扣3-5分
- 对称句式滥用（parallel_abuse）：连续出现"虽然…但依然/一方面…另一方面"超过2次，扣2-3分
- 句子节奏单调（rhythm_flat）：全章句子长度高度均匀，缺乏长短句交错的节奏变化，扣2-3分

→ 质量检测：
- 注水或重复表达：换了个词说同一件事（扣3-5分）
- 有效推进：是否有冲突升级、信息新增或关系变化（无推进扣5分）
- 结尾驱动力：读者读完想不想翻下一章
- 比喻堆砌（metaphor_overload）：平叙段每500字超过2处比喻/拟人（扣5-8分）
- 缺实质性对话：必要交涉未写清才扣分，不按对话轮数设下限

→ 冗余检测（redundancy）：
- 常规办事过程没有阻碍却逐步描写时，要求合并概述。赶路、安装、签字、核对和收纳不必逐一展开。关键凭证保留取得事实即可，不因它是伏笔就描写折叠、位置和意义
- 对话已交代结果，再补表情、脚步声、心理盘算属于重复。修订应删除或合并，不换成另一组动作
- 标注可裁切的段落（给出段落首句和裁切理由）
- 标注可精简的对话（哪些对话对情节无贡献）
- 评分 0-10（10=无冗余，0=大量水字数）

一票否决项（任一命中即不通过，不受分数影响）：
1) 核心设定冲突（setting_conflict）：与已建立的世界规则或关键事实直接矛盾
2) 重大时间线矛盾（timeline_break）：事件顺序或时间跨度出现无法自圆其说的矛盾
3) 主角或核心角色严重OOC（core_ooc）：无任何触发事件的性格或立场突变
4) 关键承诺伏笔被硬性遗忘导致断裂（critical_payoff_missing）：本章任务明确要求兑现且正文遗漏导致因果断裂，不能仅凭历史年龄判定
5) 伏笔越界（unplanned_foreshadowing）：正文明确制造了本章清单之外、需要后文解释的谜团，或提前兑现仅要求埋入的伏笔。普通物品、正常冲突与下一步行动不属于此项。定位原文并给出删改办法，修订建议不得引入新悬念
6) 剧情越界（past_chapter_goal）：正文越过了任务卡明确的终点。若本章只约定将来验收、付款或对质，正文开始展现那场后续事件就已越界，即使事件尚未完成。要求在已安排的结果处收束

通过条件（同时满足）：
- veto_items 为空
- score_total >= 75
- score_l1 >= 30

评分注意事项：
- L3分项问题可叠加扣分，但总分不得低于0
- 如果某层没有问题，该层应给出接近满分的评分，不要无故扣分
- 通过时 suggestions 写"质量合格"，不通过时给出可执行的修订建议

只输出一个合法 JSON 对象。pass 必须是 JSON 布尔值。评分字段都是整数；
score_total 必须等于 score_l1 + score_l2 + score_l3，请计算后填入。
问题列表最多各列三条，所有理由和建议简明。没有问题时用空数组。
下面是字段结构示例。先填实际命中的 veto_items，再决定 pass，结论和分数均不照抄示例：
{
  "veto_items": [],
  "pass": true,
  "score_total": 85,
  "score_l1": 38,
  "score_l2": 21,
  "score_l3": 26,
  "l1_issues": [],
  "l2_issues": [],
  "l3_issues": [],
  "failure_attribution": {"primary_layer": "none", "root_cause": "none", "severity": "none"},
  "redundancy_score": 8,
  "cut_suggestions": [],
  "tail_cut": null,
  "suggestions": "质量合格"
}
否决项格式为 {"code": "setting_conflict", "reason": "用原文说明冲突"}，code 从上列否决项选择。
可裁切项格式为 {"start": "段落的首句", "reason": "具体理由"}。
仅当本章目标已在前文完整达成，且从某个段落起直到篇末全是可删的越界或冗余尾声时，tail_cut 填 {"goal_complete":true,"start":"第一段待删除文字的完整首句"}，该首句须逐字取自正文且仅出现一次。删除之后必须仍保留本章全部必要事件和结果。普通自然收束、缺失目标、问题分散在全文或无法确定边界时填 null。
失败归因的 primary_layer 为 L1、L2 或 L3，severity 为 high、medium 或 low。
不通过时注明具体问题、原文证据和可执行的修订建议。"""

FORESHADOW_REVIEWER_SYSTEM = """你是伏笔专项审核员。先执行逐项证据检查，再给本章评分。候选描述是待证实的主张，不能当成正文事实。

逐项证据检查：
- 候选同时描述埋入、兑现与设计理由时，按本次动作检查：埋入只核对铺垫事实，不要求提前出现兑现结果；兑现核对实际结果，不把设计理由当作正文事实。
- 埋入候选：先找出描述中的区分性特征，逐个检查本章是否实际描写。仅出现同一物品或人物，缺少关键特征，填 unconfirmed。
- 例：候选“带三道刻痕的铜环被发现”，正文“他捡起铜环”或“如果铜环有刻痕，他就会留着它”，都填 unconfirmed。“他捡起一只带三道刻痕的铜环”才填 confirmed。
- 兑现候选：本章必须实际揭示线索所指的结果。明确否定、假设、计划、回忆或只提及一律填 unconfirmed。

评分维度（每项0-10分）：
1. **埋入自然度**（embed）：本章新增的伏笔/悬念是否自然融入情节，不突兀。
   - 先从正文中识别本章新增的伏笔元素（读者首次接触到的悬念/暗示/未解之谜），再评估其自然度。
   - 若本章无明显新增伏笔，给8-10分（没有不是问题）。

2. **兑现完整度**（resolve）：应兑现的伏笔是否在本章得到实质呼应。
   - 对照候选中标为兑现的条目逐条检查。
   - 实质呼应 = 有具体情节推进，而非一笔带过或侧面提及。
   - 若无应兑现伏笔，给8-10分。

3. **线索可追溯性**（traceable）：兑现时是否有足够细节让读者回想起原伏笔。
   - 读者能否通过本章描写联想到当初埋下的线索。
   - 若本章无兑现动作，给8-10分。

4. **积压风险**（overload_risk）：伏笔管理是否健康。
   - 10分 = 无积压风险（有兑现或无新增），0分 = 严重积压（大量新增却无兑现）。
   - 综合考虑：本章新增伏笔数量 vs 兑现数量，以及总体活跃伏笔规模。

说明要求：一句话概括本章伏笔处理情况。

先逐项判断状态，再评分。每个候选编号在 decisions 中出现一次，编号原样抄写。
- confirmed：本章具体动作或明确事实完成该候选。埋入要求线索实际出现，兑现要求线索所指事件或信息得到实质回答。
- unconfirmed：没发生、仍待解释、只是转述、假设、计划，或正文明确否定该候选。
- 例：候选“戒指能启动电梯”，正文“她按下戒指，电梯门立即打开。”确认。
  正文“她按下戒指，电梯毫无反应。”不确认。正文说“戒指不是开关”也不确认。
- 同一段可以同时支持多个候选，独立判定。仅确认项必须引用正文中直接证明判断的完整短句，逐字照抄。

严格只输出JSON：
每项 restated 用不超过40字复述正文实际发生的事：谁对什么做了什么、结果如何，不能照抄候选主张。
{"embed":9,"resolve":9,"traceable":9,"overload_risk":9,"comment":"一句话说明","decisions":[{"key":"候选1","verdict":"confirmed","quote":"支持判断的原句","restated":"人物的动作及结果"},{"key":"候选2","verdict":"unconfirmed","quote":"","restated":"正文未出现该事件"}]}"""


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
        "unplanned_foreshadowing",
        "past_chapter_goal",
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

    score_total = score_l1 + score_l2 + score_l3
    scores = (
        raw_result.get("score_l1", raw_result.get("l1_score")),
        raw_result.get("score_l2", raw_result.get("l2_score")),
        raw_result.get("score_l3", raw_result.get("l3_score")),
    )
    if (type(raw_result.get("pass")) is not bool
            or any(type(score) is not int or not 0 <= score <= limit
                   for score, limit in zip(scores, (45, 25, 30)))
            or raw_result.get("score_total", raw_result.get("total_score", score_total)) != score_total
            or not isinstance(raw_result.get("veto_items", []), list)):
        return _review_error_result("审稿结果字段或分数无效，请重试", "责任编辑返回格式异常")
    model_pass = raw_result["pass"]
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
        "redundancy_score": to_int(raw_result.get("redundancy_score", 10), default=10, min_value=0, max_value=10),
        "cut_suggestions": raw_result.get("cut_suggestions", []),
        "tail_cut": raw_result.get("tail_cut"),
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


def _build_foreshadow_list(ctx: dict) -> dict:
    """专项审核只处理本章分配的候选。"""
    tasks = ctx.get("outline_tasks", {})
    return {
        "to_plant": [
            {**task, "source": "大纲", "action": "plant", "handle_type": "大纲应埋入"}
            for task in tasks.get("to_plant", [])
        ],
        "to_resolve": [
            {**task, "source": "大纲", "action": "resolve", "handle_type": "大纲应兑现"}
            for task in tasks.get("to_resolve", [])
        ],
    }


def _crosscheck_confirmed(chapter_text: str, items: list) -> list | None:
    """一次批量复核；无法确认等价的候选不推进状态。"""
    if not items:
        return []
    payload = []
    for item in items:
        quote = item["quote"]
        offset = chapter_text.find(quote)
        payload.append({"key": item["key"], "candidate": item["description"],
                        "action": item["action"], "restated": item["restated"], "quote": quote,
                        "context": chapter_text[max(0, offset - 80):offset + len(quote) + 80]})
    try:
        raw = call_cross_checker_api(
            system_prompt=("你是独立的伏笔事实复核员。核对候选、复述、引文及上下文的主语、宾语、动作、因果、否定和结果。"
                           "只提到物品、传闻假设、否定或主谓颠倒均不等价。候选不是已知事实。"
                           '仅输出JSON：{"decisions":[{"key":"候选1","equivalent":true,"reason":"理由"}]}。'
                           "每个编号恰好一次，equivalent只能为JSON布尔值。"),
            user_message=json.dumps(payload, ensure_ascii=False),
            temperature=0.1, max_tokens=max(500, len(items) * 200), retry=1,
        )
    except Exception as exc:
        print(f"  [伏笔复核] 请求失败（{type(exc).__name__}），本批未评分")
        return None
    data = extract_json_obj(raw).get("decisions") if isinstance(raw, str) else None
    if not isinstance(data, list):
        print("  [伏笔复核] JSON 缺少 decisions 列表，本批未评分")
        return None
    by_key = {}
    for entry in data:
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            by_key.setdefault(entry["key"], []).append(entry)
    kept = []
    for item in items:
        matches = by_key.get(item["key"], [])
        if (len(matches) != 1 or type(matches[0].get("equivalent")) is not bool
                or not isinstance(matches[0].get("reason"), str) or not matches[0]["reason"].strip()):
            print(f"  [伏笔复核] {item['key']} 缺少有效逐项判定，本批未评分")
            return None
        if matches[0]["equivalent"]:
            kept.append({**item, "crosscheck_reason": matches[0]["reason"]})
        else:
            print(f"  [伏笔复核] {item['key']} 判定不等价")
    return kept


def score_foreshadowing(chapter_text: str, foreshadow_list: dict,
                        chapter_num: int = 0) -> dict | None:
    """
    伏笔专项评分（L2级别）。
    失败时返回 None，不中断主审核流程。
    """
    to_resolve = foreshadow_list.get("to_resolve", [])
    active_hints = foreshadow_list.get("active_hints", "")
    total_active = foreshadow_list.get("total_active", 0)

    to_plant = foreshadow_list.get("to_plant", [])
    if not to_resolve and not to_plant and not active_hints:
        return None

    # 超长正文仍可评分，但不能据截断片段确认状态。
    review_text = _truncate_for_review(chapter_text, max_len=8000)
    candidates = {
        f"候选{index}": item for index, item in enumerate(to_plant + to_resolve, 1)
    }
    candidate_lines = "\n".join(
        f"- [{key}] {'埋入' if item['action'] == 'plant' else '兑现'}：{item['description']}"
        for key, item in candidates.items()
    )

    hints_block = active_hints if active_hints else "（暂无活跃伏笔）"

    prompt = f"""=== 伏笔审核任务 ===
章节：第{chapter_num}章
当前活跃伏笔总数：{total_active}

=== 本章埋入及兑现候选 ===
{candidate_lines or "（无）"}

=== 当前活跃伏笔参考（用于判断哪些是旧伏笔）===
{hints_block}

=== 待审正文 ===
{review_text}

逐项检查正文是否实际呈现候选描述中的关键细节。缺任何关键细节，填 unconfirmed。"""

    try:
        raw = call_reviewer_api(
            system_prompt=FORESHADOW_REVIEWER_SYSTEM,
            user_message=prompt,
            temperature=0.2,
            max_tokens=max(500, 180 + 220 * len(candidates)),
        )
    except Exception:
        return None

    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    parsed = extract_json_obj(raw)
    if not parsed or not isinstance(parsed, dict):
        return None

    def _clamp(v):
        try:
            return max(0, min(10, int(v)))
        except Exception:
            return 5

    confirmed = []
    entries = parsed.get("decisions")
    if not isinstance(entries, list):
        return None
    # 漏审不能当成“未埋入”：整批候选必须各有一个有效结论。
    keys = [entry.get("key") for entry in entries if isinstance(entry, dict)]
    if (len(keys) != len(entries) or any(not isinstance(key, str) for key in keys)
            or len(keys) != len(candidates) or set(keys) != set(candidates)
            or any(entry.get("verdict") not in ("confirmed", "unconfirmed") for entry in entries)):
        print("  [伏笔审核] 逐项结论缺失、重复或无效，本批不更新状态")
        return None
    if isinstance(entries, list) and review_text == chapter_text:
        seen, duplicates = set(), set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key, quote = entry.get("key"), entry.get("quote")
            restated = entry.get("restated")
            if not isinstance(key, str) or key not in candidates:
                continue
            if key in seen:
                duplicates.add(key)
            seen.add(key)
            if (entry.get("verdict") == "confirmed" and isinstance(quote, str)
                    and quote.strip() and quote in chapter_text
                    and isinstance(restated, str) and 0 < len(restated.strip()) <= 40):
                confirmed.append((key, {**candidates[key], "key": key, "quote": quote,
                                        "restated": restated.strip()}))
        confirmed = [
            item for key, item in sorted(confirmed, key=lambda entry: int(entry[0][2:]))
            if key not in duplicates
        ]

    crosschecked = _crosscheck_confirmed(chapter_text, confirmed)
    confirmed = crosschecked or []
    can_defer = crosschecked is not None and review_text == chapter_text
    planted = {x["fid"] for x in confirmed if x.get("source") == "大纲" and x.get("action") == "plant"}
    return {
        "confirmed": confirmed,
        "crosscheck_error": crosschecked is None,
        "unconfirmed_plant": [x["fid"] for x in to_plant
                              if can_defer and x.get("source") == "大纲" and x["fid"] not in planted],
        "embed": _clamp(parsed.get("embed", 5)),
        "resolve": _clamp(parsed.get("resolve", 5)),
        "traceable": _clamp(parsed.get("traceable", 5)),
        "overload_risk": _clamp(parsed.get("overload_risk", 5)),
        "comment": str(parsed.get("comment", "")).strip(),
    }


def _apply_foreshadow_l2_judgment(result: dict, fs_score: dict,
                                  foreshadow_list: dict):
    """
    根据伏笔专项评分追加 L2 审核意见。
    - resolve < 5 且有应兑现伏笔 → 提示兑现不足
    - overload_risk < 4 → 提示积压风险
    """
    warnings = []

    to_resolve = foreshadow_list.get("to_resolve", [])
    if fs_score["resolve"] < 5 and to_resolve:
        names = "、".join(
            f.get("description", f.get("fid", ""))[:30]
            for f in to_resolve[:3]
        )
        warnings.append(f"⚠️ 伏笔兑现不足（{fs_score['resolve']}/10），请补充对[{names}]的呼应")

    if fs_score["overload_risk"] < 4:
        warnings.append(
            f"⚠️ 本章伏笔积压风险高（{fs_score['overload_risk']}/10），"
            "请删除未安排的悬念，保留本章已计划的剧情任务"
        )

    if not warnings:
        return

    for w in warnings:
        result.setdefault("l2_issues", []).append(w)

    existing = result.get("suggestions", "").strip()
    warn_text = "\n".join(warnings)
    if existing and existing != "质量合格":
        result["suggestions"] = f"{existing}\n{warn_text}"
    else:
        result["suggestions"] = warn_text


def _persist_review_result(mm: MemoryManager, chapter_num: int, result: dict):
    try:
        mm.update_chapter_review_result(chapter_num, result)
    except Exception as e:
        print(f"  [警告] 审稿结果写入数据库失败：{e}")


def _truncate_for_review(text: str, max_len: int = 6000) -> str:
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
        f"性格：{c.get('personality', '未记录')[:200]}｜"
        f"状态：{c.get('current_status', '未记录')[:150]}｜"
        f"位置：{c.get('current_location', '未记录')}｜"
        f"关系：{json.dumps(c.get('relationships', {}), ensure_ascii=False)[:200]}｜"
        f"秘密：{c.get('secret', '无')[:100]}｜"
        f"弱点：{c.get('weakness', '无')[:100]}"
        for c in chars
    ]) if chars else "（暂无人物设定）"

    recent = ctx.get("recent_summaries", [])
    recent_str = "\n".join(f"第{row['chapter_num']}章：{row['summary']}" for row in recent)

    return f"""=== 审稿任务 ===
章节：第{chapter_num}章
本章目标（任务卡）：{plot_goal}

=== 人物设定（核对OOC和状态连贯性）===
{chars_str}

=== 本章伏笔任务 ===
{format_chapter_foreshadow(ctx, chapter_num)}

=== 前情概要（核对衔接）===
{recent_str or "（首章或无近期摘要）"}

=== 待审正文 ===
{content}

先检查是否完成本章任务、是否开始下一实质事件。允许一两句自然收束，短章按内容完整性评分。若有越界，引用原文并按 tail_cut 规则提供可定位的删尾建议。再逐层评估并给出 JSON。"""


def review_chapter(novel_name: str, chapter_num: int,
                   content: str, plot_goal: str) -> dict:
    mm = MemoryManager(novel_name)
    from core.writer import chapter_length_issue
    length_issue = chapter_length_issue(content)
    if length_issue:
        result = _normalize_review_result({
            "pass": False, "score_l1": 0, "score_l2": 0, "score_l3": 0,
            "l3_issues": [length_issue], "suggestions": length_issue,
        })
        _persist_review_result(mm, chapter_num, result)
        print(f"  [责任编辑] {length_issue}")
        return result
    try:
        ctx = mm.load_context(chapter_num)
    except Exception as exc:
        result = _review_error_result("章节上下文读取失败，请重试", f"读取失败：{type(exc).__name__}")
        _persist_review_result(mm, chapter_num, result)
        return result

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

    # 伏笔专项评分（失败不中断主流程）
    foreshadow_list = _build_foreshadow_list(ctx)
    fs_score = score_foreshadowing(content, foreshadow_list, chapter_num)
    if fs_score:
        result["foreshadow_score"] = fs_score
        result["foreshadow_reviewed_content"] = content
        _apply_foreshadow_l2_judgment(result, fs_score, foreshadow_list)

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

    if result.get("foreshadow_score"):
        fs = result["foreshadow_score"]
        print(
            f"  [伏笔专项] 埋入:{fs['embed']}/10 兑现:{fs['resolve']}/10 "
            f"可追溯:{fs['traceable']}/10 积压风险:{fs['overload_risk']}/10"
        )
        if fs.get("comment"):
            print(f"    {fs['comment']}")

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


def _start_writing_session(novel_name: str, chapter_num: int) -> int | None:
    """记录一次写作/修订尝试开始，失败不影响主流程。"""
    try:
        with with_db_connection(novel_name) as conn:
            with DatabaseTransaction(conn):
                row = conn.execute(
                    "SELECT COALESCE(MAX(attempt), 0) + 1 as attempt "
                    "FROM writing_sessions WHERE chapter_num=?",
                    (chapter_num,),
                ).fetchone()
                attempt = row["attempt"] if row else 1
                cur = conn.execute("""
                    INSERT INTO writing_sessions
                    (chapter_num, attempt, started_at)
                    VALUES (?, ?, ?)
                """, (chapter_num, attempt, datetime.now().isoformat(timespec="seconds")))
                return cur.lastrowid
    except Exception as e:
        print(f"  [提示] 写作历史开始记录失败（不影响流程）：{e}")
        return None


def _finish_writing_session(novel_name: str, session_id: int | None,
                            end_reason: str, content: str = "",
                            review_score=None):
    """记录一次写作/修订尝试结束，失败不影响主流程。"""
    if not session_id:
        return
    try:
        word_count = len(content or "")
        with with_db_connection(novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE writing_sessions
                    SET ended_at=?, end_reason=?, word_count=?, review_score=?
                    WHERE id=?
                """, (
                    datetime.now().isoformat(timespec="seconds"),
                    end_reason,
                    word_count,
                    review_score,
                    session_id,
                ))
    except Exception as e:
        print(f"  [提示] 写作历史结束记录失败（不影响流程）：{e}")


def _force_approve_chapter(novel_name: str, chapter_num: int,
                           content: str, mm: MemoryManager = None) -> str:
    """标记为强制通过。"""
    if mm is None:
        mm = MemoryManager(novel_name)
    try:
        _update_status_safe(novel_name, chapter_num, "强制通过")
    except Exception as e:
        print(f"  [警告] 状态更新失败：{e}，尝试直接写入...")
        mm.update_chapter_status(chapter_num, "强制通过")
    return content or ""


def _find_unique_cut_paragraph(paragraphs: list[str], start_text: str) -> int | None:
    start_text = (start_text or "").strip()
    if not start_text:
        return None
    matches = [
        i for i, para in enumerate(paragraphs)
        if para.strip().startswith(start_text)
    ]
    if not matches:
        matches = [
            i for i, para in enumerate(paragraphs)
            if start_text in para.strip()
        ]
    return matches[0] if len(matches) == 1 else None


def _apply_cut_suggestions_to_text(content: str, cut_suggestions: list,
                                   word_min: int,
                                   selected_indexes: set[int] = None) -> tuple:
    """按用户选择保守裁切段落；返回 (正文, 已应用, 已跳过)。"""
    if selected_indexes is None:
        selected_indexes = set(range(1, len(cut_suggestions) + 1))
    paragraphs = (content or "").split("\n\n")
    applied = []
    skipped = []

    for idx, item in enumerate(cut_suggestions or [], 1):
        if idx not in selected_indexes:
            continue
        start_text = str(item.get("start", "") if isinstance(item, dict) else "").strip()
        para_idx = _find_unique_cut_paragraph(paragraphs, start_text)
        if para_idx is None:
            skipped.append((idx, "未找到唯一匹配段落"))
            continue

        candidate = paragraphs[:para_idx] + paragraphs[para_idx + 1:]
        new_text = "\n\n".join(candidate).strip()
        if len(new_text) < word_min:
            skipped.append((idx, f"裁切后低于最低字数 {word_min}"))
            continue

        removed = paragraphs[para_idx].strip()
        paragraphs = candidate
        applied.append((idx, removed[:40]))

    return "\n\n".join(paragraphs).strip(), applied, skipped


def _apply_cut_suggestions_interactive(novel_name: str, chapter_num: int,
                                       content: str, cut_suggestions: list,
                                       plot_goal: str,
                                       emotion_tag: str) -> str:
    """交互式应用责任编辑给出的精简建议。"""
    if not cut_suggestions:
        return content

    print(f"\n  [精简建议] 发现 {len(cut_suggestions)} 处可裁切段落")
    choice = input("  是否逐条查看并选择应用？(y/n，默认n)：", options=(
        ("y", "查看建议"), ("n", "保留原文"),
    )).strip().lower()
    if choice != "y":
        print("  [精简建议] 已跳过自动裁切")
        return content

    selected = set()
    paragraphs = (content or "").split("\n\n")
    for i, item in enumerate(cut_suggestions, 1):
        start_text = str(item.get("start", "") if isinstance(item, dict) else "").strip()
        reason = str(item.get("reason", "") if isinstance(item, dict) else "").strip()
        para_idx = _find_unique_cut_paragraph(paragraphs, start_text)
        preview = paragraphs[para_idx].strip()[:160] if para_idx is not None else "未找到唯一匹配段落"
        print(f"\n  {i}. {start_text or '（无首句）'}")
        if reason:
            print(f"     理由：{reason}")
        print(f"     段落预览：{preview}")
        apply_one = input("     删除该段？(y/n，默认n)：", options=(
            ("y", "删除该段"), ("n", "保留该段"),
        )).strip().lower()
        if apply_one == "y":
            selected.add(i)

    if not selected:
        print("  [精简建议] 未选择任何裁切项")
        return content

    from core.writer import chapter_word_limits
    word_min, _, _ = chapter_word_limits()
    new_content, applied, skipped = _apply_cut_suggestions_to_text(
        content, cut_suggestions, word_min, selected
    )
    for idx, reason in skipped:
        print(f"  [精简建议] 第{idx}项跳过：{reason}")
    if not applied:
        print("  [精简建议] 没有可安全应用的裁切项")
        return content

    mm = MemoryManager(novel_name)
    word_target = cfg("novel", "chapter_word_target", 3500)
    mm.save_chapter(
        chapter_num, f"第{chapter_num}章",
        new_content, "草稿",
        word_target=word_target,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
    )
    print(f"  [精简建议] 已应用 {len(applied)} 项，当前字数：{len(new_content)}字")
    return new_content



_OUTLINE_STOPWORDS = frozenset([
    "一个", "这个", "那个", "没有", "不是", "就是", "也是", "还是", "但是",
    "然后", "因为", "所以", "如果", "可以", "已经", "他们", "她们", "自己",
    "什么", "怎么", "知道", "发现", "开始", "觉得", "看到", "来到", "出来",
    "起来", "下来", "回来", "过来", "到了", "的是", "不了", "之中", "之后",
    "之前", "突然", "虽然", "然而", "于是", "可能", "有些", "这些", "那些",
    "所有", "一切", "非常", "只是", "这样", "那样", "一样", "成为", "存在",
    "出现", "不会", "不能", "不得", "他的", "她的", "它的", "我的",
    "你的", "我们", "你们", "这里", "那里", "里面", "外面", "上面", "下面",
])

def commit_approved_chapter(novel_name, chapter_num, content, result):
    """在同一事务提交已审正文状态和这一版正文的伏笔结论。"""
    if not result.get("pass") or result.get("review_error"):
        raise ValueError("无法提交未通过审核的正文")
    confirmed = (
        (result.get("foreshadow_score") or {}).get("confirmed", [])
        if result.get("foreshadow_reviewed_content") == content else []
    )
    unconfirmed = ((result.get("foreshadow_score") or {}).get("unconfirmed_plant", [])
                   if result.get("foreshadow_reviewed_content") == content else [])
    notices = []
    with with_db_connection(novel_name) as conn, DatabaseTransaction(conn):
        updated = conn.execute(
            "UPDATE chapters SET status='已审核', updated_at=datetime('now','localtime') "
            "WHERE chapter_num=? AND content=?",
            (chapter_num, content),
        )
        if updated.rowcount != 1:
            raise ValueError("章节正文已变化，请重新审核")
        confirmed_plant = {x["fid"] for x in confirmed if x["source"] == "大纲" and x["action"] == "plant"}
        for fid in sorted(set(unconfirmed) - confirmed_plant):
            row = conn.execute(
                "SELECT resolve_chapter FROM outline_foreshadowing WHERE novel_name=? "
                "AND fid=? AND status='planned' AND plant_chapter=?", (novel_name, fid, chapter_num),
            ).fetchone()
            if not row:
                continue
            resolve_chapter = row["resolve_chapter"]
            if isinstance(resolve_chapter, int) and chapter_num + 1 < resolve_chapter:
                conn.execute(
                    "UPDATE outline_foreshadowing SET plant_chapter=plant_chapter+1 "
                    "WHERE novel_name=? AND fid=? AND status='planned' AND plant_chapter=?",
                    (novel_name, fid, chapter_num),
                )
                notices.append(f"伏笔{fid}埋入未确认，顺延至第{chapter_num + 1}章")
            else:
                notices.append(f"伏笔{fid}埋入未确认且无顺延空间，兑现任务将不下发")
        for item in confirmed:
            fid = item["fid"]
            if item["source"] == "动态":
                conn.execute(
                    "UPDATE foreshadowing SET status='redeemed', redeemed_chapter=? "
                    "WHERE fid=? AND status='active' AND plant_chapter < ?",
                    (chapter_num, fid, chapter_num),
                )
            elif item["action"] == "plant":
                conn.execute(
                    "UPDATE outline_foreshadowing SET status='planted' "
                    "WHERE novel_name=? AND fid=? AND plant_chapter=? AND status='planned'",
                    (novel_name, fid, chapter_num),
                )
            else:
                conn.execute(
                    "UPDATE outline_foreshadowing SET status='resolved' "
                    "WHERE novel_name=? AND fid=? AND resolve_chapter=? AND status='planted'",
                    (novel_name, fid, chapter_num),
                )


    for message in notices:
        print(f"  [伏笔提示] {message}")


def force_on_review_exhausted():
    return cfg("novel", "on_review_exhausted", "stop") == "force_and_flag"


def _trim_reviewed_tail(content: str, result: dict) -> str:
    """只按本次主审的明确结论删除可唯一定位的整段尾声，随后仍须重审。"""
    cut = result.get("tail_cut")
    if result.get("pass") or result.get("review_error") or not isinstance(cut, dict):
        return content
    if cut.get("goal_complete") is not True:
        return content
    start = cut.get("start")
    if not isinstance(start, str) or not start.strip() or content.count(start) != 1:
        return content
    offset = content.index(start)
    if offset < 2 or content[offset - 2:offset] != "\n\n":
        return content
    prefix = content[:offset].rstrip()
    if not prefix or prefix[-1] not in '。！？!?….”’"\'':
        return content
    print(f"  [章尾修订] 按主审建议删除尾部{len(content) - len(prefix)}字，重新审核{len(prefix)}字正文")
    return prefix


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
    from core.writer import write_chapter, revise_chapter
    from core.api_client import get_failure_stats, check_switch_needed

    if max_retry is None:
        max_retry = cfg("novel", "max_retry", 3)

    mm = MemoryManager(novel_name)
    content = None

    MAX_REVIEW_RETRIES = 3
    def failed(session_id, reason, score=None, *, allow_override=False):
        if allow_override and force_on_review_exhausted():
            print(f"  [强制通过] 第{chapter_num}章内容审核未通过，按配置继续")
            _finish_writing_session(novel_name, session_id, "强制通过", content, score)
            return _force_approve_chapter(novel_name, chapter_num, content, mm)
        _update_status_safe(novel_name, chapter_num, "审稿失败")
        _finish_writing_session(novel_name, session_id, reason, content, score)
        return ""

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
    _retry_feedback = ""              # 重试反馈，独立于 plot_goal
    _original_content = None          # 最近一版正文，供局部修改使用
    _revise_mode = False              # 是否进入局部修改模式
    # ─────────────────────────────────────────────────────────────

    # 外层 while：正常流程只走一次；任务卡重写后最多再走一次
    while True:
        check_cancelled()
        _rewrite_requested = False

        # ── 平台期检测：记录每轮评分，波动过小时提前终止 ──────
        _scores_history = []
        _plateau_threshold = cfg("novel", "plateau_threshold", 5)
        _plateau_window = cfg("novel", "plateau_window", 3)
        # ─────────────────────────────────────────────────────

        for attempt in range(max_retry):
            print(f"\n  第{attempt+1}次写作尝试...")
            _session_id = _start_writing_session(novel_name, chapter_num)
            _attempt_content = ""

            try:
                if _revise_mode and _original_content:
                    content = revise_chapter(
                        novel_name, chapter_num,
                        _original_content, _retry_feedback,
                        plot_goal, emotion_tag,
                    )
                else:
                    content = write_chapter(novel_name, chapter_num,
                                            plot_goal, emotion_tag,
                                            retry_feedback=_retry_feedback)
                _attempt_content = content or ""
                reset_failure_counter("author")
            except Exception as e:
                print(f"  [错误] 写作调用失败：{e}")
                increment_failure_counter("author")
                _finish_writing_session(
                    novel_name, _session_id, "写作调用失败",
                    _attempt_content, None,
                )
                if check_switch_needed("author"):
                    print(f"\n{'='*60}")
                    print(f"  [提示] 作者模型连续失败，建议切换模型")
                    print(f"{'='*60}")
                    from core.api_client import _select_single_model
                    print("\n是否切换作者模型？(y/n，默认y)")
                    choice = input(options=_SWITCH_CHOICES).strip().lower() or "y"
                    if choice == "y":
                        print("\n【选择新的作者模型】")
                        author_choice = _select_single_model("作者模型", default="1")
                        from core.api_client import set_author_model
                        set_author_model(author_choice["model"], author_choice["provider"])
                        reset_failure_counter("author")
                continue

            _original_content = content
            result = review_chapter(novel_name, chapter_num, content, plot_goal)
            for _ in range(MAX_REVIEW_RETRIES - 1):
                if not result.get("review_error"):
                    break
                print("  [重试] 责任编辑返回异常，将重审同一稿件")
                result = review_chapter(novel_name, chapter_num, content, plot_goal)
            if result.get("review_error"):
                return failed(_session_id, "责任编辑服务异常")
            trimmed = _trim_reviewed_tail(content, result)
            if trimmed != content:
                content = trimmed
                _original_content = content
                mm.save_chapter(chapter_num, f"第{chapter_num}章", content, "草稿",
                                plot_goal=plot_goal, emotion_tag=emotion_tag)
                result = review_chapter(novel_name, chapter_num, content, plot_goal)
                if result.get("review_error"):
                    return failed(_session_id, "删尾后责任编辑服务异常")
            _scores_history.append(result.get("score_total", 0))

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
                    attempt >= 1                    # 至少第2次失败
                    and not _task_rewrite_done      # 还没重写过
                    and _all_structural             # 全是L1/L2结构问题
                    and (
                        len(repeated_vetos) > 0     # 原条件：veto code重复
                        or len(_failure_layers) >= 2  # 新增：连续2次都是L1/L2
                    )
                )
                # ─────────────────────────────────────────────────────

                # ── 冲突菜单 ──────────────────────────────────────────
                if _conflict_detected and not force_on_review_exhausted():
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
                    conflict_choice = input("\n  请选择（默认1）：", options=(
                        ("1", "基于大纲重写任务卡"), ("2", "手动输入新目标"),
                        ("3", "沿用任务卡并切换模型"), ("4", "强制通过"),
                    )).strip() or "1"

                    if conflict_choice == "1":
                        print("\n  [任务卡重写] 正在基于大纲生成新目标...")
                        from core.planner import rewrite_task_for_chapter
                        veto_descs = [
                            f"{item.get('code','')}: {item.get('reason','')}"
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
                        _retry_feedback = ""
                        _original_content = None
                        _revise_mode = False
                        _task_rewrite_done = True
                        _rewrite_requested = True
                        print(f"  [OK] 新任务卡：{plot_goal}")
                        print(f"       情绪标签：{emotion_tag}")
                        _finish_writing_session(
                            novel_name, _session_id, "任务卡自动重写",
                            content, result.get("score_total"),
                        )
                        break  # 跳出 for 循环，外层 while 重新开始

                    elif conflict_choice == "2":
                        print("\n  请输入新的情节目标（40-80字，直接回车取消）：")
                        manual_goal = input("  > ", multiline=True).strip()
                        if manual_goal and len(manual_goal) >= 10:
                            plot_goal   = manual_goal
                            previous_goal = _original_plot_goal
                            _original_plot_goal = plot_goal
                            _veto_code_counter.clear()
                            _failure_layers.clear()
                            _retry_feedback = ""
                            _original_content = None
                            _revise_mode = False
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
                                    _orig = (_row["original_plot_goal"] or previous_goal) if _row else previous_goal
                                    _rc   = ((_row["rewrite_count"] or 0) + 1) if _row else 1
                                    _conn.execute(
                                        "UPDATE chapter_tasks SET plot_goal=?, "
                                        "original_plot_goal=?, rewrite_count=?, "
                                        "updated_at=datetime('now','localtime') "
                                        "WHERE chapter_num=?",
                                        (plot_goal, _orig, _rc, chapter_num)
                                    )
                                    _conn.commit()
                            except Exception as _e:
                                print(f"  [警告] 任务卡更新失败（非致命）：{_e}")
                            print(f"  [OK] 已更新任务卡目标")
                            _finish_writing_session(
                                novel_name, _session_id, "任务卡手动重写",
                                content, result.get("score_total"),
                            )
                            break  # 跳出 for 循环，外层 while 重新开始
                        else:
                            print("  [提示] 输入无效，继续原任务卡重试")
                            # 不 break，继续 for 循环的正常重试逻辑

                    elif conflict_choice == "4":
                        print(f"  [强制通过] 保留当前版本")
                        _finish_writing_session(
                            novel_name, _session_id, "强制通过（冲突菜单）",
                            content, result.get("score_total"),
                        )
                        return _force_approve_chapter(
                            novel_name, chapter_num, content, mm
                        )

                    # choice == "3"：跳过冲突菜单，走正常重试逻辑（下方）
                # ─────────────────────────────────────────────────────

                # 若刚才选了 1 或 2 并 break，这里不会执行
                if _rewrite_requested:
                    break

                # ── 平台期检测：评分波动过小时提前终止重试 ──────
                if len(_scores_history) >= _plateau_window:
                    recent = _scores_history[-_plateau_window:]
                    if max(recent) - min(recent) < _plateau_threshold:
                        print(f"\n  [平台期] 最近{_plateau_window}轮评分 {recent}，波动<{_plateau_threshold}→停止重试")
                        return failed(_session_id, "责任编辑评分进入平台期",
                                      result.get("score_total"), allow_override=True)
                # ─────────────────────────────────────────────────────

                if attempt < max_retry - 1:
                    retry_feedback = _build_retry_feedback(result)
                    if retry_feedback:
                        _retry_feedback = retry_feedback

                    if not _revise_mode:
                        # 第1次失败：自动进入局部修改模式
                        _revise_mode = True
                        print(f"  [修改] 基于原文进行局部修改...")
                    elif not force_on_review_exhausted():
                        # 已在修改模式，给用户选择
                        print(f"\n{'='*60}")
                        print(f"  局部修改仍未通过（{result.get('score_total', 0)}/100）")
                        print(f"{'='*60}")
                        print("  1. 继续局部修改（默认）")
                        print("  2. 完全重写（原任务卡，不带修改意见）")
                        print("  3. 切换模型重新生成")
                        retry_choice = input("\n  请选择：", options=_RETRY_CHOICES).strip() or "1"

                        if retry_choice == "2":
                            _revise_mode = False
                            _retry_feedback = ""
                            plot_goal = _original_plot_goal
                            emotion_tag = _original_emotion_tag
                            print(f"  [重写] 使用原始任务卡完全重写...")
                        elif retry_choice == "3":
                            from core.api_client import _select_single_model
                            print("\n【选择新的作者模型】")
                            model_choice = _select_single_model("综合模型", default="1")
                            from core.api_client import set_author_model, set_reviewer_model
                            set_author_model(model_choice["model"], model_choice["provider"])
                            set_reviewer_model(model_choice["model"], model_choice["provider"])
                            reset_failure_counter("author")
                            reset_failure_counter("reviewer")
                            _revise_mode = False
                            _original_content = None
                            _retry_feedback = ""
                            print(f"\n  [OK] 已切换至 {model_choice['name']}")
                        # choice == "1"：继续局部修改，保持现有状态

                    _finish_writing_session(
                        novel_name, _session_id, "责任编辑不通过",
                        content, result.get("score_total"),
                    )
                    continue
                else:
                    return failed(_session_id, "责任编辑未通过",
                                  result.get("score_total"), allow_override=True)

            # ── 责任编辑通过，进入读者视角 ───────────────────────────
            # 交互式应用精简建议（优化E）
            cut = result.get("cut_suggestions", [])
            if cut and not force_on_review_exhausted():
                before_cut = content
                content = _apply_cut_suggestions_interactive(
                    novel_name, chapter_num, content, cut,
                    plot_goal, emotion_tag,
                )
                _original_content = content
                if content != before_cut:
                    result = review_chapter(novel_name, chapter_num, content, plot_goal)
                    if not result.get("pass"):
                        return failed(_session_id, "裁切后责任编辑未通过",
                                      result.get("score_total"), allow_override=not result.get("review_error"))

            reader_result = reader_review_chapter(novel_name, chapter_num, content)

            if reader_result.get("review_error"):
                return failed(_session_id, "读者审稿服务异常")

            if reader_result.get("pass"):
                verdict = "责任编辑通过，读者审稿已按配置跳过" if reader_result.get("skipped") else "双重审核通过"
                print(f"\n  [OK] 第{chapter_num}章{verdict}！")
                commit_approved_chapter(novel_name, chapter_num, content, result)

                _finish_writing_session(
                    novel_name, _session_id, "审核通过",
                    content, reader_result.get("score_total"),
                )
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
                        _retry_feedback = "\n".join(reader_feedback_parts)

                    if not _revise_mode:
                        _revise_mode = True
                        print(f"  [修改] 基于原文进行局部修改...")
                    elif not force_on_review_exhausted():
                        print(f"\n{'='*60}")
                        print(f"  局部修改仍未通过（读者视角）")
                        print(f"{'='*60}")
                        print("  1. 继续局部修改（默认）")
                        print("  2. 完全重写（原任务卡，不带修改意见）")
                        print("  3. 切换模型重新生成")
                        retry_choice = input("\n  请选择：", options=_RETRY_CHOICES).strip() or "1"

                        if retry_choice == "2":
                            _revise_mode = False
                            _retry_feedback = ""
                            plot_goal = _original_plot_goal
                            emotion_tag = _original_emotion_tag
                            print(f"  [重写] 使用原始任务卡完全重写...")
                        elif retry_choice == "3":
                            from core.api_client import _select_single_model
                            print("\n【选择新的作者模型】")
                            model_choice = _select_single_model("综合模型", default="1")
                            from core.api_client import set_author_model, set_reviewer_model
                            set_author_model(model_choice["model"], model_choice["provider"])
                            set_reviewer_model(model_choice["model"], model_choice["provider"])
                            reset_failure_counter("author")
                            reset_failure_counter("reviewer")
                            _revise_mode = False
                            _original_content = None
                            _retry_feedback = ""
                            print(f"\n  [OK] 已切换至 {model_choice['name']}")
                    _finish_writing_session(
                        novel_name, _session_id, "读者视角不通过",
                        content, reader_result.get("score_total"),
                    )
                else:
                    return failed(_session_id, "读者审稿未通过",
                                  reader_result.get("score_total"), allow_override=True)

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
