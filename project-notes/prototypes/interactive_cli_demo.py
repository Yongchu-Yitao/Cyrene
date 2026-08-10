#!/usr/bin/env python3
"""Runnable visual prototype for the proposed ``cyrene chat`` experience.

This demo is intentionally disconnected from the Cyrene daemon. It performs no
model calls and executes no tools; it only demonstrates terminal interaction.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import textwrap
import time
import unicodedata
from dataclasses import dataclass


ESC = "\x1b["


@dataclass(frozen=True)
class Theme:
    accent: str = "38;5;117"
    accent_strong: str = "38;5;81"
    text: str = "38;5;252"
    muted: str = "38;5;245"
    faint: str = "38;5;239"
    success: str = "38;5;114"
    warning: str = "38;5;221"
    danger: str = "38;5;203"
    panel: str = "38;5;238"
    bold: str = "1"
    dim: str = "2"


class UI:
    def __init__(self, *, color: bool, speed: float) -> None:
        self.color = color
        self.speed = max(0.0, speed)
        self.theme = Theme()
        self.width = max(52, min(shutil.get_terminal_size((88, 24)).columns, 96))

    def style(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return f"{ESC}{';'.join(codes)}m{text}{ESC}0m"

    def pause(self, seconds: float = 0.35) -> None:
        time.sleep(seconds * self.speed)

    @staticmethod
    def cell_width(text: str) -> int:
        """Return the terminal cell width for printable, unstyled text."""
        return sum(
            2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
            for char in text
            if not unicodedata.combining(char)
        )

    def rule(self, label: str = "") -> None:
        if label:
            prefix = f"─ {label} "
            line = prefix + "─" * max(1, self.width - self.cell_width(prefix))
        else:
            line = "─" * self.width
        print(self.style(line, self.theme.faint))

    def wrapped(self, text: str, *, indent: str = "", width: int | None = None) -> list[str]:
        return textwrap.wrap(
            text,
            width=max(24, (width or self.width) - len(indent)),
            initial_indent=indent,
            subsequent_indent=indent,
            replace_whitespace=False,
        ) or [indent]

    def header(self) -> None:
        brand = self.style("CYRENE", self.theme.accent_strong, self.theme.bold)
        product = self.style("交互式 Agent", self.theme.text)
        state = self.style("● 已连接", self.theme.success)
        mode = self.style("run_live  ·  default", self.theme.muted)
        print()
        print(f"{brand}  {product}")
        print(f"{mode}  {state}")
        self.rule()
        print(
            self.style(
                "直接输入任务，或选择下面的交互场景",
                self.theme.muted,
            )
        )
        print()

    def prompt(self, label: str = "你") -> str:
        marker = self.style("›", self.theme.accent_strong, self.theme.bold)
        try:
            return input(f"{self.style(label, self.theme.text, self.theme.bold)} {marker} ").strip()
        except EOFError:
            return "/exit"

    def status(self, label: str, detail: str = "", *, symbol: str = "◆", tone: str | None = None) -> None:
        tone = tone or self.theme.accent
        rendered_symbol = self.style(symbol, tone)
        rendered_label = self.style(label, self.theme.text, self.theme.bold)
        suffix = f"  {self.style(detail, self.theme.muted)}" if detail else ""
        print(f"{rendered_symbol} {rendered_label}{suffix}")

    def tool(self, name: str, detail: str, *, state: str = "done", duration: str = "") -> None:
        if state == "running":
            symbol, tone = "◌", self.theme.accent
        elif state == "error":
            symbol, tone = "×", self.theme.danger
        else:
            symbol, tone = "✓", self.theme.success
        left = f"  {self.style(symbol, tone)} {name:<20}"
        right = self.style(detail, self.theme.muted)
        elapsed = f"  {self.style(duration, self.theme.faint)}" if duration else ""
        print(f"{left}{right}{elapsed}")

    def stream_reply(self, text: str) -> None:
        print()
        label = self.style("Cyrene", self.theme.accent_strong, self.theme.bold)
        marker = self.style("›", self.theme.accent_strong, self.theme.bold)
        print(f"{label} {marker} ", end="", flush=True)
        chunks = text.split(" ")
        for index, chunk in enumerate(chunks):
            suffix = "" if index == len(chunks) - 1 else " "
            print(chunk + suffix, end="", flush=True)
            self.pause(0.055)
        print()

    def panel(self, title: str, lines: list[str], *, tone: str) -> None:
        inner = self.width - 4
        title_text = f" {title} "
        top_fill = max(1, inner - self.cell_width(title_text))
        print(
            self.style("╭─", tone)
            + self.style(title_text, tone, self.theme.bold)
            + self.style("─" * top_fill + "╮", tone)
        )
        for raw in lines:
            wrapped = self.wrapped(raw, width=inner)
            for line in wrapped:
                padding = " " * max(0, inner - self.cell_width(line))
                print(self.style("│ ", tone) + line + padding + self.style(" │", tone))
        print(self.style("╰" + "─" * (inner + 2) + "╯", tone))

    def spinner(self, label: str, *, seconds: float = 0.65) -> None:
        frames = ("◐", "◓", "◑", "◒")
        if not sys.stdout.isatty() or self.speed == 0:
            self.status(label, symbol="◌")
            return
        deadline = time.monotonic() + seconds * self.speed
        index = 0
        while time.monotonic() < deadline:
            frame = self.style(frames[index % len(frames)], self.theme.accent)
            text = self.style(label, self.theme.muted)
            print(f"\r{frame} {text}", end="", flush=True)
            time.sleep(0.08)
            index += 1
        print("\r" + " " * min(self.width, len(label) + 4) + "\r", end="", flush=True)


class Demo:
    def __init__(self, ui: UI, *, auto: bool = False) -> None:
        self.ui = ui
        self.auto = auto
        self.mode = "default"

    def activity_demo(self, task: str = "检查当前项目的命令行启动方式") -> None:
        self.ui.rule("运行")
        self.ui.status("理解任务", task, symbol="◇")
        self.ui.spinner("准备工具")
        self.ui.status("执行", "3 项活动", symbol="◆")

        self.ui.tool("discover", "代码与运行时工具", state="running")
        self.ui.pause()
        self.ui.tool("discover", "代码与运行时工具", duration="0.3s")

        self.ui.tool("search_files", "CLI 启动入口", state="running")
        self.ui.pause(0.55)
        self.ui.tool("search_files", "17 个匹配", duration="0.5s")

        self.ui.tool("read_file", "pyproject.toml · cli.py · host.py", state="running")
        self.ui.pause(0.7)
        self.ui.tool("read_file", "3 个文件", duration="0.7s")

        self.ui.status("正在汇总", "分析 → 回复", symbol="→", tone=self.ui.theme.accent)
        self.ui.stream_reply(
            "项目当前有 Web daemon、HTTP client 和本地 REPL 三条启动路径。"
            "建议新增 cyrene chat，复用 daemon 的 NDJSON stream，让回复、"
            "工具进度和确认请求在同一个终端会话里实时呈现。"
        )
        print(self.ui.style("  3 个工具  ·  1.5s  ·  run_live", self.ui.theme.faint))
        print()

    def plan_demo(self) -> None:
        self.ui.rule("计划")
        self.ui.status("计划模式", "方案已就绪", symbol="◆")
        self.ui.panel(
            "执行计划",
            [
                "1  流式传输      解析每次运行的 NDJSON 事件",
                "2  交互输入      历史记录、多行输入与命令补全",
                "3  运行活动      工具、阶段与计划进度",
                "4  安全边界      权限确认、中断与 JSON 模式",
            ],
            tone=self.ui.theme.accent,
        )
        print(
            f"  {self.ui.style('[1]', self.ui.theme.accent_strong)} 批准并执行   "
            f"{self.ui.style('[2]', self.ui.theme.text)} 修改计划   "
            f"{self.ui.style('[3]', self.ui.theme.text)} 取消"
        )
        if self.auto:
            print(self.ui.style("  演示选择：批准并执行", self.ui.theme.muted))
            choice = "1"
        else:
            choice = self.ui.prompt("选择")
        if choice == "1":
            self.ui.status("计划已批准", "准备执行", symbol="✓", tone=self.ui.theme.success)
        elif choice == "2":
            print(self.ui.style("请说明需要如何修改计划。", self.ui.theme.muted))
        else:
            self.ui.status("计划已取消", symbol="×", tone=self.ui.theme.warning)
        print()

    def permission_demo(self) -> None:
        self.ui.rule("需要处理")
        self.ui.panel(
            "需要授权",
            [
                "操作   写入项目文件",
                "目标   project-notes/interactive-cli-handoff.zh-CN.md",
                "原因   保存已经确认的 CLI 产品边界",
            ],
            tone=self.ui.theme.warning,
        )
        print(
            f"  {self.ui.style('[1]', self.ui.theme.accent_strong)} 仅允许一次   "
            f"{self.ui.style('[2]', self.ui.theme.text)} 拒绝"
        )
        if self.auto:
            print(self.ui.style("  演示选择：仅允许一次", self.ui.theme.muted))
            choice = "1"
        else:
            choice = self.ui.prompt("选择")
        if choice == "1":
            self.ui.status("已允许一次", "继续执行", symbol="✓", tone=self.ui.theme.success)
        else:
            self.ui.status("已拒绝", "未进行任何修改", symbol="×", tone=self.ui.theme.warning)
        print()

    def error_demo(self) -> None:
        self.ui.rule("运行")
        self.ui.status("调用模型", "等待回复", symbol="◌")
        self.ui.pause(0.7)
        self.ui.panel(
            "请求失败",
            [
                "模型端点在 300 秒内没有响应。",
                "请检查 Daemon 连接，或重试本次请求。",
            ],
            tone=self.ui.theme.danger,
        )
        print(
            f"  {self.ui.style('[r]', self.ui.theme.accent_strong)} 重试   "
            f"{self.ui.style('[e]', self.ui.theme.text)} 编辑请求   "
            f"{self.ui.style('[Enter]', self.ui.theme.text)} 返回"
        )
        print()

    def help(self) -> None:
        self.ui.rule("命令")
        commands = (
            ("/demo", "演示普通工具执行"),
            ("/plan", "演示计划审核与批准"),
            ("/permission", "演示范围明确的权限请求"),
            ("/error", "演示错误与恢复操作"),
            ("/mode default|plan|auto", "切换权限模式"),
            ("/clear", "清空当前终端记录"),
            ("/exit", "退出演示"),
        )
        for command, description in commands:
            print(
                f"  {self.ui.style(command.ljust(28), self.ui.theme.accent_strong)}"
                f"{self.ui.style(description, self.ui.theme.muted)}"
            )
        print()

    def menu(self) -> None:
        self.ui.rule("交互场景")
        options = (
            ("1", "普通运行", "工具活动、阶段变化与流式回复"),
            ("2", "计划审核", "批准、修改或取消执行计划"),
            ("3", "权限确认", "对敏感操作允许一次或拒绝"),
            ("4", "错误恢复", "重试、编辑请求或返回"),
        )
        for key, title, description in options:
            badge = self.ui.style(f"[{key}]", self.ui.theme.accent_strong, self.ui.theme.bold)
            name = self.ui.style(title.ljust(12), self.ui.theme.text, self.ui.theme.bold)
            detail = self.ui.style(description, self.ui.theme.muted)
            print(f"  {badge} {name}{detail}")
        print()
        print(
            "  "
            + self.ui.style("也可以：", self.ui.theme.faint)
            + self.ui.style("直接输入任意任务", self.ui.theme.text)
            + self.ui.style("  ·  /help  ·  /exit", self.ui.theme.faint)
        )
        print()

    def run_auto(self) -> None:
        self.ui.header()
        print(self.ui.style("自动预览：普通运行", self.ui.theme.muted))
        self.activity_demo()
        print(self.ui.style("自动预览：计划", self.ui.theme.muted))
        self.plan_demo()
        print(self.ui.style("自动预览：权限确认", self.ui.theme.muted))
        self.permission_demo()
        print(self.ui.style("自动预览：错误恢复", self.ui.theme.muted))
        self.error_demo()

    def run(self) -> None:
        self.ui.header()
        self.menu()
        while True:
            try:
                command = self.ui.prompt()
                if not command:
                    continue
                lowered = command.lower()
                if lowered in {"/exit", "exit", "quit"}:
                    print(self.ui.style("\n会话已关闭。正式版本中 Daemon 会继续运行。\n", self.ui.theme.muted))
                    return
                if lowered in {"/help", "/h"}:
                    self.help()
                elif lowered in {"1", "/demo"}:
                    self.activity_demo()
                    self.menu()
                elif lowered in {"2", "/plan"}:
                    self.plan_demo()
                    self.menu()
                elif lowered in {"3", "/permission"}:
                    self.permission_demo()
                    self.menu()
                elif lowered in {"4", "/error"}:
                    self.error_demo()
                    self.menu()
                elif lowered == "/menu":
                    self.menu()
                elif lowered == "/clear":
                    os.system("cls" if os.name == "nt" else "clear")
                    self.ui.header()
                    self.menu()
                elif lowered.startswith("/mode "):
                    mode = lowered.split(maxsplit=1)[1]
                    if mode not in {"default", "plan", "auto"}:
                        self.ui.status("未知模式", mode, symbol="×", tone=self.ui.theme.danger)
                    else:
                        self.mode = mode
                        self.ui.status("模式已切换", mode, symbol="✓", tone=self.ui.theme.success)
                        print()
                else:
                    self.activity_demo(command)
                    self.menu()
            except KeyboardInterrupt:
                print()
                self.ui.status(
                    "运行已中断",
                    "在空输入处再次按 Ctrl+C 退出",
                    symbol="■",
                    tone=self.ui.theme.warning,
                )
                print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Visual prototype for `cyrene chat`.")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Render every demo state without waiting for input.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors.",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Animation speed multiplier; use 0 for instant output.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    color = sys.stdout.isatty() and not args.no_color and os.environ.get("NO_COLOR") is None
    demo = Demo(UI(color=color, speed=args.speed), auto=args.auto)
    if args.auto:
        demo.run_auto()
    else:
        demo.run()


if __name__ == "__main__":
    main()
