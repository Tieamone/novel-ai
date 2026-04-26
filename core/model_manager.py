import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional

load_dotenv()

# 模型分类和优先级
# ==================== 模块级缓存（防止重复发现） ====================
_discover_cache: list = None


def _invalidate_discover_cache():
    global _discover_cache
    _discover_cache = None


MODEL_CATEGORIES = {
    "qwen-turbo": {"category": "cost_effective", "priority": 1, "display_name": "qwen-turbo（高性价比）"},
    "qwen-plus": {"category": "balanced", "priority": 2, "display_name": "qwen-plus（平衡推荐）"},
    "qwen-max": {"category": "premium", "priority": 3, "display_name": "qwen-max（高级）"},
    "qwen-long": {"category": "long_context", "priority": 4, "display_name": "qwen-long（长上下文）"},
    "qwen3.6-flash": {"category": "balanced", "priority": 2, "display_name": "qwen3.6-flash（快速低成本）"},
    "qwen3.6-plus": {"category": "premium", "priority": 3, "display_name": "qwen3.6-plus（高级）"},
    "qwen3.6-35b-a3b": {"category": "premium", "priority": 3, "display_name": "qwen3.6-35b-a3b（高质量）"},
    "qwen3.5-35b-a3b": {"category": "balanced", "priority": 2, "display_name": "qwen3.5-35b-a3b（稳定版）"},
    "glm-5": {"category": "premium", "priority": 3, "display_name": "glm-5（智谱旗舰）"},
    "glm-5.1": {"category": "premium", "priority": 3, "display_name": "glm-5.1（智谱旗舰）"},
}

# 默认定价（兜底用）
DEFAULT_PRICING = {
    "input": 0.0008,
    "output": 0.002,
}


def _load_custom_models() -> Optional[List[Dict]]:
    """
    从 data/custom_models.json 加载自定义模型列表
    """
    custom_file = os.path.join("data", "custom_models.json")
    if not os.path.exists(custom_file):
        return None
    
    try:
        with open(custom_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if not isinstance(data, list):
            print("[模型发现] custom_models.json 格式错误，应为数组")
            return None
        
        models = []
        for item in data:
            model_id = item.get("model")
            if not model_id:
                continue
            
            # 从已知分类获取信息，或使用自定义配置
            category_info = MODEL_CATEGORIES.get(model_id, {})
            
            models.append({
                "model": model_id,
                "name": item.get("name", category_info.get("display_name", model_id)),
                "category": item.get("category", category_info.get("category", "general")),
                "description": item.get("description", ""),
                "context_length": item.get("context_length", 32768),
                "input_price": item.get("input_price", category_info.get("input_price", DEFAULT_PRICING["input"])),
                "output_price": item.get("output_price", category_info.get("output_price", DEFAULT_PRICING["output"])),
                "has_free_quota": item.get("has_free_quota", True),
                "provider": "dashscope",
            })
        
        if models:
            return models
        return None
    except Exception as e:
        print(f"[模型发现] 加载 custom_models.json 失败：{e}")
        return None


def _parse_model_info_from_api(model_data: Dict) -> Optional[Dict]:
    """从 HTTP API 返回的数据解析单个模型信息"""
    model_id = model_data.get('model_name', '')
    if not model_id:
        return None

    # 从已知分类获取信息
    category_info = MODEL_CATEGORIES.get(model_id, {})

    # 如果是试用模型，添加友好的显示名称
    display_name = category_info.get("display_name", model_id)
    if not category_info and "qwen" in model_id.lower():
        # 未在预设列表中的 qwen 模型（如试用模型）
        display_name = model_id + "（试用）"

    # 获取定价信息（使用默认定价或从已知分类）
    if category_info:
        # 已知模型使用已知分类的定价
        input_price = DEFAULT_PRICING["input"]
        output_price = DEFAULT_PRICING["output"]
    else:
        # 未知模型使用通用定价
        input_price = DEFAULT_PRICING["input"]
        output_price = DEFAULT_PRICING["output"]

    # 假设所有从 API 获取的模型都可能有免费额度
    has_free_quota = True

    return {
        "model": model_id,
        "name": display_name,
        "category": category_info.get("category", "general") if category_info else "general",
        "description": "",
        "context_length": 32768,
        "input_price": input_price,
        "output_price": output_price,
        "has_free_quota": has_free_quota,
        "provider": "dashscope",
    }


def _get_default_qwen_models() -> List[Dict]:
    """返回默认模型列表（兜底方案）"""
    return [
        {
            "model": "qwen-plus",
            "name": "qwen-plus（平衡推荐）",
            "category": "balanced",
            "description": "通用推荐，推理能力与性价比平衡",
            "context_length": 32768,
            "input_price": 0.0008,
            "output_price": 0.002,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "qwen-turbo",
            "name": "qwen-turbo（高性价比）",
            "category": "cost_effective",
            "description": "速度快，成本低，适合审稿等任务",
            "context_length": 32768,
            "input_price": 0.0003,
            "output_price": 0.0006,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "qwen-max",
            "name": "qwen-max（高级）",
            "category": "premium",
            "description": "最强推理能力，适合复杂创作",
            "context_length": 32768,
            "input_price": 0.004,
            "output_price": 0.012,
            "has_free_quota": False,
            "provider": "dashscope",
        },
        {
            "model": "qwen-long",
            "name": "qwen-long（长上下文）",
            "category": "long_context",
            "description": "超长上下文，适合记忆管理",
            "context_length": 1000000,
            "input_price": 0.0005,
            "output_price": 0.002,
            "has_free_quota": False,
            "provider": "dashscope",
        },
    ]


def discover_all_models(refresh: bool = False) -> list:
    """
    发现所有可用模型（通义千问 + Gemini），结果模块级缓存，避免重复打印日志。
    优先读取 data/custom_models.json，其次用内置默认列表。
    """
    global _discover_cache
    if _discover_cache is not None and not refresh:
        return _discover_cache

    # 通义千问：优先 custom_models.json，否则用内置默认列表
    custom = _load_custom_models()
    if custom:
        print(f"[模型发现] 从配置文件加载了 {len(custom)} 个自定义模型")
        qwen_models = custom
    else:
        qwen_models = _get_default_qwen_models()

    # Gemini 静态列表
    gemini_models = [
        {
            "model": "gemini-2.5-flash",
            "name": "gemini-2.5-flash（推荐）",
            "category": "free",
            "description": "Google Gemini 当前稳定主力模型",
            "context_length": 1000000,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "gemini",
            "free_tier": True,
        },
        {
            "model": "gemini-2.5-flash-lite",
            "name": "gemini-2.5-flash-lite（快速）",
            "category": "free",
            "description": "Google Gemini 2.5 轻量版，速度更快",
            "context_length": 1000000,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "gemini",
            "free_tier": True,
        },
        {
            "model": "gemini-2.5-pro",
            "name": "gemini-2.5-pro（高质量）",
            "category": "premium",
            "description": "Google Gemini 2.5 高质量推理模型",
            "context_length": 1000000,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": False,
            "provider": "gemini",
            "free_tier": False,
        },
    ]

    _discover_cache = qwen_models + gemini_models
    return _discover_cache


def get_models_for_usage(usage: str, top_k: int = 5) -> List[Dict]:
    """根据用途筛选最适合的模型列表。usage: author|reviewer|reader_reviewer"""
    all_models = discover_all_models()
    excluded_patterns = {
        "author":          {"vl", "vision", "math", "coder"},
        "reviewer":        {"vl", "vision"},
        "reader_reviewer": {"vl", "vision", "math", "coder"},
    }
    char_exclude_usages = ("reviewer", "reader_reviewer")
    excl = excluded_patterns.get(usage, set())
    filtered = []
    for m in all_models:
        model_name = m["model"].lower()
        if any(p in model_name for p in excl):
            continue
        if "character" in model_name and usage in char_exclude_usages:
            continue
        filtered.append(m)
    filtered.sort(key=lambda m: (0 if m.get("has_free_quota") or m.get("free_tier") else 1))
    return filtered[:top_k]


def model_list_to_menu_format(models: List[Dict]) -> Dict[str, Dict]:
    """
    将模型列表转换为菜单选择格式（与 api_client.py 兼容）
    """
    menu = {}
    for idx, model in enumerate(models, 1):
        key = str(idx)
        menu[key] = {
            "name": model["name"],
            "model": model["model"],
            "provider": model["provider"],
            "input_price": model.get("input_price", DEFAULT_PRICING["input"]),
            "output_price": model.get("output_price", DEFAULT_PRICING["output"]),
            "free_tier": model.get("free_tier", False),
            "has_free_quota": model.get("has_free_quota", False),
            "env_key": "DASHSCOPE_API_KEY" if model["provider"] == "dashscope" else "GEMINI_API_KEY",
            "context_length": model.get("context_length", 0),  # 保留，供 is_high_capacity_model 使用
        }
    return menu