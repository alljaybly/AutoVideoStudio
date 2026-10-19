"""Tk widgets stay on the main thread. Worker communicates only via a queue."""
from __future__ import annotations

from pathlib import Path
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .editing import EditProposal, EditRequestError, next_version_path, parse_edit_request
from .engine import (Cancelled, MediaItem, Overlay, RenderError, RenderOptions,
                     Renderer, Transition, find_tools, make_logger, output_path)
from .preview import PreviewPathError, existing_mp4_path


class Studio:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.logger = make_logger()
        self.items: list[MediaItem] = []
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.renderer: Renderer | None = None
        self.preview_worker: threading.Thread | None = None
        self.previewing = False
        self.verified_tools = None
        self.pending_edit: EditProposal | None = None
        self.pending_filename: str | None = None
        self.cancel_requested = threading.Event()
        self.result: Path | None = None
        self.closing = False
        self.controls = []
        self._style()
        root.title('AutoVideo Studio')
        work_left, work_top, usable_width, usable_height = self._usable_area(root)
        window_width = min(1080, max(860, usable_width - 80))
        window_height = min(820, max(640, usable_height - 45))
        window_x = work_left + max(0, (usable_width - window_width) // 2)
        root.geometry(f'{window_width}x{window_height}+{window_x}+{work_top + 20}')
        root.minsize(860, min(640, window_height))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.report_callback_exception = self.callback_error
        self.voice = tk.StringVar()
        self.music = tk.StringVar()
        self.tools_dir = tk.StringVar()
        self.folder = tk.StringVar(value=str(Path.home() / 'Videos' if (Path.home() / 'Videos').is_dir() else Path.home()))
        self.filename = tk.StringVar(value='my-video.mp4')
        self.aspect = tk.StringVar(value='landscape')
        self.seconds = tk.StringVar(value='5')
        self.volume = tk.StringVar(value='22')
        # Transitions
        self.transition_type = tk.StringVar(value='cut')
        self.transition_duration = tk.StringVar(value='1.0')
        # Timed text overlays
        self.overlay_enabled = tk.BooleanVar(value=False)
        self.overlay_text = tk.StringVar(value='')
        self.overlay_start = tk.StringVar(value='0')
        self.overlay_end = tk.StringVar(value='5')
        self.overlay_font_size = tk.StringVar(value='24')
        self.overlay_color = tk.StringVar(value='#FFFFFF')
        self.overlay_position = tk.StringVar(value='bottom')
        self.overlay_background = tk.BooleanVar(value=True)
        # Audio ducking
        self.ducking_enabled = tk.BooleanVar(value=False)
        self.ducking_strength = tk.StringVar(value='0.30')
        self.ducking_threshold = tk.StringVar(value='-30')
        self.status = tk.StringVar(value='Ready — add visuals; narration and music are optional.')
        self.progress_text = tk.StringVar(value='No job running')
        self.output_path_text = tk.StringVar(value='No verified rendered output yet.')
        self.edit_summary = tk.StringVar(value='Supported settings only; this does not edit video pixels or speech.')
        frame = ttk.Frame(root, style='App.TFrame', padding=(16, 8))
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        header = ttk.Frame(frame, style='App.TFrame')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        ttk.Label(header, text='AutoVideo Studio', style='Title.TLabel').pack(anchor='w')
        ttk.Label(header, text='OFFLINE VIDEO WORKSPACE     •     CPU RENDERING     •     24 FPS',
                  style='Eyebrow.TLabel').pack(anchor='w', pady=(4, 0))

        # --- Scrollable content area (sections 01-05) -----------------------
        scroll_container = ttk.Frame(frame, style='App.TFrame')
        scroll_container.grid(row=1, column=0, sticky='nsew')
        scroll_container.columnconfigure(0, weight=1)
        scroll_container.rowconfigure(0, weight=1)

        canvas = tk.Canvas(scroll_container, bg='#0b1116', highlightthickness=0)
        canvas.grid(row=0, column=0, sticky='nsew')

        scrollbar = ttk.Scrollbar(scroll_container, orient='vertical', command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')

        canvas.configure(yscrollcommand=scrollbar.set)

        content = ttk.Frame(canvas, style='App.TFrame')
        canvas_window = canvas.create_window((0, 0), window=content, anchor='nw')

        content.columnconfigure(0, weight=1)

        def _configure_content(event):
            canvas.configure(scrollregion=canvas.bbox('all'))
        content.bind('<Configure>', _configure_content)

        def _configure_canvas(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind('<Configure>', _configure_canvas)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        media_card = ttk.LabelFrame(content, text='  01  VISUAL SEQUENCE  ', style='Section.TLabelframe', padding=8)
        media_card.grid(row=0, column=0, sticky='ew', pady=(0, 5))
        media_card.columnconfigure(0, weight=1)
        media_card.rowconfigure(1, weight=1)
        ttk.Label(media_card, text='Add clips and still images, then arrange their playback order.',
                  style='Muted.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 4))
        media = ttk.Frame(media_card, style='Card.TFrame')
        media.grid(row=1, column=0, sticky='nsew')

        media.columnconfigure(0, weight=1)
        media.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(media, height=3, selectmode='browse', exportselection=False,
                                  bg='#111a20', fg='#e6edf1', selectbackground='#087f8c',
                                  selectforeground='#ffffff', highlightthickness=1,
                                  highlightbackground='#263841', highlightcolor='#23c6d2',
                                  relief='flat', borderwidth=0, font=('Segoe UI', 10),
                                  activestyle='none')
        self.listbox.grid(row=0, column=0, sticky='nsew')
        ybar = ttk.Scrollbar(media, orient='vertical', command=self.listbox.yview, style='Vertical.TScrollbar')
        ybar.grid(row=0, column=1, sticky='ns')
        xbar = ttk.Scrollbar(media, orient='horizontal', command=self.listbox.xview, style='Horizontal.TScrollbar')
        xbar.grid(row=1, column=0, sticky='ew')
        self.listbox.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.empty_sequence = ttk.Label(media, text='Your visual sequence will appear here\nAdd a video or image to get started.',
                                        style='EmptyState.TLabel', justify='center')
        self.empty_sequence.place(relx=0.5, rely=0.5, anchor='center')
        buttons = ttk.Frame(media_card, style='Card.TFrame')
        buttons.grid(row=2, column=0, sticky='ew', pady=(5, 0))
        for title, command in [('＋  Add videos', lambda: self.add('video')), ('＋  Add images', lambda: self.add('image')),
                               ('Move up', lambda: self.move(-1)), ('Move down', lambda: self.move(1)), ('Remove', self.remove)]:
            self.button(buttons, title, command).pack(side='left', padx=(0, 7))

        # --- Transitions panel ------------------------------------------------
        trans_panel = ttk.LabelFrame(content, text='  02  VISUAL TRANSITIONS  ', style='Section.TLabelframe', padding=7)
        trans_panel.grid(row=1, column=0, sticky='ew', pady=(5, 0))
        trans_panel.columnconfigure(1, weight=1)
        ttk.Label(trans_panel, text='Type', style='Field.TLabel').grid(row=0, column=0, sticky='w', pady=(2, 1))
        trans_type = ttk.Combobox(trans_panel, textvariable=self.transition_type,
                                  values=['cut', 'crossfade', 'fade_to_black'], state='readonly', width=14)
        trans_type.grid(row=0, column=1, sticky='w', padx=8, pady=(2, 1))
        self.controls.append(trans_type)
        ttk.Label(trans_panel, text='Duration (seconds)', style='Field.TLabel').grid(row=0, column=2, sticky='w', pady=(2, 1))
        self.entry(trans_panel, self.transition_duration, 8).grid(row=0, column=3, sticky='w', padx=8, pady=(2, 1))
        ttk.Label(trans_panel, text='Applied between every visual gap. Cut = instant switch.', style='Muted.TLabel').grid(row=0, column=4, sticky='w', pady=(2, 1))
        ttk.Label(trans_panel, text='Crossfade blends frames; fade-to-black dips through black.', style='Muted.TLabel').grid(row=1, column=0, columnspan=5, sticky='w', pady=(0, 2))

        lower = ttk.Frame(content, style='App.TFrame')
        lower.grid(row=2, column=0, sticky='ew')
        lower.columnconfigure(0, weight=1, uniform='panels')
        lower.columnconfigure(1, weight=1, uniform='panels')
        audio_panel = ttk.LabelFrame(lower, text='  02  AUDIO MIX  ', style='Section.TLabelframe', padding=7)
        audio_panel.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        audio_panel.columnconfigure(1, weight=1)
        ttk.Label(audio_panel, text='Narration and music are optional. Clip audio is kept in sequence.',
                  style='Muted.TLabel', wraplength=430).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 3))
        self.file_row(audio_panel, 1, 'Narration', self.voice, lambda: self.audio(self.voice))
        self.file_row(audio_panel, 2, 'Background music', self.music, lambda: self.audio(self.music))
        self.button(audio_panel, 'Clear', lambda: self.music.set(''), 'Compact.TButton').grid(row=2, column=3, padx=(4, 0))
        ttk.Label(audio_panel, text='Music gain', style='Field.TLabel').grid(row=3, column=0, sticky='w', pady=(6, 1))
        self.entry(audio_panel, self.volume, 6).grid(row=3, column=1, sticky='w', padx=8, pady=(6, 1))
        ttk.Label(audio_panel, text='%  ·  0–50%, default 22%', style='Muted.TLabel').grid(row=3, column=2, columnspan=2, sticky='w', pady=(6, 1))
        # Audio ducking controls
        self.ducking_check = ttk.Checkbutton(audio_panel, text='Auto-duck music under narration', variable=self.ducking_enabled)
        self.ducking_check.grid(row=4, column=0, columnspan=4, sticky='w', pady=(8, 1))
        ttk.Label(audio_panel, text='Duck to', style='Field.TLabel').grid(row=5, column=0, sticky='w', pady=(4, 1))
        self.entry(audio_panel, self.ducking_strength, 6).grid(row=5, column=1, sticky='w', padx=8, pady=(4, 1))
        ttk.Label(audio_panel, text='of music volume during speech  ·  0.05–0.90', style='Muted.TLabel').grid(row=5, column=2, columnspan=2, sticky='w', pady=(4, 1))
        ttk.Label(audio_panel, text='Speech threshold', style='Field.TLabel').grid(row=6, column=0, sticky='w', pady=(4, 1))
        self.entry(audio_panel, self.ducking_threshold, 6).grid(row=6, column=1, sticky='w', padx=8, pady=(4, 1))
        ttk.Label(audio_panel, text='dB  ·  narration louder than this is treated as speech', style='Muted.TLabel').grid(row=6, column=2, columnspan=2, sticky='w', pady=(4, 1))

        output_panel = ttk.LabelFrame(lower, text='  03  OUTPUT  ', style='Section.TLabelframe', padding=7)
        output_panel.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        output_panel.columnconfigure(1, weight=1)
        self.file_row(output_panel, 0, 'Save to', self.folder, self.choose_folder)
        ttk.Label(output_panel, text='Filename', style='Field.TLabel').grid(row=1, column=0, sticky='w', pady=1)
        self.entry(output_panel, self.filename).grid(row=1, column=1, columnspan=2, sticky='ew', padx=8)
        ttk.Label(output_panel, text='FFmpeg folder', style='Field.TLabel').grid(row=2, column=0, sticky='w', pady=1)
        self.entry(output_panel, self.tools_dir).grid(row=2, column=1, sticky='ew', padx=8)
        self.button(output_panel, 'Browse', self.choose_tools).grid(row=2, column=2)
        self.button(output_panel, 'Auto-find', lambda: self.tools_dir.set(''), 'Compact.TButton').grid(row=2, column=3, padx=(4, 0))
        ttk.Label(output_panel, text='Frame', style='Field.TLabel').grid(row=3, column=0, sticky='w', pady=(5, 1))
        settings = ttk.Frame(output_panel, style='Card.TFrame')
        settings.grid(row=3, column=1, columnspan=3, sticky='w', pady=(5, 1))
        for text, value in [('Landscape 16:9 (1280×720)', 'landscape'), ('Vertical 9:16 (720×1280)', 'vertical')]:
            radio = ttk.Radiobutton(settings, text=text, variable=self.aspect, value=value)
            radio.pack(side='left', padx=(0, 12))
            self.controls.append(radio)
        ttk.Label(output_panel, text='Still image duration', style='Field.TLabel').grid(row=4, column=0, sticky='w', pady=(3, 0))
        self.entry(output_panel, self.seconds, 6).grid(row=4, column=1, sticky='w', padx=8, pady=(3, 0))
        ttk.Label(output_panel, text='seconds per image', style='Muted.TLabel').grid(row=4, column=2, columnspan=2, sticky='w', pady=(3, 0))

        # --- Overlays panel ---------------------------------------------------
        ovl_panel = ttk.LabelFrame(content, text='  04  TIMED TEXT OVERLAY  ', style='Section.TLabelframe', padding=7)
        ovl_panel.grid(row=3, column=0, sticky='ew', pady=(5, 0))
        ovl_panel.columnconfigure(3, weight=1)
        self.overlay_check = ttk.Checkbutton(ovl_panel, text='Add timed text overlay', variable=self.overlay_enabled)
        self.overlay_check.grid(row=0, column=0, sticky='w', pady=(2, 1))
        ttk.Label(ovl_panel, text='Text', style='Field.TLabel').grid(row=0, column=1, sticky='w', pady=(2, 1), padx=(8, 0))
        self.entry(ovl_panel, self.overlay_text, 24).grid(row=0, column=2, sticky='w', padx=8, pady=(2, 1))
        ttk.Label(ovl_panel, text='Start', style='Field.TLabel').grid(row=0, column=3, sticky='w', pady=(2, 1))
        self.entry(ovl_panel, self.overlay_start, 6).grid(row=0, column=4, sticky='w', padx=8, pady=(2, 1))
        ttk.Label(ovl_panel, text='End', style='Field.TLabel').grid(row=0, column=5, sticky='w', pady=(2, 1))
        self.entry(ovl_panel, self.overlay_end, 6).grid(row=0, column=6, sticky='w', padx=8, pady=(2, 1))
        ttk.Label(ovl_panel, text='Size', style='Field.TLabel').grid(row=1, column=0, sticky='w', pady=(2, 1))
        self.entry(ovl_panel, self.overlay_font_size, 6).grid(row=1, column=1, sticky='w', padx=8, pady=(2, 1))
        ttk.Label(ovl_panel, text='Position', style='Field.TLabel').grid(row=1, column=2, sticky='w', pady=(2, 1))
        ovl_pos = ttk.Combobox(ovl_panel, textvariable=self.overlay_position,
                               values=['top', 'centre', 'bottom'], state='readonly', width=10)
        ovl_pos.grid(row=1, column=3, sticky='w', padx=8, pady=(2, 1))
        self.controls.append(ovl_pos)
        self.overlay_bg_check = ttk.Checkbutton(ovl_panel, text='Background box', variable=self.overlay_background)
        self.overlay_bg_check.grid(row=1, column=4, sticky='w', pady=(2, 1))
        ttk.Label(ovl_panel, text='Color', style='Field.TLabel').grid(row=1, column=5, sticky='w', pady=(2, 1))
        self.entry(ovl_panel, self.overlay_color, 10).grid(row=1, column=6, sticky='w', padx=8, pady=(2, 1))
        ttk.Label(ovl_panel, text='Appears between start and end seconds (FFmpeg time). Enable to render.', style='Muted.TLabel').grid(row=2, column=0, columnspan=7, sticky='w', pady=(0, 2))

        edit_panel = ttk.LabelFrame(content, text='  05  SUPPORTED EDITING COMMANDS  ', style='Section.TLabelframe', padding=6)
        edit_panel.grid(row=4, column=0, sticky='ew', pady=(5, 0))
        edit_panel.columnconfigure(0, weight=1)
        ttk.Label(edit_panel, text='Describe one settings change. Review the proposal, then choose Apply Changes.',
                  style='Muted.TLabel').grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 2))
        self.edit_text = tk.Text(edit_panel, height=1, wrap='word', bg='#0e171d', fg='#edf3f6',
                                 insertbackground='#edf3f6', selectbackground='#087f8c',
                                 relief='flat', highlightthickness=1, highlightbackground='#273740',
                                 highlightcolor='#19b8c6', font=('Segoe UI', 9))
        self.edit_text.grid(row=1, column=0, sticky='ew', padx=(0, 8))
        self.controls.append(self.edit_text)
        self.button(edit_panel, 'Review request', self.review_edit, 'Compact.TButton').grid(row=1, column=1, padx=(0, 6))
        self.button(edit_panel, 'Clear', self.clear_edit, 'Compact.TButton').grid(row=1, column=2, padx=(0, 6))
        self.edit_summary_label = ttk.Label(edit_panel, textvariable=self.edit_summary, style='Muted.TLabel', wraplength=850)
        self.edit_summary_label.grid(row=2, column=0, columnspan=2, sticky='w', pady=(2, 0))
        proposal_buttons = ttk.Frame(edit_panel, style='Card.TFrame')
        proposal_buttons.grid(row=2, column=2, columnspan=2, sticky='e', pady=(2, 0))
        self.apply_edit_button = self.button(proposal_buttons, 'Apply Changes', self.apply_edit, 'Accent.TButton')
        self.apply_edit_button.pack(side='left', padx=(0, 6))
        self.cancel_edit_button = self.button(proposal_buttons, 'Cancel proposal', self.cancel_edit, 'Compact.TButton')
        self.cancel_edit_button.pack(side='left')
        self.apply_edit_button.configure(state='disabled')

        footer = ttk.Frame(frame, style='App.TFrame')
        footer.grid(row=2, column=0, sticky='ew', pady=(5, 0))
        footer.columnconfigure(0, weight=1)
        actions = ttk.Frame(footer, style='App.TFrame')
        actions.grid(row=0, column=0, sticky='ew')
        self.start_button = self.button(actions, 'Render video', self.start, 'Accent.TButton')
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancel job', command=self.cancel, state='disabled', style='Danger.TButton')
        self.cancel_button.pack(side='left', padx=9)
        self.preview_button = self.button(actions, 'Preview Video · default player', self.preview_video)
        self.preview_button.pack(side='left', padx=(0, 9))
        self.preview_button.configure(state='disabled')
        self.open_button = ttk.Button(actions, text='Show verified output', command=self.open_output, state='disabled', style='Secondary.TButton')
        self.open_button.pack(side='left')
        progress_card = ttk.Frame(footer, style='Status.TFrame', padding=(7, 3))
        progress_card.grid(row=1, column=0, sticky='ew', pady=(5, 0))
        progress_card.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_card, maximum=100, mode='determinate', style='Accent.Horizontal.TProgressbar')
        self.progress.grid(row=0, column=0, sticky='ew')
        ttk.Label(progress_card, textvariable=self.progress_text, style='StatusMuted.TLabel').grid(row=1, column=0, sticky='w', pady=(2, 0))
        self.status_label = ttk.Label(progress_card, textvariable=self.status, style='StatusText.TLabel', wraplength=1000)
        self.status_label.grid(row=2, column=0, sticky='w', pady=(2, 0))
        self.output_path_label = ttk.Label(progress_card, textvariable=self.output_path_text,
                                           style='StatusMuted.TLabel', wraplength=1000)
        self.output_path_label.grid(row=3, column=0, sticky='w', pady=(1, 0))
        root.after(100, self.poll)

    @staticmethod
    def _usable_area(root) -> tuple[int, int, int, int]:
        """Return the work area (left, top, width, height), excluding the taskbar."""
        full_w, full_h = root.winfo_screenwidth(), root.winfo_screenheight()
        if os.name != 'nt':
            return 0, 0, full_w, full_h
        try:
            import ctypes
            import ctypes.wintypes as wtype
            rect = wtype.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, max(1, rect.right - rect.left), max(1, rect.bottom - rect.top)
        except OSError:
            pass
        return 0, 0, full_w, full_h

    def _style(self):
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        self.colors = {'background': '#0b1116', 'panel': '#121b22', 'panel_alt': '#17232b',
                       'border': '#273740', 'text': '#edf3f6', 'muted': '#9aabb4',
                       'accent': '#19b8c6', 'accent_hover': '#29cbd6', 'danger': '#753e45'}
        self.root.configure(background=self.colors['background'])
        style.configure('.', background=self.colors['background'], foreground=self.colors['text'],
                        font=('Segoe UI', 10), borderwidth=0, focuscolor=self.colors['accent'])
        style.configure('App.TFrame', background=self.colors['background'])
        style.configure('Card.TFrame', background=self.colors['panel'])
        style.configure('Status.TFrame', background=self.colors['panel_alt'])
        style.configure('TLabel', background=self.colors['background'], foreground=self.colors['text'])
        style.configure('Title.TLabel', background=self.colors['background'], foreground=self.colors['text'],
                        font=('Segoe UI Semibold', 25))
        style.configure('Eyebrow.TLabel', background=self.colors['background'], foreground=self.colors['accent'],
                        font=('Segoe UI', 9, 'bold'))
        style.configure('Muted.TLabel', background=self.colors['panel'], foreground=self.colors['muted'],
                        font=('Segoe UI', 9))
        style.configure('EmptyState.TLabel', background='#111a20', foreground='#72858e',
                        font=('Segoe UI', 10))
        style.configure('Prompt.TLabelframe', background=self.colors['panel'], bordercolor=self.colors['border'],
                        relief='solid', borderwidth=1)
        style.configure('Field.TLabel', background=self.colors['panel'], foreground=self.colors['text'],
                        font=('Segoe UI', 9, 'bold'))
        style.configure('StatusText.TLabel', background=self.colors['panel_alt'], foreground=self.colors['text'],
                        font=('Segoe UI', 9))
        style.configure('StatusMuted.TLabel', background=self.colors['panel_alt'], foreground=self.colors['muted'],
                        font=('Segoe UI', 9))
        style.configure('Section.TLabelframe', background=self.colors['panel'], bordercolor=self.colors['border'],
                        relief='solid', borderwidth=1)
        style.configure('Section.TLabelframe.Label', background=self.colors['panel'], foreground=self.colors['accent'],
                        font=('Segoe UI', 9, 'bold'))
        style.configure('TEntry', fieldbackground='#0e171d', foreground=self.colors['text'],
                        insertcolor=self.colors['text'], bordercolor=self.colors['border'],
                        lightcolor=self.colors['border'], darkcolor=self.colors['border'], padding=(7, 4))
        style.map('TEntry', bordercolor=[('focus', self.colors['accent'])])
        style.configure('TRadiobutton', background=self.colors['panel'], foreground=self.colors['text'],
                        indicatorcolor='#0e171d', padding=3)
        style.map('TRadiobutton', background=[('active', self.colors['panel'])],
                  foreground=[('active', self.colors['accent'])],
                  indicatorcolor=[('selected', self.colors['accent']), ('!selected', '#0e171d')])
        style.configure('TButton', background='#24343d', foreground=self.colors['text'],
                        padding=(11, 7), font=('Segoe UI', 9, 'bold'), bordercolor=self.colors['border'])
        style.map('TButton', background=[('disabled', '#19242a'), ('pressed', '#1c5961'), ('active', '#304650')],
                  foreground=[('disabled', '#71818a'), ('active', '#ffffff')])
        style.configure('Secondary.TButton', background='#24343d', foreground=self.colors['text'],
                        padding=(12, 8), font=('Segoe UI', 9, 'bold'))
        style.configure('Accent.TButton', background=self.colors['accent'], foreground='#061417',
                        padding=(19, 10), font=('Segoe UI', 10, 'bold'), bordercolor=self.colors['accent'])
        style.map('Accent.TButton', background=[('disabled', '#35545a'), ('pressed', '#07818b'), ('active', self.colors['accent_hover'])],
                  foreground=[('disabled', '#91a4a8')])
        style.configure('Danger.TButton', background=self.colors['danger'], foreground='#fff0f0',
                        padding=(12, 8), font=('Segoe UI', 9, 'bold'))
        style.configure('Compact.TButton', background='#24343d', foreground=self.colors['text'],
                        padding=(7, 3), font=('Segoe UI', 9, 'bold'), bordercolor=self.colors['border'])
        style.map('Compact.TButton', background=[('disabled', '#19242a'), ('pressed', '#1c5961'), ('active', '#304650')],
                  foreground=[('disabled', '#71818a'), ('active', '#ffffff')])
        style.configure('Compact.TEntry', fieldbackground='#0e171d', foreground=self.colors['text'],
                        insertcolor=self.colors['text'], bordercolor=self.colors['border'],
                        lightcolor=self.colors['border'], darkcolor=self.colors['border'], padding=(5, 3))
        style.map('Compact.TEntry', bordercolor=[('focus', self.colors['accent'])])
        style.configure('Accent.Horizontal.TProgressbar', troughcolor='#0d171c', background=self.colors['accent'],
                        bordercolor='#0d171c', lightcolor=self.colors['accent'], darkcolor=self.colors['accent'])
        style.configure('Vertical.TScrollbar', background='#24343d', troughcolor=self.colors['panel'],
                        bordercolor=self.colors['panel'], arrowcolor=self.colors['muted'])
        style.configure('Horizontal.TScrollbar', background='#24343d', troughcolor=self.colors['panel'],
                        bordercolor=self.colors['panel'], arrowcolor=self.colors['muted'])

    def button(self, parent, text, command, style='Secondary.TButton'):
        widget = ttk.Button(parent, text=text, command=command, style=style)
        self.controls.append(widget)
        return widget

    def entry(self, parent, variable, width=None):
        widget = ttk.Entry(parent, textvariable=variable, style='Compact.TEntry',
                           **({'width': width} if width else {}))
        self.controls.append(widget)
        return widget

    def file_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label, style='Field.TLabel').grid(row=row, column=0, sticky='w', pady=1)
        self.entry(parent, variable).grid(row=row, column=1, sticky='ew', padx=8, pady=1)
        self.button(parent, 'Browse', command, 'Compact.TButton').grid(row=row, column=2, padx=(2, 0))

    def refresh(self, selected=None):
        self.listbox.delete(0, 'end')
        if self.items:
            self.empty_sequence.place_forget()
        else:
            self.empty_sequence.place(relx=0.5, rely=0.5, anchor='center')
        for n, item in enumerate(self.items, 1):
            self.listbox.insert('end', f'{n:02d}  [{item.kind.upper()}]  {item.path}')
        if selected is not None and self.items:
            self.listbox.selection_set(selected)
            self.listbox.see(selected)

    def add(self, kind):
        filters = [('Videos', '*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.ts *.mts *.m2ts *.wmv *.mpg *.mpeg')] if kind == 'video' else [('Static images', '*.png *.jpg *.jpeg *.bmp *.webp')]
        paths = filedialog.askopenfilenames(title=f'Select {kind} files', filetypes=filters + [('All files', '*.*')])
        self.items.extend(MediaItem(Path(p), kind) for p in paths)
        self.refresh(len(self.items)-1 if self.items else None)

    def move(self, delta):
        selected = self.listbox.curselection()
        if selected:
            old = selected[0]
            new = old + delta
            if 0 <= new < len(self.items):
                self.items[old], self.items[new] = self.items[new], self.items[old]
                self.refresh(new)

    def remove(self):
        selected = self.listbox.curselection()
        if selected:
            self.items.pop(selected[0])
            self.refresh(min(selected[0], len(self.items)-1) if self.items else None)

    @staticmethod
    def audio(variable):
        name = filedialog.askopenfilename(title='Select audio', filetypes=[('Audio', '*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4'), ('All files', '*.*')])
        if name:
            variable.set(name)

    def choose_folder(self):
        name = filedialog.askdirectory(title='Existing destination folder')
        if name:
            self.folder.set(name)

    def choose_tools(self):
        name = filedialog.askdirectory(title='Folder containing BOTH ffmpeg.exe and ffprobe.exe')
        if name:
            self.tools_dir.set(name)

    def busy(self, value):
        for control in self.controls:
            control.configure(state='disabled' if value else 'normal')
        self.cancel_button.configure(state='normal' if value else 'disabled')
        self.open_button.configure(state='normal' if self.result and not value else 'disabled')
        self.preview_button.configure(state='normal' if self.result and not value and not self.previewing else 'disabled')
        self.start_button.configure(state='disabled' if value or self.previewing else 'normal')
        self.apply_edit_button.configure(state='normal' if self.pending_edit and not value else 'disabled')

    def _next_render_path(self) -> Path:
        current = output_path(self.folder.get(), self.filename.get())
        return next_version_path(current, self.result)

    def review_edit(self):
        try:
            proposal = parse_edit_request(
                self.edit_text.get('1.0', 'end-1c'),
                aspect=self.aspect.get(),
                image_seconds=float(self.seconds.get()),
                music_volume=float(self.volume.get()),
                has_music=bool(self.music.get().strip()),
            )
            next_path = self._next_render_path()
        except EditRequestError as exc:
            self.pending_edit = None
            self.pending_filename = None
            self.apply_edit_button.configure(state='disabled')
            self.edit_summary.set(str(exc))
            self.edit_summary_label.configure(foreground='#ff8585')
            return
        except (ValueError, RenderError) as exc:
            self.pending_edit = None
            self.pending_filename = None
            self.apply_edit_button.configure(state='disabled')
            message = str(exc) if isinstance(exc, RenderError) else 'Check the current image duration and music gain values first.'
            self.edit_summary.set(message)
            self.edit_summary_label.configure(foreground='#ff8585')
            return
        self.pending_edit = proposal
        self.pending_filename = next_path.name
        self.edit_summary.set(f'Proposed: {proposal.summary} Next render filename: {next_path.name}. Applying settings will not start rendering.')
        self.edit_summary_label.configure(foreground=self.colors['accent'])
        self.apply_edit_button.configure(state='normal')

    def apply_edit(self):
        proposal = self.pending_edit
        if proposal is None or self.pending_filename is None:
            return
        if proposal.aspect:
            self.aspect.set(proposal.aspect)
        if proposal.image_seconds is not None:
            self.seconds.set(f'{proposal.image_seconds:g}')
        if proposal.music_volume is not None:
            self.volume.set(f'{proposal.music_volume:g}')
        if proposal.remove_music:
            self.music.set('')
        self.filename.set(self.pending_filename)
        self.pending_edit = None
        self.pending_filename = None
        self.edit_summary.set(f'Settings applied. Review the controls, then choose Render video to create {self.filename.get()}.')
        self.edit_summary_label.configure(foreground='#59d8a2')
        self.apply_edit_button.configure(state='disabled')
        self.status.set('Approved settings are ready. Rendering starts only when you choose Render video.')
        self.status_label.configure(foreground=self.colors['accent'])

    def cancel_edit(self):
        self.pending_edit = None
        self.pending_filename = None
        self.edit_summary.set('Proposal cancelled. Current settings are unchanged.')
        self.edit_summary_label.configure(foreground=self.colors['muted'])
        self.apply_edit_button.configure(state='disabled')

    def clear_edit(self):
        self.edit_text.delete('1.0', 'end')
        self.cancel_edit()
        self.edit_summary.set('Supported settings only; this does not edit video pixels or speech.')

    def preview_video(self):
        if self.previewing:
            return
        try:
            path = existing_mp4_path(self.result)
        except PreviewPathError as exc:
            messagebox.showerror('Preview unavailable', str(exc))
            return
        if self.verified_tools is None:
            messagebox.showerror('Preview unavailable', 'The FFprobe tools for this verified render are unavailable. Render the video again to enable preview.')
            return
        self.previewing = True
        self.busy(False)
        self.start_button.configure(state='disabled')
        self.preview_button.configure(state='disabled')
        self.status.set('Checking the verified MP4, then opening it in the system default video player…')
        self.status_label.configure(foreground=self.colors['accent'])
        self.preview_worker = threading.Thread(target=self.run_preview, args=(path, self.verified_tools), daemon=False)
        self.preview_worker.start()

    def run_preview(self, path: Path, tools):
        try:
            path = existing_mp4_path(path)
            info = Renderer(tools).probe(path)
            if not info.has_video or info.duration <= 0:
                raise RenderError('FFprobe could not confirm a video stream and positive duration for this output.')
            if os.name == 'nt':
                getattr(os, 'startfile')(str(path))
            else:
                subprocess.Popen(['xdg-open', str(path)])
            self.events.put({'preview_status': 'opened', 'path': str(path), 'duration': info.duration})
        except (PreviewPathError, RenderError) as exc:
            self.events.put({'preview_status': 'failed', 'error': str(exc)})
        except OSError:
            self.events.put({'preview_status': 'failed', 'error': 'The system could not open this MP4. Check that a default video player is installed and associated with .mp4 files.'})

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if self.previewing:
            self.status.set('Wait for the system player to open before starting another render.')
            return
        try:
            if not self.folder.get().strip():
                raise RenderError('Choose an existing output folder.')
            # One transition per visual gap (N-1 transitions for N items).
            transition_dur = float(self.transition_duration.get())
            transitions = tuple(
                Transition(self.transition_type.get(), transition_dur)
                for _ in range(max(0, len(self.items) - 1))
            )
            overlays = ()
            if self.overlay_enabled.get():
                overlays = (
                    Overlay(
                        content=self.overlay_text.get().strip(),
                        start=float(self.overlay_start.get()),
                        end=float(self.overlay_end.get()),
                        size=int(self.overlay_font_size.get()),
                        color=self.overlay_color.get(),
                        position=self.overlay_position.get(),
                        background=bool(self.overlay_background.get()),
                    ),
                )
            options = RenderOptions(
                tuple(self.items),
                Path(self.voice.get()).expanduser().resolve() if self.voice.get().strip() else None,
                output_path(self.folder.get(), self.filename.get()),
                Path(self.music.get()).expanduser().resolve() if self.music.get().strip() else None,
                self.aspect.get(), float(self.seconds.get()), float(self.volume.get()) / 100,
                transitions=transitions,
                overlays=overlays,
                ducking=bool(self.ducking_enabled.get()),
                ducking_strength=float(self.ducking_strength.get()),
                ducking_speech_threshold=float(self.ducking_threshold.get()),
            )
        except (ValueError, RenderError) as exc:
            messagebox.showerror('Check settings', str(exc) if isinstance(exc, RenderError) else 'Image duration, music gain, transition duration, overlay times/size, and ducking values must be numbers.')
            return
        # Snapshot all Tk variables here; the worker never reads any Tk state.
        tool_folder = self.tools_dir.get().strip()
        self.cancel_requested.clear()
        self.progress['value'] = 0
        self.busy(True)
        self.status.set('Running — validating inputs and FFmpeg installation…')
        self.status_label.configure(foreground=self.colors['accent'])
        self.worker = threading.Thread(target=self.run_job, args=(options, tool_folder), daemon=False)
        self.worker.start()

    def run_job(self, options, tool_folder):
        try:
            tools = find_tools(tool_folder or None)
            renderer = Renderer(tools, self.events.put, self.logger)
            self.renderer = renderer
            if self.cancel_requested.is_set():
                renderer.cancel()
            result = renderer.render(options)
            self.events.put({'terminal': 'completed', 'path': str(result), 'tools': renderer.tools})
        except Cancelled as exc:
            self.events.put({'terminal': 'cancelled', 'error': str(exc)})
        except RenderError as exc:
            self.events.put({'terminal': 'failed', 'error': str(exc)})
        except Exception as exc:
            self.logger.error('worker exception_type=%s', type(exc).__name__)
            self.events.put({'terminal': 'failed', 'error': 'Unexpected worker error. Restart and consult the diagnostic log.'})

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                preview_status = event.get('preview_status')
                if preview_status:
                    self.previewing = False
                    if preview_status == 'opened':
                        duration = event['duration']
                        self.status.set(f'Opened verified output in the system default player ({duration:.1f} seconds). Playback controls are provided by that player.')
                        self.status_label.configure(foreground='#59d8a2')
                    else:
                        self.status.set(f'Preview failed: {event["error"]}')
                        self.status_label.configure(foreground='#ff8585')
                        if not self.closing:
                            messagebox.showerror('Preview failed', event['error'])
                    self.preview_button.configure(state='normal' if self.result and not (self.worker and self.worker.is_alive()) else 'disabled')
                    self.start_button.configure(state='disabled' if self.worker and self.worker.is_alive() else 'normal')
                    continue
                terminal = event.get('terminal')
                if terminal:
                    self.progress.stop()
                    self.progress.configure(mode='determinate')
                    self.busy(False)
                    self.progress['value'] = 100 if terminal == 'completed' else 0
                    self.progress_text.set(terminal.capitalize())
                    if terminal == 'completed':
                        self.result = Path(event['path'])
                        self.verified_tools = event['tools']
                        self.output_path_text.set(f'Verified output: {self.result}')
                        self.open_button.configure(state='normal')
                        self.preview_button.configure(state='normal')
                        self.status.set(f'Completed and verified: {self.result}')
                        self.status_label.configure(foreground='#59d8a2')
                        if not self.closing:
                            messagebox.showinfo('Video verified', f'Your MP4 passed structure and full-decode verification.\n\n{self.result}')
                    else:
                        self.status.set(f'{terminal.capitalize()}: {event["error"]}')
                        self.status_label.configure(foreground='#ff8585' if terminal == 'failed' else self.colors['muted'])
                        if terminal == 'failed' and not self.closing:
                            messagebox.showerror('Render failed', event['error'])
                else:
                    self.status.set(event['message'])
                    self.status_label.configure(foreground=self.colors['accent'])
                    value = event.get('progress')
                    if value is None:
                        if str(self.progress['mode']) != 'indeterminate':
                            self.progress.configure(mode='indeterminate')
                            self.progress.start(15)
                        self.progress_text.set('Progress unavailable for this operation')
                    else:
                        self.progress.stop()
                        self.progress.configure(mode='determinate')
                        self.progress['value'] = value
                        self.progress_text.set(f'Current operation: {value:.1f}% (FFmpeg media time, not overall job time)')
        except queue.Empty:
            pass
        if self.closing and (not self.worker or not self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(100, self.poll)

    def cancel(self):
        self.cancel_requested.set()
        if self.renderer:
            self.renderer.cancel()
        self.cancel_button.configure(state='disabled')
        self.status.set('Cancellation requested — stopping FFmpeg and removing temporary files…')
        self.status_label.configure(foreground=self.colors['muted'])

    def close(self):
        if self.worker and self.worker.is_alive():
            if messagebox.askyesno('Job running', 'Cancel the current job and close after cleanup?'):
                self.closing = True
                self.cancel()
        else:
            self.root.destroy()

    def open_output(self):
        if not self.result or not self.result.is_file():
            messagebox.showerror('Output not found', 'The verified file was moved or deleted. Render again or locate it manually.')
            return
        try:
            if os.name == 'nt':
                subprocess.Popen(['explorer.exe', '/select,', str(self.result)])
            else:
                subprocess.Popen(['xdg-open', str(self.result.parent)])
        except OSError:
            messagebox.showerror('Cannot open folder', f'Open this folder manually:\n{self.result.parent}\n\nFile: {self.result.name}')

    def callback_error(self, exc_type, exc, traceback):
        self.logger.error('GUI exception_type=%s', exc_type.__name__)
        messagebox.showerror('Application error', 'An unexpected interface error occurred. Restart the application. The error type was logged; media contents were not logged.')


def launch():
    root = tk.Tk()
    Studio(root)
    root.mainloop()
