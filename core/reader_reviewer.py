import sys
import os
import json
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.api_client import call_reader_reviewer_api, increment_failure_counter, reset_failure_counter
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg
from core.utils import with_db_connection, DatabaseTransaction

READER_REVIEWER_SYSTEM = """你是一位资深的网络小说读者，阅读过数千本网文，对读者的阅读体验有深刻的理解。

你的职责是从读者的视角对刚完成的章节进行批判性评估。你不是在找语法错误，而是在找：
1. 作为读者，我读这一章时会感到困惑吗？
2. 这一章的剧情推进符合我的期待吗？
3. 这一章和前文的衔接自然吗？
4. 整体画风和前面一致吗？

你需要给出诚实的反馈，但不要吹毛求疵。作为读者，你更在意的是阅读体验的流畅性和故事的吸引力。"""

READER_REVIEW_PROMPT = """请从读者视角评估以下章节。

=== 上一章内容（供参考） ===
{prev_chapter_content}

=== 当前要评估的章节 ===
{current_chapter_content}

=== 评估标准 ===
请从以下四个维度进行评分（0-100分），每项25分：

1. 剧情逻辑连贯性与合理性（25分）
   - 剧情发展是否符合逻辑？
   - 人物行为是否有合理的动机？
   - 是否有明显的逻辑漏洞或矛盾？

2. 与前文内容的关联性与一致性（25分）
   - 和上一章的衔接是否自然？
   - 人物状态、场景设定是否与前文一致？
   - 是否有遗忘前文重要设定的情况？

3. 整体画风的统一性与稳定性（25分）
   - 文风、叙事节奏是否和前文保持一致？
   - 人物性格、对话风格是否统一？
   - 是否有突兀的风格变化？

4. 阅读体验与吸引力（25分）
   - 这一章读起来流畅吗？
   - 是否有让读者想继续看下一章的钩子？
   - 是否有明显的注水或拖沓？

=== 一票否决项（任一命中则直接不通过） ===
- 出现明显的剧情崩坏，人物行为完全不可理喻
- 和前文出现重大矛盾（如死亡人物复活、设定彻底冲突）
- 文风突变到读者无法接受的程度

请严格按以下JSON格式输出，不要任何其他内容：
{{
  "pass": true/false,
  "score_total": 0-100,
  "score_logic": 0-25,
  "score_consistency": 0-25,
  "score_style": 0-25,
  "score_experience": 0-25,
  "veto_triggered": false,
  "veto_reason": "",
  "issues": ["具体问题1", "具体问题2"],
  "suggestions": "具体的改进建议"
}}"""


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


def _is_transient_error(error):
    """判断是否为暂时性错误（可重试恢复）"""
    transient_keywords = ["timeout", "locked", "rate limit", "503", "502", "429"]
    return any(kw in str(error).lower() for kw in transient_keywords)


def _truncate_content(text: str, max_len: int, label: str = "内容") -> str:
    """智能截断文本：优先保留开头完整段落，超长时保留首尾关键部分"""
    if not text or len(text) <= max_len:
        return text or ""

    # 策略1: 尝试按段落截断（保留完整段落）
    paragraphs = text.split('\n\n')
    result = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > max_len * 0.8:  # 保留20%空间给结尾
            break
        result.append(para)
        current_len += len(para) + 2  # +2 for \n\n

    # 如果已经收集了足够多的内容（>70%），直接返回
    if current_len >= max_len * 0.7:
        truncated = '\n\n'.join(result)
        remaining = len(text) - current_len
        print(f"  [提示] {label}过长（{len(text)}字），已保留前{current_len}字（{remaining}字已省略）")
        return truncated

    # 策略2: 如果段落太少但总长度很长，使用首尾保留策略
    half = max_len // 2
    truncated = text[:half] + "\n\n[... 中间部分已省略 ...]\n\n" + text[-half:]
    print(f"  [提示] {label}过长（{len(text)}字），已智能截断至约{len(truncated)}字（保留开头和结尾）")
    return truncated


def _normalize_review_result(raw_result: dict) -> dict:
    score_logic = _to_int(raw_result.get("score_logic"), 0, 0, 25)
    score_consistency = _to_int(raw_result.get("score_consistency"), 0, 0, 25)
    score_style = _to_int(raw_result.get("score_style"), 0, 0, 25)
    score_experience = _to_int(raw_result.get("score_experience"), 0, 0, 25)

    total_raw = raw_result.get("score_total")
    if total_raw is None:
        score_total = score_logic + score_consistency + score_style + score_experience
    else:
        score_total = _to_int(total_raw, 0, 0, 100)

    expected_total = score_logic + score_consistency + score_style + score_experience
    if expected_total and abs(score_total - expected_total) > 10:
        score_total = expected_total

    pass_threshold = cfg("model", "reader_reviewer", "pass_threshold", 75)
    model_pass = bool(raw_result.get("pass"))
    veto_triggered = bool(raw_result.get("veto_triggered"))
    final_pass = (not veto_triggered) and model_pass and (score_total >= pass_threshold)

    issues = raw_result.get("issues", [])
    if isinstance(issues, str):
        issues = [issues]
    if not isinstance(issues, list):
        issues = []
    issues = [str(i).strip() for i in issues if str(i).strip()]

    suggestions = str(raw_result.get("suggestions", "")).strip()
    if not suggestions:
        suggestions = "质量合格" if final_pass else "优先修复逻辑和一致性问题"

    veto_reason = str(raw_result.get("veto_reason", "")).strip()

    return {
        "pass": final_pass,
        "score_total": score_total,
        "score_logic": score_logic,
        "score_consistency": score_consistency,
        "score_style": score_style,
        "score_experience": score_experience,
        "veto_triggered": veto_triggered,
        "veto_reason": veto_reason,
        "issues": issues,
        "suggestions": suggestions,
    }


def reader_review_chapter(novel_name: str, chapter_num: int,
                         current_content: str) -> dict:
    """
    拟读者视角审批：将上一章作为上下文，评估当前章节
    """
    enabled = cfg("model", "reader_reviewer", "enabled", True)
    if not enabled:
        return {
            "pass": True,
            "score_total": 100,
            "score_logic": 25,
            "score_consistency": 25,
            "score_style": 25,
            "score_experience": 25,
            "veto_triggered": False,
            "veto_reason": "",
            "issues": [],
            "suggestions": "读者视角审批已禁用",
            "skipped": True,
        }

    mm = MemoryManager(novel_name)

    # 获取上一章内容
    prev_chapter_content = ""
    if chapter_num > 1:
        prev_chapter = mm.load_chapter(chapter_num - 1)
        if prev_chapter and prev_chapter.get("content"):
            prev_chapter_content = prev_chapter["content"]

    print(f"  [读者视角] 正在评估第{chapter_num}章...")

    prompt = READER_REVIEW_PROMPT.format(
        prev_chapter_content=prev_chapter_content or "（首章，无上一章）",
        current_chapter_content=current_content
    )

    try:
        raw = call_reader_reviewer_api(
            system_prompt=READER_REVIEWER_SYSTEM,
            user_message=prompt,
            temperature=0.3,
            max_tokens=1200,
        )
    except Exception as e:
        if _is_transient_error(e):
            print(f"  [读者视角] ⚠️ 评估遇到暂时性问题: {str(e)[:100]}")
            print(f"  [读者视角] 💡 建议稍后重新评估此章节")
        else:
            print(f"\n❌ [读者视角] 评估失败（严重错误）")
            print(f"   详情: {str(e)[:200]}")
        increment_failure_counter("reader_reviewer")
        return {
            "pass": True,  # 评估失败时不阻塞流程
            "score_total": 75,
            "score_logic": 20,
            "score_consistency": 20,
            "score_style": 20,
            "score_experience": 15,
            "veto_triggered": False,
            "veto_reason": "",
            "issues": ["读者视角评估服务不可用"],
            "suggestions": "",
            "error": str(e),
        }

    parsed = _extract_json_obj(raw)
    if not parsed:
        print("  [读者视角] 评估结果格式异常")
        increment_failure_counter("reader_reviewer")
        return {
            "pass": True,
            "score_total": 75,
            "score_logic": 20,
            "score_consistency": 20,
            "score_style": 20,
            "score_experience": 15,
            "veto_triggered": False,
            "veto_reason": "",
            "issues": ["读者视角评估结果格式异常"],
            "suggestions": "",
        }

    result = _normalize_review_result(parsed)

    # 打印评估结果
    status = "通过" if result.get("pass") else "不通过"
    print(
        f"  [读者视角] {status} | 总分：{result['score_total']}/100 "
        f"(逻辑:{result['score_logic']}/25 一致:{result['score_consistency']}/25 "
        f"风格:{result['score_style']}/25 体验:{result['score_experience']}/25)"
    )

    if result.get("veto_triggered"):
        print(f"  [读者视角] 一票否决：{result.get('veto_reason')}")
        increment_failure_counter("reader_reviewer")
    elif not result.get("pass"):
        print(f"  [读者视角] 问题：")
        for issue in result.get("issues", [])[:3]:
            print(f"    - {issue}")
        increment_failure_counter("reader_reviewer")
    else:
        reset_failure_counter("reader_reviewer")

    # 保存评估结果到数据库（列已由 db.py _migrate 统一管理）
    try:
        with with_db_connection(novel_name) as conn:
            with DatabaseTransaction(conn):
                conn.execute("""
                    UPDATE chapters
                    SET reader_review_score=?,
                        reader_review_passed=?,
                        reader_review_issues=?
                    WHERE chapter_num=?
                """, (
                    result["score_total"],
                    1 if result["pass"] else 0,
                    json.dumps(result.get("issues", []), ensure_ascii=False),
                    chapter_num
                ))
    except Exception as e:
        print(f"  [读者视角] 保存评估结果失败：{e}")

    return result