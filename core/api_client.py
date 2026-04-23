import os
import time
from dotenv import load_dotenv

load_dotenv()

# ==================== 从 config.yaml 读取默认模型 ====================
def _load_default_model(role: str, fallback: str) -> str:
    """从 config.yaml 读取指定角色的默认模型名称"""
    try:
        from core.config_loader import get as cfg
        val = cfg("model", role, "default_model")
        if val and isinstance(val, str):
            return val.strip()
    except Exception:
        pass
    return fallback

_author_model = _load_default_model("author", "qwen3.6-flash")
_author_provider = "dashscope"

_reviewer_model = _load_default_model("reviewer", "qwen3.6-flash")
_reviewer_provider = "dashscope"

_reader_reviewer_model = _load_default_model("reader_reviewer", "qwen3.6-flash")
_reader_reviewer_provider = "dashscope"

_session_stats = {
    "total_calls": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cost_yuan": 0.0,
    "model_used": "",
}

_failure_stats = {
    "author_failures": 0,
    "reviewer_failures": 0,
    "reader_reviewer_failures": 0,
}

_switch_history = []
_available_models = None

# ==================== 已知免费试用模型（用于定价显示修正） ====================
FREE_TRIAL_MODEL_NAMES = {
    "qwen3.6-flash", "qwen3.6-flash-2026-04-16",
    "qwen3.6-35b-a3b",
    "qwen3.6-plus", "qwen3.6-plus-2026-04-02",
    "qwen3.5-35b-a3b", "qwen3.5-flash-2026-02-23",
    "glm-5.1", "glm-5",
    "gui-plus-2026-02-26",
    "qwen-flash-character-2026-02-26",
}

# DashScope 端点映射
_DASHSCOPE_ENDPOINTS = {
    "beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "intl":    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "us":      "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
}
# 运行时端点缓存（由验证成功后写入）
_working_endpoint: str = None


def _get_dashscope_base_url() -> str:
    """
    获取 DashScope 端点，优先级：
    1. 环境变量 DASHSCOPE_BASE_URL
    2. config.yaml 中 model.api_region（beijing / intl / us）
    3. 运行时已验证成功的端点缓存
    4. 兜底：北京节点（中国大陆用户默认可达）

    注意：去掉了 HEAD 探测逻辑，因为国际节点 HTTPS 握手会成功
    但 API 调用因区域不匹配而失败，探测结果没有意义。
    """
    # 1. 环境变量
    env_url = os.getenv("DASHSCOPE_BASE_URL", "").strip()
    if env_url:
        return env_url

    # 2. config.yaml
    try:
        from core.config_loader import get as cfg
        region = (cfg("model", "api_region") or "").strip().lower()
        if region in _DASHSCOPE_ENDPOINTS:
            return _DASHSCOPE_ENDPOINTS[region]
    except Exception:
        pass

    # 3. 运行时缓存（上次验证成功的节点）
    if _working_endpoint:
        return _working_endpoint

    # 4. 兜底：北京节点
    return _DASHSCOPE_ENDPOINTS["beijing"]


def get_available_models(refresh: bool = False, usage: str = None) -> dict:
    global _available_models
    if _available_models is None or refresh:
        try:
            from core.model_manager import discover_all_models, model_list_to_menu_format
            models = discover_all_models(refresh=refresh)
            _available_models = model_list_to_menu_format(models)
        except Exception as e:
            print(f"[警告] 动态模型发现失败，使用默认模型：{e}")
            _available_models = AVAILABLE_MODELS

    if usage:
        try:
            from core.model_manager import get_models_for_usage, model_list_to_menu_format
            filtered_models = get_models_for_usage(usage, top_k=8)
            return model_list_to_menu_format(filtered_models)
        except Exception as e:
            print(f"[警告] 模型筛选失败，返回所有模型：{e}")
            return _available_models

    return _available_models


def get_current_author_model() -> dict:
    models = get_available_models()
    for key, info in models.items():
        if info.get("model") == _author_model:
            return info
    for key, info in models.items():
        if _author_model in info.get("model", ""):
            return info
    return {"model": _author_model, "provider": _author_provider, "context_length": 0}


def get_model_pricing(model_name: str) -> dict:
    """
    获取模型定价。
    Bug修复: 已知免费试用模型强制返回 0 定价，
    避免 custom_models.json 中错误的付费价格影响显示。
    """
    # 已知免费试用模型，直接返回0（不被 custom_models.json 覆盖）
    if model_name in FREE_TRIAL_MODEL_NAMES:
        return {"input": 0.0, "output": 0.0}

    models = get_available_models()
    for key, info in models.items():
        if info.get("model") == model_name:
            return {
                "input": info.get("input_price", 0.0008),
                "output": info.get("output_price", 0.002),
            }
    if model_name in MODEL_PRICING:
        return MODEL_PRICING[model_name]
    return {"input": 0.0008, "output": 0.002}


MODEL_PRICING = {
    "qwen-turbo":               {"input": 0.0003,  "output": 0.0006},
    "qwen-plus":                {"input": 0.0008,  "output": 0.002},
    "qwen-max":                 {"input": 0.04,    "output": 0.12},
    "qwen-long":                {"input": 0.0005,  "output": 0.002},
    "qwen3.6-flash":            {"input": 0.0,     "output": 0.0},
    "qwen3.6-flash-2026-04-16": {"input": 0.0,     "output": 0.0},
    "qwen3.6-35b-a3b":          {"input": 0.0,     "output": 0.0},
    "qwen3.6-plus":             {"input": 0.0,     "output": 0.0},
    "qwen3.6-plus-2026-04-02":  {"input": 0.0,     "output": 0.0},
    "qwen3.5-35b-a3b":          {"input": 0.0,     "output": 0.0},
    "qwen3.5-flash-2026-02-23": {"input": 0.0,     "output": 0.0},
    "glm-5.1":                  {"input": 0.0,     "output": 0.0},
    "glm-5":                    {"input": 0.0,     "output": 0.0},
    "gui-plus-2026-02-26":      {"input": 0.0,     "output": 0.0},
    "qwen-flash-character-2026-02-26": {"input": 0.0, "output": 0.0},
    "gemini-2.5-flash":         {"input": 0.0,     "output": 0.0},
    "gemini-2.5-flash-lite":    {"input": 0.0,     "output": 0.0},
    "gemini-2.5-pro":           {"input": 0.0,     "output": 0.0},
}

FREE_TIER_MODELS = {"gemini-2.5-flash", "gemini-2.5-flash-lite"}

AVAILABLE_MODELS = {
    "1": {
        "name": "Qwen3.6 Flash（推荐，免费试用）",
        "model": "qwen3.6-flash",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": True,
    },
    "2": {
        "name": "Qwen3.6 Plus（高质量，免费试用）",
        "model": "qwen3.6-plus",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": True,
    },
    "3": {
        "name": "Qwen3.6 35B-A3B（开源大模型，免费试用）",
        "model": "qwen3.6-35b-a3b",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": True,
    },
    "4": {
        "name": "GLM-5.1（智谱旗舰，免费试用）",
        "model": "glm-5.1",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": True,
    },
    "5": {
        "name": "Qwen3.5 35B-A3B（稳定版，免费试用）",
        "model": "qwen3.5-35b-a3b",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": True,
    },
    "6": {
        "name": "通义千问 Plus（付费稳定）",
        "model": "qwen-plus",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": False,
    },
    "7": {
        "name": "通义千问 Turbo（速度快，适合批量）",
        "model": "qwen-turbo",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": False,
    },
    "8": {
        "name": "Gemini 2.5 Flash（Google免费模型）",
        "model": "gemini-2.5-flash",
        "provider": "gemini",
        "env_key": "GEMINI_API_KEY",
        "free_tier": True,
    },
}


def _get_api_key(*env_names: str) -> str:
    for env_name in env_names:
        value = os.getenv(env_name)
        if value is None:
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            return value
    return ""


def _format_api_error(error, api_name="API", attempt=1, max_attempts=3):
    error_str = str(error)
    error_lower = error_str.lower()

    if "connection error" in error_lower or "connecterror" in error_lower or "10061" in error_lower or "connection refused" in error_lower:
        category = "网络连接失败"
        suggestion = (
            "无法连接到 DashScope 端点。请尝试：\n"
            "  1. 在 config.yaml 中将 api_region 改为 beijing\n"
            "  2. 或在 .env 中设置：DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "  3. 检查网络/防火墙/代理设置"
        )
    elif "api_key_invalid" in error_lower or "api key not found" in error_lower or "invalid api key" in error_lower:
        category = "密钥无效"
        suggestion = "检查 DASHSCOPE_API_KEY 是否正确（格式：sk-xxxxx）"
    elif "401" in error_str or "unauthorized" in error_lower:
        category = "认证失败"
        suggestion = "请检查 .env 中的 DASHSCOPE_API_KEY 是否正确"
    elif "429" in error_str or "rate" in error_lower or "limit" in error_lower:
        category = "请求频率超限"
        suggestion = f"等待{10*(attempt)}秒后重试，或降低调用频率"
    elif "404" in error_str or "not found" in error_lower:
        category = "模型不存在或无权限"
        suggestion = "检查模型名称是否正确，确认该模型已开通试用额度"
    elif "400" in error_str:
        category = "请求参数错误"
        suggestion = "检查输入内容长度是否超过模型限制"
    elif "500" in error_str or "502" in error_str or "503" in error_str:
        category = "服务端错误"
        suggestion = "稍后重试，或切换到其他可用模型"
    elif "timeout" in error_lower or "timed out" in error_lower:
        category = "请求超时"
        suggestion = "检查网络连接，或增加超时时间"
    else:
        category = "未知错误"
        suggestion = "查看详细错误信息并联系支持"

    return {
        "category": category,
        "message": error_str,
        "suggestion": suggestion,
        "attempt_info": f"第 {attempt}/{max_attempts} 次尝试",
        "full_error": repr(error)
    }


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = get_model_pricing(model)
    cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1000
    return round(cost, 6)


def _update_stats(model: str, input_tokens: int, output_tokens: int, cost: float):
    _session_stats["total_calls"] += 1
    _session_stats["total_input_tokens"] += input_tokens
    _session_stats["total_output_tokens"] += output_tokens
    _session_stats["total_cost_yuan"] += cost
    _session_stats["model_used"] = model


def get_session_stats() -> dict:
    return _session_stats.copy()


def print_session_stats():
    s = _session_stats
    total_tokens = s["total_input_tokens"] + s["total_output_tokens"]
    cost = s["total_cost_yuan"]
    print("\n" + "=" * 50)
    print("  本次会话用量统计")
    print("=" * 50)
    print(f"  模型：{s['model_used']}")
    print(f"  调用次数：{s['total_calls']} 次")
    print(f"  输入 token：{s['total_input_tokens']:,}")
    print(f"  输出 token：{s['total_output_tokens']:,}")
    print(f"  总 token：{total_tokens:,}")
    print(f"  预估费用：¥{cost:.4f} 元")
    if cost < 0.01:
        print("  （费用极低，基本免费）")
    print("=" * 50)


# ==================== 三种独立模型管理 ====================

def get_current_model() -> str:
    return _author_model


def get_author_model() -> str:
    return _author_model


def get_reviewer_model() -> str:
    return _reviewer_model


def get_reader_reviewer_model() -> str:
    return _reader_reviewer_model


def set_author_model(model_name: str, provider: str = None):
    global _author_model, _author_provider
    old_model = _author_model
    _author_model = model_name
    if provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"
    _author_provider = provider
    _switch_history.append({"type": "author", "old_model": old_model, "new_model": model_name, "timestamp": time.time()})
    print(f"[模型切换] 作者模型已切换：{old_model} → {model_name}")


def set_reviewer_model(model_name: str, provider: str = None):
    global _reviewer_model, _reviewer_provider
    old_model = _reviewer_model
    _reviewer_model = model_name
    if provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"
    _reviewer_provider = provider
    _switch_history.append({"type": "reviewer", "old_model": old_model, "new_model": model_name, "timestamp": time.time()})
    print(f"[模型切换] 审核模型已切换：{old_model} → {model_name}")


def set_reader_reviewer_model(model_name: str, provider: str = None):
    global _reader_reviewer_model, _reader_reviewer_provider
    old_model = _reader_reviewer_model
    _reader_reviewer_model = model_name
    if provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"
    _reader_reviewer_provider = provider
    _switch_history.append({"type": "reader_reviewer", "old_model": old_model, "new_model": model_name, "timestamp": time.time()})
    print(f"[模型切换] 读者视角模型已切换：{old_model} → {model_name}")


def increment_failure_counter(counter_type: str):
    if counter_type in _failure_stats:
        _failure_stats[counter_type] += 1


def reset_failure_counter(counter_type: str = None):
    if counter_type:
        if counter_type in _failure_stats:
            _failure_stats[counter_type] = 0
    else:
        for k in _failure_stats:
            _failure_stats[k] = 0


def get_failure_stats() -> dict:
    return _failure_stats.copy()


def get_switch_history() -> list:
    return _switch_history.copy()


def check_switch_needed(counter_type: str) -> bool:
    from core.config_loader import get as cfg
    threshold = cfg("novel", "failure_switch_threshold", 3)
    return _failure_stats.get(counter_type, 0) >= threshold


# ==================== 统一调用入口 ====================

def call_api(system_prompt: str, user_message: str,
             model_name: str = None,
             provider: str = None,
             max_tokens: int = None,
             temperature: float = None,
             retry: int = 3) -> str:
    from core.config_loader import get as cfg
    if max_tokens is None:
        max_tokens = cfg("model", "max_tokens", 4096)
    if temperature is None:
        temperature = 0.85

    if model_name is None:
        model_name = _author_model
        provider = _author_provider
    elif provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"

    if provider == "dashscope":
        return _call_dashscope(system_prompt, user_message, model_name, max_tokens, temperature, retry)
    elif provider == "gemini":
        return _call_gemini(system_prompt, user_message, model_name, max_tokens, temperature, retry)
    else:
        raise ValueError(f"未知的provider: {provider}")


def _is_thinking_model(model_name: str) -> bool:
    """判断是否为深度思考/混合思考模型"""
    thinking_patterns = [
        "qwen3.6-", "qwen3.5-35b", "qwen3.5-397b", "qwen3.5-120b",
        "qwen3-next", "qwq-",
        "glm-5", "glm-4.7", "glm-4.6", "glm-4.5",
        "deepseek-r1", "deepseek-v3.2",
        "kimi-k2", "minimax-m2.5", "minimax-m2.1",
    ]
    model_lower = model_name.lower()
    return any(p.lower() in model_lower for p in thinking_patterns)


def _call_dashscope(system_prompt, user_message, model_name, max_tokens, temperature, retry):
    """
    使用 OpenAI 兼容接口调用 DashScope。
    Bug修复：改用 while 循环 + 独立端点切换标志，
    彻底解决 retry=1 时端点切换后无重试机会的问题。
    """
    try:
        from openai import OpenAI
    except ImportError:
        return _call_dashscope_sdk(system_prompt, user_message, model_name, max_tokens, temperature, retry)

    api_key = _get_api_key("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError(
            "未找到 DASHSCOPE_API_KEY，请在 .env 文件中设置：\n"
            "  DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx"
        )

    is_thinking = _is_thinking_model(model_name)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    base_url = _get_dashscope_base_url()
    endpoint_switched = False  # 端点只切换一次，且不消耗 retry 次数

    def make_client(url):
        return OpenAI(api_key=api_key, base_url=url)

    client = make_client(base_url)
    attempt = 0

    while attempt < retry:
        try:
            # Bug修复8: GLM系列不支持 enable_thinking 字段（会报400）
            # Qwen3.x 系列用 extra_body 关闭思考模式，节省token
            is_qwen_thinking = is_thinking and not any(
                p in model_name.lower() for p in ("glm-", "gui-plus")
            )
            kwargs = dict(
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if is_qwen_thinking:
                kwargs["extra_body"] = {"enable_thinking": False}

            response = client.chat.completions.create(**kwargs)
            resp_content = response.choices[0].message.content or ""

            try:
                usage = response.usage
                input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", 0) or 0
                cost = _calc_cost(model_name, input_tokens, output_tokens)
                _update_stats(model_name, input_tokens, output_tokens, cost)
            except Exception:
                pass

            # 调用成功：缓存本次使用的端点
            global _working_endpoint
            _working_endpoint = base_url
            return resp_content

        except Exception as e:
            err_str = str(e).lower()

            # 连接/超时错误：只切换一次端点，不计入 retry 次数
            is_conn_err = any(kw in err_str for kw in (
                "connection error", "connecterror", "connection refused",
                "network", "timed out", "timeout", "connect timeout"
            ))
            if is_conn_err and not endpoint_switched:
                if "intl" in base_url or "-us." in base_url:
                    fallback = _DASHSCOPE_ENDPOINTS["beijing"]
                else:
                    fallback = _DASHSCOPE_ENDPOINTS["intl"]
                print(f"  [端点切换] {base_url} 连接失败，自动切换 → {fallback}")
                base_url = fallback
                client = make_client(base_url)
                endpoint_switched = True
                # 不增加 attempt，直接用新端点重试
                continue

            # 限速错误
            if any(kw in err_str for kw in ("429", "rate", "limit", "quota")):
                wait = 30 * (attempt + 1)
                print(f"  [限速] DashScope API限速，等待{wait}秒后重试...")
                time.sleep(wait)
                attempt += 1
                continue

            # 其他错误
            err_info = _format_api_error(e, "DashScope", attempt + 1, retry)
            print(f"\n❌ [{err_info['category']}] DashScope")
            print(f"   模型：{model_name}  端点：{base_url}")
            print(f"   详情: {err_info['message'][:300]}")
            print(f"   建议: {err_info['suggestion']}")
            attempt += 1
            if attempt < retry:
                print(f"   第 {attempt}/{retry} 次重试...")
                time.sleep(2 ** (attempt - 1))
            else:
                raise RuntimeError(f"DashScope API连续失败{retry}次: {e}")

    raise RuntimeError(f"DashScope API连续失败{retry}次")


def _call_dashscope_sdk(system_prompt, user_message, model_name, max_tokens, temperature, retry):
    """旧版 DashScope SDK 调用（兜底方案，openai 库未安装时使用）"""
    from dashscope import Generation

    api_key = _get_api_key("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("未找到 DASHSCOPE_API_KEY，请检查 .env 文件")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    is_thinking = _is_thinking_model(model_name)

    for attempt in range(retry):
        try:
            call_kwargs = dict(
                api_key=api_key,
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                result_format="message",
            )
            if is_thinking:
                try:
                    call_kwargs["enable_thinking"] = False
                    response = Generation.call(**call_kwargs)
                except TypeError:
                    del call_kwargs["enable_thinking"]
                    response = Generation.call(**call_kwargs)
            else:
                response = Generation.call(**call_kwargs)

            if response.status_code == 200:
                usage = response.usage or {}
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                cost = _calc_cost(model_name, input_tokens, output_tokens)
                _update_stats(model_name, input_tokens, output_tokens, cost)
                return response.output.choices[0].message.content
            elif response.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  [限速] API限速，等待{wait}秒后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"API错误: {response.status_code} - {response.message}")
        except RuntimeError:
            raise
        except Exception as e:
            err_info = _format_api_error(e, "DashScope(SDK)", attempt + 1, retry)
            print(f"\n❌ [{err_info['category']}] DashScope(SDK)")
            print(f"   详情: {err_info['message'][:200]}")
            print(f"   建议: {err_info['suggestion']}")
            if attempt < retry - 1:
                print(f"   {err_info['attempt_info']}...")
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"API连续失败{retry}次: {e}")

    raise RuntimeError(f"API连续失败{retry}次")


def _call_gemini(system_prompt, user_message, model_name, max_tokens, temperature, retry):
    from google import genai

    api_key = _get_api_key("GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("未找到 GEMINI_API_KEY 或 GOOGLE_API_KEY，请检查 .env 文件")

    is_free = model_name in FREE_TIER_MODELS
    client = genai.Client(api_key=api_key)
    full_prompt = f"{system_prompt}\n\n---\n\n{user_message}"

    for attempt in range(retry):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )
            try:
                input_tokens = response.usage_metadata.prompt_token_count or 0
                output_tokens = response.usage_metadata.candidates_token_count or 0
                cost = _calc_cost(model_name, input_tokens, output_tokens)
                _update_stats(model_name, input_tokens, output_tokens, cost)
            except Exception:
                pass
            if is_free:
                time.sleep(4)
            return response.text

        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("429", "quota", "resource_exhausted", "rate")):
                wait = 65 if is_free else 30 * (attempt + 1)
                print(f"  [限速] Gemini API限速，等待{wait}秒后重试...")
                time.sleep(wait)
                continue

            err_info = _format_api_error(e, "Gemini", attempt + 1, retry)
            print(f"\n❌ [{err_info['category']}] Gemini")
            print(f"   详情: {err_info['message'][:200]}")
            print(f"   建议: {err_info['suggestion']}")
            if attempt < retry - 1:
                print(f"   {err_info['attempt_info']}...")
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"Gemini API连续失败{retry}次: {e}")

    raise RuntimeError(f"Gemini API连续失败{retry}次")


# ==================== 三种独立调用入口 ====================

def call_author_api(system_prompt: str, user_message: str,
                   max_tokens: int = None, temperature: float = None, retry: int = 3) -> str:
    return call_api(system_prompt=system_prompt, user_message=user_message,
                    model_name=_author_model, provider=_author_provider,
                    max_tokens=max_tokens, temperature=temperature, retry=retry)


def call_reviewer_api(system_prompt: str, user_message: str,
                     max_tokens: int = None, temperature: float = None, retry: int = 3) -> str:
    return call_api(system_prompt=system_prompt, user_message=user_message,
                    model_name=_reviewer_model, provider=_reviewer_provider,
                    max_tokens=max_tokens, temperature=temperature, retry=retry)


def call_reader_reviewer_api(system_prompt: str, user_message: str,
                            max_tokens: int = None, temperature: float = None, retry: int = 3) -> str:
    return call_api(system_prompt=system_prompt, user_message=user_message,
                    model_name=_reader_reviewer_model, provider=_reader_reviewer_provider,
                    max_tokens=max_tokens, temperature=temperature, retry=retry)


# ==================== 交互式模型选择 ====================

def select_model_interactive() -> dict:
    global _author_model, _author_provider
    global _reviewer_model, _reviewer_provider
    global _reader_reviewer_model, _reader_reviewer_provider

    print("\n" + "=" * 50)
    print("  请选择写作模型（作者模型）")
    print("=" * 50)

    models = get_available_models(usage="author")
    for key, info in models.items():
        api_key_val = os.getenv(info["env_key"], "")
        has_key = api_key_val and len(api_key_val) > 10
        status = "✓ Key已填写" if has_key else "✗ Key未填写"
        # Bug修复: 显示价格时使用修正后的定价（试用模型强制显示免费）
        pricing = get_model_pricing(info["model"])
        inp = pricing.get("input", 0)
        out = pricing.get("output", 0)
        if inp == 0.0 and out == 0.0:
            price_str = "★ 免费/试用额度"
        else:
            price_str = f"输入¥{inp * 1000:.2f}/百万token  输出¥{out * 1000:.2f}/百万token"
        print(f"  {key}. {info['name']:<38} [{status}]")
        print(f"      {price_str}")

    default_choice = "1"
    for key, info in models.items():
        if info.get("model") == _author_model:
            default_choice = key
            break

    print()
    choice = input(f"请选择作者模型（直接回车默认选{default_choice}，当前：{_author_model}）：").strip() or default_choice

    if choice not in models:
        print("[提示] 无效选择，使用默认模型")
        choice = default_choice

    selected = models[choice]

    api_key_val = os.getenv(selected["env_key"], "")
    if not api_key_val or len(api_key_val) < 10:
        print(f"\n[错误] 未找到 {selected['env_key']}，请在 .env 文件中填写：")
        print(f"  {selected['env_key']}=sk-xxxxxxxx")
        print("请重新选择\n")
        return select_model_interactive()

    _author_model = selected["model"]
    _author_provider = selected["provider"]
    _session_stats["model_used"] = selected["model"]

    _INITIAL_REVIEWER = _load_default_model("reviewer", "qwen3.6-flash")
    _INITIAL_READER = _load_default_model("reader_reviewer", "qwen3.6-flash")
    reviewer_is_default = (_reviewer_model == _INITIAL_REVIEWER)
    reader_is_default = (_reader_reviewer_model == _INITIAL_READER)

    if reviewer_is_default:
        _reviewer_model = _INITIAL_REVIEWER
        _reviewer_provider = "dashscope"
    if reader_is_default:
        _reader_reviewer_model = _INITIAL_READER
        _reader_reviewer_provider = "dashscope"

    print(f"\n[OK] 作者模型已选择：{selected['name']}")
    print(f"     模型代码：{selected['model']}")
    if selected["provider"] == "dashscope":
        print(f"     API端点：{_get_dashscope_base_url()}")
    print(f"     审核模型：{_reviewer_model}{'（默认）' if reviewer_is_default else '（已保留自定义）'}")
    print(f"     读者视角模型：{_reader_reviewer_model}{'（默认）' if reader_is_default else '（已保留自定义）'}")
    if selected.get("free_tier"):
        print("     [试用额度] 使用免费试用 Token，请注意余量")
    print("     正在验证模型可用性...")

    try:
        call_author_api(system_prompt="你是助手。", user_message="回复ok两个字",
                        max_tokens=10, temperature=0.1, retry=1)
        print("     [✓ 验证通过] 模型响应正常\n")
    except Exception as e:
        print(f"     [✗ 验证失败] {e}")
        print("     该模型当前不可用，请重新选择\n")
        return select_model_interactive()

    return selected


def select_all_models_interactive():
    print("\n" + "=" * 60)
    print("  高级模型配置 - 分别选择三种模型")
    print("=" * 60)

    print("\n【1/3】选择作者模型（用于生成章节内容）")
    author_choice = _select_single_model("作者模型", default="1", usage="author")
    set_author_model(author_choice["model"], author_choice["provider"])

    print("\n【2/3】选择审核模型（用于责任编辑审稿）")
    reviewer_choice = _select_single_model("审核模型", default="1", usage="reviewer")
    set_reviewer_model(reviewer_choice["model"], reviewer_choice["provider"])

    print("\n【3/3】选择读者视角模型（用于读者视角评估）")
    reader_choice = _select_single_model("读者视角模型", default="1", usage="reader_reviewer")
    set_reader_reviewer_model(reader_choice["model"], reader_choice["provider"])

    print("\n" + "=" * 60)
    print("  模型配置完成")
    print("=" * 60)
    print(f"  作者模型：{_author_model}")
    print(f"  审核模型：{_reviewer_model}")
    print(f"  读者视角模型：{_reader_reviewer_model}")


def _select_single_model(prompt_title: str, default: str, usage: str = None) -> dict:
    print("-" * 60)
    models = get_available_models(usage=usage)
    for key, info in models.items():
        api_key_val = os.getenv(info["env_key"], "")
        has_key = api_key_val and len(api_key_val) > 10
        status = "✓ Key已填写" if has_key else "✗ Key未填写"
        pricing = get_model_pricing(info["model"])
        inp = pricing.get("input", 0)
        out = pricing.get("output", 0)
        if inp == 0.0 and out == 0.0:
            price_str = "★ 免费/试用额度"
        else:
            price_str = f"输入¥{inp * 1000:.2f}/百万  输出¥{out * 1000:.2f}/百万"
        print(f"  {key}. {info['name']:<32} [{status}]")
        print(f"      {price_str}")

    print()
    choice = input(f"请选择{prompt_title}（直接回车默认选{default}）：").strip() or default

    if choice not in models:
        print("[提示] 无效选择，使用默认")
        choice = default

    selected = models[choice]

    api_key_val = os.getenv(selected["env_key"], "")
    if not api_key_val or len(api_key_val) < 10:
        print(f"\n[错误] 未找到 {selected['env_key']}")
        return _select_single_model(prompt_title, default)

    print(f"  正在验证 {selected['name']}...")
    try:
        call_api(system_prompt="你是助手。", user_message="回复ok",
                 model_name=selected["model"], provider=selected["provider"],
                 max_tokens=10, temperature=0.1, retry=1)
        print(f"  [✓] {selected['name']} 验证通过")
    except Exception as e:
        print(f"  [✗] 验证失败：{e}")
        return _select_single_model(prompt_title, default)

    return selected