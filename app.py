from collections import Counter
from dataclasses import replace
import json
import os
from pathlib import Path
from queue import Empty, Queue
import sqlite3
from threading import Event, Thread
from time import sleep
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import unicodedata
import webbrowser

from app_paths import LOCAL_DATA
from auto_scan import ScanJob, migrate_anchors, worker
from capture import capture_game, collect_with_hotkey
from echoes import Inventory, PERCENT_STATS
from ingest import Reader, ReadResult
from optimizer import Profile, evaluate, solve
from profiles import ALL_SONATAS, GUIDES, OFFICIAL_GUIDES, PREVIOUS_GUIDES, ROLE_ORDER, guide_defaults
from rules import SETS
from scanner import SONATA_NAMES


LOCAL = LOCAL_DATA
BG = "#101827"
PANEL = "#182437"
ROW = "#1c2a40"
EDGE = "#30415b"
TEXT = "#eff5fc"
MUTED = "#a8b9ce"
ACCENT = "#76e1c6"
ACCENT_TEXT = "#102e2b"
BUILD_BORDER = "#eb6670"
FONT = ("Microsoft YaHei UI", 10)
TARGETS = (("target_crit_rate", "暴击目标 %"), ("target_crit_damage", "暴伤目标 %"),
           ("target_energy", "共鸣效率目标 %"))


def display_stats(stats):
    return "\n".join(f"{name.removesuffix('%')} {value:g}{'%' if name in PERCENT_STATS else ''}"
                     for name, value in stats)


def setup_theme(root):
    root.configure(background=BG)
    root.option_add("*Font", FONT)
    root.option_add("*TCombobox*Listbox.background", ROW)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", "#35566a")
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=FONT, background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Title.TLabel", font=(FONT[0], 24, "bold"))
    style.configure("Section.TLabel", background=PANEL, font=(FONT[0], 13, "bold"))
    style.configure("Name.TLabel", background=PANEL, font=(FONT[0], 19, "bold"))
    style.configure("Count.TLabel", background=PANEL, foreground=ACCENT, font=(FONT[0], 11, "bold"))
    style.configure("Hint.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Status.TLabel", foreground=MUTED)
    style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Echo.TFrame", background=ROW)
    style.configure("Echo.TLabel", background=ROW, foreground=TEXT)
    style.configure("EchoName.TLabel", background=ROW, foreground=TEXT, font=(FONT[0], 10, "bold"))
    style.configure("EchoMeta.TLabel", background=ROW, foreground=MUTED)
    style.configure("TButton", background=ROW, bordercolor=EDGE, lightcolor=ROW,
                    darkcolor=ROW, padding=(14, 8), foreground=TEXT, focusthickness=1)
    style.map("TButton", background=[("active", "#304660"), ("pressed", "#263850")])
    style.configure("Primary.TButton", background=ACCENT, foreground=ACCENT_TEXT,
                    lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=ACCENT,
                    font=(FONT[0], 10, "bold"), padding=(19, 9))
    style.map("Primary.TButton", background=[("active", "#a8f3e1"), ("pressed", "#5dcdb1")],
              foreground=[("active", ACCENT_TEXT)])
    style.configure("TMenubutton", background=ROW, foreground=TEXT, bordercolor=EDGE,
                    lightcolor=ROW, darkcolor=ROW, padding=(13, 8), arrowcolor=TEXT)
    menu_fill = [("pressed", "#263850"), ("active", "#304660")]
    style.map("TMenubutton", background=menu_fill, lightcolor=menu_fill, darkcolor=menu_fill,
              foreground=[("pressed", TEXT), ("active", TEXT)],
              arrowcolor=[("pressed", TEXT), ("active", TEXT)],
              bordercolor=[("pressed", EDGE), ("active", EDGE), ("focus", "#7d9ab5")])
    style.layout("Page.TRadiobutton", [
        ("Button.border", {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("Page.TRadiobutton", background=PANEL, foreground=MUTED,
                    bordercolor=EDGE, lightcolor=PANEL, darkcolor=PANEL, borderwidth=1,
                    anchor="center", padding=(12, 9), font=(FONT[0], 10, "bold"))
    page_fill = [("selected", ACCENT), ("active", ROW)]
    style.map("Page.TRadiobutton", background=page_fill, lightcolor=page_fill, darkcolor=page_fill,
              foreground=[("selected", ACCENT_TEXT), ("active", TEXT)],
              bordercolor=[("selected", ACCENT), ("focus", "#7d9ab5")])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    borderwidth=0, rowheight=34)
    style.map("Treeview", background=[("selected", "#244c53")],
              foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=ROW, foreground=MUTED, borderwidth=0,
                    padding=(7, 7), font=(FONT[0], 9, "bold"))
    style.map("Treeview.Heading", background=[("active", ROW)])
    style.configure("Vertical.TScrollbar", background=EDGE, troughcolor=PANEL,
                    bordercolor=PANEL, darkcolor=EDGE, lightcolor=EDGE, arrowcolor=MUTED)
    for widget in ("TEntry", "TCombobox"):
        style.configure(widget, fieldbackground=ROW, foreground=TEXT, bordercolor=EDGE,
                        lightcolor=ROW, darkcolor=ROW, insertcolor=TEXT, padding=5)
    style.configure("TCombobox", background=ROW, arrowcolor=MUTED,
                    selectbackground="#35566a", selectforeground=TEXT)
    style.map("TCombobox", fieldbackground=[("disabled", PANEL), ("readonly", ROW)],
              foreground=[("disabled", MUTED), ("readonly", TEXT)],
              background=[("pressed", "#263850"), ("active", "#304660")],
              bordercolor=[("focus", ACCENT)], arrowcolor=[("active", TEXT)])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=ROW,
                    bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)


class SearchCombobox(ttk.Combobox):
    def __init__(self, parent, variable, choices=(), *, label, on_error,
                 on_select=None, allow_custom=False, **options):
        super().__init__(parent, textvariable=variable, height=10, **options)
        self.variable = variable
        self.label = label
        self.on_error = on_error
        self.on_select = on_select
        self.allow_custom = allow_custom
        self.committed = self.get()
        self.set_choices(choices)
        self.trace = variable.trace_add("write", self.filter_choices)
        self.bind("<Destroy>", self.remove_trace)
        self.bind("<Return>", self.select_from_input)
        self.bind("<Control-a>", self.select_text)
        self.bind("<<ComboboxSelected>>", self.select_from_list)

    @staticmethod
    def search_key(value):
        return "".join(unicodedata.normalize("NFKC", value).casefold().split())

    def matching_choices(self):
        query = self.search_key(self.variable.get())
        return tuple(choice for choice in self.choices if query in self.search_key(choice))

    def set_choices(self, choices):
        self.choices = tuple(dict.fromkeys(choices))
        self.filter_choices()

    def filter_choices(self, *_):
        choices = self.choices if self.variable.get() == self.committed else self.matching_choices()
        self.configure(values=choices)

    def commit(self, value):
        self.committed = value
        self.variable.set(value)

    def resolve(self):
        text = self.get().strip()
        if text and (text in self.choices or (self.allow_custom and text == self.committed)):
            return text
        if text:
            matches = self.matching_choices()
            exact = next((choice for choice in matches
                          if self.search_key(choice) == self.search_key(text)), None)
            if exact:
                return exact
            if len(matches) == 1:
                return matches[0]
            if self.allow_custom and not matches:
                return text
            if matches:
                raise ValueError(f"「{self.label}」有多个匹配项，请展开下拉框选择")
        raise ValueError(f"请选择有效的「{self.label}」")

    def select_from_input(self, _event):
        try:
            self.commit(self.resolve())
        except ValueError as error:
            self.on_error(str(error))
        else:
            if self.on_select:
                self.on_select()
        return "break"

    def select_from_list(self, _event):
        self.commit(self.get())
        if self.on_select:
            self.on_select()

    def select_text(self, _event):
        self.selection_range(0, "end")
        return "break"

    def remove_trace(self, _event):
        self.variable.trace_remove("write", self.trace)


class Window:
    def __init__(self, root):
        self.root = root
        root.title("MingchaoHelp · 鸣潮助手")
        root.geometry("1120x760")
        root.minsize(940, 660)
        root.protocol("WM_DELETE_WINDOW", self.close)
        setup_theme(root)
        self.inventory = Inventory(LOCAL / "inventory.sqlite3")
        self.inventory.clean_deleted_cards()
        self.result_queue = Queue()
        self.progress = Queue()
        self.capture_stop = None
        self.auto_job = None
        self.busy = False
        self.echoes = {}
        self.status = tk.StringVar(value="准备就绪")

        header = ttk.Frame(root, padding=(24, 17, 24, 9))
        header.pack(fill="x")
        ttk.Label(header, text="鸣潮助手", style="Title.TLabel").pack(side="left")
        self.header_count = tk.StringVar()
        ttk.Label(header, textvariable=self.header_count, style="Status.TLabel").pack(side="right")
        navigation = ttk.Frame(root, padding=(24, 3, 24, 13))
        navigation.pack(fill="x")
        self.selected_page = tk.StringVar(value="inventory")
        for page, label in (("inventory", "声骸库存"), ("optimizer", "计算搭配")):
            ttk.Radiobutton(navigation, text=label, width=10, value=page,
                            variable=self.selected_page, style="Page.TRadiobutton",
                            command=self.show_page).pack(side="left", padx=(0, 8))
        self.content = ttk.Frame(root)
        self.content.pack(fill="both", expand=True, padx=24, pady=(0, 3))
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        footer = ttk.Frame(root, padding=(24, 3, 24, 13))
        footer.pack(side="bottom", fill="x", before=self.content)
        self.indicator = ttk.Progressbar(footer, mode="indeterminate", length=86)
        self.indicator.pack(side="right", padx=(14, 0))
        ttk.Label(footer, textvariable=self.status, style="Status.TLabel").pack(side="left")

        self.pages = {"inventory": self.build_inventory_tab(),
                      "optimizer": self.build_optimizer_tab()}
        self.show_page()
        self.refresh()
        if any((LOCAL / "pending").glob("*.png")) or any((LOCAL / "captures").glob("*.png")) or any(
                (LOCAL / "icons").glob("*.png")) or any((LOCAL / "auto-scan").glob("*/start.png")):
            self.root.after(100, self.migrate)

    def show_page(self):
        selected = self.selected_page.get()
        for name, page in self.pages.items():
            if name == selected:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()

    def build_inventory_tab(self):
        tab = ttk.Frame(self.content)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(tab, style="Card.TFrame", padding=(17, 13))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        scan_text = ttk.Frame(toolbar, style="Card.TFrame")
        scan_text.pack(side="left", fill="x", expand=True)
        ttk.Label(scan_text, text="背包采集", style="Section.TLabel").pack(anchor="w")
        ttk.Label(scan_text, text="按等级从高到低排序 · 扫完 +25 自动停止 · Esc 暂停",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
        self.auto_button = ttk.Button(toolbar, text="自动扫描", command=self.toggle_auto_scan,
                                      style="Primary.TButton")
        self.auto_button.pack(side="right")
        ttk.Button(toolbar, text="导入截图", command=self.import_images).pack(side="right", padx=8)
        more = ttk.Menubutton(toolbar, text="其他采集")
        more_menu = tk.Menu(more, tearoff=False, background=PANEL, foreground=TEXT,
                            activebackground=ROW, activeforeground=TEXT,
                            borderwidth=0, activeborderwidth=0, relief="flat")
        more_menu.add_command(label="3 秒后抓取当前声骸", command=self.capture)
        more_menu.add_command(label="开启或结束快捷键采集", command=self.toggle_capture)
        more.configure(menu=more_menu)
        more.pack(side="right")

        listing = ttk.Frame(tab, padding=(14, 12), style="Card.TFrame")
        listing.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        listing.rowconfigure(1, weight=1)
        listing.columnconfigure(0, weight=1)

        table_header = ttk.Frame(listing, style="Card.TFrame")
        table_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 11))
        ttk.Label(table_header, text="已入库", style="Section.TLabel").pack(side="left")
        self.count = tk.StringVar()
        ttk.Label(table_header, textvariable=self.count, style="Count.TLabel").pack(side="left", padx=10)
        ttk.Button(table_header, text="按套装清除", command=self.choose_sonata_to_clear).pack(side="left")
        self.filter_text = tk.StringVar()
        query = ttk.Entry(table_header, textvariable=self.filter_text, width=20)
        query.pack(side="right")
        ttk.Label(table_header, text="查找", style="Hint.TLabel").pack(side="right", padx=8)

        columns = [("name", "声骸", 164), ("cost", "COST", 53), ("level", "等级", 55),
                   ("sonata", "合鸣套装", 134), ("owner", "穿戴角色", 106)]
        self.tree = ttk.Treeview(listing, columns=[c[0] for c in columns], show="headings",
                                 selectmode="browse")
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            center = key in {"cost", "level"}
            self.tree.column(key, width=width, stretch=not center, anchor="center" if center else "w")
        self.tree.tag_configure("odd", background=ROW)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self.show_echo)
        self.filter_text.trace_add("write", lambda *_: self.render_inventory())
        self.inventory_note = tk.StringVar()
        ttk.Label(listing, textvariable=self.inventory_note, style="Hint.TLabel").grid(
            row=2, column=0, sticky="w", pady=(9, 0))

        side = ttk.Frame(tab, padding=17, width=305, style="Card.TFrame")
        side.grid(row=1, column=1, sticky="nsew")
        ttk.Label(side, text="声骸详情", style="Section.TLabel").pack(anchor="w")
        self.echo_name = tk.StringVar(value="请选择声骸")
        ttk.Label(side, textvariable=self.echo_name, style="Name.TLabel", wraplength=265).pack(
            anchor="w", fill="x", pady=(14, 3))
        self.echo_meta = tk.StringVar()
        ttk.Label(side, textvariable=self.echo_meta, style="Hint.TLabel", wraplength=265).pack(
            anchor="w", fill="x", pady=(0, 13))
        self.stat_panels = {}
        for key, label in (("main", "主属性"), ("sub", "副词条")):
            ttk.Label(side, text=label, style="Section.TLabel").pack(anchor="w", pady=(9, 5))
            info = tk.StringVar(value="—")
            ttk.Label(side, textvariable=info, width=31, style="Card.TLabel",
                      font=(FONT[0], 10)).pack(anchor="w", fill="x")
            self.stat_panels[key] = info
        self.detail = tk.StringVar()
        ttk.Label(side, textvariable=self.detail, style="Hint.TLabel", wraplength=265).pack(
            anchor="w", fill="x", pady=(10, 0))
        return tab

    def refresh(self):
        self.echoes = {echo.id: echo for echo in self.inventory.all()}
        self.update_sonata_choices()
        failures = self.inventory.failure_count()
        uncertain = sum(bool(echo.warnings) for echo in self.echoes.values())
        self.header_count.set(f"本地库存  {len(self.echoes):,} 件")
        self.inventory_note.set(f"字段未识别 {uncertain:,} 件  ·  整条未识别 {failures:,} 件"
                                if failures or uncertain else "识别完成的词条会自动入库")
        self.render_inventory()

    def render_inventory(self):
        selected = self.tree.selection()
        query = self.filter_text.get().strip().casefold()
        y = self.tree.yview()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for echo in self.echoes.values():
            if query and query not in f"{echo.name} {echo.sonata} {echo.owner}".casefold():
                continue
            self.tree.insert("", "end", iid=echo.id, tags=("odd",) if shown % 2 else (),
                             values=(echo.name, echo.cost, f"+{echo.level}", echo.sonata, echo.owner or "—"))
            shown += 1
        self.count.set(f"{shown:,} 件")
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        elif shown:
            self.tree.selection_set(self.tree.get_children()[0])
        self.tree.yview_moveto(y[0])
        self.show_echo()

    def show_echo(self, _event=None):
        selected = self.tree.selection()
        echo = self.echoes.get(selected[0]) if selected else None
        self.echo_name.set(echo.name if echo else "请选择声骸")
        if echo:
            meta = f"COST {echo.cost}  ·  +{echo.level}\n{echo.sonata}"
            if echo.owner:
                meta += f"  ·  {echo.owner}"
        else:
            meta = ""
        self.echo_meta.set(meta)
        for key, panel in self.stat_panels.items():
            panel.set(display_stats(getattr(echo, key)) or "—" if echo else "—")
        self.detail.set(" · ".join(echo.warnings) if echo else "")

    def choose_sonata_to_clear(self):
        if self.busy:
            return
        counts = Counter(echo.sonata for echo in self.echoes.values())
        if not counts:
            self.status.set("库存里没有可以清除的声骸")
            return
        selected = self.tree.selection()
        echo = self.echoes.get(selected[0]) if selected else None
        names = sorted(counts)
        dialog = tk.Toplevel(self.root)
        dialog.title("按合鸣套装清除")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill="both", expand=True)
        sonata = tk.StringVar(value=echo.sonata if echo else names[0])
        ttk.Label(form, text="要清除的套装").grid(row=0, column=0, padx=(0, 10))
        count = tk.StringVar()
        choice = SearchCombobox(form, sonata, names, label="合鸣套装", on_error=count.set, width=24)
        choice.grid(row=0, column=1)

        def show_count(*_):
            name = sonata.get()
            count.set(f"库中有 {counts[name]:,} 件" if name in counts else "输入套装名称或从下拉框选择")

        trace = sonata.trace_add("write", show_count)
        dialog.bind("<Destroy>", lambda event: sonata.trace_remove("write", trace)
                    if event.widget is dialog else None)
        show_count()
        ttk.Label(form, textvariable=count).grid(row=1, column=1, sticky="w", pady=(6, 12))

        def clear():
            try:
                name = choice.resolve()
            except ValueError as error:
                count.set(str(error))
                return
            choice.commit(name)
            if not messagebox.askyesno(
                "确认清除",
                f"清除「{name}」的 {counts[name]:,} 条本地声骸记录？此操作无法撤销。",
                icon="warning", parent=dialog,
            ):
                return
            try:
                job = ScanJob.latest(LOCAL)
                state = job.read() if job and job.folder.is_dir() else {}
                removed, pending = self.inventory.clear_sonata(name)
            except (OSError, sqlite3.Error, ValueError) as error:
                messagebox.showerror("清除失败", str(error), parent=dialog)
                return
            notice = f"已清除「{name}」的 {len(removed):,} 件声骸"
            if state and (state.get("sonata_filter") == name or
                          any(key.startswith(f"capture-auto-{job.id}-") for key in removed)):
                try:
                    job.write("complete", "该套装已清除，下次请从头扫描")
                except OSError:
                    notice += " · 下次扫描请选择从头开始"
            if pending:
                notice += f" · {pending:,} 张卡片将在下次启动时重试清除"
            dialog.destroy()
            self.refresh()
            self.set_output("库存已更新，请重新计算搭配。")
            self.status.set(notice)

        ttk.Button(form, text="取消", command=dialog.destroy).grid(row=2, column=0, sticky="e")
        ttk.Button(form, text="清除套装", command=clear).grid(row=2, column=1, sticky="e")
        dialog.grab_set()
        choice.focus_set()

    def run(self, task, callback, on_error=None):
        self.busy = True
        self.indicator.start(12)

        def do_work():
            try:
                self.result_queue.put((None, task()))
            except Exception as error:
                self.result_queue.put((error, None))

        def collect():
            try:
                while True:
                    self.status.set(self.progress.get_nowait())
            except Empty:
                pass
            try:
                error, result = self.result_queue.get_nowait()
            except Empty:
                self.root.after(80, collect)
                return
            self.busy = False
            self.indicator.stop()
            if error:
                self.status.set(str(error))
                self.refresh()
                if on_error:
                    on_error()
            else:
                callback(result)

        Thread(target=do_work, daemon=True).start()
        self.root.after(80, collect)

    def migrate(self):
        if self.busy:
            self.root.after(100, self.migrate)
            return

        def task():
            with Reader(LOCAL) as reader:
                count = reader.migrate(self.progress.put)
            migrate_anchors(LOCAL)
            return count

        def finished(count):
            self.refresh()
            self.status.set(f"旧版待处理记录已自动识别 · {count} 件" if count else "准备就绪")

        self.run(task, finished)

    def import_images(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(title="选择声骸详情截图",
                                            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if paths:
            self.read_images(tuple(Path(path) for path in paths))

    def read_images(self, sources):
        def task():
            results = []
            with Reader(LOCAL) as reader:
                for number, source in enumerate(sources, 1):
                    self.progress.put(f"正在识别 · {number} / {len(sources)}")
                    results.append(reader.record(source))
            return results

        self.run(task, self.read_finished)

    def read_finished(self, result):
        self.refresh()
        saved = result.count(ReadResult.SAVED)
        skipped = result.count(ReadResult.SKIPPED)
        failed = result.count(ReadResult.FAILED)
        self.status.set(f"已入库 +25 声骸 {saved} 件 · 跳过 {skipped} 件 · 识别失败 {failed} 件")

    def capture(self):
        if self.busy:
            return
        self.busy = True
        self.status.set("3 秒后截图 · 请切回鸣潮并选中一件声骸")
        self.root.after(3000, self.capture_current)

    def capture_current(self):
        def task():
            with Reader(LOCAL) as reader:
                return [reader.record(capture_game())]

        self.run(task, self.read_finished)

    def toggle_capture(self):
        if self.capture_stop:
            self.capture_stop.set()
            return
        if self.busy:
            return
        stop = Event()
        self.capture_stop = stop

        def task():
            with Reader(LOCAL) as reader:
                return collect_with_hotkey(stop, self.progress.put, reader.record)

        def reset():
            self.capture_stop = None

        def finished(result):
            reset()
            self.read_finished(result)

        self.run(task, finished, reset)

    def toggle_auto_scan(self):
        if self.auto_job:
            self.auto_job.stop_flag.touch()
            self.status.set("正在停止自动扫描")
            return
        if self.busy:
            return
        job = ScanJob.latest(LOCAL)
        if job and job.folder.is_dir() and job.read()["phase"] != "complete" and job.read()["next"]:
            resume = messagebox.askyesnocancel(
                "继续自动扫描",
                "发现未完成的扫描。选择「是」从上次位置继续，「否」从头扫描。",
            )
            if resume is None:
                return
            if not resume:
                job = None
        elif job and (job.read()["phase"] == "complete" or not job.folder.is_dir()):
            job = None
        if job is None:
            job = ScanJob.create(LOCAL)
        job.prepare()
        self.auto_job = job
        self.auto_button.configure(text="停止自动扫描")
        self.status.set("等待 Windows 授权 · 授权后切回声骸背包顶部")

        def execute():
            process = job.start(Path(__file__).resolve().with_name("auto_scan.py"), os.getpid())
            try:
                while process.running():
                    sleep(.18)
                state = job.read()
                if state["phase"] in {"waiting", "running"}:
                    job.write("stopped", "扫描进程提前退出，已写入的词条仍在库存")
                return job.read()
            finally:
                process.close()

        def reset():
            self.auto_job = None
            self.auto_button.configure(text="自动扫描")

        def finished(state):
            reset()
            self.refresh()
            failures = state.get("failed", 0)
            suffix = f" · 本轮有 {failures} 件未识别" if failures else ""
            unreadable = state.get("unreadable_badges", 0)
            if unreadable:
                suffix += f" · {unreadable} 张等级未读清"
            self.status.set(state["message"] + suffix)

        last_count = job.read()["count"]

        def poll():
            nonlocal last_count
            if self.auto_job is not job:
                return
            try:
                state = job.read()
                if state["phase"] == "running" and not job.stop_flag.exists():
                    self.status.set(state["message"])
                if state["count"] != last_count:
                    last_count = state["count"]
                    self.refresh()
            except OSError:
                pass
            self.root.after(1000, poll)

        self.run(execute, finished, reset)
        self.root.after(250, poll)

    optimizer_prompt = "选择角色和合鸣套装，按需填写目标，再点击「开始计算」。"

    def build_optimizer_tab(self):
        tab = ttk.Frame(self.content)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        overview = ttk.Frame(tab, style="Card.TFrame", padding=(16, 12))
        overview.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        overview.columnconfigure(8, weight=1)
        self.profile = {}
        ttk.Label(overview, text="角色", style="Card.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.profile["name"] = tk.StringVar(value="心")
        self.role_choice = SearchCombobox(
            overview, self.profile["name"], ROLE_ORDER, label="角色", allow_custom=True,
            on_select=self.load_role, on_error=self.status.set, width=10)
        self.role_choice.grid(row=0, column=1, padx=(0, 14))
        for column, (key, title) in zip((2, 4, 6), TARGETS):
            value = tk.StringVar()
            self.profile[key] = value
            ttk.Label(overview, text=title, style="Card.TLabel").grid(row=0, column=column, padx=(0, 6))
            ttk.Entry(overview, textvariable=value, width=7).grid(row=0, column=column + 1, padx=(0, 12))
        ttk.Button(overview, text="开始计算", command=self.search, style="Primary.TButton").grid(
            row=0, column=9, sticky="e")
        self.source_button = ttk.Button(overview, text="查看攻略", command=self.open_role_guide)
        self.source_button.grid(row=0, column=8, sticky="e", padx=(0, 8))
        self.profile["required_set"] = tk.StringVar()
        ttk.Label(overview, text="合鸣套装", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        set_var = self.profile["required_set"]
        self.sonata_choice = SearchCombobox(
            overview, set_var, label="合鸣套装", on_error=self.status.set,
            on_select=self.update_first_echo, width=25)
        self.sonata_choice.grid(row=1, column=1, columnspan=3, sticky="w", pady=(8, 0))
        self.first_echo_text = tk.StringVar()
        ttk.Label(overview, textvariable=self.first_echo_text, style="Card.TLabel",
                  wraplength=395).grid(row=1, column=4, columnspan=6, sticky="w", padx=(10, 0), pady=(8, 0))
        self.guide_description = tk.StringVar()
        ttk.Label(overview, textvariable=self.guide_description, style="Hint.TLabel",
                  wraplength=625).grid(row=2, column=0, columnspan=8, sticky="w", pady=(7, 0))
        self.reset_button = ttk.Button(overview, text="恢复攻略推荐", command=self.reset_role_guide)
        self.reset_button.grid(row=2, column=8, columnspan=2, sticky="e", pady=(7, 0))

        self.output = tk.Canvas(tab, background=PANEL, highlightthickness=0,
                                borderwidth=0, width=0, yscrollincrement=24)
        self.output.grid(row=1, column=0, sticky="nsew")
        self.output_body = ttk.Frame(self.output, style="Card.TFrame", padding=(14, 12))
        output_window = self.output.create_window((0, 0), window=self.output_body, anchor="nw")
        self.output.bind("<Configure>", lambda event: self.output.itemconfigure(output_window, width=event.width))
        self.output_body.bind("<Configure>", lambda _: self.output.configure(scrollregion=self.output.bbox("all")))
        scroll = ttk.Scrollbar(tab, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns")
        self.root.bind("<MouseWheel>", self.scroll_results, add=True)
        self.profile_path = LOCAL / "character-settings.json"
        try:
            self.role_settings = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.role_settings = {}
        self.role_choice.set_choices((*ROLE_ORDER, *self.role_settings))
        self.load_role()
        self.set_output(self.optimizer_prompt)
        return tab

    def load_role(self):
        name = self.profile["name"].get().strip()
        self.role_choice.commit(name)
        self.loaded_role = name
        guide = GUIDES.get(name)
        source = "库洛攻略站" if name in OFFICIAL_GUIDES else "查看攻略"
        state = "normal" if guide else "disabled"
        self.source_button.configure(text=source, state=state)
        reset = "恢复官方推荐" if name in OFFICIAL_GUIDES else "恢复攻略推荐"
        self.reset_button.configure(text=reset, state=state)
        if guide:
            main = "  ·  ".join(f"{cost} COST {' / '.join(stats)}" for cost, stats in guide.mains)
            self.guide_description.set(main)
        else:
            self.guide_description.set("自定义角色 · 可设置暴击、暴伤和共鸣效率目标")
        self.apply_role_fields(guide_defaults(guide), self.saved_role_settings(name))

    def saved_role_settings(self, name):
        saved = self.role_settings.get(name, {})
        previous = PREVIOUS_GUIDES.get(name)
        if GUIDES.get(name) != previous and saved and not saved.get("uses_guide_defaults"):
            previous_defaults = guide_defaults(previous)
            previous_defaults.pop("required_set")
            saved = {key: value for key, value in saved.items() if value != previous_defaults.get(key)}
        return saved

    def apply_role_fields(self, defaults, overrides=None):
        overrides = overrides or {}
        for key, _ in TARGETS:
            self.profile[key].set(overrides.get(key, defaults[key]))
        self.sonata_choice.commit(overrides.get("required_set") or defaults["required_set"])
        self.update_sonata_choices()
        self.update_first_echo()

    def reset_role_guide(self):
        if self.busy:
            return
        try:
            name = self.role_choice.resolve()
        except ValueError as error:
            self.status.set(str(error))
            return
        guide = GUIDES.get(name)
        if not guide:
            self.status.set("该角色暂无攻略推荐")
            return
        self.role_choice.commit(name)
        if name != self.loaded_role:
            self.load_role()
        self.apply_role_fields(guide_defaults(guide))
        self.role_settings[name] = {"uses_guide_defaults": True}
        self.set_output(self.optimizer_prompt)
        try:
            self.save_role_settings()
        except OSError:
            self.status.set("已恢复推荐值，但无法写入角色设置")
        else:
            self.status.set("已恢复推荐值，可以重新计算搭配")

    def save_role_settings(self):
        temporary = self.profile_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.role_settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.profile_path)

    def update_sonata_choices(self):
        guide = GUIDES.get(self.loaded_role)
        recommended = guide.sonata_choices if guide else ()
        known = set(SONATA_NAMES) | {echo.sonata for echo in self.echoes.values()
                                     if echo.sonata != "未识别"}
        selected = self.sonata_choice.committed
        choices = dict.fromkeys((*recommended, ALL_SONATAS, *sorted(known), selected))
        self.sonata_choice.set_choices(choices)

    def update_first_echo(self):
        guide = GUIDES.get(self.loaded_role)
        _, firsts = guide.echo_setup(self.sonata_choice.committed) if guide else ((), ())
        self.first_echo_text.set("首位声骸  " + (" / ".join(firsts) if firsts else "自动选择"))

    def open_role_guide(self):
        guide = GUIDES.get(self.profile["name"].get().strip())
        if guide:
            webbrowser.open(guide.url)

    def search(self):
        if self.busy:
            return
        try:
            name = self.role_choice.resolve()
            self.role_choice.commit(name)
            if name != self.loaded_role:
                self.load_role()
            selected = self.sonata_choice.resolve()
            self.sonata_choice.commit(selected)
            required_set = "" if selected == ALL_SONATAS else selected
            settings = {key: self.profile[key].get().strip() for key, _ in TARGETS}
            targets = {key: float(value or 0) for key, value in settings.items()}
            settings["required_set"] = selected

            guide = GUIDES.get(name)
            recommendations = guide_defaults(guide)
            groups = tuple(tuple(stat.strip() for stat in group.replace("＝", "=").split("="))
                           for group in guide.priorities) if guide else ()
            profile = Profile(
                name=name,
                **targets,
                element=recommendations["element"],
                scaling=recommendations["scaling"],
                base=10000 if recommendations["scaling"] == "生命" else 1000,
                ranking=recommendations["ranking"],
                priorities=tuple(stat for group in groups for stat in group),
                priority_ranks=tuple(rank for rank, group in enumerate(groups) for _ in group),
                main_choices=guide.mains if guide else (),
                recommended_sets=guide.sonatas if guide else (),
            )
            requirements, firsts = guide.echo_setup(selected) if guide else (
                ((required_set, 5),) if required_set else (), ())
            profile.validate()
        except ValueError as error:
            self.status.set(str(error))
            return
        echoes = list(self.echoes.values())
        overrides = {key: value for key, value in settings.items() if value != recommendations[key]}
        self.role_settings[profile.name] = {**overrides, "uses_guide_defaults": True}
        self.role_choice.set_choices((*ROLE_ORDER, *self.role_settings))
        self.update_first_echo()
        status = (f"正在搜索「{required_set}」" if required_set else
                  f"正在从 {len(echoes)} 件库存中搜索")
        try:
            self.save_role_settings()
        except OSError:
            status += " · 无法写入角色设置"
        self.status.set(status)
        self.run(lambda: solve(profile, echoes, set_requirements=requirements, first_echoes=firsts),
                 lambda result: self.show_results(profile, result, echoes))

    def scroll_results(self, event):
        if event.widget == self.output or str(event.widget).startswith(f"{self.output}."):
            self.output.yview_scroll(-int(event.delta / 120), "units")
            return "break"

    def clear_output(self):
        for child in self.output_body.winfo_children():
            child.destroy()
        self.output.yview_moveto(0)

    def result_label(self, parent, text, style="Card.TLabel", **options):
        label = ttk.Label(parent, text=text, style=style, justify="left", **options)
        label.bind("<Configure>", lambda event: label.configure(wraplength=event.width))
        return label

    def set_output(self, text):
        self.clear_output()
        self.result_label(self.output_body, text).pack(fill="x")

    def show_build_echoes(self, parent, echoes, first_id, owner, show_sonata):
        grid = ttk.Frame(parent, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))
        cards = []
        ordered = sorted(echoes, key=lambda echo: (echo.id != first_id, -echo.cost))
        for echo in ordered:
            detail = ttk.Frame(grid, style="Echo.TFrame", padding=(9, 7))
            cards.append(detail)
            title = f"首位 · {echo.name}" if echo.id == first_id else echo.name
            self.result_label(detail, title, "EchoName.TLabel").pack(fill="x", pady=(0, 2))
            meta = [f"COST {echo.cost}  ·  +{echo.level}"]
            if show_sonata:
                meta.append(echo.sonata)
            if echo.owner and echo.owner != owner:
                meta.append(f"借自{echo.owner}")
            self.result_label(detail, "\n".join(meta), "EchoMeta.TLabel").pack(fill="x")
            for title, stats in (("主属性", echo.main), ("副词条", echo.sub)):
                if stats:
                    self.result_label(detail, title, "EchoName.TLabel").pack(fill="x", pady=(6, 0))
                    self.result_label(detail, display_stats(stats), "Echo.TLabel").pack(fill="x")

        min_width = round(grid.winfo_fpixels("1.6i"))
        columns = 0

        def arrange(event):
            nonlocal columns
            count = max(1, min(len(cards), event.width // min_width))
            if count == columns:
                return
            for column in range(max(columns, count)):
                grid.columnconfigure(column, weight=int(column < count), uniform="echo" if column < count else "")
            for index, detail in enumerate(cards):
                row, column = divmod(index, count)
                gap = (0, 6) if column + 1 < count else 0
                detail.grid(row=row, column=column, sticky="nsew", padx=gap, pady=(0, 6))
            columns = count

        grid.bind("<Configure>", arrange)

    def show_results(self, profile, result, inventory, *, scroll_position=0):
        self.clear_output()
        available = {echo.id: echo for echo in inventory}
        score_label = "推荐词条分" if profile.ranking == "推荐词条" else "伤害指数"
        heading = f"{profile.name} · {result.candidates} 件可用"
        if result.builds:
            heading += f" · {len(result.builds)} 套搭配"
        if not result.complete:
            heading += " · 搜索超时"
        self.result_label(self.output_body, heading, "Count.TLabel").pack(fill="x", pady=(0, 8))

        def mark_worn(echoes, number):
            if self.busy:
                return
            try:
                self.inventory.equip(profile.name, echoes)
            except (ValueError, sqlite3.Error) as error:
                self.status.set(f"穿戴同步失败 · {error}")
                return
            position = self.output.yview()[0]
            self.refresh()
            self.show_results(profile, result, self.echoes.values(), scroll_position=position)
            self.status.set(f"{profile.name} 已穿戴搭配 {number} · 已同步到声骸库存")

        current = [echo for echo in available.values() if echo.owner == profile.name]
        worn_ids = {echo.id for echo in current}
        baseline = evaluate(replace(profile, minimum_energy=0), current) if len(current) == 5 else None
        notices = [
            f"「{sonata}」有 {count} 种可用声骸，还差 {target - count} 种不同名声骸。"
            for sonata, count, target in result.set_shortages
        ]
        if result.missing_first:
            notices.append("当前条件下缺少推荐首位声骸。")
        if not result.builds and not notices:
            notices.append("没有符合套装、首位、COST 和效率要求的搭配。")
        if notices:
            self.result_label(self.output_body, "\n".join(notices)).pack(fill="x", pady=(0, 10))

        for number, build in enumerate(result.builds, 1):
            echoes = tuple(available.get(echo.id, echo) for echo in build.echoes)
            echo_ids = {echo.id for echo in echoes}
            is_worn = echo_ids == worn_ids
            border = tk.Frame(self.output_body, background=PANEL, borderwidth=0, highlightthickness=2,
                              highlightbackground=BUILD_BORDER, highlightcolor=BUILD_BORDER)
            border.pack(fill="x", pady=(0, 12))
            card = ttk.Frame(border, style="Card.TFrame", padding=(12, 10))
            card.pack(fill="x")
            header = ttk.Frame(card, style="Card.TFrame")
            header.pack(fill="x", pady=(0, 6))
            button = ttk.Button(header, text="当前穿戴" if is_worn else "已穿戴",
                                command=lambda pieces=echoes, index=number: mark_worn(pieces, index))
            if is_worn or echo_ids - available.keys():
                button.state(["disabled"])
            button.pack(side="right", padx=(8, 0))
            total = sum(echo.cost for echo in echoes)
            title = f"搭配 {number}  ·  {score_label} {build.score:,.1f}  ·  COST {total}"
            if not is_worn and baseline and baseline.score > 0:
                difference = 100 * (build.score / baseline.score - 1)
                if abs(difference) >= 0.05:
                    title += f"  ·  较当前 {difference:+.1f}%"
            self.result_label(header, title, "Count.TLabel").pack(side="left", fill="x", expand=True)

            sonatas = Counter(echo.sonata for echo in echoes)
            sets = "、".join(f"{name} ×{count}" + ("（仅计词条）" if name not in SETS else "")
                            for name, count in sonatas.items() if name != "未识别")
            lines = [f"合鸣  {sets}"] if sets else []
            scale, crit, crit_damage, energy, element = profile.panel(build.normal)
            _, on_crit, _, _, on_element = profile.panel(build.triggered)
            panel = (f"面板估算  {profile.scaling} {scale:,.1f} · 暴击 {crit:.1f}% · 暴伤 {crit_damage:.1f}%"
                     f" · 共鸣效率 {energy:.1f}%")
            if element:
                panel += f" · {profile.element}加成 {element:.1f}%"
            lines.append(panel)
            triggered = []
            if on_crit != crit:
                triggered.append(f"暴击 {on_crit:.1f}%")
            if on_element != element:
                triggered.append(f"{profile.element}加成 {on_element:.1f}%")
            if triggered:
                lines.append("触发时  " + " · ".join(triggered))
            goals = profile.goals(build.normal, build.triggered)
            if goals:
                shortfalls = [f"{name}差 {target - value:.1f} 个百分点"
                              for name, value, target in goals if value < target - 1e-8]
                lines.append("目标  " + ("、".join(shortfalls) if shortfalls else "全部达标"))
            self.result_label(card, "\n".join(lines)).pack(fill="x")
            self.show_build_echoes(card, echoes, build.first.id, profile.name, len(sonatas) > 1)
        if scroll_position:
            self.output.update_idletasks()
            self.output.yview_moveto(scroll_position)
        self.status.set("已更新搭配结果")

    def close(self):
        if self.capture_stop:
            self.capture_stop.set()
        if self.auto_job:
            self.auto_job.stop_flag.touch()
        self.inventory.close()
        self.root.destroy()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(sys.argv[2], int(sys.argv[3]))
    else:
        app = tk.Tk()
        Window(app)
        app.mainloop()

