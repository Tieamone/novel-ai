import yaml
from pathlib import Path

_config = None
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_project_root() -> Path:
    return PROJECT_ROOT


def resolve_project_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config() -> dict:
    global _config
    if _config is not None:
        return _config

    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        _config = _get_defaults()
        return _config

    try:
        with open(config_path, encoding="utf-8") as f:
            _config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[警告] config.yaml 读取失败，使用默认配置: {e}")
        _config = _get_defaults()

    return _config


def get(section: str, key: str, *args, default=None):
    """
    安全的多级 key 访问，任一层不是 dict 或 key 不存在都返回默认值。
    Bug修复12: 原版在中间层为 None 时会 TypeError，现在每层都做 isinstance 检查。

    用法：
        get("model", "max_tokens")              → config["model"]["max_tokens"]
        get("model", "max_tokens", 4096)        → 带默认值
        get("model", "reader_reviewer", "pass_threshold", 75)  → 三层嵌套
        get("model", "reader_reviewer", "pass_threshold", default=75)  → 同上
    """
    config = load_config()

    # 确定最终 fallback
    if args:
        *nested_keys, fallback = args
        if default is not None:
            fallback = default
    else:
        nested_keys = []
        fallback = default

    # 第一层
    val = config.get(section)
    if val is None:
        return fallback

    # 第二层
    if not isinstance(val, dict):
        return fallback
    val = val.get(key)
    if val is None:
        return fallback

    # 后续嵌套层
    for nk in nested_keys:
        if not isinstance(val, dict):
            return fallback
        val = val.get(nk)
        if val is None:
            return fallback

    return val


def get_data_dir(novel_name: str = "") -> Path:
    base = resolve_project_path(get("paths", "data_dir", "data"))
    return base / novel_name if novel_name else base


def get_output_dir(novel_name: str = "") -> Path:
    base = resolve_project_path(get("paths", "output_dir", "output"))
    return base / novel_name if novel_name else base


def _get_defaults() -> dict:
    return {
        "model": {
            "max_tokens": 4096,
            "api_region": "beijing",
            "author": {"default_model": "qwen3.6-flash"},
            "reviewer": {"default_model": "qwen3.6-flash"},
            "reader_reviewer": {
                "default_model": "qwen3.6-flash",
                "enabled": True,
                "pass_threshold": 75,
            },
        },
        "novel": {
            "chapter_word_target": 3000,
            "chapter_word_min": 2500,
            "chapter_word_max": 3500,
            "max_retry": 3,
            "recent_summary_count": 5,
            "compress_after_chapters": 20,
            "pre_split_chapters": 50,
            "failure_switch_threshold": 3,
            "progress_review_window": 10,
            "plateau_threshold": 5,
            "plateau_window": 3,
        },
        "paths": {
            "data_dir": "data",
            "output_dir": "output",
        }
    }
