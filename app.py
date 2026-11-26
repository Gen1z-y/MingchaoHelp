from collections import Counter
from dataclasses import replace
import json
import os
from pathlib import Path
from queue import Empty, Queue
import sqlite3
from threading import Thread
from time import sleep
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import unicodedata
import webbrowser

from app_paths import LOCAL_DATA
from auto_scan import COLUMNS, ScanJob, migrate_anchors, worker
from echoes import Inventory, PERCENT_STATS, STAT_NAMES, UNKNOWN_OWNER
from guide_sync import read_cached_guides, sync_guides
from ingest import Reader, ReadResult
from optimizer import PANEL_BASES, Profile, evaluate, solve
from profiles import ALL_SONATAS, FALLBACK_GUIDES, OFFICIAL_GUIDES, PREVIOUS_GUIDES, ROLE_ORDER, guide_defaults
from rules import ADDITIONAL_TARGETS, SETS
from weapons import CHARACTER_PANELS, NO_WEAPON, WEAPONS, WEAPONS_BY_ID, CharacterPanel
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
INVENTORY_FILTERS = {"按角色": "owner", "按套装": "sonata"}
FONT = ("Microsoft YaHei UI", 10)
TARGETS = (("target_crit_rate", "暴击 %"), ("target_crit_damage", "暴伤 %"),
           ("target_energy", "共鸣效率 %"), ("target_attack", "攻击"))
TARGET_LABELS = dict(TARGETS) | {
    key: f"{stat}{' %' if stat in PERCENT_STATS else ''}"
    for key, stat in ADDITIONAL_TARGETS.items()
}


def display_stats(stats, separator="\n"):
    return separator.join(f"{name.removesuffix('%')} {value:g}{'%' if name in PERCENT_STATS else ''}"
                     for name, value in stats)


def display_main_slots(slots):
    costs = {}
    for (cost, stat), count in Counter(slots).items():
        name = f"{stat} ×{count}" if count > 1 else stat
        costs.setdefault(cost, []).append(name)
    return " · ".join(f"{cost} COST {' + '.join(names)}" for cost, names in costs.items())


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
    button_fill = [("pressed", "#263850"), ("active", "#304660")]
    style.map("TButton", background=button_fill)
    style.configure("Inventory.TButton", padding=(8, 8))
    style.configure("Card.TCheckbutton", background=PANEL, foreground=TEXT,
                    indicatorbackground=ROW, indicatorforeground=ACCENT_TEXT,
                    upperbordercolor=MUTED, lowerbordercolor=MUTED, padding=(5, 3))
    style.map("Card.TCheckbutton", background=button_fill,
              foreground=[("disabled", MUTED), ("active", TEXT)],
              indicatorbackground=[("selected", ACCENT)])
    style.configure("Primary.TButton", background=ACCENT, foreground=ACCENT_TEXT,
                    lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=ACCENT,
                    font=(FONT[0], 10, "bold"), padding=(19, 9))
    style.map("Primary.TButton", background=[("active", "#a8f3e1"), ("pressed", "#5dcdb1")],
              foreground=[("active", ACCENT_TEXT)])
    style.configure("TMenubutton", background=ROW, foreground=TEXT, bordercolor=EDGE,
                    lightcolor=ROW, darkcolor=ROW, padding=(13, 8), arrowcolor=TEXT)
    style.map("TMenubutton", background=button_fill, lightcolor=button_fill, darkcolor=button_fill,
              foreground=[("pressed", TEXT), ("active", TEXT)],
              arrowcolor=[("pressed", TEXT), ("active", TEXT)],
              bordercolor=[("pressed", EDGE), ("active", EDGE), ("focus", "#7d9ab5")])
    style.configure("Inline.TMenubutton", padding=(9, 4))
    style.configure("Card.TSeparator", background=EDGE)
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
        if sys.platform == "win32":
            root.iconbitmap(default=str(Path(__file__).with_name("assets") / "mingchaohelp.ico"))
        root.geometry("1120x760")
        root.minsize(940, 660)
        root.protocol("WM_DELETE_WINDOW", self.close)
        setup_theme(root)
        self.inventory = Inventory(LOCAL / "inventory.sqlite3")
        self.inventory.clean_deleted_cards()
        self.result_queue = Queue()
        self.progress = Queue()
        self.auto_job = None
        self.busy = False
        self.echoes = {}
        self.status = tk.StringVar(value="准备就绪")
        self.guide_cache = LOCAL / "kuro-guides"
        self.apply_guide_catalog(read_cached_guides(self.guide_cache))

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
        self.root.after(1000, self.start_guide_sync)
        if any((LOCAL / "pending").glob("*.png")) or any((LOCAL / "captures").glob("*.png")) or any(
                (LOCAL / "icons").glob("*.png")) or any((LOCAL / "auto-scan").glob("*/start.png")):
            self.root.after(100, self.migrate)

    def apply_guide_catalog(self, cached):
        cached = {name: guide for name, guide in cached.items() if name not in OFFICIAL_GUIDES}
        self.official_guides = OFFICIAL_GUIDES | cached
        self.guides = FALLBACK_GUIDES | self.official_guides
        self.guide_sonatas = set(SONATA_NAMES) | {sonata for guide in cached.values() for sonata in guide.sonatas}
        new_order = sorted(cached, key=lambda name: cached[name].catalog_order, reverse=True)
        self.role_order = tuple(dict.fromkeys((*new_order, *ROLE_ORDER)))
        self.character_panels = CHARACTER_PANELS | {
            name: CharacterPanel(guide.weapon_type, guide.base_stats, guide.skill_bonuses)
            for name, guide in cached.items() if guide.weapon_type and guide.base_stats
        }

    def start_guide_sync(self):
        result_queue = Queue()

        def worker():
            try:
                result_queue.put(sync_guides(self.guide_cache))
            except (OSError, ValueError, TypeError, AttributeError):
                result_queue.put((None, True))

        def receive():
            try:
                cached, pending = result_queue.get_nowait()
            except Empty:
                self.root.after(250, receive)
                return
            if cached is not None:
                added = cached.keys() - self.official_guides.keys()
                self.apply_guide_catalog(cached)
                self.role_choice.set_choices((*self.role_order, *self.role_settings))
                if added and not self.busy:
                    self.status.set("已收录库洛攻略 · " + "、".join(sorted(added)))
            hours = 0.5 if pending else 6
            self.root.after(int(hours * 60 * 60 * 1000), self.start_guide_sync)

        Thread(target=worker, daemon=True).start()
        self.root.after(250, receive)

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

        listing = ttk.Frame(tab, padding=(14, 12), style="Card.TFrame")
        listing.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        listing.rowconfigure(1, weight=1)
        listing.columnconfigure(0, weight=1)

        table_header = ttk.Frame(listing, style="Card.TFrame")
        table_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 11))
        actions = ttk.Frame(table_header, style="Card.TFrame")
        actions.pack(fill="x")
        ttk.Label(actions, text="已入库", style="Section.TLabel").pack(side="left")
        self.count = tk.StringVar()
        ttk.Label(actions, textvariable=self.count, style="Count.TLabel").pack(side="left", padx=8)
        ttk.Button(actions, text="按套装清除", command=self.choose_sonata_to_clear,
                   style="Inventory.TButton").pack(side="left")
        self.unequip_button = ttk.Button(actions, text="按角色卸下", command=self.choose_role_to_unequip,
                                         style="Inventory.TButton")
        self.unequip_button.pack(side="left", padx=(8, 0))

        ttk.Label(actions, text="查找", style="Hint.TLabel").pack(side="left", padx=(8, 4))
        self.filter_mode = tk.StringVar(value="按角色")
        ttk.Combobox(actions, textvariable=self.filter_mode, values=tuple(INVENTORY_FILTERS),
                     state="readonly", width=7).pack(side="left", fill="y", padx=(0, 6))
        self.filter_text = tk.StringVar()
        self.inventory_query = SearchCombobox(actions, self.filter_text, label="查找",
                                              on_error=self.status.set, allow_custom=True, width=12)
        self.inventory_query.pack(side="left", fill="both", expand=True)

        columns = [("name", "声骸", 164), ("cost", "COST", 53), ("level", "等级", 55),
                   ("sonata", "合鸣套装", 134), ("owner", "穿戴角色", 106)]
        self.tree = ttk.Treeview(listing, columns=[c[0] for c in columns], show="headings",
                                 selectmode="browse")
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            center = key in {"cost", "level"}
            self.tree.column(key, width=width, stretch=not center, anchor="center" if center else "w")
        self.tree.tag_configure("odd", background=ROW)
        self.tree.tag_configure("review", foreground="#ffc779")
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self.show_echo)
        self.filter_text.trace_add("write", lambda *_: self.render_inventory())
        self.filter_mode.trace_add("write", self.update_inventory_filter)
        review_bar = ttk.Frame(listing, style="Card.TFrame")
        review_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.review_only = tk.BooleanVar()
        ttk.Checkbutton(review_bar, text="只看待核对", variable=self.review_only,
                        style="Card.TCheckbutton").pack(side="left")
        self.review_only.trace_add("write", lambda *_: self.render_inventory())
        self.inventory_note = tk.StringVar()
        ttk.Label(review_bar, textvariable=self.inventory_note, style="Hint.TLabel").pack(side="left", padx=8)
        self.failure_button = ttk.Button(review_bar, command=self.show_scan_failures)

        side = ttk.Frame(tab, padding=17, width=305, style="Card.TFrame")
        side.grid(row=1, column=1, sticky="nsew")
        detail_header = ttk.Frame(side, style="Card.TFrame")
        detail_header.pack(fill="x")
        ttk.Label(detail_header, text="声骸详情", style="Section.TLabel").pack(side="left")
        self.edit_echo_button = ttk.Button(detail_header, text="编辑", command=self.edit_selected_echo)
        self.edit_echo_button.pack(side="right")
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
        uncertain = sum(echo.needs_review for echo in self.echoes.values())
        self.header_count.set(f"本地库存  {len(self.echoes):,} 件")
        self.inventory_note.set(f"{uncertain:,} 件待核对" if uncertain else "")
        if failures:
            self.failure_button.configure(text=f"扫描异常 {failures:,} 条")
            self.failure_button.pack(side="right")
        else:
            self.failure_button.pack_forget()
        self.update_inventory_filter()

    def update_inventory_filter(self, *_):
        field = INVENTORY_FILTERS[self.filter_mode.get()]
        choices = {getattr(echo, field) for echo in self.echoes.values()}
        self.inventory_query.set_choices(sorted(choices - {""}))
        self.render_inventory()

    def render_inventory(self):
        selected = self.tree.selection()
        query = SearchCombobox.search_key(self.filter_text.get())
        field = INVENTORY_FILTERS[self.filter_mode.get()]
        y = self.tree.yview()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        review_only = self.review_only.get()
        for echo in self.echoes.values():
            if review_only and not echo.needs_review:
                continue
            if query and query not in SearchCombobox.search_key(getattr(echo, field)):
                continue
            tags = ("odd",) if shown % 2 else ()
            if echo.needs_review:
                tags += ("review",)
            self.tree.insert("", "end", iid=echo.id, tags=tags,
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
        self.edit_echo_button.state(["!disabled"] if echo else ["disabled"])
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

    def new_dialog(self, title, *, resizable=False):
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(resizable, resizable)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        return dialog

    def show_dialog(self, dialog, *, size=None, modal=True, focus=None):
        dialog.update_idletasks()
        width, height = size or (dialog.winfo_reqwidth(), dialog.winfo_reqheight())
        x = self.root.winfo_x() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - height) // 2
        dimensions = f"{width}x{height}" if size else ""
        dialog.geometry(f"{dimensions}+{x}+{y}")
        dialog.deiconify()
        if modal:
            dialog.grab_set()
        if focus:
            focus.focus_set()

    def edit_selected_echo(self):
        selection = self.tree.selection()
        if self.busy or not selection:
            return
        echo = self.echoes[selection[0]]
        dialog = self.new_dialog("编辑声骸")
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill="both", expand=True)
        fields = {}
        sonatas = sorted(self.guide_sonatas | {entry.sonata for entry in self.echoes.values()})
        roles = tuple(dict.fromkeys(("", UNKNOWN_OWNER, *self.role_order, *self.role_settings)))
        metadata = (
            ("name", "名称", echo.name, ()),
            ("sonata", "合鸣套装", echo.sonata, sonatas),
            ("cost", "COST", str(echo.cost), ("1", "3", "4")),
            ("owner", "穿戴角色", echo.owner, roles),
        )
        for row, (key, label, value, choices) in enumerate(metadata):
            variable = fields[key] = tk.StringVar(value=value)
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=2)
            if choices:
                widget = ttk.Combobox(form, textvariable=variable, values=choices,
                                      state="readonly" if key == "cost" else "normal")
            else:
                widget = ttk.Entry(form, textvariable=variable)
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)

        stats = {"main": [], "sub": []}
        for key, title, start, count in (("main", "主属性", 4, 2), ("sub", "副词条", 7, 5)):
            ttk.Label(form, text=title).grid(row=start, column=0, sticky="w", pady=(9, 3))
            entries = getattr(echo, key)
            for index in range(count):
                name, value = entries[index] if index < len(entries) else ("", None)
                stat = tk.StringVar(value=name)
                number = tk.StringVar(value=f"{value:g}" if value is not None else "")
                stats[key].append((stat, number))
                row = start + index + 1
                ttk.Combobox(form, textvariable=stat, values=("", *STAT_NAMES), state="readonly",
                             width=22).grid(row=row, column=1, sticky="ew", pady=2)
                ttk.Entry(form, textvariable=number, width=9).grid(row=row, column=2, padx=(8, 0), pady=2)

        notice = tk.StringVar(value=" · ".join(echo.warnings) or "穿戴角色留空表示未穿戴。")
        ttk.Label(form, textvariable=notice, wraplength=350).grid(
            row=13, column=0, columnspan=3, sticky="w", pady=(8, 0))

        def read_stats(entries):
            values = []
            for stat, number in entries:
                name = stat.get()
                value = number.get().strip()
                if name or value:
                    if not name or not value:
                        raise ValueError("请同时填写词条名称和数值")
                    try:
                        values.append((name, float(value.removesuffix("%"))))
                    except ValueError:
                        raise ValueError(f"「{name}」的数值无效") from None
            return tuple(values)

        def save():
            if self.busy:
                notice.set("请等当前操作完成再保存")
                return
            try:
                edited = replace(
                    echo, name=fields["name"].get().strip(), sonata=fields["sonata"].get().strip(),
                    cost=int(fields["cost"].get()), owner=fields["owner"].get().strip(),
                    main=read_stats(stats["main"]), sub=read_stats(stats["sub"]), warnings=(),
                )
                self.inventory.update_echo(echo, edited)
            except (ValueError, sqlite3.Error) as error:
                notice.set(str(error))
                return
            dialog.destroy()
            self.refresh()
            self.set_output("库存已更新，请重新计算搭配。")
            self.status.set(f"已保存「{edited.name}」的修正")

        ttk.Button(form, text="保存修正", command=save, style="Primary.TButton").grid(
            row=14, column=1, columnspan=2, sticky="e", pady=(8, 0))
        self.show_dialog(dialog)

    def show_scan_failures(self):
        failures = self.inventory.recent_failures()
        if not failures:
            return
        dialog = self.new_dialog("扫描异常", resizable=True)
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill="both", expand=True)
        description = "可按扫描时的背包排序和筛选定位补扫，也可导入单件详情截图。"
        if self.inventory.failure_count() > len(failures):
            description = f"显示最近 {len(failures)} 条。{description}"
        ttk.Label(form, text=description, wraplength=565).pack(fill="x", pady=(0, 8))
        entries = tk.Text(form, wrap="word", background=PANEL, foreground=TEXT, padx=9, pady=7,
                          relief="flat", highlightthickness=0)
        scrollbar = ttk.Scrollbar(form, command=entries.yview)
        entries.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        entries.pack(side="left", fill="both", expand=True)
        for record_id, reason in failures:
            slot = record_id.rsplit("-", 1)[-1]
            if record_id.startswith("capture-auto-") and slot.isdecimal():
                row, col = divmod(int(slot), len(COLUMNS))
                label = f"自动扫描第 {row + 1} 行、第 {col + 1} 列"
            else:
                label = "详情截图"
            entries.insert("end", f"{label}  ·  {reason}\n\n")
        entries.configure(state="disabled")
        self.show_dialog(dialog, size=(650, 390), modal=False)

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
        dialog = self.new_dialog("按合鸣套装清除")
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
        self.show_dialog(dialog, focus=choice)

    def choose_role_to_unequip(self):
        if self.busy:
            return
        counts = Counter(echo.owner for echo in self.echoes.values()
                         if echo.owner and echo.owner != UNKNOWN_OWNER)
        if not counts:
            self.status.set("库存里没有已绑定角色的声骸")
            return
        selected = self.tree.selection()
        echo = self.echoes.get(selected[0]) if selected else None
        default = echo.owner if echo and echo.owner in counts else ""

        dialog = self.new_dialog("按角色卸下")
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill="both", expand=True)
        owner = tk.StringVar(value=default)
        notice = tk.StringVar()
        ttk.Label(form, text="穿戴角色").grid(row=0, column=0, sticky="w", padx=(0, 10))
        choice = SearchCombobox(form, owner, sorted(counts), label="穿戴角色",
                                 on_error=notice.set, width=23)
        choice.grid(row=0, column=1, sticky="ew")
        ttk.Label(form, textvariable=notice).grid(row=1, column=0, columnspan=2, sticky="w", pady=(9, 12))

        def show_count(*_):
            name = owner.get()
            notice.set(f"将解除 {counts[name]:,} 件声骸的穿戴绑定" if name in counts else "请选择已穿戴声骸的角色")

        show_count()
        trace = owner.trace_add("write", show_count)
        dialog.bind("<Destroy>", lambda event: owner.trace_remove("write", trace)
                    if event.widget is dialog else None)

        def unequip():
            try:
                name = choice.resolve()
                count = self.inventory.unequip(name)
            except (ValueError, sqlite3.Error) as error:
                notice.set(str(error))
                return
            dialog.destroy()
            self.refresh()
            self.set_output("穿戴已更新，请重新计算搭配。")
            self.status.set(f"已卸下「{name}」的 {count:,} 件声骸" if count else f"「{name}」没有已绑定的声骸")

        ttk.Button(form, text="取消", command=dialog.destroy).grid(row=2, column=0, sticky="w")
        ttk.Button(form, text="卸下声骸", command=unequip, style="Primary.TButton").grid(row=2, column=1, sticky="e")
        self.show_dialog(dialog, focus=choice)

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
        paths = filedialog.askopenfilenames(parent=self.root, title="选择声骸详情截图",
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
                parent=self.root,
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

    optimizer_prompt = "选择角色、武器和合鸣套装，再点击「开始计算」。"

    def build_optimizer_tab(self):
        tab = ttk.Frame(self.content)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        overview = ttk.Frame(tab, style="Card.TFrame", padding=(16, 12))
        overview.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        overview.columnconfigure(0, weight=1)
        selection = ttk.Frame(overview, style="Card.TFrame")
        selection.grid(row=0, column=0, sticky="ew")
        for column, weight in enumerate((2, 3, 3, 3)):
            selection.columnconfigure(column, weight=weight)
        for column, title in ((0, "角色"), (2, "合鸣套装"), (3, "首位声骸")):
            ttk.Label(selection, text=title, style="Hint.TLabel").grid(
                row=0, column=column, sticky="w", pady=(0, 4))

        self.profile = {}
        self.profile["name"] = tk.StringVar(value="心")
        self.role_choice = SearchCombobox(
            selection, self.profile["name"], self.role_order, label="角色", allow_custom=True,
            on_select=self.load_role, on_error=self.status.set, width=11)
        self.role_choice.grid(row=1, column=0, sticky="new", padx=(0, 14))

        weapon_header = ttk.Frame(selection, style="Card.TFrame")
        weapon_header.grid(row=0, column=1, sticky="w", pady=(0, 4))
        ttk.Label(weapon_header, text="武器", style="Hint.TLabel").pack(side="left")
        self.weapon_level = tk.StringVar()
        ttk.Label(weapon_header, textvariable=self.weapon_level, style="Hint.TLabel").pack(side="left", padx=(8, 0))
        self.weapon_choice = SearchCombobox(
            selection, tk.StringVar(value=NO_WEAPON), label="武器",
            on_error=self.status.set, on_select=self.update_weapon_level, width=21)
        self.weapon_choice.grid(row=1, column=1, sticky="new", padx=(0, 14))

        self.profile["required_set"] = tk.StringVar()
        self.sonata_choice = SearchCombobox(
            selection, self.profile["required_set"], label="合鸣套装", on_error=self.status.set,
            on_select=self.update_first_echo, width=23)
        self.sonata_choice.grid(row=1, column=2, sticky="new", padx=(0, 14))
        self.first_echo_text = tk.StringVar()
        ttk.Label(selection, textvariable=self.first_echo_text, style="Card.TLabel",
                  wraplength=210, padding=(0, 6)).grid(row=1, column=3, sticky="nw")

        goals = ttk.Frame(overview, style="Card.TFrame")
        goals.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        goals.columnconfigure(1, weight=1)
        ttk.Label(goals, text="面板目标", style="Hint.TLabel", padding=(0, 6)).grid(
            row=0, column=0, sticky="nw", padx=(0, 14))
        self.target_fields = ttk.Frame(goals, style="Card.TFrame", height=32)
        self.target_fields.grid(row=0, column=1, sticky="ew")
        self.target_fields.bind("<Configure>", lambda event: self.layout_target_fields(event.width))
        self.target_widgets = {}
        for key, title in TARGET_LABELS.items():
            self.profile[key] = tk.StringVar()
            field = ttk.Frame(self.target_fields, style="Card.TFrame")
            ttk.Label(field, text=title, style="Card.TLabel").pack(side="left", padx=(0, 6))
            entry = ttk.Entry(field, textvariable=self.profile[key], width=7, justify="right")
            entry.pack(side="left")
            self.target_widgets[key] = field, entry
        self.add_target_button = ttk.Menubutton(self.target_fields, text="添加目标", style="Inline.TMenubutton")
        self.add_target_menu = tk.Menu(self.add_target_button, tearoff=False)
        self.add_target_button.configure(menu=self.add_target_menu)

        ttk.Separator(overview, style="Card.TSeparator").grid(row=2, column=0, sticky="ew", pady=(10, 8))
        footer = ttk.Frame(overview, style="Card.TFrame")
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.guide_description = tk.StringVar()
        hint = ttk.Label(footer, textvariable=self.guide_description, style="Hint.TLabel", wraplength=360)
        hint.grid(row=0, column=0, sticky="ew", padx=(0, 14))
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=event.width))
        actions = ttk.Frame(footer, style="Card.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        self.source_button = ttk.Button(actions, text="查看攻略", command=self.open_role_guide)
        self.source_button.pack(side="left", padx=(0, 8))
        self.reset_button = ttk.Button(actions, text="恢复攻略推荐", command=self.reset_role_guide)
        self.reset_button.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="开始计算", command=self.search, style="Primary.TButton").pack(side="left")

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
        self.role_choice.set_choices((*self.role_order, *self.role_settings))
        self.load_role()
        self.set_output(self.optimizer_prompt)
        return tab

    def load_role(self):
        name = self.profile["name"].get().strip()
        self.role_choice.commit(name)
        self.loaded_role = name
        guide = self.guides.get(name)
        source = "库洛攻略站" if name in self.official_guides else "查看攻略"
        state = "normal" if guide else "disabled"
        self.source_button.configure(text=source, state=state)
        reset = "恢复官方推荐" if name in self.official_guides else "恢复攻略推荐"
        self.reset_button.configure(text=reset, state=state)
        saved = self.saved_role_settings(name)
        self.apply_role_fields(guide_defaults(guide), saved)
        self.load_weapon(name, saved.get("weapon_id"))

    def saved_role_settings(self, name):
        saved = self.role_settings.get(name, {})
        previous = PREVIOUS_GUIDES.get(name)
        if self.guides.get(name) != previous and saved and not saved.get("uses_guide_defaults"):
            previous_defaults = guide_defaults(previous)
            previous_defaults.pop("required_set")
            saved = {key: value for key, value in saved.items() if value != previous_defaults.get(key)}
        return saved

    def apply_role_fields(self, defaults, overrides=None):
        overrides = overrides or {}
        for key in TARGET_LABELS:
            self.profile[key].set(overrides.get(key, defaults[key]))
        self.show_target_fields({key for key in TARGET_LABELS if self.profile[key].get().strip()})
        self.sonata_choice.commit(overrides.get("required_set") or defaults["required_set"])
        self.update_sonata_choices()
        self.update_first_echo()

    def show_target_fields(self, active):
        self.active_targets = active
        self.add_target_menu.delete(0, "end")
        for key, (field, _) in self.target_widgets.items():
            if key not in active:
                field.place_forget()
                self.add_target_menu.add_command(label=TARGET_LABELS[key],
                                                 command=lambda key=key: self.add_target_field(key))
        self.add_target_button.state(["disabled"] if len(active) == len(TARGET_LABELS) else ["!disabled"])
        self.layout_target_fields(self.target_fields.winfo_width())

    def layout_target_fields(self, available_width):
        if available_width <= 1:
            return
        widgets = [field for key, (field, _) in self.target_widgets.items() if key in self.active_targets]
        widgets.append(self.add_target_button)
        row_height = max(widget.winfo_reqheight() for widget in widgets)
        x = y = 0
        for widget in widgets:
            width = widget.winfo_reqwidth()
            if x and x + width > available_width:
                x = 0
                y += row_height + 6
            widget.place(x=x, y=y + (row_height - widget.winfo_reqheight()) // 2)
            x += width + 14
        height = y + row_height
        if int(self.target_fields.cget("height")) != height:
            self.target_fields.configure(height=height)

    def add_target_field(self, key):
        self.show_target_fields(self.active_targets | {key})
        self.target_widgets[key][1].focus_set()

    def load_weapon(self, name, weapon_id):
        character = self.character_panels.get(name)
        guide = self.guides.get(name)
        weapon_type = character.weapon_type if character else (guide.weapon_type if guide else "")
        choices = (NO_WEAPON, *(weapon.name for weapon in WEAPONS.values()
                               if not weapon_type or weapon.weapon_type == weapon_type))
        selected = WEAPONS_BY_ID.get(weapon_id)
        self.weapon_choice.set_choices(choices)
        self.weapon_choice.commit(selected.name if selected and selected.name in choices else NO_WEAPON)
        self.update_weapon_level()

    def update_weapon_level(self):
        weapon = WEAPONS.get(self.weapon_choice.committed)
        self.weapon_level.set(f"{weapon.level} 级 · 一阶" if weapon else "")

    def reset_role_guide(self):
        if self.busy:
            return
        try:
            name = self.role_choice.resolve()
        except ValueError as error:
            self.status.set(str(error))
            return
        guide = self.guides.get(name)
        if not guide:
            self.status.set("该角色暂无攻略推荐")
            return
        self.role_choice.commit(name)
        if name != self.loaded_role:
            self.load_role()
        self.apply_role_fields(guide_defaults(guide))
        weapon = WEAPONS.get(self.weapon_choice.committed)
        self.role_settings[name] = {"uses_guide_defaults": True}
        if weapon:
            self.role_settings[name]["weapon_id"] = weapon.id
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
        guide = self.guides.get(self.loaded_role)
        recommended = guide.sonata_choices if guide else ()
        known = self.guide_sonatas | {echo.sonata for echo in self.echoes.values()
                                     if echo.sonata != "未识别"}
        selected = self.sonata_choice.committed
        choices = dict.fromkeys((*recommended, ALL_SONATAS, *sorted(known), selected))
        self.sonata_choice.set_choices(choices)

    def update_first_echo(self):
        guide = self.guides.get(self.loaded_role)
        selected = self.sonata_choice.committed
        _, firsts = guide.echo_setup(selected) if guide else ((), ())
        self.first_echo_text.set(" / ".join(firsts) if firsts else "自动选择")
        layouts = guide.main_layouts(selected) if guide else ()
        summaries = tuple(dict.fromkeys(display_main_slots(slots) for _, slots in layouts))
        if summaries:
            self.guide_description.set("\n".join(
                ("主词条  " if index == 0 else "备选  ") + text
                for index, text in enumerate(summaries)
            ))
        else:
            self.guide_description.set("  ·  ".join(
                f"{cost} COST {' / '.join(stats)}" for cost, stats in (guide.mains if guide else ())
            ))

    def open_role_guide(self):
        guide = self.guides.get(self.profile["name"].get().strip())
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
            weapon_name = self.weapon_choice.resolve()
            self.weapon_choice.commit(weapon_name)
            self.update_weapon_level()
            weapon = WEAPONS.get(weapon_name)
            settings = {key: self.profile[key].get().strip() for key in TARGET_LABELS}
            targets = {key: float(settings[key] or 0) for key, _ in TARGETS}
            additional_targets = tuple((stat, float(settings[key]))
                                       for key, stat in ADDITIONAL_TARGETS.items() if settings[key])
            settings["required_set"] = selected

            guide = self.guides.get(name)
            recommendations = guide_defaults(guide)
            groups = tuple(tuple(stat.strip() for stat in group.replace("＝", "=").split("="))
                           for group in guide.priorities) if guide else ()
            profile = Profile(
                name=name,
                **targets,
                additional_targets=additional_targets,
                character=self.character_panels.get(name),
                weapon=weapon,
                element=recommendations["element"],
                scaling=recommendations["scaling"],
                base=PANEL_BASES[recommendations["scaling"]],
                ranking=recommendations["ranking"],
                priorities=tuple(stat for group in groups for stat in group),
                priority_ranks=tuple(rank for rank, group in enumerate(groups) for _ in group),
                main_choices=guide.mains if guide else (),
                recommended_sets=guide.sonatas if guide else (),
            )
            requirements, firsts = guide.echo_setup(selected) if guide else (
                ((required_set, 5),) if required_set else (), ())
            main_layouts = guide.main_layouts(selected) if guide else ()
            profile.validate()
        except ValueError as error:
            self.status.set(str(error))
            return
        echoes = list(self.echoes.values())
        overrides = {key: value for key, value in settings.items() if value != recommendations[key]}
        self.role_settings[profile.name] = {**overrides, "uses_guide_defaults": True}
        if weapon:
            self.role_settings[profile.name]["weapon_id"] = weapon.id
        self.show_target_fields({key for key in TARGET_LABELS if settings[key]})
        self.role_choice.set_choices((*self.role_order, *self.role_settings))
        self.update_first_echo()
        status = (f"正在搜索「{required_set}」" if required_set else
                  f"正在从 {len(echoes)} 件库存中搜索")
        try:
            self.save_role_settings()
        except OSError:
            status += " · 无法写入角色设置"
        self.status.set(status)
        self.run(lambda: solve(profile, echoes, set_requirements=requirements, first_echoes=firsts,
                               main_layouts=main_layouts),
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
        if profile.weapon:
            heading += f" · {profile.weapon.name} {profile.weapon.level} 级一阶"
        if result.builds:
            heading += f" · {len(result.builds)} 套搭配"
        if not result.complete:
            heading += " · 搜索超时"
        self.result_label(self.output_body, heading, "Count.TLabel").pack(fill="x", pady=(0, 8))
        if profile.character:
            bonuses = profile.character.skill_bonuses
            if bonuses is None:
                text = "技能树常驻属性暂无数据"
            elif bonuses:
                text = "技能树常驻属性（按点满计入） · " + display_stats(bonuses, " · ")
            else:
                text = "技能树无常驻属性节点"
            self.result_label(self.output_body, text).pack(fill="x", pady=(0, 8))

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
        if result.missing_first and not result.diagnostics:
            notices.append("当前条件下缺少推荐首位声骸。")
        notices.extend(result.diagnostics)
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
                goal_states = []
                for name, value, target in goals:
                    percent = name in PERCENT_STATS
                    suffix = "%" if percent else ""
                    gap = target - value
                    unit = " 个百分点" if percent else ""
                    state = f"差 {gap:,.1f}{unit}" if gap > 1e-8 else "达标"
                    if name not in {profile.scaling, "暴击", "暴击伤害", "共鸣效率"}:
                        goal_states.append(f"{name} {value:,.1f}{suffix}（目标 ≥ {target:,g}{suffix}，{state}）")
                    else:
                        goal_states.append(f"{name} ≥ {target:,g}{suffix}（{state}）")
                lines.append("目标  " + " · ".join(goal_states))
            self.result_label(card, "\n".join(lines)).pack(fill="x")
            self.show_build_echoes(card, echoes, build.first.id, profile.name, len(sonatas) > 1)
        if scroll_position:
            self.output.update_idletasks()
            self.output.yview_moveto(scroll_position)
        self.status.set("已更新搭配结果")

    def close(self):
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

