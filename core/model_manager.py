import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional

load_dotenv()

# 模型分类和优先级（含用户试用模型）
MODEL_CATEGORIES = {
    # 付费稳定版
    "qwen-turbo":    {"category": "cost_effective", "priority": 5, "display_name": "qwen-turbo（高性价比）"},
    "qwen-plus":     {"category": "balanced",       "priority": 6, "display_name": "qwen-plus（平衡推荐）"},
    "qwen-max":      {"category": "premium",        "priority": 7, "display_name": "qwen-max（高级）"},
    "qwen-long":     {"category": "long_context",   "priority": 8, "display_name": "qwen-long（长上下文）"},
    # 试用模型（Qwen3.6系列）
    "qwen3.6-flash":            {"category": "balanced", "priority": 1, "display_name": "qwen3.6-flash（快速低成本，试用）"},
    "qwen3.6-flash-2026-04-16": {"category": "balanced", "priority": 1, "display_name": "qwen3.6-flash 04-16（试用）"},
    "qwen3.6-plus":             {"category": "premium",  "priority": 2, "display_name": "qwen3.6-plus（高质量，试用）"},
    "qwen3.6-plus-2026-04-02":  {"category": "premium",  "priority": 2, "display_name": "qwen3.6-plus 04-02（试用）"},
    "qwen3.6-35b-a3b":          {"category": "premium",  "priority": 3, "display_name": "qwen3.6-35b-a3b（开源大模型，试用）"},
    # 试用模型（Qwen3.5系列）
    "qwen3.5-35b-a3b":          {"category": "balanced", "priority": 4, "display_name": "qwen3.5-35b-a3b（稳定版，试用）"},
    "qwen3.5-flash-2026-02-23": {"category": "balanced", "priority": 4, "display_name": "qwen3.5-flash 02-23（试用）"},
    # 智谱GLM系列
    "glm-5.1":                  {"category": "premium",  "priority": 3, "display_name": "glm-5.1（智谱旗舰，试用）"},
    # 其他试用模型
    "gui-plus-2026-02-26":              {"category": "balanced", "priority": 4, "display_name": "gui-plus（试用）"},
    "qwen-flash-character-2026-02-26":  {"category": "balanced", "priority": 4, "display_name": "qwen-flash-character（角色扮演，试用）"},
}

# 默认定价（兜底用）
DEFAULT_PRICING = {
    "input": 0.0008,
    "output": 0.002,
}

# 试用模型列表（免费额度，定价为0）
FREE_TRIAL_MODELS = {
    "qwen3.6-flash", "qwen3.6-flash-2026-04-16",
    "qwen3.6-35b-a3b",
    "qwen3.6-plus", "qwen3.6-plus-2026-04-02",
    "qwen3.5-35b-a3b", "qwen3.5-flash-2026-02-23",
    "glm-5.1",
    "gui-plus-2026-02-26",
    "qwen-flash-character-2026-02-26",
}


def fetch_qwen_models() -> List[Dict]:
    """
    获取通义千问可用模型列表（混合方案）
    1. 优先尝试从配置文件加载
    2. 然后尝试自动发现
    3. 最后使用默认列表（含试用模型）
    """
    # 1. 尝试从配置文件加载
    custom_models = _load_custom_models()
    if custom_models:
        print(f"[模型发现] 从配置文件加载了 {len(custom_models)} 个自定义模型")
        return custom_models

    # 2. 尝试自动发现
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key or len(api_key) < 10:
        print("[模型发现] DASHSCOPE_API_KEY 未配置，使用默认模型列表（含试用模型）")
        return _get_default_qwen_models()

    try:
        import requests

        print("[模型发现] 正在获取通义千问模型列表...")

        url = "https://dashscope.aliyuncs.com/api/v1/deployments/models"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        params = {"page_no": 1, "page_size": 100}

        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code != 200:
            print(f"[模型发现] API 请求失败：{response.status_code}，使用默认模型列表")
            return _get_default_qwen_models()

        data = response.json()
        models_data = data.get('output', {}).get('models', [])

        if not models_data:
            print("[模型发现] 模型列表为空，使用默认模型列表")
            return _get_default_qwen_models()

        qwen_models = []
        for model in models_data:
            model_id = model.get('model_name', '')
            # Bug修复: 扩展匹配范围，包含 glm、gui 等试用模型
            if any(kw in model_id.lower() for kw in ('qwen', 'glm', 'gui-plus')):
                model_info = _parse_model_info_from_api(model)
                if model_info:
                    qwen_models.append(model_info)

        if not qwen_models:
            print("[模型发现] 未找到支持的模型，使用默认列表")
            return _get_default_qwen_models()

        # 按优先级排序（低数字优先）
        qwen_models.sort(key=lambda x: MODEL_CATEGORIES.get(x["model"], {}).get("priority", 999))
        print(f"[模型发现] 成功获取 {len(qwen_models)} 个模型")
        return qwen_models

    except ImportError:
        print("[模型发现] requests 库未安装，使用默认模型列表")
        return _get_default_qwen_models()
    except Exception as e:
        print(f"[模型发现] 获取模型列表失败：{e}，使用默认模型列表")
        return _get_default_qwen_models()


def _load_custom_models() -> Optional[List[Dict]]:
    """从 data/custom_models.json 加载自定义模型列表"""
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

            category_info = MODEL_CATEGORIES.get(model_id, {})
            is_free_trial = model_id in FREE_TRIAL_MODELS

            models.append({
                "model": model_id,
                "name": item.get("name", category_info.get("display_name", model_id)),
                "category": item.get("category", category_info.get("category", "general")),
                "description": item.get("description", ""),
                "context_length": item.get("context_length", 131072),
                "input_price": item.get("input_price", 0.0 if is_free_trial else DEFAULT_PRICING["input"]),
                "output_price": item.get("output_price", 0.0 if is_free_trial else DEFAULT_PRICING["output"]),
                "has_free_quota": item.get("has_free_quota", is_free_trial),
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

    category_info = MODEL_CATEGORIES.get(model_id, {})
    is_free_trial = model_id in FREE_TRIAL_MODELS

    display_name = category_info.get("display_name", model_id)
    if not category_info:
        # 未在预设列表中的模型，添加试用标签
        display_name = model_id + "（试用）"

    return {
        "model": model_id,
        "name": display_name,
        "category": category_info.get("category", "general") if category_info else "general",
        "description": "",
        "context_length": 131072,  # Bug修复: qwen3.6系列支持128K，更新默认值
        "input_price": 0.0 if is_free_trial else DEFAULT_PRICING["input"],
        "output_price": 0.0 if is_free_trial else DEFAULT_PRICING["output"],
        "has_free_quota": is_free_trial,
        "provider": "dashscope",
    }


def _get_default_qwen_models() -> List[Dict]:
    """
    返回默认模型列表（兜底方案）
    Bug修复: 补充用户的试用模型，确保启动时有可用模型
    """
    return [
        # 试用模型优先（免费额度）
        {
            "model": "qwen3.6-flash",
            "name": "qwen3.6-flash（快速低成本，试用推荐）",
            "category": "balanced",
            "description": "Qwen3.6快速版，128K上下文，有免费试用额度",
            "context_length": 131072,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "qwen3.6-plus",
            "name": "qwen3.6-plus（高质量，试用）",
            "category": "premium",
            "description": "Qwen3.6旗舰版，高质量输出，有免费试用额度",
            "context_length": 131072,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "qwen3.6-35b-a3b",
            "name": "qwen3.6-35b-a3b（开源大模型，试用）",
            "category": "premium",
            "description": "Qwen3.6开源35B参数版，有免费试用额度",
            "context_length": 131072,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "glm-5.1",
            "name": "glm-5.1（智谱旗舰，试用）",
            "category": "premium",
            "description": "智谱GLM-5.1旗舰模型，有免费试用额度",
            "context_length": 131072,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "qwen3.5-35b-a3b",
            "name": "qwen3.5-35b-a3b（稳定版，试用）",
            "category": "balanced",
            "description": "Qwen3.5稳定版，有免费试用额度",
            "context_length": 131072,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        # 付费稳定版
        {
            "model": "qwen-plus",
            "name": "qwen-plus（付费稳定）",
            "category": "balanced",
            "description": "通用推荐，推理能力与性价比平衡",
            "context_length": 32768,
            "input_price": 0.0008,
            "output_price": 0.002,
            "has_free_quota": False,
            "provider": "dashscope",
        },
        {
            "model": "qwen-turbo",
            "name": "qwen-turbo（速度快，付费）",
            "category": "cost_effective",
            "description": "速度快，成本低，适合审稿等任务",
            "context_length": 32768,
            "input_price": 0.0003,
            "output_price": 0.0006,
            "has_free_quota": False,
            "provider": "dashscope",
        },
        {
            "model": "qwen-max",
            "name": "qwen-max（高级，付费）",
            "category": "premium",
            "description": "最强推理能力，适合复杂创作",
            "context_length": 32768,
            "input_price": 0.004,
            "output_price": 0.012,
            "has_free_quota": False,
            "provider": "dashscope",
        },
    ]


def get_gemini_models() -> List[Dict]:
    """返回 Gemini 模型列表（静态）"""
    return [
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


def discover_all_models(refresh: bool = False) -> List[Dict]:
    """发现所有可用模型（通义千问 + Gemini）"""
    qwen_models = fetch_qwen_models()
    gemini_models = get_gemini_models()
    return qwen_models + gemini_models


def filter_models_for_usage(models: List[Dict], usage: str, top_k: int = 5) -> List[Dict]:
    """
    根据用途筛选模型，返回最适合的top_k个
    usage: "author" | "reviewer" | "reader_reviewer"
    """
    usage_rules = {
        "author": {
            "categories": ["premium", "balanced", "free"],
            "prefer_free": True,
            "exclude_vl": True,
            "exclude_math": True,
            "exclude_coder": True,
        },
        "reviewer": {
            "categories": ["cost_effective", "balanced", "free"],
            "prefer_free": True,
            "exclude_vl": True,
            "exclude_math": False,
            "exclude_coder": False,
        },
        "reader_reviewer": {
            "categories": ["balanced", "premium", "free"],
            "prefer_free": True,
            "exclude_vl": True,
            "exclude_math": True,
            "exclude_coder": True,
        },
    }

    rules = usage_rules.get(usage, usage_rules["author"])

    filtered = []
    for m in models:
        if m["category"] not in rules["categories"]:
            continue

        model_name = m["model"].lower()
        if rules["exclude_vl"] and ("vl" in model_name or "vision" in model_name):
            continue
        if rules["exclude_math"] and "math" in model_name:
            continue
        if rules["exclude_coder"] and "coder" in model_name:
            continue
        # 排除角色扮演模型（不适合写作审核）
        if "character" in model_name and usage in ("reviewer", "reader_reviewer"):
            continue

        filtered.append(m)

    def sort_key(m):
        free_priority = 0 if m.get("has_free_quota", False) else 1
        cat = m["category"]
        cats = rules["categories"]
        category_priority = cats.index(cat) if cat in cats else len(cats)
        return (category_priority, free_priority)

    filtered.sort(key=sort_key)
    return filtered[:top_k]


def get_models_for_usage(usage: str, top_k: int = 5) -> List[Dict]:
    all_models = discover_all_models()
    return filter_models_for_usage(all_models, usage, top_k)


def get_all_models_grouped() -> Dict[str, List[Dict]]:
    all_models = discover_all_models()
    return {
        "author": filter_models_for_usage(all_models, "author"),
        "reviewer": filter_models_for_usage(all_models, "reviewer"),
        "reader_reviewer": filter_models_for_usage(all_models, "reader_reviewer"),
    }


def model_list_to_menu_format(models: List[Dict]) -> Dict[str, Dict]:
    """将模型列表转换为菜单选择格式"""
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
            "context_length": model.get("context_length", 0),
        }
    return menu


def save_models_to_cache(models: List[Dict], cache_file: str = "data/models_cache.json"):
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(models, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[模型缓存] 保存失败：{e}")


def load_models_from_cache(cache_file: str = "data/models_cache.json") -> Optional[List[Dict]]:
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[模型缓存] 加载失败：{e}")
        return None


if __name__ == "__main__":
    print("=" * 60)
    print("  模型发现测试")
    print("=" * 60)
    models = discover_all_models()
    print(f"\n共发现 {len(models)} 个模型：\n")
    for m in models:
        price_info = "★ 免费/试用" if m["input_price"] == 0 and m["output_price"] == 0 else \
                     f"输入¥{m['input_price'] * 1000:.2f}/百万 输出¥{m['output_price'] * 1000:.2f}/百万"
        quota_tag = " [有免费额度]" if m.get("has_free_quota") else ""
        print(f"  - {m['name']}{quota_tag}")
        print(f"    {price_info}")
        if m.get("description"):
            print(f"    {m['description']}")
        print()