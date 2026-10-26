# -*- coding: utf-8 -*-
"""代码输入助手 —— 把代码"敲"进不能粘贴的网页编辑器

在窗口里粘贴代码，然后【双击鼠标中键】或按快捷键，程序会把代码逐字输入到
当前焦点窗口，并正确处理换行后的缩进。

Windows / macOS 双平台，界面与逻辑完全一致，差异都在 backend_*.py 里：
  · Windows：SendInput 注入 + WH_MOUSE_LL / WH_KEYBOARD_LL 全局钩子，无第三方依赖
  · macOS  ：Quartz CGEvent 注入 + CGEventTap 监听，需 pyobjc 与"辅助功能"授权

源码运行：python code_typer.py
"""

import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core import DEFAULTS, load_cfg, save_cfg, Typist

if sys.platform == "win32":
    from win.backend_win import WinBackend as Backend
elif sys.platform == "darwin":
    from mac.backend_mac import MacBackend as Backend
else:
    raise SystemExit("本程序仅支持 Windows 与 macOS")

APP_TITLE = "Typecoh-代码输入助手"

BG = "#eef1f5"
CARD = "#ffffff"
FG = "#1f2328"
MUTED = "#6b7280"
ACCENT = "#2563eb"
LINE = "#d7dce3"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.be = Backend()
        self.title("%s · %s %s 开始输入" % (APP_TITLE, self.be.trigger_label, self.be.hotkey_label))
        self.configure(bg=BG)
        self.geometry("880x720")
        self.minsize(760, 600)
        self.cfg = load_cfg()
        self._q = queue.Queue()
        self.typer = None
        self.vars = {}

        self._build_ui()
        self._push_cfg_to_ui()

        self.be.start_hooks(
            on_double=lambda: self._q.put(("start",)),
            on_hotkey=lambda: self._q.put(("start",)),
            on_abort=lambda: self._q.put(("stop",)),
        )
        self.be.set_armed(bool(self.cfg["intercept_middle"]))
        self.after(60, self._pump)
        self.after(700, self._check_env)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_status("就绪",
                         "在下方粘贴代码，光标点进目标编辑器，%s 或 %s 开始"
                         % (self.be.trigger_label, self.be.hotkey_label))

    # ---------------- UI ----------------
    def _build_ui(self):
        self._style()
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=14, pady=12)

        head = tk.Frame(root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        head.pack(fill="x")
        inner = tk.Frame(head, bg=CARD)
        inner.pack(fill="x", padx=14, pady=11)
        self.dot = tk.Canvas(inner, width=10, height=10, bg=CARD, highlightthickness=0)
        self.dot.pack(side="left", pady=2)
        self._dot_item = self.dot.create_oval(1, 1, 9, 9, fill="#22c55e", outline="")
        self.lbl_state = tk.Label(inner, text="就绪", bg=CARD, fg=FG,
                                  font=("Microsoft YaHei UI", 11, "bold"))
        self.lbl_state.pack(side="left", padx=(8, 12))
        self.lbl_hint = tk.Label(inner, text="", bg=CARD, fg=MUTED,
                                 font=("Microsoft YaHei UI", 9))
        self.lbl_hint.pack(side="left")

        code_wrap = tk.Frame(root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        code_wrap.pack(fill="both", expand=True, pady=(10, 10))
        bar = tk.Frame(code_wrap, bg=CARD)
        bar.pack(fill="x", padx=12, pady=(9, 4))
        tk.Label(bar, text="待输入代码", bg=CARD, fg=FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self.lbl_count = tk.Label(bar, text="0 行 / 0 字符", bg=CARD, fg=MUTED,
                                  font=("Microsoft YaHei UI", 9))
        self.lbl_count.pack(side="left", padx=10)
        tk.Label(bar, text="（本窗口内可直接粘贴）", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        body = tk.Frame(code_wrap, bg=CARD)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.txt = tk.Text(body, bg="#fbfcfd", fg="#0f172a", insertbackground="#0f172a",
                           font=("Consolas", 11), undo=True, wrap="none", height=12,
                           relief="flat", padx=10, pady=8, selectbackground="#bcd4fb")
        vs = ttk.Scrollbar(body, orient="vertical", command=self.txt.yview)
        hs = ttk.Scrollbar(body, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.txt.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.txt.bind("<<Modified>>", self._on_modified)
        self.txt.bind("<KeyRelease>", self._on_modified)

        opts = tk.Frame(root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        opts.pack(fill="x")
        grid = tk.Frame(opts, bg=CARD)
        grid.pack(fill="x", padx=14, pady=12)
        for c in range(4):
            grid.columnconfigure(c, weight=1)

        self._spin(grid, "每字符间隔 ms", "char_delay", 0, 500, 1, 0, 0)
        self._spin(grid, "抖动 ± ms", "jitter", 0, 300, 1, 1, 0)
        self._spin(grid, "回车后等待 ms", "line_delay", 0, 1500, 10, 2, 0)
        self._spin(grid, "Tab = 空格数", "tab_width", 1, 8, 1, 3, 0)
        self._spin(grid, "开打前等待 ms", "pre_delay", 0, 5000, 50, 0, 1)

        checks = tk.Frame(grid, bg=CARD)
        checks.grid(row=1, column=1, columnspan=3, rowspan=2, sticky="w", padx=(6, 0))
        self._check(checks, "开始前清空编辑器", "clear_first", 0, 0)
        self._check(checks, "拦截中键双击触发", "intercept_middle", 0, 1)
        self._check(checks, "全角标点转半角", "halfwidth", 1, 0)
        self._check(checks, "拟人化停顿", "human_pause", 1, 1)
        if self.be.name == "win":
            self._check(checks, "兼容模式 (虚拟键输入)", "compat_vk", 2, 0)
        self._check(checks, "末尾补一个回车", "trailing_newline", 2, 1)

        btns = tk.Frame(root, bg=BG)
        btns.pack(fill="x", pady=(12, 8))
        self.btn_start = tk.Button(btns, text="开始输入  (%s)" % self.be.hotkey_label,
                                   command=self.start_typing,
                                   bg=ACCENT, fg="white", relief="flat", cursor="hand2",
                                   font=("Microsoft YaHei UI", 10, "bold"), padx=22, pady=8,
                                   activebackground="#1d4ed8", activeforeground="white")
        self.btn_start.pack(side="left")
        self.btn_stop = tk.Button(btns, text="停止  (ESC)", command=self.stop_typing,
                                  bg="#e5e7eb", fg="#374151", relief="flat", cursor="hand2",
                                  font=("Microsoft YaHei UI", 10), padx=18, pady=8,
                                  activebackground="#d1d5db")
        self.btn_stop.pack(side="left", padx=8)
        tk.Button(btns, text="使用说明", command=self._help, bg=CARD, fg=FG, relief="flat",
                  cursor="hand2", font=("Microsoft YaHei UI", 10), padx=16, pady=8,
                  highlightbackground=LINE, highlightthickness=1,
                  activebackground="#f3f4f6").pack(side="left")

        self.pbar = ttk.Progressbar(btns, orient="horizontal", mode="determinate", maximum=100)
        self.pbar.pack(side="left", fill="x", expand=True, padx=(18, 0))

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TCheckbutton", background=CARD, foreground=FG,
                     font=("Microsoft YaHei UI", 9), focuscolor=CARD)
        st.map("TCheckbutton", background=[("active", CARD)])
        st.configure("TScrollbar", background="#e2e6eb", troughcolor="#f4f6f8",
                     bordercolor="#f4f6f8", arrowcolor=MUTED, relief="flat")
        st.configure("TProgressbar", background=ACCENT, troughcolor="#e5e7eb",
                     bordercolor="#e5e7eb", lightcolor=ACCENT, darkcolor=ACCENT)

    def _spin(self, parent, label, key, lo, hi, inc, col, row):
        box = tk.Frame(parent, bg=CARD)
        box.grid(row=row, column=col, sticky="w", pady=3, padx=(6, 0))
        tk.Label(box, text=label, bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        var = tk.StringVar()
        tk.Spinbox(box, from_=lo, to=hi, increment=inc, width=5, textvariable=var,
                   relief="flat", bg="#f3f5f8", fg=FG, font=("Consolas", 10),
                   justify="center", highlightthickness=1, highlightbackground="#dfe4ea",
                   buttonbackground="#eef1f5").pack(side="left", padx=(6, 0))
        self.vars[key] = var

    def _check(self, parent, label, key, r, c):
        var = tk.BooleanVar()
        ttk.Checkbutton(parent, text=label, variable=var,
                        command=lambda k=key: self._on_toggle(k)
                        ).grid(row=r, column=c, sticky="w", padx=(0, 16), pady=2)
        self.vars[key] = var

    def _push_cfg_to_ui(self):
        for key, var in self.vars.items():
            val = self.cfg.get(key, DEFAULTS[key])
            if isinstance(var, tk.BooleanVar):
                var.set(bool(val))
            else:
                var.set(str(val))

    def _collect_cfg(self):
        cfg = dict(DEFAULTS)
        for key, var in self.vars.items():
            if isinstance(var, tk.BooleanVar):
                cfg[key] = bool(var.get())
            else:
                try:
                    cfg[key] = int(float(var.get()))
                except Exception:
                    cfg[key] = DEFAULTS[key]
        return cfg

    def _on_toggle(self, key):
        if key == "intercept_middle":
            self.be.set_armed(bool(self.vars["intercept_middle"].get()))

    def _on_modified(self, _evt=None):
        try:
            self.txt.edit_modified(False)
        except tk.TclError:
            pass
        content = self.txt.get("1.0", "end-1c")
        self.lbl_count.configure(text="%d 行 / %d 字符"
                                 % (content.count("\n") + 1, len(content)))

    # ---------------- 状态 ----------------
    def _set_status(self, state, hint, color="#22c55e"):
        self.lbl_state.configure(text=state)
        self.lbl_hint.configure(text=hint)
        self.dot.itemconfigure(self._dot_item, fill=color)

    def _check_env(self):
        hint = self.be.permission_hint()
        if hint:
            self._set_status("缺少权限", hint, "#ef4444")
            return
        if self.be.hook_ok is False:
            self._set_status("快捷键不可用",
                             "全局监听未启动，请改用「开始输入」按钮", "#f59e0b")

    # ---------------- 主流程 ----------------
    def _pump(self):
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]
                if kind == "start":
                    self.start_typing()
                elif kind == "stop":
                    self.stop_typing()
                elif kind == "progress":
                    self.pbar["value"] = msg[1]
                elif kind == "target":
                    self.lbl_hint.configure(text="正在输入 → %s" % msg[1])
                elif kind == "finish":
                    self._finish(*msg[1:])
        except queue.Empty:
            pass
        self.after(60, self._pump)

    def start_typing(self):
        if self.typer is not None:
            return
        code = self.txt.get("1.0", "end-1c")
        if not code.strip():
            self._set_status("没有代码", "请先在上方粘贴要输入的代码", "#f59e0b")
            return
        cfg = self._collect_cfg()
        self.cfg = cfg
        save_cfg(cfg)
        self.pbar["value"] = 0
        self._set_status("输入中…", "按 ESC 可立即中止", "#2563eb")
        self.typer = Typist(
            code, cfg, self.be,
            own_titles=[APP_TITLE, "Python", "python3", "CodeTyper"],
            on_progress=lambda p: self._q.put(("progress", p)),
            on_finish=lambda ok, m: self._q.put(("finish", ok, m)),
            on_target=lambda t: self._q.put(("target", t)),
        )
        threading.Thread(target=self.typer.run, daemon=True).start()

    def stop_typing(self):
        if self.typer is not None:
            self.typer.stop()

    def _finish(self, ok, msg):
        self.typer = None
        if ok:
            self._set_status("完成", msg, "#22c55e")
        else:
            self._set_status("已停止", msg, "#f59e0b")

    def _help(self):
        messagebox.showinfo(
            "使用说明",
            "1. 把代码粘贴到上方的框里。\n"
            "2. 在目标编辑器里点一下，让光标落在编辑区。\n"
            "3. %s（或按 %s）开始，程序逐字输入。\n"
            "4. 任何时候按 ESC 立即停止。\n\n"
            "缩进是怎么处理的：\n"
            "  编辑器回车后通常会自动缩进。程序在每次回车后用\n"
            "  Home → Home → Shift+End 选中该行已有的空白，再用代码里的\n"
            "  真实缩进替换掉，所以 4 空格 / 2 空格 / Tab 都不会错位。\n\n"
            "安全保护：\n"
            "  开打前会记住当前前台窗口，中途切走就立刻停止，\n"
            "  不会把代码打进别的程序里。\n\n"
            "注意：\n"
            "  · 若目标编辑器自带模板代码，请先取消「开始前清空编辑器」。\n"
            "  · 目标程序若以管理员权限运行，本工具也需同等权限。\n"
            "  · 字符丢失或乱序：把「每字符间隔」调大到 25~40ms。\n\n"
            "当前平台：%s" % (self.be.trigger_label, self.be.hotkey_label,
                          self.be.platform_label),
        )

    def _on_close(self):
        try:
            if self.typer is not None:
                self.typer.stop()
            self.be.stop_hooks()
        except Exception:
            pass
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _app = App()
        _app.txt.insert("1.0", "def f():\n    print('hi')\n")
        _app.update_idletasks()
        _app.update()
        _app._on_modified()
        _app.after(300, _app._on_close)
        _app.mainloop()
        print("selftest ok")
    else:
        main()
