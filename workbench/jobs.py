"""串行运行工作台任务，供浏览器查看进度并提交人工选择。"""

import os
from collections import deque
from threading import Condition, Lock, Thread
from uuid import uuid4

from core.interaction import using_interaction


ACTIVE_STATES = {"running", "waiting", "stopping"}


class Cancelled(BaseException):
    """跳出业务层的普通错误重试，由任务入口恢复状态。"""


class Job:
    def __init__(self, title, novel):
        self.id = uuid4().hex
        self.title = title
        self.novel = novel
        self.status = "running"
        self.result = None
        self.error = None
        self.prompt = None
        self.log = deque(maxlen=350)
        self.sequence = 0
        self.prompt_id = 0
        self.reply = None
        self.cancelled = False
        self.changed = Condition()
        self.secrets = [
            value for key, value in os.environ.items()
            if key.endswith("_API_KEY") and len(value) >= 10
        ]

    def redact(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "已隐藏")
        return value

    def write(self, message):
        message = self.redact(message.strip())
        if not message:
            return
        with self.changed:
            self.sequence += 1
            self.log.append({"id": self.sequence, "text": message[:8000]})

    def check_cancelled(self):
        with self.changed:
            if self.cancelled:
                raise Cancelled()

    def ask(self, text, options, multiline, context):
        with self.changed:
            self.check_cancelled()
            self.prompt_id += 1
            self.prompt = {
                "id": self.prompt_id,
                "text": self.redact(text.strip()) or "请选择后续操作",
                "options": options,
                "multiline": multiline,
                "context": self.redact(context[:12000]) if context else
                "\n".join(row["text"] for row in list(self.log)[-16:])[-4200:],
            }
            self.reply = None
            self.status = "waiting"
            self.changed.wait_for(lambda: self.reply is not None or self.cancelled)
            self.prompt = None
            self.check_cancelled()
            self.status = "running"
            return self.reply

    def answer(self, prompt_id, value):
        with self.changed:
            if (self.status != "waiting" or not self.prompt
                    or self.prompt["id"] != prompt_id or self.reply is not None):
                raise ValueError("该确认已处理，请刷新任务状态")
            if self.prompt["options"] and value not in {pair[0] for pair in self.prompt["options"]}:
                raise ValueError("请选择当前确认中的选项")
            self.reply = value
            self.changed.notify_all()

    def cancel(self):
        with self.changed:
            if self.status in ACTIVE_STATES:
                self.cancelled = True
                self.status = "stopping"
                self.prompt = None
                self.changed.notify_all()

    def snapshot(self, after=0):
        with self.changed:
            return {
                "id": self.id, "title": self.title, "novel": self.novel,
                "status": self.status, "result": self.result, "error": self.error,
                "prompt": self.prompt, "sequence": self.sequence,
                "log": [row for row in self.log if row["id"] > after],
            }

    def run(self, action):
        status, result, error = "failed", None, None
        try:
            with using_interaction(self):
                self.check_cancelled()
                result = action()
            status = "completed"
        except Cancelled:
            status = "cancelled"
        except ValueError as exc:
            error = self.redact(str(exc))
        except Exception as exc:
            self.write(f"任务异常：{type(exc).__name__}：{exc}")
            error = "操作未完成，请查看任务记录"
        finally:
            with self.changed:
                self.status, self.result, self.error = status, result, error
                self.prompt = None
                self.changed.notify_all()


class Jobs:
    def __init__(self):
        self.lock = Lock()
        self.items = {}
        self.latest = None

    def start(self, title, novel, action):
        with self.lock:
            if self.latest and self.latest.snapshot()["status"] in ACTIVE_STATES:
                raise ValueError("已有操作正在执行，请先处理当前任务")
            if len(self.items) >= 12:
                self.items.pop(next(iter(self.items)))
            job = Job(title, novel)
            self.items[job.id] = self.latest = job
            Thread(target=job.run, args=(action,), daemon=True).start()
            return job.snapshot()

    def get(self, job_id):
        with self.lock:
            return self.items.get(job_id)

    def current(self, known=None):
        with self.lock:
            return self.latest.snapshot() if self.latest and self.latest.id != known else None
