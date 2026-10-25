"""Snippet editor window: add, rename, reorder and delete tabs, groups and
snippets, and change the settings, without editing config.json by hand."""

import json
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Callable

TITLE = "k5qref - Setup"
C_ERROR = "#d93025"
C_MUTED = "#5f6368"
C_LINK = "#1a73e8"


class InvalidConfig(Exception):
    """Raised while building the config to save; iid is the tree item to
    point the user at."""

    def __init__(self, message: str, iid: str | None = None) -> None:
        super().__init__(message)
        self.iid = iid


class ConfigEditor(tk.Toplevel):
    """Tab > Group > Snippet tree on the left, the selected item's fields
    on the right. The tree itself holds order and nesting (moving an item
    is a tree.move); tab and group names are the tree item text, and
    snippet dicts live in self.snips so keys other than title/text
    survive a save."""

    def __init__(self, master: tk.Misc, config: dict,
                 tabs: list[tuple[str | None, list[tuple[str | None, list[dict]]]]],
                 path: str, check_hotkey: Callable[[str], object],
                 on_save: Callable[[], None],
                 open_json: Callable[[], None]) -> None:
        super().__init__(master)
        self.base = config
        self.path = path
        self.check_hotkey = check_hotkey
        self.on_save = on_save
        self.open_json = open_json
        self.kind: dict[str, str] = {}   # iid -> "tab" | "group" | "snip"
        self.snips: dict[str, dict] = {}
        self.current: str | None = None
        self.dirty = False
        # set while filling widgets from the model, so their traces don't
        # count as user edits
        self._loading = False
        self.disk_mtime = self._mtime()

        self.title(TITLE)
        self.geometry("880x560")
        self.minsize(640, 400)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self._load(config, tabs)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        self.f_tab = base.copy()
        self.f_tab.configure(weight="bold")
        self.f_text = tkfont.Font(family="Consolas", size=10)

        settings = ttk.Frame(self, padding=(10, 10, 10, 0))
        settings.pack(fill="x")
        ttk.Label(settings, text="Hotkey").pack(side="left")
        self.hotkey_var = tk.StringVar()
        self.hotkey_var.trace_add("write", self._mark_dirty)
        ttk.Entry(settings, textvariable=self.hotkey_var, width=22).pack(
            side="left", padx=(6, 18))
        self.paste_var = tk.BooleanVar()
        self.paste_var.trace_add("write", self._mark_dirty)
        ttk.Checkbutton(
            settings, variable=self.paste_var,
            text="Paste into the previous window after selecting",
        ).pack(side="left")
        f_link = base.copy()
        f_link.configure(underline=True)
        link = ttk.Label(settings, text="Edit source JSON", font=f_link,
                         foreground=C_LINK, cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda e: self.open_json())

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.pack(side="bottom", fill="x")
        ttk.Button(bottom, text="Close", command=self.close).pack(side="right")
        ttk.Button(bottom, text="Save", command=self.save).pack(
            side="right", padx=(0, 6))
        self.status = ttk.Label(bottom, foreground=C_MUTED)
        self.status.pack(side="left", fill="x", expand=True)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(panes)
        panes.add(left, weight=2)
        buttons = ttk.Frame(left)
        buttons.pack(side="bottom", fill="x", pady=(6, 0))
        for label, cmd, width in (
                ("+ Tab", self.add_tab, 8), ("+ Group", self.add_group, 8),
                ("+ Snippet", self.add_snippet, 9), ("Delete", self.delete, 7),
                ("▲", lambda: self.move(-1), 3), ("▼", lambda: self.move(+1), 3)):
            ttk.Button(buttons, text=label, command=cmd, width=width).pack(
                side="left", padx=(0, 4))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("tab", font=self.f_tab)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Delete>", lambda e: self.delete())
        self.tree.bind("<Control-Up>", lambda e: self.move(-1) or "break")
        self.tree.bind("<Control-Down>", lambda e: self.move(+1) or "break")

        detail = ttk.Frame(panes, padding=(14, 0, 0, 0))
        panes.add(detail, weight=3)
        self.empty_label = ttk.Label(
            detail, foreground=C_MUTED,
            text="Select an item, or add a tab to get started.")

        self.name_frame = ttk.Frame(detail)
        self.name_label = ttk.Label(self.name_frame)
        self.name_label.pack(anchor="w")
        self.name_var = tk.StringVar()
        self.name_var.trace_add("write", self._on_name)
        self.name_entry = ttk.Entry(self.name_frame, textvariable=self.name_var)
        self.name_entry.pack(fill="x", pady=(2, 0))

        self.snip_frame = ttk.Frame(detail)
        ttk.Label(self.snip_frame, text="Title").pack(anchor="w")
        self.title_var = tk.StringVar()
        self.title_var.trace_add("write", self._on_title)
        self.title_entry = ttk.Entry(self.snip_frame, textvariable=self.title_var)
        self.title_entry.pack(fill="x", pady=(2, 10))
        ttk.Label(self.snip_frame, text="Text").pack(anchor="w")
        self.text_var = tk.StringVar()
        self.text_var.trace_add("write", self._on_text)
        ttk.Entry(self.snip_frame, textvariable=self.text_var,
                  font=self.f_text).pack(fill="x", pady=(2, 0))

        self.bind("<Control-s>", lambda e: self.save())

    def set_status(self, message: str, error: bool = False) -> None:
        self.status.config(text=message, foreground=C_ERROR if error else C_MUTED)

    def _update_title(self) -> None:
        self.title(("• " if self.dirty else "") + TITLE)

    def _mark_dirty(self, *_) -> None:
        if self._loading:
            return
        if not self.dirty:
            self.dirty = True
            self._update_title()
        self.set_status("")

    # --------------------------------------------------------------- model

    def _load(self, config: dict, tabs) -> None:
        self._loading = True
        self.hotkey_var.set(config.get("hotkey", ""))
        self.paste_var.set(bool(config.get("auto_paste", True)))
        for name, groups in tabs:
            # a config without tabs shows one unnamed tab; it needs a name
            # here, and a single tab still shows no tab bar in the picker
            tab = self._insert("", "end", "tab", name or "Snippets")
            for group, items in groups:
                parent = tab if group is None else \
                    self._insert(tab, "end", "group", str(group))
                for s in items:
                    if isinstance(s, dict):
                        self._insert(parent, "end", "snip", dict(s))
        self._loading = False
        top = self.tree.get_children()
        if top:
            self._select(top[0])
        else:
            self._show_detail(None)

    def _insert(self, parent: str, index, kind: str, value) -> str:
        if kind == "snip":
            text = self._snip_label(value)
        else:
            text = value
        iid = self.tree.insert(parent, index, text=text, tags=(kind,), open=True)
        self.kind[iid] = kind
        if kind == "snip":
            self.snips[iid] = value
        self._mark_dirty()
        return iid

    @staticmethod
    def _snip_label(s: dict) -> str:
        return s.get("title") or "(untitled)"

    def _tab_of(self, iid: str | None) -> str | None:
        while iid and self.tree.parent(iid):
            iid = self.tree.parent(iid)
        return iid or None

    def _unique_name(self, parent: str, base: str) -> str:
        taken = {self.tree.item(i, "text") for i in self.tree.get_children(parent)}
        name, n = base, 2
        while name in taken:
            name, n = f"{base} {n}", n + 1
        return name

    def _count_snips(self, iid: str) -> int:
        if self.kind[iid] == "snip":
            return 1
        return sum(self._count_snips(c) for c in self.tree.get_children(iid))

    def _forget(self, iid: str) -> None:
        for child in self.tree.get_children(iid):
            self._forget(child)
        self.kind.pop(iid, None)
        self.snips.pop(iid, None)

    # ----------------------------------------------------------- selection

    def _select(self, iid: str) -> None:
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        # <<TreeviewSelect>> arrives later; show the fields now so callers
        # can focus them right away
        self._show_detail(iid)

    def _on_select(self, _event) -> None:
        sel = self.tree.selection()
        iid = sel[0] if sel else None
        if iid != self.current:
            self._show_detail(iid)

    def _show_detail(self, iid: str | None) -> None:
        self.current = iid
        self._loading = True
        for frame in (self.empty_label, self.name_frame, self.snip_frame):
            frame.pack_forget()
        kind = self.kind.get(iid)
        if kind == "snip":
            s = self.snips[iid]
            self.title_var.set(s.get("title", ""))
            self.text_var.set(s.get("text", ""))
            self.snip_frame.pack(fill="x")
        elif kind:
            self.name_label.config(text="Tab name" if kind == "tab" else "Group name")
            self.name_var.set(self.tree.item(iid, "text"))
            self.name_frame.pack(fill="x")
        else:
            self.empty_label.pack(anchor="w")
        self._loading = False

    def _focus_field(self, iid: str) -> None:
        entry = self.title_entry if self.kind[iid] == "snip" else self.name_entry
        entry.focus_set()
        entry.select_range(0, "end")

    # --------------------------------------------------------- field edits

    def _on_name(self, *_) -> None:
        if self._loading or self.kind.get(self.current) not in ("tab", "group"):
            return
        self.tree.item(self.current, text=self.name_var.get())
        self._mark_dirty()

    def _on_title(self, *_) -> None:
        if self._loading or self.kind.get(self.current) != "snip":
            return
        s = self.snips[self.current]
        s["title"] = self.title_var.get()
        self.tree.item(self.current, text=self._snip_label(s))
        self._mark_dirty()

    def _on_text(self, *_) -> None:
        if self._loading or self.kind.get(self.current) != "snip":
            return
        self.snips[self.current]["text"] = self.text_var.get()
        self._mark_dirty()

    # ------------------------------------------------------------- actions

    def add_tab(self) -> None:
        tab = self._tab_of(self.current)
        index = self.tree.index(tab) + 1 if tab else "end"
        iid = self._insert("", index, "tab", self._unique_name("", "New tab"))
        self._select(iid)
        self._focus_field(iid)

    def add_group(self) -> None:
        tab = self._tab_of(self.current) or \
            self._insert("", "end", "tab", "Snippets")
        # place it after the tab-level item that holds the selection
        anchor = self.current
        while anchor and self.tree.parent(anchor) != tab:
            anchor = self.tree.parent(anchor)
        index = self.tree.index(anchor) + 1 if anchor else "end"
        iid = self._insert(tab, index, "group", self._unique_name(tab, "New group"))
        self._select(iid)
        self._focus_field(iid)

    def add_snippet(self) -> None:
        cur = self.current
        if self.kind.get(cur) == "snip":
            parent, index = self.tree.parent(cur), self.tree.index(cur) + 1
        elif cur:
            parent, index = cur, "end"
        else:
            tabs = self.tree.get_children()
            parent = tabs[0] if tabs else self._insert("", "end", "tab", "Snippets")
            index = "end"
        iid = self._insert(parent, index, "snip", {"title": "New snippet", "text": ""})
        self._select(iid)
        self._focus_field(iid)

    def delete(self) -> None:
        cur = self.current
        if not cur:
            return
        kind = self.kind[cur]
        count = self._count_snips(cur)
        if kind != "snip" and count:
            name = self.tree.item(cur, "text")
            noun = "snippet" if count == 1 else "snippets"
            if not messagebox.askyesno(
                    TITLE, f'Delete {kind} "{name}" and its {count} {noun}?',
                    parent=self):
                return
        nxt = self.tree.next(cur) or self.tree.prev(cur) or self.tree.parent(cur)
        self.current = None   # nothing left to commit into
        self._forget(cur)
        self.tree.delete(cur)
        self._mark_dirty()
        if nxt:
            self._select(nxt)
        else:
            self._show_detail(None)

    def move(self, delta: int) -> None:
        """Move the selection up or down among its siblings. Snippets also
        step into a neighboring group and back out of their own group at
        either end, so they can travel anywhere within a tab."""
        cur = self.current
        if not cur:
            return
        tree = self.tree
        parent = tree.parent(cur)
        siblings = tree.get_children(parent)
        j = siblings.index(cur) + delta
        if 0 <= j < len(siblings):
            neighbor = siblings[j]
            if self.kind[cur] == "snip" and self.kind[neighbor] == "group":
                tree.move(cur, neighbor, "end" if delta < 0 else 0)
                tree.item(neighbor, open=True)
            else:
                tree.move(cur, parent, j)
        elif self.kind[cur] == "snip" and self.kind.get(parent) == "group":
            gi = tree.index(parent)
            tree.move(cur, tree.parent(parent), gi if delta < 0 else gi + 1)
        else:
            return
        tree.see(cur)
        self._mark_dirty()

    # ---------------------------------------------------------------- save

    def _item_name(self, iid: str, taken: dict) -> str:
        name = self.tree.item(iid, "text").strip()
        kind = self.kind[iid]
        if not name:
            raise InvalidConfig(f"Every {kind} needs a name.", iid)
        if name in taken:
            raise InvalidConfig(f'Two {kind}s here are named "{name}".', iid)
        return name

    def _tab_value(self, tab: str) -> dict | list:
        """A tab of only groups is saved as {group: [snippets]}. Snippets
        outside any group need the list form ([snippet, {group: [...]}]),
        the only shape that keeps them in place."""
        groups: dict[str, list[dict]] = {}
        items: list[dict] = []
        for iid in self.tree.get_children(tab):
            if self.kind[iid] == "snip":
                items.append(self.snips[iid])
            else:
                name = self._item_name(iid, groups)
                groups[name] = [self.snips[c] for c in self.tree.get_children(iid)]
                items.append({name: groups[name]})
        return groups if len(groups) == len(items) else items

    def _build_config(self) -> dict:
        hotkey = self.hotkey_var.get().strip()
        try:
            self.check_hotkey(hotkey)
        except ValueError as e:
            raise InvalidConfig(f"Hotkey: {e}") from None
        tabs: dict[str, dict | list] = {}
        for tab in self.tree.get_children():
            tabs[self._item_name(tab, tabs)] = self._tab_value(tab)
        cfg = dict(self.base)   # keeps any keys the editor doesn't know
        if "tabs" not in cfg:
            # a tabless config was shown as one tab and is saved as "tabs";
            # leaving "snippets" too would keep a stale second copy
            cfg.pop("snippets", None)
        cfg.update(hotkey=hotkey, auto_paste=self.paste_var.get(), tabs=tabs)
        return cfg

    def _mtime(self) -> float:
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return 0.0

    def save(self) -> bool:
        try:
            cfg = self._build_config()
        except InvalidConfig as e:
            self.set_status(str(e), error=True)
            if e.iid:
                self._select(e.iid)
                self._focus_field(e.iid)
            return False
        if self._mtime() != self.disk_mtime and not messagebox.askyesno(
                TITLE, "config.json was changed outside the editor after it "
                "was opened. Overwrite those changes?", parent=self):
            return False
        # write a temp file and swap it in, so the picker's auto-reload can
        # never read a half-written config
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.path)
        except OSError as e:
            self.set_status(f"Could not save: {e}", error=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        self.base = cfg
        self.disk_mtime = self._mtime()
        self.dirty = False
        self._update_title()
        self.set_status("Saved.")
        self.on_save()
        return True

    def close(self) -> None:
        if self.dirty:
            answer = messagebox.askyesnocancel(
                TITLE, "Save changes before closing?", parent=self)
            if answer is None or (answer and not self.save()):
                return
        self.destroy()
