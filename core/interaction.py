"""命令行和工作台共用的交互入口，交互对象仅在当前任务内生效。"""

import builtins
from contextlib import contextmanager
from contextvars import ContextVar


_interaction = ContextVar("interaction", default=None)


@contextmanager
def using_interaction(interaction):
    token = _interaction.set(interaction)
    try:
        yield
    finally:
        _interaction.reset(token)


def ask(prompt="", *, options=(), multiline=False, context=""):
    interaction = _interaction.get()
    if interaction is None:
        return builtins.input(prompt)
    return interaction.ask(prompt, options, multiline, context)


def read_block():
    interaction = _interaction.get()
    if interaction is not None:
        return interaction.ask("请输入完整内容", (), True, "").strip()
    lines = []
    while True:
        try:
            line = builtins.input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def say(*values, sep=" ", end="\n", file=None, flush=False):
    interaction = _interaction.get()
    if interaction is None or file is not None:
        return builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
    interaction.write((sep if sep is not None else " ").join(map(str, values))
                      + (end if end is not None else "\n"))


def check_cancelled():
    interaction = _interaction.get()
    if interaction is not None:
        interaction.check_cancelled()
