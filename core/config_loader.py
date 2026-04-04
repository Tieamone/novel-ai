import yaml
from pathlib import Path

_config = None


def load_config() -> dict:
    global _config
    if _config is not None:
        return _config

    config_path = Path("config.yaml")
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
    config = load_config()
    if not args and default is None:
        return config.get(section, {}).get(key)
    if not args:
        return config.get(section, {}).get(key, default)
    *nested_keys, fallback = args
    if default is not None:
        fallback = default
    current = config.get(section, {})
    if not isinstance(current, dict):
        return fallback
    current = current.get(key, fallback)
    for nk in nested_keys:
        if not isinstance(current, dict):
            return fallback
        current = current.get(nk, fallback)
    return current


def get_data_dir(novel_name: str = "") -> Path:
    base = Path(get("paths", "data_dir", "data"))
    return base / novel_name if novel_name else base


def get_output_dir(novel_name: str = "") -> Path:
    base = Path(get("paths", "output_dir", "output"))
    return base / novel_name if novel_name else base


def _get_defaults() -> dict:
    return {
        "model": {
            "max_tokens": 4096,
        },
        "novel": {
            "chapter_word_target": 3000,
            "max_retry": 3,
            "recent_summary_count": 5,
            "compress_after_chapters": 20,
            "pre_split_chapters": 50,
        },
        "paths": {
            "data_dir": "data",
            "output_dir": "output",
        }
    }
