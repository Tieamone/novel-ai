import json
import re
from core.api_client import call_reader_reviewer_api, increment_failure_counter, reset_failure_counter
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg
from core.utils import with_db_connection, DatabaseTransaction, \
    extract_json_obj, to_int, is_transient_error

READER_REVIEWER_SYSTEM = """你是一位阅读过数千本网文的资深读者，你最受不了的就是一眼看出是AI写的网文。

你对以下这些东西有天然的厌恶：
- 角色一句话不说就直接"感到紧张/心中涌起暖意/不禁有些感动"——你觉得这些句子像贴标签，不像真人的反应
- 深吸一口气平复情绪、握紧拳头下定决心——你见过这两句话至少一万遍了
- 章节结尾写"这一切才刚刚开始/前方的路还很长/无论前方有多少困难"——你觉得这是在敷衍读者
- 全章句子长度差不多，读起来像在背书，没有节奏起伏

除了AI痕迹，你也在意真实的阅读体验：
- 这章有没有推进什么实质性的东西（信息、关系、冲突）
- 和上一章的衔接自不自然，有没有突兀的跳跃
- 人物说话像不像这个人、文风前后一不一致
- 读完这章你想不想翻下一章

你的评估要诚实，但不要鸡蛋里挑骨头——小问题不影响阅读体验的不用扣分。"""

READER_REVIEW_PROMPT = """请从读者视角评估以下章节。

=== 上一章结尾（供衔接参考，约800字）===
{prev_chapter_content}

=== 当前要评估的章节 ===
{current_chapter_content}

=== 评估标准（共100分，四个维度各25分）===

【1. AI痕迹与真实感】25分
作为读者你最敏感这一项，以下每发现一处扣3-5分：
- 情绪标签直陈：直接写"她感到/他不禁/她心中涌起"而非用行为/生理反应表达
- 高频烂俗动作：深吸一口气平复情绪、握紧拳头下定决心、瞳孔收缩察觉危险
- 模板收束句：这才刚开始/路还很长/无论前方有多少困难/故事远未结束
- 对称句式堆砌：虽然…但依然/一方面…另一方面（连续出现2次以上）
- 句子节奏单调：全章句子长度高度均匀，读起来像背书

【2. 剧情逻辑与推进实质】25分
- 人物行为是否有合理动机，有无逻辑漏洞
- 这章推进了什么实质内容（信息新增/关系变化/冲突升级），还是原地踏步
- 是否有让读者想继续看下一章的悬念或钩子

【3. 前后一致性与衔接】25分
- 和上一章的衔接是否自然（状态、场景、情绪是否连贯）
- 人物性格、说话方式是否与前文一致
- 是否有遗忘前文重要设定或状态的情况

【4. 整体阅读流畅度】25分
- 叙事节奏（快慢）是否合适，有无明显注水或拖沓
- 对话是否自然，有没有"说明书式对话"（角色互相解释信息给读者听）
- 场景描写是否有具体细节，还是全是抽象空洞的描述

=== 一票否决项（任一命中则直接不通过）===
- 剧情崩坏：人物行为完全不可理喻且无任何铺垫
- 重大设定矛盾：与前文已建立的事实直接冲突（死亡人物复活等）
- 文风突变：叙事视角、人称或整体风格发生无法接受的剧变

请严格按以下JSON格式输出，不要任何其他内容：
{{
  "pass": true/false,
  "score_total": 0-100,
  "score_ai_authenticity": 0-25,
  "score_logic": 0-25,
  "score_consistency": 0-25,
  "score_readability": 0-25,
  "veto_triggered": false,
  "veto_reason": "",
  "issues": ["具体问题（引用原文句子）", "具体问题2"],
  "suggestions": "具体可执行的改进建议"
}}"""


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
    truncated = text[:half] + "\n\n[...节选中段已省略，以下为章节结尾...]\n\n" + text[-half:]
    print(f"  [提示] {label}过长（{len(text)}字），已智能截断至约{len(truncated)}字（保留开头和结尾）")
    return truncated


def _normalize_review_result(raw_result: dict) -> dict:
    # 新版字段（AI真实感）+ 旧版字段兼容（score_style/score_experience）
    score_ai  = to_int(raw_result.get("score_ai_authenticity"), 0, 0, 25)
    score_logic = to_int(raw_result.get("score_logic"), 0, 0, 25)
    score_consistency = to_int(raw_result.get("score_consistency"), 0, 0, 25)
    # readability 优先取新字段，兜底取旧字段
    score_readability = to_int(
        raw_result.get("score_readability") or raw_result.get("score_experience"),
        0, 0, 25
    )
    # style 字段（旧版兼容，不计入新总分）
    score_style = to_int(raw_result.get("score_style"), 0, 0, 25)

    total_raw = raw_result.get("score_total")
    if total_raw is None:
        # 新版：四维求和（用字段存在性判断，避免 score_ai=0 时误判为旧版）
        if "score_ai_authenticity" in raw_result:
            score_total = score_ai + score_logic + score_consistency + score_readability
        else:
            # 旧版兼容
            score_total = score_logic + score_consistency + score_style + score_readability
    else:
        score_total = to_int(total_raw, 0, 0, 100)

    # 子项合计校验（偏差超过10分时以子项合计为准）
    if "score_ai_authenticity" in raw_result:
        expected = score_ai + score_logic + score_consistency + score_readability
    else:
        expected = score_logic + score_consistency + score_style + score_readability
    if expected and abs(score_total - expected) > 10:
        score_total = expected

    # Bug修复12: 分数范围强制夹值
    score_total = max(0, min(100, score_total))

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
        suggestions = "质量合格" if final_pass else "优先修复AI痕迹和逻辑问题"

    veto_reason = str(raw_result.get("veto_reason", "")).strip()

    return {
        "pass": final_pass,
        "score_total": score_total,
        "score_ai_authenticity": score_ai,
        "score_logic": score_logic,
        "score_consistency": score_consistency,
        "score_readability": score_readability,
        "score_style": score_style,          # 旧版兼容字段，保留不删
        "score_experience": score_readability,  # 旧版兼容字段，保留不删
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
            "score_ai_authenticity": 25,
            "score_logic": 25,
            "score_consistency": 25,
            "score_readability": 25,
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

    # 上一章只传结尾部分，避免两章全文撑爆 token 窗口
    if prev_chapter_content and len(prev_chapter_content) > 800:
        prev_snippet = "...（省略前段）...\n" + prev_chapter_content[-800:]
    else:
        prev_snippet = prev_chapter_content or "（首章，无上一章）"

    # 当前章节：超长时使用智能截断（优先保留开头完整段落，兼顾首尾）
    current_snippet = _truncate_content(current_content, 5000, "当前章节") or current_content

    prompt = READER_REVIEW_PROMPT.format(
        prev_chapter_content=prev_snippet,
        current_chapter_content=current_snippet
    )

    try:
        raw = call_reader_reviewer_api(
            system_prompt=READER_REVIEWER_SYSTEM,
            user_message=prompt,
            temperature=0.3,
            max_tokens=1200,
        )
    except Exception as e:
        if is_transient_error(e):
            print(f"  [读者视角] ⚠️ 评估遇到暂时性问题: {str(e)[:100]}")
            print(f"  [读者视角] 💡 建议稍后重新评估此章节")
        else:
            print(f"\n❌ [读者视角] 评估失败（严重错误）")
            print(f"   详情: {str(e)[:200]}")
        increment_failure_counter("reader_reviewer")
        return {
            "pass": False,
            "score_total": 0,
            "score_ai_authenticity": 0,
            "score_logic": 0,
            "score_consistency": 0,
            "score_readability": 0,
            "score_style": 0,
            "score_experience": 0,
            "veto_triggered": False,
            "veto_reason": "",
            "issues": ["读者视角评估服务不可用"],
            "suggestions": "",
            "error": str(e),
            "review_error": True,
        }

    parsed = extract_json_obj(raw)
    if not parsed:
        print("  [读者视角] 评估结果格式异常")
        increment_failure_counter("reader_reviewer")
        return {
            "pass": False,
            "score_total": 0,
            "score_ai_authenticity": 0,
            "score_logic": 0,
            "score_consistency": 0,
            "score_readability": 0,
            "score_style": 0,
            "score_experience": 0,
            "veto_triggered": False,
            "veto_reason": "",
            "issues": ["读者视角评估结果格式异常"],
            "suggestions": "",
            "review_error": True,
        }

    result = _normalize_review_result(parsed)

    # 打印评估结果
    status = "通过" if result.get("pass") else "不通过"
    ai_score = result.get("score_ai_authenticity", 0)
    print(
        f"  [读者视角] {status} | 总分：{result['score_total']}/100 "
        f"(真实感:{ai_score}/25 逻辑:{result['score_logic']}/25 "
        f"一致:{result['score_consistency']}/25 流畅:{result.get('score_readability', result.get('score_experience', 0))}/25)"
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
