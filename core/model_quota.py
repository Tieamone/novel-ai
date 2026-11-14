"""百炼账号额度快照：仅在启动或打开模型菜单时按需同步。"""

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from core.config_loader import get_data_dir, get_project_root

_lock = Lock()
_error = ""


def now():
    return datetime.now(timezone.utc)


def cache_path():
    return get_data_dir() / "model_quota_cache.json"


def validate_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("额度结果不是列表")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("model"), str) or not row["model"]:
            raise ValueError("额度结果缺少模型名称")
        if row["model"] in seen:
            raise ValueError("额度结果包含重复模型")
        seen.add(row["model"])
        if not isinstance(row.get("type"), str):
            raise ValueError("额度结果缺少模型类型")
        for key in ("remaining", "total"):
            if type(row.get(key)) not in (int, float) or not 0 <= row[key] < float("inf"):
                raise ValueError("额度数值无效")
        datetime.strptime(row["expires"], "%Y-%m-%d")
        if type(row.get("autoStop")) is not bool:
            raise ValueError("用完即停状态无效")
    return rows


def read_snapshot():
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        if data.get("region") != "cn-beijing":
            return None
        stamp = datetime.fromisoformat(data["synced_at"])
        if stamp.tzinfo is None:
            return None
        validate_rows(data["models"])
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def is_stale(snapshot):
    if not snapshot:
        return True
    age = now() - datetime.fromisoformat(snapshot["synced_at"])
    return age < timedelta(0) or age >= timedelta(hours=24)


def query_cli():
    executable = shutil.which("bl.cmd" if os.name == "nt" else "bl")
    fallback = Path("D:/Tools/bailian-cli/bl.cmd")
    if not executable and fallback.is_file():
        executable = str(fallback)
    if not executable:
        raise ValueError("未找到百炼 CLI，请安装并将 bl 加入 PATH")
    result = subprocess.run(
        [executable, "usage", "free", "--all", "--console-region", "cn-beijing", "--output", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        cwd=str(get_project_root()),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        # 不回显 CLI 原始响应，避免错误日志包含登录凭据。
        detail = (result.stderr + result.stdout).lower()
        if any(word in detail for word in ("login", "auth", "credential", "401", "403", "登录")):
            raise ValueError("控制台登录不可用，请在终端执行 bl auth login --console 后重试")
        raise ValueError(f"额度查询失败（退出码 {result.returncode}），请检查网络或控制台登录")
    return validate_rows(json.loads(result.stdout))


def sync_quota(force=False):
    global _error
    with _lock:
        snapshot = read_snapshot()
        if not force and not is_stale(snapshot):
            return snapshot
        print("[免费额度] 正在查询百炼北京区额度（最多等待 30 秒）…", flush=True)
        try:
            rows = query_cli()
            snapshot = {"region": "cn-beijing", "synced_at": now().isoformat(), "models": rows}
            path = cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            _error = ""
            print(f"[免费额度] 已同步 {len(rows)} 个模型，仅更新候选列表")
        except subprocess.TimeoutExpired:
            _error = "额度查询超时，请检查网络后重试"
        except (OSError, ValueError, TypeError, KeyError) as error:
            _error = str(error) if type(error) is ValueError else "额度读取或保存失败，请检查 CLI、数据格式与目录权限"
        if _error:
            print(f"[免费额度] {_error}；保留上次成功快照")
        return read_snapshot()


def quota_status():
    snapshot = read_snapshot()
    return {"synced_at": snapshot["synced_at"] if snapshot else None,
            "stale": is_stale(snapshot), "error": _error, "region": "cn-beijing"}


def merge_quota(models):
    snapshot = read_snapshot()
    if not snapshot:
        return []
    # 静态配置只补充已由 CLI 确认的模型参数，不再提供候选资格。
    metadata = {model["model"]: model for model in models if model.get("provider") == "dashscope"}
    today = now().astimezone(timezone(timedelta(hours=8))).date().isoformat()
    result = []
    for row in snapshot["models"]:
        if row["type"] != "Text" or row["remaining"] <= 0 or row["expires"] < today:
            continue
        model = dict(metadata.get(row["model"], {
            "model": row["model"], "name": row["model"], "provider": "dashscope",
            "context_length": 0, "max_output_tokens": None,
        }))
        model.update(has_free_quota=True, quota_status="有额度", quota=row)
        result.append(model)
    return result



def quota_label(model):
    row = model.get("quota")
    if not row:
        return "免费额度未知"
    stop = "已开启" if row["autoStop"] else "未开启"
    return (f"{model['quota_status']} · 剩余 {row['remaining']:,} / {row['total']:,}"
            f" · 到期 {row['expires']} · 用完即停{stop}")
