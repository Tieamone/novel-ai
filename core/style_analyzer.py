"""
文法指纹分析模块（优化D）
从参考文本中提取写作风格特征，生成可复用的文法指纹。
"""

import json
import re
import math
from collections import Counter
from pathlib import Path
from core.config_loader import get_data_dir
from core.api_client import call_author_api


# ==================== 统计层（不依赖 LLM）====================

def _compute_stats(text: str) -> dict:
    """计算文本的统计特征"""
    # 按句号、问号、叹号分句
    sentences = re.split(r'[。！？!?]', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sentence_lengths = [len(s) for s in sentences]

    # 按段落分
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    para_lengths = [len(p) for p in paragraphs]

    # 对话检测
    dialogue_pattern = re.compile(r'[""「].*?[""」]')
    dialogue_lines = 0
    total_lines = 0
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        total_lines += 1
        if dialogue_pattern.search(line):
            dialogue_lines += 1

    # 词频统计（简单按标点和空格分词）
    _split_re = re.compile(r'[，。、；：！？\s,.;:!?\\/\-—()\[\]【】""''""「」]+')
    words = [w for w in _split_re.split(text) if len(w) >= 2]
    word_freq = Counter(words)
    top_words = word_freq.most_common(20)

    # 计算句长标准差
    avg_sentence_len = sum(sentence_lengths) / max(len(sentence_lengths), 1)
    if len(sentence_lengths) > 1:
        variance = sum((x - avg_sentence_len) ** 2 for x in sentence_lengths) / len(sentence_lengths)
        std_sentence_len = math.sqrt(variance)
    else:
        std_sentence_len = 0

    # 短句/长句比例
    short_threshold = 10
    long_threshold = 30
    short_count = sum(1 for l in sentence_lengths if l < short_threshold)
    long_count = sum(1 for l in sentence_lengths if l > long_threshold)
    total_sentences = max(len(sentence_lengths), 1)

    return {
        "total_chars": len(text),
        "total_sentences": len(sentence_lengths),
        "avg_sentence_len": round(avg_sentence_len, 1),
        "std_sentence_len": round(std_sentence_len, 1),
        "short_sentence_ratio": round(short_count / total_sentences, 2),
        "long_sentence_ratio": round(long_count / total_sentences, 2),
        "total_paragraphs": len(paragraphs),
        "avg_paragraph_len": round(sum(para_lengths) / max(len(para_lengths), 1), 1),
        "dialogue_ratio": round(dialogue_lines / max(total_lines, 1), 2),
        "top_words": [{"word": w, "count": c} for w, c in top_words],
    }


# ==================== 语义层（调用 LLM）====================

_STYLE_ANALYSIS_PROMPT = """你是一位专业的文学风格分析师。请分析以下文本的写作风格特征，输出 JSON 格式。

文本：
---
{text}
---

请从以下维度分析，输出严格的 JSON（不要有其他文字）：

{{
  "rhetoric": "修辞偏好（比喻频率、排比使用、口语化程度等）",
  "rhythm": "节奏特征（快节奏/慢节奏、紧张/舒缓交替模式等）",
  "narrative_pov": "叙事视角偏好（第一人称/第三人称有限/全知等）",
  "emotion_expression": "情感表达方式（直白/含蓄/通过动作暗示等）",
  "sentence_style": "句式偏好（短句为主/长短交替/复合句等）",
  "dialogue_style": "对话风格（简洁/繁复/带动作描写/纯对话等）",
  "unique_features": "独特风格特征（任何显著的个人化写作习惯）"
}}"""


def _analyze_semantic(reference_text: str) -> dict:
    """调用 LLM 提取语义层风格特征"""
    # 截取参考文本，避免超出 token 限制
    truncated = reference_text[:3000] if len(reference_text) > 3000 else reference_text

    try:
        raw = call_author_api(
            system_prompt="你是一位专业的文学风格分析师，只输出 JSON，不要任何说明。",
            user_message=_STYLE_ANALYSIS_PROMPT.format(text=truncated),
            temperature=0.3,
            max_tokens=1000,
        )
        # 提取 JSON
        import json as _json
        # 尝试从回复中提取 JSON
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        result = _json.loads(raw)
        return result
    except Exception as e:
        print(f"  [文法指纹] 语义分析失败：{e}")
        return {
            "rhetoric": "未知",
            "rhythm": "未知",
            "narrative_pov": "未知",
            "emotion_expression": "未知",
            "sentence_style": "未知",
            "dialogue_style": "未知",
            "unique_features": "未知",
        }


# ==================== 核心接口 ====================

def analyze_style(reference_text: str) -> dict:
    """从参考文本提取文法指纹（统计层 + 语义层）"""
    print("  [文法指纹] 正在分析统计特征...")
    stats = _compute_stats(reference_text)

    print("  [文法指纹] 正在分析语义特征...")
    style_guide = _analyze_semantic(reference_text)

    return {
        "stats": stats,
        "style_guide": style_guide,
    }


def save_fingerprint(novel_name: str, fingerprint: dict):
    """保存文法指纹到 data/{novel_name}/style_fingerprint.json"""
    path = get_data_dir(novel_name) / "style_fingerprint.json"
    path.write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [OK] 文法指纹已保存到 {path}")


def load_fingerprint(novel_name: str) -> dict | None:
    """从文件加载文法指纹"""
    path = get_data_dir(novel_name) / "style_fingerprint.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_style_prompt(fingerprint: dict) -> str:
    """将文法指纹转换为可注入 prompt 的文本块"""
    if not fingerprint:
        return ""

    stats = fingerprint.get("stats", {})
    guide = fingerprint.get("style_guide", {})

    parts = []

    # 统计层特征
    if stats:
        parts.append(f"- 句子平均长度：{stats.get('avg_sentence_len', '?')}字，标准差{stats.get('std_sentence_len', '?')}")
        parts.append(f"- 短句比例：{stats.get('short_sentence_ratio', 0)*100:.0f}%，长句比例：{stats.get('long_sentence_ratio', 0)*100:.0f}%")
        parts.append(f"- 段落平均长度：{stats.get('avg_paragraph_len', '?')}字")
        parts.append(f"- 对话占比：{stats.get('dialogue_ratio', 0)*100:.0f}%")
        top = stats.get("top_words", [])
        if top:
            top_str = "、".join([f"{w['word']}({w['count']}次)" for w in top[:10]])
            parts.append(f"- 高频词：{top_str}")

    # 语义层特征
    if guide and any(v != "未知" for v in guide.values()):
        parts.append(f"- 修辞偏好：{guide.get('rhetoric', '未知')}")
        parts.append(f"- 节奏特征：{guide.get('rhythm', '未知')}")
        parts.append(f"- 叙事视角：{guide.get('narrative_pov', '未知')}")
        parts.append(f"- 情感表达：{guide.get('emotion_expression', '未知')}")
        parts.append(f"- 句式偏好：{guide.get('sentence_style', '未知')}")
        parts.append(f"- 对话风格：{guide.get('dialogue_style', '未知')}")
        if guide.get("unique_features") and guide["unique_features"] != "未知":
            parts.append(f"- 独特特征：{guide['unique_features']}")

    return "\n".join(parts) if parts else ""
