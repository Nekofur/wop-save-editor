# -*- coding: utf-8 -*-
"""War Thunder Offline 存档修改器（图形界面）。

功能：修改金鹰 / 银狮 / Gaijin 币，解锁任意载具，查看并解锁隐藏载具。

界面用 tkinter（系统 Python），所有读写都通过同目录的 wop_profile.py，
再由游戏自己的 backend.ProfileStore 落盘。游戏运行期间自动切换为只读，
避免破坏存档。
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wop_profile as wp  # noqa: E402

APP_TITLE = "War Thunder Offline 存档修改器"
TREE_FONT = ("Microsoft YaHei UI", 9)
UI_FONT = ("Microsoft YaHei UI", 9)

LOG_DIR = wp.LOG_ROOT
CRASH_LOG = LOG_DIR / "crash.log"
READY_MARKER = LOG_DIR / "startup.ok"


def write_crash(tag: str, text: str):
    """任何异常都落盘，pythonw 无控制台时也能事后排查。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG.open("a", encoding="utf-8") as stream:
            stream.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {tag} =====\n{text}\n")
    except OSError:
        pass


def fmt(number: int) -> str:
    return f"{int(number):,}"


class EditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1220x800")
        self.minsize(1040, 680)
        self._setup_style()
        self.report_callback_exception = self._report_callback_exception

        self.game_root: Path | None = wp.find_game_root()
        self.profile: wp.GameProfile | None = None
        self.snapshot: dict | None = None
        self.rows: list[dict] = []
        self.hidden_rows: list[dict] = []
        self.owned: set[str] = set()
        self._game_busy = ""
        self._live = None
        self._live_warned = False
        self._ready = False
        self._write_widgets: list[tk.Widget] = []

        self.status_var = tk.StringVar(value="正在初始化…")
        self.info_var = tk.StringVar(value="")
        self.guard_var = tk.StringVar(value="")
        self.vehicle_count_var = tk.StringVar(value="")
        self.hidden_count_var = tk.StringVar(value="")
        self.filter_count_var = tk.StringVar(value="")
        self.hidden_filter_count_var = tk.StringVar(value="")

        try:
            READY_MARKER.unlink(missing_ok=True)
        except OSError:
            pass
        self._log_path = LOG_DIR / f"editor-{datetime.now():%Y%m%d}.log"
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        if self.game_root is None:
            self.after(100, self._fatal_no_game)
            return
        self._build_ui()
        self.after(60, self.reload_accounts)

    # ------------------------------------------------------------ 启动自检
    def _mark_ready(self):
        """界面确实建好并能读存档后，才写就绪标记，供启动器判断成败。"""
        if self._ready:
            return
        self._ready = True
        try:
            READY_MARKER.parent.mkdir(parents=True, exist_ok=True)
            READY_MARKER.write_text(datetime.now().isoformat(), encoding="utf-8")
        except OSError:
            pass

    def _report_callback_exception(self, exc, value, tb):
        """界面回调里的异常不再静默：写日志 + 弹窗。"""
        text = "".join(traceback.format_exception(exc, value, tb))
        write_crash("callback", text)
        try:
            self.log("界面回调异常：" + repr(value))
        except tk.TclError:
            pass
        try:
            messagebox.showerror(APP_TITLE, f"发生错误：\n{value}\n\n详细信息已写入 logs\\crash.log")
        except tk.TclError:
            pass


    # ------------------------------------------------------------------ 样式
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        self.option_add("*Font", UI_FONT)
        style.configure("Treeview", font=TREE_FONT, rowheight=22)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Warn.TLabel", foreground="#b3261e")
        style.configure("Ok.TLabel", foreground="#146c2e")
        style.configure("Muted.TLabel", foreground="#5f6368")

    def _fatal_no_game(self):
        messagebox.showerror(
            APP_TITLE,
            "没有找到 War Thunder Offline 游戏目录。\n\n"
            "请确认游戏已解压，并在本工具同目录的 config.json 中填写正确的 gameRoot。")
        self.destroy()

    # ------------------------------------------------------------------ 界面
    def _build_ui(self):
        outer = ttk.Frame(self, padding=(10, 8))
        outer.pack(fill="both", expand=True)

        # 顶部：游戏目录与账号
        top = ttk.LabelFrame(outer, text=" 存档 ", padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="游戏目录：").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text=str(self.game_root), foreground="#5f6368").grid(
            row=0, column=1, columnspan=5, sticky="w", padx=(0, 8))
        ttk.Label(top, text="账号：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.account_box = ttk.Combobox(top, state="readonly", width=28)
        self.account_box.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.account_box.bind("<<ComboboxSelected>>", lambda _e: self.load_account())
        ttk.Button(top, text="刷新账号", command=self.reload_accounts).grid(
            row=1, column=2, padx=6, pady=(6, 0))
        ttk.Button(top, text="重新检测游戏状态", command=self.refresh_guard).grid(
            row=1, column=3, padx=6, pady=(6, 0))
        ttk.Button(top, text="打开存档文件夹", command=self.open_save_folder).grid(
            row=1, column=4, padx=6, pady=(6, 0))
        ttk.Button(top, text="刷新数据", command=self.refresh_all).grid(
            row=1, column=5, padx=6, pady=(6, 0))
        self.topmost_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="窗口置顶", variable=self.topmost_var,
                        command=self._apply_topmost).grid(
            row=1, column=6, padx=(6, 0), pady=(6, 0))

        # 信息条
        info = ttk.Frame(outer, padding=(2, 6))
        info.pack(fill="x")
        ttk.Label(info, textvariable=self.info_var, font=("Microsoft YaHei UI", 9, "bold")).pack(
            side="left")
        ttk.Label(info, textvariable=self.guard_var, style="Warn.TLabel").pack(
            side="right")

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True, pady=(2, 6))
        self._build_currency_tab()
        self._build_vehicle_tab()
        self._build_hidden_tab()
        self._build_log_tab()

        status = ttk.Label(self, textvariable=self.status_var, relief="sunken",
                           anchor="w", padding=(8, 4))
        status.pack(side="bottom", fill="x")

    def _apply_topmost(self):
        try:
            self.attributes("-topmost", bool(self.topmost_var.get()))
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 货币页
    def _build_currency_tab(self):
        tab = ttk.Frame(self.notebook, padding=(12, 10))
        self.notebook.add(tab, text=" 货币 ")

        ttk.Label(tab, text="余额修改", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(
            tab,
            text="输入目标值后点“保存货币”。工具先备份整份存档，再通过游戏后端写入；"
                 "数值上限为 1 万亿。",
            style="Muted.TLabel").grid(row=1, column=0, columnspan=5, sticky="w", pady=(0, 12))

        self.currency_entries: dict[str, ttk.Entry] = {}
        self.currency_current: dict[str, tk.StringVar] = {}
        rows = (
            ("gold", "金鹰（Golden Eagles）"),
            ("silver", "银狮（Silver Lions）"),
            ("coins", "Gaijin 币（GJN）"),
        )
        for index, (key, label) in enumerate(rows, start=2):
            ttk.Label(tab, text=label).grid(row=index, column=0, sticky="w", pady=4)
            entry = ttk.Entry(tab, width=22, justify="right")
            entry.grid(row=index, column=1, sticky="w", padx=(10, 12), pady=4)
            current = tk.StringVar(value="当前：—")
            self.currency_current[key] = current
            ttk.Label(tab, textvariable=current, style="Muted.TLabel").grid(
                row=index, column=2, sticky="w")
            self.currency_entries[key] = entry

        buttons = ttk.Frame(tab)
        buttons.grid(row=5, column=0, columnspan=5, sticky="w", pady=(16, 4))
        save = ttk.Button(buttons, text="保存货币", style="Accent.TButton",
                          command=self.save_currency)
        save.pack(side="left")
        self._write_widgets.append(save)
        ttk.Button(buttons, text="重新读取", command=self.refresh_all).pack(
            side="left", padx=8)

        quick = ttk.Frame(tab)
        quick.grid(row=6, column=0, columnspan=5, sticky="w", pady=(4, 4))
        ttk.Label(quick, text="快速填入：", style="Muted.TLabel").pack(side="left")
        for label, key, value in (
            ("银狮 100 万", "silver", 1_000_000),
            ("银狮 1 亿", "silver", 100_000_000),
            ("金鹰 1 亿", "gold", 100_000_000),
            ("GJN 10 万", "coins", 100_000),
        ):
            ttk.Button(quick, text=label,
                       command=lambda k=key, v=value: self._fill_currency(k, v)).pack(
                side="left", padx=3)

        note = (
            "说明：金鹰与 Gaijin 币是商店货币，银狮用于研发购买与乘员培训。\n"
            "修改只影响当前账号存档，不会影响其他账号；游戏运行中工具会自动锁定写入。")
        ttk.Label(tab, text=note, style="Muted.TLabel", justify="left").grid(
            row=7, column=0, columnspan=5, sticky="w", pady=(18, 0))
        tab.columnconfigure(4, weight=1)

    def _fill_currency(self, key: str, value: int):
        entry = self.currency_entries[key]
        entry.delete(0, "end")
        entry.insert(0, str(value))

    # ------------------------------------------------------------ 载具页
    def _build_vehicle_tab(self):
        tab = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(tab, text=" 载具解锁 ")

        filters = ttk.Frame(tab)
        filters.pack(fill="x")

        ttk.Label(filters, text="搜索：").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.search_var, width=22)
        search.pack(side="left", padx=(0, 8))
        search.bind("<Return>", lambda _e: self.render_vehicles())

        ttk.Label(filters, text="国家：").pack(side="left")
        self.country_var = tk.StringVar(value="全部")
        country_values = ["全部"] + [label for label in wp.COUNTRY_LABELS.values()]
        ttk.Combobox(filters, textvariable=self.country_var, values=country_values,
                     state="readonly", width=8).pack(side="left", padx=(0, 8))

        ttk.Label(filters, text="兵种：").pack(side="left")
        self.group_var = tk.StringVar(value="全部")
        ttk.Combobox(filters, textvariable=self.group_var,
                     values=["全部", "陆军", "空军", "直升机", "舰船", "小艇", "其他"],
                     state="readonly", width=8).pack(side="left", padx=(0, 8))

        ttk.Label(filters, text="状态：").pack(side="left")
        self.state_var = tk.StringVar(value="全部")
        ttk.Combobox(filters, textvariable=self.state_var,
                     values=["全部", "仅未拥有", "仅已拥有", "仅隐藏载具池"],
                     state="readonly", width=12).pack(side="left", padx=(0, 8))

        ttk.Button(filters, text="应用筛选", command=self.render_vehicles).pack(side="left")
        ttk.Button(filters, text="重置", command=self.reset_filters).pack(
            side="left", padx=6)

        tree_frame = ttk.Frame(tab)
        tree_frame.pack(fill="both", expand=True, pady=(8, 6))
        columns = ("name", "id", "state", "class", "country", "group", "rank", "br", "price", "tags")
        self.vehicle_tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                         selectmode="extended")
        headings = {
            "name": ("载具名称", 200, "w"),
            "id": ("目录 ID", 190, "w"),
            "state": ("状态", 70, "center"),
            "class": ("类型", 95, "w"),
            "country": ("国家", 55, "center"),
            "group": ("兵种", 55, "center"),
            "rank": ("等级", 42, "center"),
            "br": ("权重", 48, "center"),
            "price": ("价格", 105, "w"),
            "tags": ("标签", 115, "w"),
        }
        for key, (text, width, anchor) in headings.items():
            self.vehicle_tree.heading(key, text=text)
            self.vehicle_tree.column(key, width=width, anchor=anchor, stretch=(key == "id"))
        self.vehicle_tree.tag_configure("owned", foreground="#80868b")
        self.vehicle_tree.tag_configure("hidden", foreground="#7b1fa2")
        self.vehicle_tree.tag_configure("normal", foreground="#202124")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.vehicle_tree.yview)
        self.vehicle_tree.configure(yscrollcommand=vsb.set)
        self.vehicle_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.vehicle_count_var).pack(side="left")
        ttk.Button(bottom, text="全选当前结果", command=self.select_all_vehicles).pack(
            side="right", padx=4)
        self.btn_unlock_filter = ttk.Button(
            bottom, text="解锁当前筛选结果", command=self.unlock_filtered, style="Accent.TButton")
        self.btn_unlock_filter.pack(side="right", padx=4)
        self.btn_unlock_selected = ttk.Button(
            bottom, text="解锁选中载具", command=self.unlock_selected_vehicles,
            style="Accent.TButton")
        self.btn_unlock_selected.pack(side="right", padx=4)
        self._write_widgets.extend([self.btn_unlock_filter, self.btn_unlock_selected])
        ttk.Label(
            tab,
            text="提示：解锁＝直接把载具加入拥有列表，不扣银狮、不需要研发；"
                 "解锁后到游戏里把载具编入乘员组即可使用。",
            style="Muted.TLabel").pack(fill="x", pady=(6, 0))

    # ------------------------------------------------------------ 隐藏载具页
    def _build_hidden_tab(self):
        tab = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(tab, text=" 隐藏载具 ")

        ttk.Label(
            tab,
            text="隐藏载具池取自游戏自带的 data/hidden-vehicle-pool.json："
                 "原版科技树里被研发、购买资格或限时销售挡住的载具，商店不出售。",
            wraplength=1100, justify="left", style="Muted.TLabel").pack(fill="x", pady=(0, 6))

        tree_frame = ttk.Frame(tab)
        tree_frame.pack(fill="both", expand=True)
        columns = ("name", "id", "state", "class", "country", "rank", "br", "price", "tags")
        self.hidden_tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                        selectmode="extended")
        headings = {
            "name": ("载具名称", 200, "w"),
            "id": ("目录 ID", 200, "w"),
            "state": ("状态", 70, "center"),
            "class": ("类型", 105, "w"),
            "country": ("国家", 55, "center"),
            "rank": ("等级", 42, "center"),
            "br": ("权重", 48, "center"),
            "price": ("价格", 105, "w"),
            "tags": ("标签", 115, "w"),
        }
        for key, (text, width, anchor) in headings.items():
            self.hidden_tree.heading(key, text=text)
            self.hidden_tree.column(key, width=width, anchor=anchor, stretch=(key == "id"))
        self.hidden_tree.tag_configure("owned", foreground="#80868b")
        self.hidden_tree.tag_configure("normal", foreground="#202124")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.hidden_tree.yview)
        self.hidden_tree.configure(yscrollcommand=vsb.set)
        self.hidden_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", pady=(6, 0))
        ttk.Label(bottom, textvariable=self.hidden_count_var).pack(side="left")
        self.btn_unlock_all_hidden = ttk.Button(
            bottom, text="解锁全部隐藏载具", command=self.unlock_all_hidden,
            style="Accent.TButton")
        self.btn_unlock_all_hidden.pack(side="right", padx=4)
        self.btn_unlock_hidden_selected = ttk.Button(
            bottom, text="解锁选中", command=self.unlock_selected_hidden,
            style="Accent.TButton")
        self.btn_unlock_hidden_selected.pack(side="right", padx=4)
        self._write_widgets.extend([self.btn_unlock_all_hidden,
                                    self.btn_unlock_hidden_selected])

    # ------------------------------------------------------------ 日志页
    def _build_log_tab(self):
        tab = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(tab, text=" 日志 ")
        self.log_text = ScrolledText(tab, height=10, font=("Consolas", 9), wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        ttk.Button(tab, text="清空日志显示", command=self.clear_log).pack(
            side="right", pady=(6, 0))

    # ------------------------------------------------------------------ 日志
    def log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            with self._log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ 账号
    def reload_accounts(self):
        accounts = wp.list_accounts(self.game_root)
        if not accounts:
            messagebox.showerror(APP_TITLE, "Save 目录下没有找到任何本地账号存档。")
            self.status_var.set("未找到账号存档")
            return
        labels = [f"{item['username']}  ({item['folder']})" if item["username"] != item["folder"]
                  else item["folder"] for item in accounts]
        self._accounts = dict(zip(labels, accounts))
        self.account_box.configure(values=labels)
        preferred = wp.active_account_folder(self.game_root)
        target = labels[0]
        for label, item in self._accounts.items():
            if item["folder"] == preferred:
                target = label
                break
        self.account_box.set(target)
        self.load_account()

    def load_account(self):
        label = self.account_box.get()
        if label not in getattr(self, "_accounts", {}):
            return
        item = self._accounts[label]
        if self.profile is not None:
            self.profile.close()
        self.profile = wp.GameProfile(self.game_root, item["path"])
        self.log(f"载入账号 {item['username']}（{item['path']}）")
        self.refresh_all(full=True)

    def open_save_folder(self):
        if self.profile is None:
            return
        import os
        os.startfile(str(self.profile.save_dir))  # noqa: S606 - 打开资源管理器

    # ------------------------------------------------------------------ 刷新
    def refresh_all(self, full: bool = False):
        if self.profile is None:
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self.refresh_guard()
            self.snapshot = self.profile.snapshot()
            self.owned = set(self.snapshot["owned"])
            if full or not self.rows:
                self.rows = self.profile.all_units()
                self.hidden_rows = self.profile.hidden_units()
            self._update_info()
            self._update_currency_fields()
            self.render_vehicles()
            self.render_hidden()
            self.status_var.set(
                f"就绪　│　账号 {self.snapshot['name']}　│　修订 {self.snapshot['revision']}"
                f"　│　已拥有载具 {len(self.owned)}")
            self._mark_ready()
        except Exception as error:  # noqa: BLE001 - 界面上必须给出原因
            self.log("刷新失败：" + repr(error))
            write_crash("refresh", traceback.format_exc())
            messagebox.showerror(APP_TITLE, f"读取存档失败：\n{error}")
        finally:
            self.config(cursor="")

    def refresh_guard(self):
        self._game_busy = wp.game_running()
        self._live = None
        if self._game_busy and self.profile is not None:
            try:
                self._live = self.profile.live()
            except Exception as error:  # noqa: BLE001
                self.log("实时通道不可用：" + repr(error))
        if self._live:
            self.guard_var.set("⚡ 游戏运行中：实时模式已连接，写入立即生效（回机库后刷新显示）")
        elif self._game_busy:
            self.guard_var.set("⚠ 游戏运行中且未连上本地服务：只读模式，关闭游戏后才能写入")
        else:
            self.guard_var.set("")
        self._update_write_state()
        return bool(self._live) or not self._game_busy

    def _update_write_state(self):
        writable = (not self._game_busy) or bool(self._live)
        state = "normal" if writable else "disabled"
        for widget in self._write_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _update_info(self):
        snap = self.snapshot
        if not snap:
            return
        self.info_var.set(
            f"账号 {snap['name']}　│　金鹰 {fmt(snap['gold'])}　│　"
            f"银狮 {fmt(snap['silver'])}　│　Gaijin 币 {fmt(snap['gaijinCoins'])}　│　"
            f"修订 {snap['revision']}　│　存档时间 {snap['savedAt']}")

    def _update_currency_fields(self):
        snap = self.snapshot
        if not snap:
            return
        values = {"gold": snap["gold"], "silver": snap["silver"],
                  "coins": snap["gaijinCoins"]}
        for key, value in values.items():
            entry = self.currency_entries[key]
            entry.delete(0, "end")
            entry.insert(0, str(value))
            self.currency_current[key].set(f"当前：{fmt(value)}")

    # ------------------------------------------------------------------ 筛选
    def _country_code(self) -> str:
        label = self.country_var.get()
        for code, name in wp.COUNTRY_LABELS.items():
            if name == label:
                return code
        return ""

    def _group_code(self) -> str:
        label = self.group_var.get()
        for code, name in wp.GROUP_LABELS.items():
            if name == label:
                return code
        return ""

    def _filtered_rows(self) -> list[dict]:
        search = self.search_var.get().strip().lower()
        country = self._country_code()
        group = self._group_code()
        state = self.state_var.get()
        result = []
        for row in self.rows:
            if country and row["country"] != country:
                continue
            if group and row["group"] != group:
                continue
            if state == "仅未拥有" and row["id"] in self.owned:
                continue
            if state == "仅已拥有" and row["id"] not in self.owned:
                continue
            if state == "仅隐藏载具池" and not row["inHiddenPool"]:
                continue
            if search and search not in row["id"].lower() and search not in row["name"].lower() \
                    and search not in row["nameEn"].lower():
                continue
            result.append(row)
        return result

    def reset_filters(self):
        self.search_var.set("")
        self.country_var.set("全部")
        self.group_var.set("全部")
        self.state_var.set("全部")
        self.render_vehicles()

    # ------------------------------------------------------------------ 表格
    @staticmethod
    def _price_text(row: dict) -> str:
        if row["gold"]:
            return f"金鹰 {fmt(row['gold'])}"
        if row["silver"]:
            return f"银狮 {fmt(row['silver'])}"
        return "—"

    @staticmethod
    def _tags_text(row: dict) -> str:
        tags = []
        if row["event"]:
            tags.append("活动")
        if row["gift"]:
            tags.append("礼包")
        if row["gold"]:
            tags.append("金币载具")
        if row["inHiddenPool"]:
            tags.append("隐藏池")
        if not row["inShop"]:
            tags.append("商店无售")
        return " / ".join(tags)

    def _insert_rows(self, tree: ttk.Treeview, rows: list[dict], hidden_tab: bool):
        tree.delete(*tree.get_children())
        for row in rows:
            owned = row["id"] in self.owned
            tag = "owned" if owned else ("hidden" if row["inHiddenPool"] else "normal")
            if hidden_tab:
                values = (row["displayName"], row["id"], "已拥有" if owned else "未拥有",
                          row["classLabel"], row["countryLabel"], row["rank"],
                          row["battleRating"], self._price_text(row), self._tags_text(row))
            else:
                values = (row["displayName"], row["id"], "已拥有" if owned else "未拥有",
                          row["classLabel"], row["countryLabel"], row["groupLabel"],
                          row["rank"], row["battleRating"], self._price_text(row),
                          self._tags_text(row))
            tree.insert("", "end", iid=row["id"], values=values, tags=(tag,))

    def render_vehicles(self):
        rows = self._filtered_rows()
        self._insert_rows(self.vehicle_tree, rows, hidden_tab=False)
        self.vehicle_count_var.set(
            f"显示 {len(rows)} / 共 {len(self.rows)} 辆　已拥有 {len(self.owned)} 辆")
        self.btn_unlock_filter.configure(text=f"解锁当前筛选结果（{len(rows)}）")

    def render_hidden(self):
        rows = self.hidden_rows
        self._insert_rows(self.hidden_tree, rows, hidden_tab=True)
        owned_hidden = sum(1 for row in rows if row["id"] in self.owned)
        self.hidden_count_var.set(
            f"隐藏载具 {len(rows)} 辆，已拥有 {owned_hidden} 辆")

    def select_all_vehicles(self):
        self.vehicle_tree.selection_set(self.vehicle_tree.get_children())

    # ------------------------------------------------------------------ 写入
    def guard_write(self) -> bool:
        if self.profile is None:
            return False
        self.refresh_guard()
        if self._game_busy and not self._live:
            messagebox.showerror(
                APP_TITLE,
                "游戏正在运行，但没有连上游戏的本地服务，无法安全写入。\n\n"
                "请退出游戏后修改；如果你刚才才登录，请点“重新检测游戏状态”重试。")
            return False
        if self._live and not self._live_warned:
            answer = messagebox.askyesno(
                APP_TITLE,
                "游戏正在运行，将使用实时模式写入。\n\n"
                "写入由游戏自己的本地服务执行（等同游戏内操作），不会损坏存档。\n"
                "写完后回到机库或重新打开载具界面即可看到变化。\n\n继续吗？")
            if not answer:
                return False
            self._live_warned = True
        if self.snapshot and self.snapshot.get("activeBattle"):
            answer = messagebox.askyesno(
                APP_TITLE,
                "这个存档里有一场未结算的对局。\n\n"
                "现在写入可能影响该局结算。建议先正常进入游戏把对局打完，再退出修改。\n\n"
                "仍要继续吗？")
            return answer
        return True

    def _backup(self) -> Path | None:
        try:
            path = self.profile.backup()
            self.log(f"已备份存档到 {path}")
            return path
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"备份存档失败，已取消写入：\n{error}")
            self.log("备份失败：" + repr(error))
            return None

    def save_currency(self):
        if not self.guard_write():
            return
        snap = self.snapshot or {}
        wanted = {}
        labels = {"gold": "金鹰", "silver": "银狮", "coins": "Gaijin 币"}
        for key, entry in self.currency_entries.items():
            text = entry.get().strip().replace(",", "").replace("，", "")
            if text == "":
                continue
            try:
                value = int(text)
            except ValueError:
                messagebox.showerror(APP_TITLE, f"{labels[key]}必须是整数。")
                return
            if value < 0:
                messagebox.showerror(APP_TITLE, f"{labels[key]}不能为负数。")
                return
            if value > 1_000_000_000_000:
                messagebox.showerror(APP_TITLE, f"{labels[key]}超过上限（1 万亿）。")
                return
            current = {"gold": snap.get("gold"), "silver": snap.get("silver"),
                       "coins": snap.get("gaijinCoins")}[key]
            if value != current:
                wanted[key] = value
        if not wanted:
            messagebox.showinfo(APP_TITLE, "输入值和当前余额一致，没有需要写入的内容。")
            return
        if self._backup() is None:
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            result = self.profile.set_currency(**wanted)
            detail = "，".join(f"{labels[k]}→{fmt(v)}" for k, v in wanted.items())
            self.log(f"写入货币：{detail}；新修订 {result['revision']}")
            self.refresh_all()
            self.status_var.set(f"货币已保存：{detail}（修订 {result['revision']}）")
            hint = ("\n\n实时模式下已写入，回到机库即可看到新余额。"
                    if result.get("mode") == "live" else
                    "\n\n重新进入游戏即可看到新余额。")
            messagebox.showinfo(APP_TITLE, "货币已保存。\n\n" + detail + hint)
        except Exception as error:  # noqa: BLE001
            self.log("写入货币失败：" + repr(error))
            write_crash("currency", traceback.format_exc())
            messagebox.showerror(APP_TITLE, f"写入货币失败：\n{error}")
        finally:
            self.config(cursor="")

    def _unlock(self, units: list[str], source: str):
        units = [unit for unit in dict.fromkeys(units) if unit in self.profile.catalog]
        if not units:
            messagebox.showinfo(APP_TITLE, "没有选中任何载具。")
            return
        if len(units) > 300:
            answer = messagebox.askyesno(
                APP_TITLE,
                f"即将解锁 {len(units)} 辆载具。\n\n"
                "数量较多时会一次性写入，完成后请进游戏确认机库显示正常。\n\n继续吗？")
            if not answer:
                return
        if self._backup() is None:
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            result = self.profile.grant_units(units)
            self.log(f"{source}：请求 {len(units)} 辆，实际新增 {result['added']} 辆，"
                     f"跳过 {len(result['skipped'])} 辆，修订 {result['revision']}")
            for unit, reason in result["skipped"][:20]:
                self.log(f"  跳过 {unit}：{reason}")
            self.refresh_all()
            message = f"解锁完成：新增 {result['added']} 辆载具。"
            if result["skipped"]:
                message += f"\n跳过 {len(result['skipped'])} 辆（需要配套载具等）。"
            if result.get("mode") == "live":
                message += "\n\n实时模式：回到机库刷新后即可在对应国家的乘员组里编入。"
            else:
                message += "\n\n进游戏后在对应国家的乘员组里编入即可使用。"
            messagebox.showinfo(APP_TITLE, message)
        except Exception as error:  # noqa: BLE001
            self.log("解锁失败：" + repr(error))
            write_crash("unlock", traceback.format_exc())
            messagebox.showerror(APP_TITLE, f"解锁失败：\n{error}")
        finally:
            self.config(cursor="")

    def unlock_selected_vehicles(self):
        if not self.guard_write():
            return
        self._unlock(list(self.vehicle_tree.selection()), "解锁选中载具")

    def unlock_filtered(self):
        if not self.guard_write():
            return
        rows = self._filtered_rows()
        self._unlock([row["id"] for row in rows], "解锁筛选结果")

    def unlock_selected_hidden(self):
        if not self.guard_write():
            return
        self._unlock(list(self.hidden_tree.selection()), "解锁选中隐藏载具")

    def unlock_all_hidden(self):
        if not self.guard_write():
            return
        self._unlock([row["id"] for row in self.hidden_rows], "解锁全部隐藏载具")


def main() -> int:
    # pythonw 没有控制台，任何启动异常都必须落盘并弹窗，不能静默退出。
    sys.excepthook = lambda kind, value, tb: write_crash(
        "excepthook", "".join(traceback.format_exception(kind, value, tb)))
    try:
        app = EditorApp()
    except Exception as error:  # noqa: BLE001
        text = traceback.format_exc()
        write_crash("startup", text)
        try:
            messagebox.showerror(APP_TITLE,
                                 f"修改器启动失败：\n{error}\n\n详细信息已写入 logs\\crash.log")
        except tk.TclError:
            pass
        return 1
    try:
        app.mainloop()
    except Exception:  # noqa: BLE001
        write_crash("mainloop", traceback.format_exc())
        return 1
    finally:
        if getattr(app, "profile", None) is not None:
            app.profile.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
