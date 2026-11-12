"""Small Windows UI for the v2 research and FTP upload workflows."""

import json
import ftplib
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "ftp_config.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
FTP_DIRECTORY_ERRORS = ftplib.all_errors + (AttributeError,)


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ftp_profile_ready(profile):
    return all(str(profile.get(key, "")).strip() for key in ("host", "username", "password"))


def is_url_line(line):
    line = line.strip()
    return bool(re.search(r"https?://|www\.", line, re.IGNORECASE))


def clean_remote_part(value):
    return str(value or "").replace("\\", "/").strip(" /")


def normalize_remote_root(value):
    path = str(value or "").strip().replace("\\", "/")
    if not path:
        return ""
    if re.match(r"^[A-Za-z]:/", path):
        path = "/" + path
    if path and not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def configured_upload_root(profile):
    explicit = str(profile.get("remote_upload_root", "")).strip()
    if explicit:
        return normalize_remote_root(explicit)
    parts = (
        clean_remote_part(profile.get("remote_base_dir", "/D:/wwwroot")),
        clean_remote_part(profile.get("customer_directory", "")),
        clean_remote_part(profile.get("uploads_directory", "uploads")),
    )
    return normalize_remote_root("/" + "/".join(part for part in parts if part))


def parse_list_directories(lines):
    directories=[]
    for line in lines:
        if line.startswith('d'):
            parts=line.split(None,8)
            if len(parts)==9:
                directories.append(parts[8])
        else:
            match=re.match(r'^\d{2}[-/]\d{2}[-/]\d{2,4}\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\s+<DIR>\s+(.+)$',line,re.I)
            if match:
                directories.append(match.group(1))
    return sorted({name for name in directories if name not in ('.','..')},key=str.casefold)


def list_remote_directories(ftp, progress=None):
    report=progress or (lambda message: None)
    report('登录成功，正在建立被动数据连接并读取目录（MLSD，单次超时10秒）…')
    try:
        entries = ftp.mlsd()
        directories = []
        for name, facts in entries:
            entry_type = str(facts.get("type", "")).lower()
            if name not in (".", "..") and entry_type == "dir":
                directories.append(name)
        return sorted(set(directories), key=str.casefold)
    except ftplib.error_perm as exc:
        if not str(exc).startswith(('500','501','502','504')):
            raise
    except AttributeError:
        pass
    report('服务器不支持MLSD，改用LIST读取文件夹（不逐项进入目录）…')
    lines=[]
    ftp.retrlines('LIST',lines.append)
    return parse_list_directories(lines)


def close_ftp_connection(ftp):
    if ftp is None:
        return
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


class FTPDirectoryDialog:
    """Connect to a selected FTP profile and browse directories read-only."""

    def __init__(self, master, config, profile_name, on_select, log_callback):
        self.profile_name = profile_name
        self.profile = dict(config.get("profiles", {}).get(profile_name, {}))
        self.global_config = config
        self.on_select = on_select
        self.log_callback = log_callback
        self.ftp = None
        self.current_path = ""
        self.entries = []
        self.events = queue.Queue()
        self.busy = False
        self.active_operation = ""
        self.operation_id = 0
        self.operation_started = 0
        self.operation_phase = ""
        self.pending_ftp = None
        self.closing = False

        self.window = tk.Toplevel(master)
        self.window.title(f"浏览 FTP 目录 - {profile_name}")
        self.window.geometry("680x500")
        self.window.minsize(520, 360)
        self.window.transient(master)
        self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(2, weight=1)
        self.status_var = tk.StringVar(value="正在连接FTP并读取目录…")
        self.path_var = tk.StringVar(value="FTP路径：连接中…")

        host = self.profile.get("host", profile_name)
        ttk.Label(self.window, text=f"服务器：{profile_name}（{host}）").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        ttk.Label(self.window, textvariable=self.path_var, wraplength=620).grid(row=1, column=0, sticky="ew", padx=12, pady=4)

        list_frame = ttk.Frame(self.window)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.folder_list = tk.Listbox(list_frame, activestyle="dotbox")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=scrollbar.set)
        self.folder_list.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.folder_list.bind("<Double-Button-1>", self._navigate_selected)

        controls = ttk.Frame(self.window)
        controls.grid(row=3, column=0, sticky="ew", padx=12, pady=4)
        controls.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(controls, textvariable=self.status_var, wraplength=620)
        self.status_label.grid(row=0, column=0, sticky="w", pady=4)

        buttons = ttk.Frame(self.window)
        buttons.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 12))
        self.up_button = ttk.Button(buttons, text="上一级", command=self._go_up)
        self.up_button.pack(side="left")
        self.refresh_button = ttk.Button(buttons, text="刷新", command=lambda: self._run_operation("refresh"))
        self.refresh_button.pack(side="left", padx=6)
        self.reconnect_button = ttk.Button(buttons, text="重新连接", command=lambda: self._run_operation("connect"))
        self.reconnect_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="取消", command=self._close)
        self.cancel_button.pack(side="right")
        self.choose_button = ttk.Button(buttons, text="选择当前目录", command=self._choose_current)
        self.choose_button.pack(side="right", padx=6)
        self.controls = [self.folder_list, self.up_button, self.refresh_button, self.reconnect_button, self.choose_button]
        self.navigation_controls = [self.folder_list, self.up_button, self.refresh_button, self.choose_button]

        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.after(100, self._drain_events)
        self._run_operation("connect")

    def _set_busy(self, value):
        self.busy = value
        for control in self.controls:
            control.configure(state="disabled" if value else "normal")
        if not value and not self.ftp:
            for control in self.navigation_controls:
                control.configure(state="disabled")

    def _run_operation(self, operation, directory=None):
        if self.busy or self.closing:
            return
        self._set_busy(True)
        self.active_operation = operation
        self.operation_id += 1
        token = self.operation_id
        self.operation_started = time.monotonic()
        self.operation_phase = "正在连接FTP" if operation=='connect' else "正在读取目录"
        target_directory = directory
        self.status_var.set("正在连接并读取目录…" if operation == "connect" else "正在读取目录…")

        def emit(event):
            self.events.put((token,event))

        def progress(message):
            emit(("progress",message))

        def worker():
            ftp = self.ftp
            created_ftp = False
            try:
                if operation == "connect":
                    ftp_config = {key: value for key, value in self.global_config.items() if key not in ("profiles", "default_profile")}
                    ftp_config.update(self.profile)
                    ftp_cls = ftplib.FTP_TLS if ftp_config.get("use_tls") else ftplib.FTP
                    ftp = ftp_cls()
                    self.pending_ftp = ftp
                    created_ftp = True
                    progress(f"正在连接 {ftp_config['host']}:{int(ftp_config.get('port',21))}（单次超时10秒）…")
                    ftp.connect(
                        ftp_config["host"],
                        int(ftp_config.get("port", 21)),
                        timeout=10,
                    )
                    progress('FTP控制连接成功，正在登录…')
                    ftp.login(ftp_config["username"], ftp_config["password"])
                    progress('FTP登录成功，正在读取当前路径…')
                    ftp.set_pasv(bool(ftp_config.get("passive", True)))
                    if ftp_config.get("use_tls"):
                        ftp.prot_p()
                    try:
                        ftp.sendcmd("OPTS UTF8 ON")
                    except ftplib.all_errors:
                        pass
                    directory = ftp.pwd()
                elif operation == "up":
                    ftp.cwd("..")
                    directory = ftp.pwd()
                elif operation == "navigate":
                    ftp.cwd(target_directory)
                    directory = ftp.pwd()
                else:
                    directory = ftp.pwd()

                folders = list_remote_directories(ftp,progress)
                if self.closing or token!=self.operation_id:
                    close_ftp_connection(ftp)
                    return
                emit(("loaded", operation, ftp, directory, folders, created_ftp))
                if self.closing:
                    close_ftp_connection(ftp)
            except Exception as exc:
                if ftp:
                    try:
                        ftp.close()
                    except Exception:
                        pass
                if not self.closing and token==self.operation_id:
                    detail=f'{type(exc).__name__}: {exc}'
                    if isinstance(exc,TimeoutError):
                        detail+='。若停在读取目录阶段，请检查FTP被动数据端口、防火墙和当前网络。'
                    emit(("error", operation, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self):
        if self.closing:
            return
        try:
            while True:
                token,event = self.events.get_nowait()
                if token!=self.operation_id:
                    continue
                if event[0]=='progress':
                    self.operation_phase=event[1]
                    self.log_callback(event[1])
                    continue
                if event[0] == "loaded":
                    _, operation, ftp, path, folders, created_ftp = event
                    if created_ftp and self.ftp and self.ftp is not ftp:
                        self.ftp.close()
                    self.ftp = ftp
                    self.current_path = path
                    self.entries = folders
                    self.path_var.set(f"FTP路径：{path}")
                    self.folder_list.configure(state='normal')
                    self.folder_list.delete(0, "end")
                    for item in folders:
                        self.folder_list.insert("end", item.rstrip("/").rsplit("/", 1)[-1])
                    self.status_var.set(f"已连接，找到 {len(folders)} 个子文件夹。双击文件夹可进入。")
                    if created_ftp:
                        self.log_callback(f"FTP连接成功：{self.profile_name}，当前目录 {path}")
                    elif operation in ("navigate", "up"):
                        self.log_callback(f"FTP目录：{path}")
                    self._set_busy(False)
                elif event[0] == "error":
                    _, operation, message = event
                    if self.ftp:
                        self.ftp.close()
                    self.ftp=None
                    self.pending_ftp=None
                    self.current_path=''
                    self.status_var.set(f"连接或读取失败：{message}")
                    self.log_callback(f"FTP目录浏览失败（{self.profile_name}）：{message}")
                    self._set_busy(False)
                    messagebox.showerror("FTP连接/读取失败", message, parent=self.window)
        except queue.Empty:
            pass
        if self.busy:
            elapsed=time.monotonic()-self.operation_started
            self.status_var.set(f'{self.operation_phase} 已等待{elapsed:.0f}秒（本次操作总上限45秒）')
            if elapsed>=45:
                self.operation_id+=1
                for connection in (self.pending_ftp,self.ftp):
                    if connection:
                        try:
                            connection.close()
                        except Exception:
                            pass
                self.ftp=None
                self.pending_ftp=None
                self._set_busy(False)
                message='FTP操作超过45秒，已停止等待。最后阶段：'+self.operation_phase
                self.status_var.set(message)
                self.log_callback(message)
        self.window.after(100, self._drain_events)

    def _navigate_selected(self, _event=None):
        if self.busy or not self.ftp:
            return
        selection = self.folder_list.curselection()
        if not selection:
            return
        self._run_operation("navigate", self.entries[selection[0]])

    def _go_up(self):
        if self.busy or not self.ftp:
            return
        self._run_operation("up")

    def _choose_current(self):
        if self.busy or not self.ftp or not self.current_path:
            return
        chosen_path = normalize_remote_root(self.current_path)
        self.on_select(chosen_path)
        self.log_callback(f"已选择FTP上传根目录：{chosen_path}")
        self._close()

    def _close(self):
        if self.closing:
            return
        self.closing = True
        ftp = self.ftp
        self.ftp = None
        while True:
            try:
                _,event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "loaded" and event[2] is not ftp:
                threading.Thread(target=close_ftp_connection, args=(event[2],), daemon=True).start()
        # Navigation uses the existing connection on its worker thread. Let that
        # worker close it after the current command finishes to avoid socket races.
        if ftp and not (self.busy and self.active_operation in ("up", "navigate", "refresh")):
            threading.Thread(target=close_ftp_connection, args=(ftp,), daemon=True).start()
        if self.pending_ftp:
            try:
                self.pending_ftp.close()
            except Exception:
                pass
        self.window.destroy()


class ResearchUploadUI:
    def __init__(self, root):
        self.root = root
        self.root.title("官网调研与图片上传")
        self.root.geometry("1080x820")
        self.root.minsize(850, 650)

        try:
            self.config = load_config()
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("配置读取失败", f"无法读取 ftp_config.json：\n{exc}")
            raise

        self.profiles = self.config.get("profiles", {})
        if not self.profiles:
            messagebox.showerror("配置错误", "ftp_config.json 中没有 profiles 配置。")
            raise ValueError("ftp_config.json has no profiles")

        self.profile_var = tk.StringVar(
            value=self.config.get("default_profile") if self.config.get("default_profile") in self.profiles else next(iter(self.profiles))
        )
        self.remote_root_var = tk.StringVar()
        self.ftp_status_var = tk.StringVar(value="尚未连接")
        self.domain_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.month_var = tk.StringVar(value=datetime.now().strftime("%Y%m"))
        self.remote_preview_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="仅采集指定网址（跳过 Google）")
        self.results_var = tk.StringVar(value="20")
        self.dry_run_var = tk.BooleanVar(value=False)
        self.input_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")
        self.log_queue = queue.Queue()
        self.process = None
        self.active_buttons = []
        self.closing = False

        self._build_ui()
        self._load_profile_defaults()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=3)
        self.root.rowconfigure(1, weight=2)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))
        self.research_tab = ttk.Frame(self.tabs, padding=12)
        self.upload_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.research_tab, text="网站调研")
        self.tabs.add(self.upload_tab, text="图片上传")

        self._build_research_tab()
        self._build_upload_tab()

        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=8)
        log_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Label(log_frame, textvariable=self.status_var).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(log_frame, text="程序等待输入时：").grid(row=1, column=1, sticky="e", padx=(8, 2), pady=(6, 0))
        self.input_entry = ttk.Entry(log_frame, textvariable=self.input_var, width=14)
        self.input_entry.grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Button(log_frame, text="发送（空白=回车）", command=self._send_input).grid(row=1, column=3, sticky="e", padx=(4, 0), pady=(6, 0))

    def _build_research_tab(self):
        tab = self.research_tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(4, weight=1)

        ttk.Label(tab, text="目标公司官网：").grid(row=0, column=0, sticky="w", pady=5)
        self.target_entry = ttk.Entry(tab)
        self.target_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)
        self.target_entry.insert(0, "")

        ttk.Label(tab, text="查找模式：").grid(row=1, column=0, sticky="w", pady=5)
        mode_box = ttk.Combobox(
            tab,
            textvariable=self.mode_var,
            state="readonly",
            values=("仅采集指定网址（跳过 Google）", "Google 搜索关键词 + 指定网址"),
        )
        mode_box.grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(tab, text="每个关键词 Google 结果数：").grid(row=1, column=2, sticky="e", padx=(12, 4), pady=5)
        ttk.Spinbox(tab, from_=1, to=100, textvariable=self.results_var, width=6).grid(row=1, column=3, sticky="w", pady=5)

        ttk.Label(
            tab,
            text="关键词和官网网址：每个关键词单独一行；要指定官网时，在该关键词下面逐行粘贴网址。可直接加载 keywords.txt。",
            wraplength=900,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 4))

        buttons = ttk.Frame(tab)
        buttons.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        ttk.Button(buttons, text="加载项目 keywords.txt", command=self._load_keywords_file).pack(side="left")
        ttk.Label(buttons, text="示例：关键词一行，下面跟 https://example.com/；下一关键词开始新块。", foreground="#555").pack(side="left", padx=10)

        text_frame = ttk.Frame(tab)
        text_frame.grid(row=4, column=0, columnspan=4, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.keyword_text = tk.Text(text_frame, height=12, wrap="word", undo=True)
        keyword_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.keyword_text.yview)
        self.keyword_text.configure(yscrollcommand=keyword_scroll.set)
        self.keyword_text.grid(row=0, column=0, sticky="nsew")
        keyword_scroll.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(tab)
        actions.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.research_button = ttk.Button(actions, text="开始调研", command=self._start_research)
        self.research_button.pack(side="left")
        self.active_buttons.append(self.research_button)
        ttk.Label(actions, text="所有关键词按顺序执行；每个关键词会生成独立 runs 结果目录。", foreground="#555").pack(side="left", padx=10)

    def _build_upload_tab(self):
        tab = self.upload_tab
        tab.columnconfigure(1, weight=1)

        ttk.Label(tab, text="FTP 服务器：").grid(row=0, column=0, sticky="w", pady=7)
        self.profile_box = ttk.Combobox(tab, textvariable=self.profile_var, state="readonly", values=tuple(self.profiles))
        self.profile_box.grid(row=0, column=1, sticky="w", pady=7)
        self.profile_box.bind("<<ComboboxSelected>>", self._on_profile_change)
        self.browse_ftp_button = ttk.Button(tab, text="连接FTP并选择目录…", command=self._browse_remote_directory)
        self.browse_ftp_button.grid(row=0, column=2, sticky="w", padx=(10, 0), pady=7)
        ttk.Label(tab, textvariable=self.ftp_status_var, foreground="#555").grid(row=0, column=3, sticky="w", padx=(8, 0), pady=7)

        ttk.Label(tab, text="远程上传根目录：").grid(row=1, column=0, sticky="w", pady=7)
        self.remote_root_entry = ttk.Entry(tab, textvariable=self.remote_root_var)
        self.remote_root_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=7)
        ttk.Label(
            tab,
            text="月份子目录会自动追加。这里填写FTP路径（server1以 /D:/ 开头，server2以 /wwwroot/ 开头），不要填Windows物理路径；也可连接后浏览选择。",
            foreground="#555",
            wraplength=850,
        ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(0, 7))
        self.remote_root_var.trace_add("write", lambda *_: self._update_remote_preview())

        ttk.Label(tab, text="本地图片目录：").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Entry(tab, textvariable=self.source_var).grid(row=3, column=1, columnspan=2, sticky="ew", pady=7)
        ttk.Button(tab, text="选择文件夹…", command=self._choose_source).grid(row=3, column=3, padx=(8, 0), pady=7)

        ttk.Label(tab, text="上传月份：").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Entry(tab, textvariable=self.month_var, width=10).grid(row=4, column=1, sticky="w", pady=7)
        ttk.Label(tab, text="格式：YYYYMM，例如 202610").grid(row=4, column=2, sticky="w", padx=(8, 0), pady=7)
        self.month_var.trace_add("write", lambda *_: self._update_remote_preview())

        ttk.Label(tab, text="图片公网域名：").grid(row=5, column=0, sticky="w", pady=7)
        ttk.Entry(tab, textvariable=self.domain_var).grid(row=5, column=1, columnspan=3, sticky="ew", pady=7)

        ttk.Label(tab, text="含月份的目标目录：").grid(row=6, column=0, sticky="w", pady=7)
        ttk.Label(tab, textvariable=self.remote_preview_var, wraplength=800).grid(row=6, column=1, columnspan=3, sticky="w", pady=7)

        ttk.Checkbutton(tab, text="仅模拟检查，不实际上传", variable=self.dry_run_var).grid(row=7, column=1, sticky="w", pady=(10, 5))
        actions = ttk.Frame(tab)
        actions.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.upload_button = ttk.Button(actions, text="开始上传图片", command=self._start_upload)
        self.upload_button.pack(side="left")
        self.active_buttons.append(self.upload_button)
        self.active_buttons.append(self.browse_ftp_button)
        ttk.Label(actions, text="上传后在所选图片目录生成 image_url_mapping.csv。", foreground="#555").pack(side="left", padx=10)

    def _load_profile_defaults(self):
        profile = self.profiles.get(self.profile_var.get(), {})
        self.remote_root_var.set(configured_upload_root(profile))
        self.domain_var.set(profile.get("public_domain", ""))
        self.ftp_status_var.set("尚未连接")
        self._update_remote_preview()

    def _on_profile_change(self, _event=None):
        self._load_profile_defaults()

    def _update_remote_preview(self):
        root = normalize_remote_root(self.remote_root_var.get())
        month = clean_remote_part(self.month_var.get())
        self.remote_preview_var.set(root + ("/" + month if month else "") if root else "尚未设置远程上传根目录")

    def _browse_remote_directory(self):
        if self.process:
            return
        profile_name = self.profile_var.get()
        if not ftp_profile_ready(self.profiles.get(profile_name, {})):
            messagebox.showinfo("尚未配置FTP", "分发版只提供空白FTP模板。请向同事索取带完整内容的 ftp_config.json，替换项目根目录的空模板后重新启动工具。网站调研功能可先使用。")
            return
        self.ftp_status_var.set("FTP目录浏览器已打开")
        self._log(f"连接FTP以浏览目录：{profile_name}")
        FTPDirectoryDialog(
            self.root,
            self.config,
            profile_name,
            self._set_remote_directory,
            self._log,
        )

    def _set_remote_directory(self, path):
        self.remote_root_var.set(normalize_remote_root(path))
        self.ftp_status_var.set("已连接并选择目录")

    def _choose_source(self):
        initial = self.source_var.get().strip() or str(BASE)
        if not Path(initial).is_dir():
            initial = str(BASE)
        selected = filedialog.askdirectory(title="选择待上传图片目录", initialdir=initial)
        if selected:
            self.source_var.set(selected)

    def _load_keywords_file(self):
        path = filedialog.askopenfilename(
            title="选择关键词文件",
            initialdir=str(BASE),
            initialfile="keywords.txt",
            filetypes=(("文本文件", "*.txt"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc))
            return
        self.keyword_text.delete("1.0", "end")
        self.keyword_text.insert("1.0", content)
        self._log(f"已载入关键词文件：{path}")

    def _parse_keyword_blocks(self, text):
        blocks = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if is_url_line(line):
                if not blocks:
                    raise ValueError(f"网址前缺少关键词：{line}")
                blocks[-1]["urls"].append(line)
            else:
                blocks.append({"keyword": line, "urls": []})
        if not blocks:
            raise ValueError("请至少输入一个关键词。")
        return blocks

    def _start_research(self):
        if self.process:
            return
        target = self.target_entry.get().strip()
        if not re.search(r"https?://[^\s]+", target, re.IGNORECASE):
            messagebox.showerror("信息不完整", "请填写目标公司官网，例如 https://www.example.com/")
            return
        try:
            blocks = self._parse_keyword_blocks(self.keyword_text.get("1.0", "end-1c"))
            results = int(self.results_var.get())
            if results < 1:
                raise ValueError("Google 结果数必须大于 0。")
        except ValueError as exc:
            messagebox.showerror("关键词格式有误", str(exc))
            return

        mode = "urls-only" if self.mode_var.get().startswith("仅采集") else "google-plus-urls"
        if mode == "urls-only":
            missing = [block["keyword"] for block in blocks if not block["urls"]]
            if missing:
                messagebox.showerror(
                    "缺少指定网址",
                    "仅采集指定网址模式要求每个关键词下面至少有一个网址。\n\n缺少网址的关键词：\n" + "\n".join(missing[:10]),
                )
                return

        jobs_dir = BASE / "runs" / "_ui_jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        keywords_path = jobs_dir / f"keywords_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.txt"
        keywords_path.write_text(self.keyword_text.get("1.0", "end-1c").strip() + "\n", encoding="utf-8")

        command = [
            sys.executable,
            str(BASE / "research.py"),
            "--keywords-file",
            str(keywords_path),
            "--target-url",
            target,
            "--keyword-url-mode",
            mode,
            "--companies",
            "0",
            "--results",
            str(results),
        ]
        self._log(f"启动调研：{len(blocks)} 个关键词，模式={mode}")
        self._log(f"输入副本：{keywords_path}")
        self._run_command(command, "网站调研")

    def _start_upload(self):
        if self.process:
            return
        profile_name = self.profile_var.get()
        profile = self.profiles.get(profile_name, {})
        if not self.dry_run_var.get() and not ftp_profile_ready(profile):
            messagebox.showinfo("尚未配置FTP", "请向同事索取带完整内容的 ftp_config.json，替换项目根目录的空模板后重新启动工具。")
            return
        source = Path(self.source_var.get().strip())
        remote_root = normalize_remote_root(self.remote_root_var.get())
        domain = self.domain_var.get().strip()
        month = self.month_var.get().strip()

        if not source.is_dir():
            messagebox.showerror("信息不完整", "请选择有效的本地图片目录。")
            return
        if not remote_root:
            messagebox.showerror("信息不完整", "请填写远程上传根目录，或连接FTP后选择目录。")
            return
        if not re.fullmatch(r"\d{6}", month):
            messagebox.showerror("月份格式有误", "上传月份请使用 YYYYMM，例如 202610。")
            return

        images = [path for path in source.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
        if not images:
            messagebox.showerror("没有图片", "所选目录中没有 jpg、jpeg、png、webp 或 gif 图片。")
            return

        self._update_remote_preview()
        host = profile.get("host", profile_name)
        destination = self.remote_preview_var.get()
        if not self.dry_run_var.get() and not messagebox.askyesno(
            "确认上传",
            f"服务器：{profile_name} ({host})\n远程目录：{destination}\n图片数量：{len(images)}\n\n确认开始上传？",
        ):
            return

        command = [
            sys.executable,
            str(BASE / "deploy_ftp.py"),
            "--profile",
            profile_name,
            "--remote-upload-root",
            remote_root,
            "--source",
            str(source),
            "--month",
            month,
        ]
        if domain:
            command.extend(("--public-domain", domain))
        if self.dry_run_var.get():
            command.append("--dry-run")

        self._log(f"启动上传：{len(images)} 张图片，服务器={profile_name}")
        self._log(f"远程目录：{destination}")
        self._run_command(command, "FTP 上传")

    def _run_command(self, command, task_name):
        for button in self.active_buttons:
            button.configure(state="disabled")
        self.status_var.set(f"{task_name}运行中…")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(BASE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.process = None
            for button in self.active_buttons:
                button.configure(state="normal")
            self.status_var.set("启动失败")
            self._log(f"启动失败：{exc}")
            messagebox.showerror("启动失败", str(exc))
            return

        threading.Thread(target=self._read_process_output, args=(self.process,), daemon=True).start()

    def _read_process_output(self, process):
        try:
            if process.stdout:
                for line in process.stdout:
                    self.log_queue.put(("line", line.rstrip("\r\n")))
            return_code = process.wait()
            if process.stdin:
                process.stdin.close()
            self.log_queue.put(("done", return_code))
        except Exception as exc:
            self.log_queue.put(("line", f"读取运行日志时出错：{exc}"))
            self.log_queue.put(("done", -1))

    def _drain_log_queue(self):
        try:
            while True:
                kind, value = self.log_queue.get_nowait()
                if kind == "line":
                    self._log(value)
                else:
                    self.process = None
                    for button in self.active_buttons:
                        button.configure(state="normal")
                    if value == 0:
                        self.status_var.set("任务完成")
                        self._log("任务完成。")
                    else:
                        self.status_var.set(f"任务结束，退出代码 {value}")
                        self._log(f"任务结束，退出代码 {value}。")
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(100, self._drain_log_queue)

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(message) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _send_input(self):
        process = self.process
        if not process or process.poll() is not None or not process.stdin:
            self.status_var.set("当前没有等待输入的任务")
            return
        value = self.input_var.get()
        try:
            process.stdin.write(value + "\n")
            process.stdin.flush()
            self._log("已向当前任务发送输入。")
            self.input_var.set("")
        except (OSError, ValueError) as exc:
            self._log(f"发送输入失败：{exc}")

    def _on_close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("任务运行中", "当前任务仍在运行。关闭窗口会停止该任务，确定关闭吗？"):
                return
            if os.name == "nt":
                subprocess.Popen(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                self.process.terminate()
        self.closing = True
        self.root.destroy()


def main():
    root = tk.Tk()
    ResearchUploadUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
