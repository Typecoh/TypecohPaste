# -*- coding: utf-8 -*-
"""与平台无关的核心逻辑：配置、文本规整、逐字输入引擎。"""

import json
import os
import random
import threading
import time
import unicodedata

DEFAULTS = {
    "char_delay": 15,      # 每个字符基础间隔 ms
    "jitter": 8,           # 随机抖动 ms
    "line_delay": 90,      # 回车后等待编辑器自动缩进的 ms
    "tab_width": 4,        # Tab 展开成几个空格
    "pre_delay": 400,      # 开打前的准备时间 ms
    "clear_first": True,
    "intercept_middle": True,
    "human_pause": True,
    "halfwidth": True,
    "compat_vk": False,    # 仅 Windows 有意义
    "trailing_newline": False,
    "keep_blank": False,
}


def config_dir():
    """配置放到用户目录，避免装在 Program Files / .app 里时没有写权限。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif os.uname().sysname == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~")
    path = os.path.join(base, "CodeTyper")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


CFG_PATH = os.path.join(config_dir(), "config.json")


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in DEFAULTS if k in data})
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def normalize_punct(text):
    """全角标点 → 半角（中文输入法下的括号/引号会让代码直接编译报错）。"""
    text = unicodedata.normalize("NFKC", text)
    table = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
             "\u3002": ".", "\u3001": ",", "\uff5e": "~", "\u2014": "-"}
    return "".join(table.get(c, c) for c in text)


class Typist:
    """把一段文本按"人工打字"的节奏逐字注入前台窗口。"""

    def __init__(self, text, cfg, backend, own_titles=None,
                 on_progress=None, on_finish=None, on_target=None):
        self.text = text
        self.cfg = cfg
        self.be = backend
        self.own_titles = tuple(own_titles or ())
        self.on_progress = on_progress or (lambda p: None)
        self.on_finish = on_finish or (lambda ok, msg: None)
        self.on_target = on_target or (lambda title: None)
        self.stop_event = threading.Event()
        self.abort_reason = ""
        self._target = None
        self._total = 1
        self._done = 0
        self._last_report = 0.0

    # ---- 基础 ----
    def stop(self):
        self.stop_event.set()

    def _abort(self):
        if self.stop_event.is_set():
            self.abort_reason = self.abort_reason or "已中止"
            return True
        if self._target is not None and self.be.foreground()[0] != self._target:
            self.stop_event.set()
            self.abort_reason = "目标窗口已切换，已自动停止（避免把代码打进别的窗口）"
            return True
        if self.be.esc_pressed():
            self.stop_event.set()
            self.abort_reason = "已按 ESC 中止"
            return True
        return False

    def _sleep(self, ms):
        """分片休眠，保证 ESC / 窗口切换在 20ms 内就能被察觉。"""
        end = time.perf_counter() + max(ms, 0) / 1000.0
        while True:
            remain = end - time.perf_counter()
            if remain <= 0:
                return
            time.sleep(min(remain, 0.02))
            if self.stop_event.is_set():
                return
            if self.be.esc_pressed():
                self.stop_event.set()
                self.abort_reason = self.abort_reason or "已按 ESC 中止"
                return

    def _char_delay(self):
        base = self.cfg["char_delay"]
        jitter = self.cfg["jitter"]
        return base + (random.uniform(0, jitter) if jitter > 0 else 0)

    def _tick(self, n=1):
        self._done += n
        now = time.perf_counter()
        if now - self._last_report > 0.1:
            self._last_report = now
            self.on_progress(min(99, int(self._done * 100 / max(self._total, 1))))

    # ---- 动作 ----
    def _type_spaces(self, n, fast=True):
        for _ in range(n):
            if self._abort():
                return
            self.be.tap("space", 0)
            self._tick()
            self._sleep(min(self.cfg["char_delay"], 8) if fast else self._char_delay())

    def _normalize_indent(self, indent):
        """
        回车后编辑器通常会按自己的规则自动缩进。这里先把它产出的空白整段选中抹掉，
        再写入代码里的真实缩进 —— 与编辑器到底缩几个空格、用空格还是 Tab 无关。
        """
        self.be.tap("home", 0)
        self._sleep(10)
        self.be.tap("home", 0)          # 智能 Home 要按两次才回到行首
        self._sleep(10)
        self.be.down("shift")
        self.be.tap("end", 0)
        self.be.up("shift")
        self._sleep(6)
        if indent <= 0:
            self.be.tap("del", 0)
        else:
            self._type_spaces(indent)
        self._tick(3)

    def _type_text(self, body):
        compat = self.cfg["compat_vk"] and self.be.name == "win"
        human = self.cfg["human_pause"]
        for idx, ch in enumerate(body):
            if self._abort():
                return
            if ch == " ":
                self.be.tap("space", 0)
            else:
                self.be.type_char(ch, compat=compat)
            self._tick()
            delay = self._char_delay()
            if human and idx > 0 and idx % 90 == 0:
                delay += random.uniform(120, 380)   # 偶尔停顿，更接近真人节奏
            self._sleep(delay)

    # ---- 主流程 ----
    def run(self):
        ok, msg = True, "输入完成"
        try:
            cfg = self.cfg
            text = normalize_punct(self.text) if cfg["halfwidth"] else self.text
            lines = [ln.replace("\r", "") for ln in text.split("\n")]
            while len(lines) > 1 and not lines[-1].strip() and not cfg["keep_blank"]:
                lines.pop()
            if not lines:
                self.on_finish(False, "没有可输入的内容")
                return

            self._total = sum(len(ln) for ln in lines) + 8 * len(lines)
            if cfg["pre_delay"] > 0:
                self._sleep(cfg["pre_delay"])
            if self._abort():
                self.on_finish(False, "已中止")
                return

            ident, title = self.be.foreground()
            if any(title.startswith(t) or title == t for t in self.own_titles if t):
                self.on_finish(False, "当前焦点在本工具窗口，请先把光标点进目标编辑器再开始")
                return
            self._target = ident
            self.on_target(title or "(未命名窗口)")

            if cfg["clear_first"]:
                self.be.select_all_delete()
                self._sleep(100)

            tab = cfg["tab_width"]
            for i, raw in enumerate(lines):
                if self._abort():
                    break
                line = raw.replace("\t", " " * tab)
                stripped = line.lstrip(" ")
                indent = len(line) - len(stripped)
                body = stripped.rstrip(" ")
                if not body:
                    indent = 0
                if i > 0:
                    self.be.tap("enter", 0)
                    self._sleep(cfg["line_delay"])
                    self._normalize_indent(indent)
                elif cfg["clear_first"]:
                    self._normalize_indent(indent)
                else:
                    self._type_spaces(indent)
                self._type_text(body)

            if self.stop_event.is_set():
                ok, msg = False, (self.abort_reason or "已中止")
            elif cfg["trailing_newline"]:
                self.be.tap("enter", 0)
        except Exception as exc:                      # noqa: BLE001
            ok, msg = False, "出错：%s" % exc
        self.on_progress(100)
        self.on_finish(ok, msg)
