"""
Color Match Auto-Clicker
========================
Watches two screen pixels and clicks the mouse when their colors match.

Requirements:
    pip install pyautogui pillow keyboard

Usage:
    python color_clicker.py
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import math

try:
    import pyautogui
    import keyboard
    from PIL import ImageGrab
except ImportError:
    import subprocess, sys
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyautogui", "pillow", "keyboard"])
    import pyautogui
    import keyboard
    from PIL import ImageGrab

pyautogui.FAILSAFE = True  # Move mouse to top-left corner to abort

# ── Helpers ────────────────────────────────────────────────────────────────────

def get_pixel_color(x, y):
    """Return (r, g, b) of a single pixel."""
    img = ImageGrab.grab(bbox=(x, y, x + 1, y + 1))
    return img.getpixel((0, 0))[:3]

def color_distance(c1, c2):
    """Euclidean distance between two RGB colours."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)

def find_color_in_row(y, target_color, color_tolerance, x_start, x_end):
    """Scan a horizontal region at row y to find the X position of target_color.
    Returns the absolute X coordinate of the closest match, or None if no match within tolerance."""
    img = ImageGrab.grab(bbox=(x_start, y, x_end, y + 1))
    best_x, best_dist = None, float("inf")
    for x, c in enumerate(img.getdata()):
        d = color_distance(c[:3], target_color)
        if d < best_dist:
            best_dist = d
            best_x = x
    return (x_start + best_x) if best_dist <= color_tolerance else None

def find_color_zone_in_row(y, target_color, color_tolerance, x_start, x_end):
    """Find the leftmost and rightmost X of all pixels matching target_color within tolerance.
    Returns (x_left, x_right) or (None, None) if no pixels match."""
    img = ImageGrab.grab(bbox=(x_start, y, x_end, y + 1))
    x_left = x_right = None
    for x, c in enumerate(img.getdata()):
        if color_distance(c[:3], target_color) <= color_tolerance:
            if x_left is None:
                x_left = x
            x_right = x
    if x_left is None:
        return None, None
    return x_start + x_left, x_start + x_right

# ── Main App ───────────────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Color Match Auto-Clicker")
        self.resizable(False, False)
        self.configure(bg="#1a1a2e")

        # State
        self.pixels = [None, None]       # (x, y) for each watch point
        self.pixel_colors = [None, None] # target RGB color for each watch point
        self.scan_area = None            # (x_start, x_end) scan bounds, None = full width
        self.click_pos = None             # where to click (defaults to pixel 1)
        self.running = False
        self.monitor_thread = None
        self.click_count = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = 16
        BG   = "#1a1a2e"
        CARD = "#16213e"
        ACC  = "#0f3460"
        HOT  = "#e94560"
        FG   = "#eaeaea"
        MONO = ("Consolas", 10)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TScale", background=CARD, troughcolor=ACC, sliderlength=18)
        style.configure("TButton", background=ACC, foreground=FG, borderwidth=0,
                        focusthickness=0, font=("Segoe UI", 10, "bold"), padding=6)
        style.map("TButton", background=[("active", HOT)])

        header = tk.Label(self, text="🎯  Color Match Auto-Clicker",
                          font=("Segoe UI", 14, "bold"), bg=BG, fg=HOT)
        header.pack(pady=(PAD, 4), padx=PAD)

        sub = tk.Label(self, text="Click fires when Pixel A and Pixel B are vertically aligned",
                       font=("Segoe UI", 9), bg=BG, fg="#888")
        sub.pack(padx=PAD, pady=(0, PAD))

        # ── Pixel cards ──────────────────────────────────────────────────────
        self.pixel_frames = []
        self.coord_labels = []
        self.color_swatches = []
        self.hex_labels = []

        pixels_row = tk.Frame(self, bg=BG)
        pixels_row.pack(padx=PAD, fill="x")

        for i, label in enumerate(("Pixel A", "Pixel B")):
            card = tk.Frame(pixels_row, bg=CARD, padx=12, pady=10,
                            highlightbackground=ACC, highlightthickness=1)
            card.pack(side="left", expand=True, fill="both",
                      padx=(0 if i == 0 else 8, 0))
            self.pixel_frames.append(card)

            tk.Label(card, text=label, font=("Segoe UI", 10, "bold"),
                     bg=CARD, fg=FG).pack(anchor="w")

            coord = tk.Label(card, text="Not set", font=MONO,
                             bg=CARD, fg="#aaa")
            coord.pack(anchor="w", pady=(2, 6))
            self.coord_labels.append(coord)

            swatch_row = tk.Frame(card, bg=CARD)
            swatch_row.pack(anchor="w")

            swatch = tk.Label(swatch_row, width=4, height=1, bg="#333",
                              relief="flat", borderwidth=2)
            swatch.pack(side="left")
            self.color_swatches.append(swatch)

            hex_lbl = tk.Label(swatch_row, text="  #------", font=MONO,
                               bg=CARD, fg="#888")
            hex_lbl.pack(side="left", padx=4)
            self.hex_labels.append(hex_lbl)

            btn = ttk.Button(card, text=f"Pick Pixel {chr(65+i)}",
                             command=lambda idx=i: self._schedule_pick(idx))
            btn.pack(pady=(8, 0), fill="x")

        # ── Scan area ─────────────────────────────────────────────────────────
        scan_frame = tk.Frame(self, bg=CARD, padx=12, pady=10,
                              highlightbackground=ACC, highlightthickness=1)
        scan_frame.pack(padx=PAD, pady=(PAD, 0), fill="x")

        tk.Label(scan_frame, text="Scan Area  (defaults to full screen width)",
                 font=("Segoe UI", 9, "bold"), bg=CARD, fg="#aaa").pack(anchor="w")

        self.scan_area_lbl = tk.Label(scan_frame, text="Full screen width",
                                      font=MONO, bg=CARD, fg="#888")
        self.scan_area_lbl.pack(anchor="w", pady=(2, 6))

        scan_btn_row = tk.Frame(scan_frame, bg=CARD)
        scan_btn_row.pack(fill="x")
        ttk.Button(scan_btn_row, text="Draw Scan Area",
                   command=self._schedule_area_pick).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(scan_btn_row, text="Clear",
                   command=self._clear_scan_area).pack(side="left", expand=True, fill="x", padx=(4, 0))

        # ── Click position ────────────────────────────────────────────────────
        click_frame = tk.Frame(self, bg=CARD, padx=12, pady=10,
                               highlightbackground=ACC, highlightthickness=1)
        click_frame.pack(padx=PAD, pady=PAD, fill="x")

        tk.Label(click_frame, text="Click Position  (defaults to Pixel A location)",
                 font=("Segoe UI", 9, "bold"), bg=CARD, fg="#aaa").pack(anchor="w")

        self.click_coord_lbl = tk.Label(click_frame, text="Same as Pixel A",
                                        font=MONO, bg=CARD, fg="#888")
        self.click_coord_lbl.pack(anchor="w", pady=(2, 6))

        ttk.Button(click_frame, text="Pick Click Position",
                   command=lambda: self._schedule_pick("click")).pack(fill="x")

        # ── Tolerance ─────────────────────────────────────────────────────────
        tol_frame = tk.Frame(self, bg=BG)
        tol_frame.pack(padx=PAD, fill="x")

        tk.Label(tol_frame, text="Color Tolerance:", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side="left")

        self.tol_var = tk.IntVar(value=10)
        self.tol_label = tk.Label(tol_frame, text="10", width=3,
                                  font=("Segoe UI", 9, "bold"), bg=BG, fg=HOT)
        self.tol_label.pack(side="right")

        tol_slider = ttk.Scale(tol_frame, from_=0, to=100,
                               variable=self.tol_var, orient="horizontal",
                               command=lambda v: self.tol_label.config(
                                   text=str(int(float(v)))))
        tol_slider.pack(side="left", expand=True, fill="x", padx=8)

        # ── Position Tolerance ────────────────────────────────────────────────
        pos_frame = tk.Frame(self, bg=BG)
        pos_frame.pack(padx=PAD, pady=(8, 0), fill="x")

        tk.Label(pos_frame, text="Position Tolerance (px):", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side="left")

        self.pos_tol_var = tk.IntVar(value=5)
        self.pos_tol_label = tk.Label(pos_frame, text="5", width=3,
                                      font=("Segoe UI", 9, "bold"), bg=BG, fg=HOT)
        self.pos_tol_label.pack(side="right")

        pos_slider = ttk.Scale(pos_frame, from_=0, to=50,
                               variable=self.pos_tol_var, orient="horizontal",
                               command=lambda v: self.pos_tol_label.config(
                                   text=str(int(float(v)))))
        pos_slider.pack(side="left", expand=True, fill="x", padx=8)

        # ── Trigger mode ──────────────────────────────────────────────────────
        mode_frame = tk.Frame(self, bg=BG)
        mode_frame.pack(padx=PAD, pady=(8, 0), fill="x")

        tk.Label(mode_frame, text="Trigger Mode:", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side="left")

        self.zone_mode_var = tk.BooleanVar(value=False)
        tk.Checkbutton(mode_frame,
                       text="Zone  (click while A is at or past B — use for red/green bar)",
                       variable=self.zone_mode_var,
                       bg=BG, fg=FG, selectcolor=ACC, activebackground=BG,
                       font=("Segoe UI", 9)).pack(side="left", padx=8)

        edge_frame = tk.Frame(self, bg=BG)
        edge_frame.pack(padx=PAD, pady=(2, 0), fill="x")
        tk.Label(edge_frame, text="         ", bg=BG).pack(side="left")
        self.edge_trigger_var = tk.BooleanVar(value=True)
        tk.Checkbutton(edge_frame,
                       text="Edge trigger  (fire once per zone entry, re-arms on exit — best for bouncing elements)",
                       variable=self.edge_trigger_var,
                       bg=BG, fg="#aaa", selectcolor=ACC, activebackground=BG,
                       font=("Segoe UI", 9)).pack(side="left")

        # ── Zone Inset ────────────────────────────────────────────────────────
        inset_frame = tk.Frame(self, bg=BG)
        inset_frame.pack(padx=PAD, pady=(4, 0), fill="x")
        tk.Label(inset_frame, text="         ", bg=BG).pack(side="left")
        tk.Label(inset_frame, text="Zone Inset (px):", font=("Segoe UI", 9),
                 bg=BG, fg="#aaa").pack(side="left")

        self.zone_inset_var = tk.IntVar(value=5)
        self.zone_inset_label = tk.Label(inset_frame, text="5", width=3,
                                         font=("Segoe UI", 9, "bold"), bg=BG, fg=HOT)
        self.zone_inset_label.pack(side="right")

        zone_inset_slider = ttk.Scale(inset_frame, from_=0, to=30,
                                      variable=self.zone_inset_var, orient="horizontal",
                                      command=lambda v: self.zone_inset_label.config(
                                          text=str(int(float(v)))))
        zone_inset_slider.pack(side="left", expand=True, fill="x", padx=8)

        # ── Click type ────────────────────────────────────────────────────────
        type_frame = tk.Frame(self, bg=BG)
        type_frame.pack(padx=PAD, pady=(PAD, 4), fill="x")

        tk.Label(type_frame, text="Click Type:", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side="left")

        self.click_type = tk.StringVar(value="left")
        for val, txt in (("left", "Left"), ("right", "Right"), ("double", "Double")):
            tk.Radiobutton(type_frame, text=txt, variable=self.click_type, value=val,
                           bg=BG, fg=FG, selectcolor=ACC, activebackground=BG,
                           font=("Segoe UI", 9)).pack(side="left", padx=8)

        # ── Cooldown ──────────────────────────────────────────────────────────
        cool_frame = tk.Frame(self, bg=BG)
        cool_frame.pack(padx=PAD, pady=(0, PAD), fill="x")

        tk.Label(cool_frame, text="Cooldown (s):", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side="left")

        self.cooldown_var = tk.DoubleVar(value=0.5)
        self.cool_label = tk.Label(cool_frame, text="0.50", width=4,
                                   font=("Segoe UI", 9, "bold"), bg=BG, fg=HOT)
        self.cool_label.pack(side="right")

        cool_slider = ttk.Scale(cool_frame, from_=0.0, to=5.0,
                                variable=self.cooldown_var, orient="horizontal",
                                command=lambda v: self.cool_label.config(
                                    text=f"{float(v):.2f}"))
        cool_slider.pack(side="left", expand=True, fill="x", padx=8)

        # ── Start / Stop ──────────────────────────────────────────────────────
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(padx=PAD, pady=(0, 8), fill="x")

        self.start_btn = ttk.Button(btn_row, text="▶  Start",
                                    command=self._toggle)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        ttk.Button(btn_row, text="Reset", command=self._reset).pack(
            side="left", expand=True, fill="x", padx=(4, 0))

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Set both pixels to begin.")
        status = tk.Label(self, textvariable=self.status_var,
                          font=("Segoe UI", 9), bg=ACC, fg=FG,
                          anchor="w", padx=8, pady=4)
        status.pack(fill="x", side="bottom")

        # Hotkey hint
        tk.Label(self, text="Press  F6  to toggle anywhere  •  Move mouse to top-left to abort",
                 font=("Segoe UI", 8), bg=BG, fg="#555").pack(
            side="bottom", pady=(0, 2))

        keyboard.add_hotkey("F6", self._toggle)

    # ── Pixel picking ──────────────────────────────────────────────────────────

    def _schedule_pick(self, idx):
        label = "Click Position" if idx == "click" else f"Pixel {chr(65 + idx)}"
        self.status_var.set(f"Click to select {label}…")
        overlay = tk.Toplevel(self)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-alpha", 0.01)
        overlay.attributes("-topmost", True)
        overlay.config(cursor="crosshair")

        def on_click(event):
            x, y = event.x_root, event.y_root
            overlay.destroy()
            self.after(50, lambda: self._capture_pos(idx, x, y))

        overlay.bind("<Button-1>", on_click)

    def _capture_pos(self, idx, x, y):
        color = get_pixel_color(x, y)
        hex_c = rgb_to_hex(color)

        if idx == "click":
            self.click_pos = (x, y)
            self.click_coord_lbl.config(text=f"({x}, {y})", fg="#eaeaea")
            self.status_var.set(f"Click position set → ({x}, {y})")
        else:
            self.pixels[idx] = (x, y)
            self.pixel_colors[idx] = color
            self.coord_labels[idx].config(text=f"({x}, {y})", fg="#eaeaea")
            self.color_swatches[idx].config(bg=hex_c)
            self.hex_labels[idx].config(text=f"  {hex_c}", fg="#eaeaea")
            self.status_var.set(f"Pixel {chr(65+idx)} set → ({x}, {y})  color {hex_c}")

    def _schedule_area_pick(self):
        self.status_var.set("Click and drag to define the scan area…")
        overlay = tk.Toplevel(self)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.25)
        overlay.config(cursor="crosshair", bg="#1a1a2e")

        canvas = tk.Canvas(overlay, bg="#1a1a2e", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        canvas.create_text(
            overlay.winfo_screenwidth() // 2, 40,
            text="Click and drag to define scan area  •  Esc to cancel",
            fill="#e94560", font=("Segoe UI", 12, "bold"),
        )

        start = {}
        rect_id = [None]

        def on_press(event):
            start["x"], start["y"] = event.x, event.y

        def on_drag(event):
            if rect_id[0]:
                canvas.delete(rect_id[0])
            rect_id[0] = canvas.create_rectangle(
                start["x"], start["y"], event.x, event.y,
                outline="#e94560", width=2,
            )

        def on_release(event):
            x1 = min(start["x"], event.x)
            x2 = max(start["x"], event.x)
            overlay.destroy()
            if x2 - x1 > 4:  # ignore accidental tiny drags
                self._set_scan_area(x1, x2)
            else:
                self.status_var.set("Scan area selection cancelled (too small).")

        overlay.bind("<Escape>", lambda _: overlay.destroy())
        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

    def _set_scan_area(self, x1, x2):
        self.scan_area = (x1, x2)
        self.scan_area_lbl.config(
            text=f"x: {x1}–{x2}  ({x2 - x1}px wide)", fg="#eaeaea")
        self.status_var.set(f"Scan area set → x:{x1}–{x2}  ({x2 - x1}px wide)")

    def _clear_scan_area(self):
        self.scan_area = None
        self.scan_area_lbl.config(text="Full screen width", fg="#888")
        self.status_var.set("Scan area cleared — using full screen width.")

    # ── Monitoring loop ────────────────────────────────────────────────────────

    def _toggle(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if None in self.pixels:
            messagebox.showwarning("Not ready", "Please set both Pixel A and Pixel B first.")
            return
        self.running = True
        self.click_count = 0
        self.start_btn.config(text="■  Stop")
        self.status_var.set("Monitoring…  (F6 or Stop button to halt)")
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _stop(self):
        self.running = False
        self.start_btn.config(text="▶  Start")
        self.status_var.set(f"Stopped.  Total clicks: {self.click_count}")

    def _monitor_loop(self):
        ay = self.pixels[0][1]
        by = self.pixels[1][1]
        color_a = self.pixel_colors[0]
        color_b = self.pixel_colors[1]
        cx, cy = self.click_pos if self.click_pos else self.pixels[0]
        screen_width = pyautogui.size().width  # cache once

        last_click = 0
        last_x_a = None     # last known leaf X for local search
        SEARCH_RADIUS = 120  # px window around last position before full scan

        was_in_zone = False  # edge-trigger state: True while leaf is in zone

        while self.running:
            try:
                tolerance  = self.tol_var.get()
                cooldown   = self.cooldown_var.get()
                click_type = self.click_type.get()
                pos_tol    = self.pos_tol_var.get()
                zone_mode  = self.zone_mode_var.get()
                inset      = self.zone_inset_var.get()

                x_start, x_end = self.scan_area if self.scan_area else (0, screen_width)

                # Local search first, fall back to full scan
                if last_x_a is not None:
                    ls = max(x_start, last_x_a - SEARCH_RADIUS)
                    le = min(x_end,   last_x_a + SEARCH_RADIUS)
                    x_a = find_color_in_row(ay, color_a, tolerance, ls, le)
                    if x_a is None:
                        x_a = find_color_in_row(ay, color_a, tolerance, x_start, x_end)
                else:
                    x_a = find_color_in_row(ay, color_a, tolerance, x_start, x_end)
                if x_a is not None:
                    last_x_a = x_a

                now = time.time()

                if zone_mode:
                    # Always scan fresh — no caching, accuracy over speed
                    x_b_left, x_b_right = find_color_zone_in_row(by, color_b, tolerance, x_start, x_end)

                    if x_a is None or x_b_left is None:
                        missing = ("Pixel A: not found  " if x_a is None else "") + \
                                  ("Pixel B zone: not found" if x_b_left is None else "")
                        self.after(0, lambda m=missing: self.status_var.set(f"Monitoring…  {m}"))
                        should_click = False
                    else:
                        # Apply inset: leaf must be comfortably inside zone boundaries
                        eff_left  = x_b_left  + inset
                        eff_right = x_b_right - inset
                        in_zone = eff_left <= x_a <= eff_right
                        self.after(0, lambda xa=x_a, bl=eff_left, br=eff_right, z=in_zone: self.status_var.set(
                            f"Monitoring…  A@x={xa}  zone=[{bl}–{br}]  {'✓ IN ZONE' if z else '✗ out'}"))
                        should_click = in_zone
                else:
                    x_b = find_color_in_row(by, color_b, tolerance, x_start, x_end)
                    if x_a is None or x_b is None:
                        missing = "  ".join(
                            f"Pixel {chr(65+i)}: not found"
                            for i, x in enumerate((x_a, x_b)) if x is None
                        )
                        self.after(0, lambda m=missing: self.status_var.set(f"Monitoring…  {m}"))
                        should_click = False
                    else:
                        diff = abs(x_a - x_b)
                        self.after(0, lambda xa=x_a, xb=x_b, d=diff: self.status_var.set(
                            f"Monitoring…  A@x={xa}  B@x={xb}  diff={d}px"))
                        should_click = diff <= pos_tol

                # Determine whether to fire: edge trigger (zone mode only) or time-based cooldown
                edge_trigger = zone_mode and self.edge_trigger_var.get()
                if edge_trigger:
                    if x_a is not None and x_b_left is not None:
                        if should_click and not was_in_zone:
                            fire = True
                        else:
                            fire = False
                        was_in_zone = should_click
                    else:
                        fire = False
                else:
                    fire = should_click and (now - last_click) >= cooldown

                if fire:
                    # Pre-click verification: fresh grab confirms leaf is still in zone
                    # before committing the click — eliminates latency-caused misfires
                    if zone_mode:
                        x_a_v = find_color_in_row(ay, color_a, tolerance, x_start, x_end)
                        xl_v, xr_v = find_color_zone_in_row(by, color_b, tolerance, x_start, x_end)
                        confirmed = (x_a_v is not None and xl_v is not None and
                                     (xl_v + inset) <= x_a_v <= (xr_v - inset))
                    else:
                        confirmed = True

                    if confirmed:
                        if click_type == "double":
                            pyautogui.doubleClick(cx, cy)
                        elif click_type == "right":
                            pyautogui.rightClick(cx, cy)
                        else:
                            pyautogui.click(cx, cy)
                        last_click = now
                        self.click_count += 1
                        self.after(0, lambda n=self.click_count, xa=x_a: self.status_var.set(
                            f"✅ Click #{n}  (A@x={xa})"))
                    else:
                        # Leaf moved out between detection and click — skip and re-arm
                        was_in_zone = False
                        self.after(0, lambda: self.status_var.set(
                            "⚠ Verification failed — leaf moved, skipped"))

                time.sleep(0.01)  # ~100 fps polling

            except Exception as e:
                self.after(0, lambda err=e: self.status_var.set(f"Error: {err}"))
                break

    # ── Misc ───────────────────────────────────────────────────────────────────

    def _reset(self):
        self._stop()
        self.pixels = [None, None]
        self.pixel_colors = [None, None]
        self.scan_area = None
        self.click_pos = None
        for i in range(2):
            self.coord_labels[i].config(text="Not set", fg="#aaa")
            self.color_swatches[i].config(bg="#333")
            self.hex_labels[i].config(text="  #------", fg="#888")
        self.scan_area_lbl.config(text="Full screen width", fg="#888")
        self.click_coord_lbl.config(text="Same as Pixel A", fg="#888")
        self.status_var.set("Reset.  Set both pixels to begin.")

    def _on_close(self):
        self.running = False
        keyboard.unhook_all()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
