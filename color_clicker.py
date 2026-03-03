"""IdleOn Chopping Assistant — clicks when the leaf enters the bar zone."""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import math

try:
    import pyautogui
    import keyboard
    import mss as _mss_lib
except ImportError:
    import subprocess, sys
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyautogui", "keyboard", "mss"])
    import pyautogui
    import keyboard
    import mss as _mss_lib

pyautogui.FAILSAFE = False

# ── Helpers ────────────────────────────────────────────────────────────────────

def color_distance(c1, c2):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)

def _scan_row(raw_bgra, width, target_rgb, tolerance):
    best_x, best_dist = None, float("inf")
    for x in range(width):
        i = x * 4
        r, g, b = raw_bgra[i + 2], raw_bgra[i + 1], raw_bgra[i]
        d = color_distance((r, g, b), target_rgb)
        if d < best_dist:
            best_dist = d
            best_x = x
    return best_x if best_dist <= tolerance else None

def _scan_zone(raw_bgra, width, target_rgb, tolerance):
    x_left = x_right = None
    for x in range(width):
        i = x * 4
        r, g, b = raw_bgra[i + 2], raw_bgra[i + 1], raw_bgra[i]
        if color_distance((r, g, b), target_rgb) <= tolerance:
            if x_left is None:
                x_left = x
            x_right = x
    return x_left, x_right

# ── Hardcoded target colours & tolerance ──────────────────────────────────────
PIXEL_A_COLOR   = (0xbd, 0xec, 0x3f)  # #bdec3f — leaf
PIXEL_B_COLOR   = (0xfc, 0xff, 0x7a)  # #fcff7a — green bar
COLOR_TOLERANCE = 10                   # Euclidean RGB distance threshold

# ── Main App ───────────────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("IdleOn Chopping Assistant")
        self.resizable(False, False)
        self.configure(bg="#1a1a2e")

        self.pixels    = [None, None]
        self.scan_area = None
        self.click_pos = None
        self.running   = False
        self.click_count = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD  = 16
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

        tk.Label(self, text="🪓  IdleOn Chopping Assistant",
                 font=("Segoe UI", 14, "bold"), bg=BG, fg=HOT).pack(pady=(PAD, 4), padx=PAD)
        tk.Label(self, text="Click fires when the Leaf enters the Bar zone",
                 font=("Segoe UI", 9), bg=BG, fg="#888").pack(padx=PAD, pady=(0, 4))
        tk.Label(self, text="Manually chop until the Gold bar appears, then press F6 to enable.",
                 font=("Segoe UI", 9), bg=BG, fg="#666").pack(padx=PAD, pady=(0, PAD))

        # ── Pixel cards ──────────────────────────────────────────────────────
        self.coord_labels = []
        pixels_row = tk.Frame(self, bg=BG)
        pixels_row.pack(padx=PAD, fill="x")

        for i, (label, color, btn_text) in enumerate((
            ("Leaf", PIXEL_A_COLOR, "Select Leaf Area"),
            ("Bar",  PIXEL_B_COLOR, "Select Chopping Bar (top half)"),
        )):
            card = tk.Frame(pixels_row, bg=CARD, padx=12, pady=10,
                            highlightbackground=ACC, highlightthickness=1)
            card.pack(side="left", expand=True, fill="both", padx=(0 if i == 0 else 8, 0))

            tk.Label(card, text=label, font=("Segoe UI", 10, "bold"),
                     bg=CARD, fg=FG).pack(anchor="w")

            coord = tk.Label(card, text="Not set", font=MONO, bg=CARD, fg="#aaa")
            coord.pack(anchor="w", pady=(2, 6))
            self.coord_labels.append(coord)

            _hex = rgb_to_hex(color)
            swatch_row = tk.Frame(card, bg=CARD)
            swatch_row.pack(anchor="w")
            tk.Label(swatch_row, width=4, height=1, bg=_hex,
                     relief="flat", borderwidth=2).pack(side="left")
            tk.Label(swatch_row, text=f"  {_hex}", font=MONO,
                     bg=CARD, fg=FG).pack(side="left", padx=4)

            ttk.Button(card, text=btn_text,
                       command=lambda idx=i: self._schedule_pick(idx)).pack(pady=(8, 0), fill="x")

        # ── Scan area ─────────────────────────────────────────────────────────
        scan_frame = tk.Frame(self, bg=CARD, padx=12, pady=10,
                              highlightbackground=ACC, highlightthickness=1)
        scan_frame.pack(padx=PAD, pady=(PAD, 0), fill="x")

        tk.Label(scan_frame, text="Scan Area  (defaults to full screen width)",
                 font=("Segoe UI", 9, "bold"), bg=CARD, fg="#aaa").pack(anchor="w")
        self.scan_area_lbl = tk.Label(scan_frame, text="Full screen width", font=MONO, bg=CARD, fg="#888")
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

        tk.Label(click_frame, text="Click Position  (select the CHOP icon)",
                 font=("Segoe UI", 9, "bold"), bg=CARD, fg="#aaa").pack(anchor="w")
        self.click_coord_lbl = tk.Label(click_frame, text="Not set", font=MONO, bg=CARD, fg="#aaa")
        self.click_coord_lbl.pack(anchor="w", pady=(2, 6))
        ttk.Button(click_frame, text="Pick Click Position",
                   command=lambda: self._schedule_pick("click")).pack(fill="x")

        # ── Edge Trigger ──────────────────────────────────────────────────────
        edge_frame = tk.Frame(self, bg=BG)
        edge_frame.pack(padx=PAD, pady=(8, 0), fill="x")
        self.edge_trigger_var = tk.BooleanVar(value=True)
        tk.Checkbutton(edge_frame,
                       text="Edge trigger  (fire once per zone entry, re-arms on exit)",
                       variable=self.edge_trigger_var,
                       bg=BG, fg=FG, selectcolor=ACC, activebackground=BG,
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
        ttk.Scale(inset_frame, from_=0, to=30, variable=self.zone_inset_var, orient="horizontal",
                  command=lambda v: self.zone_inset_label.config(
                      text=str(int(float(v))))).pack(side="left", expand=True, fill="x", padx=8)

        # ── Prediction ────────────────────────────────────────────────────────
        pred_frame = tk.Frame(self, bg=BG)
        pred_frame.pack(padx=PAD, pady=(4, 0), fill="x")
        tk.Label(pred_frame, text="         ", bg=BG).pack(side="left")
        tk.Label(pred_frame, text="Prediction (frames):", font=("Segoe UI", 9),
                 bg=BG, fg="#aaa").pack(side="left")
        self.lookahead_var = tk.IntVar(value=1)
        self.lookahead_label = tk.Label(pred_frame, text="1", width=3,
                                        font=("Segoe UI", 9, "bold"), bg=BG, fg=HOT)
        self.lookahead_label.pack(side="right")
        ttk.Scale(pred_frame, from_=0, to=8, variable=self.lookahead_var, orient="horizontal",
                  command=lambda v: self.lookahead_label.config(
                      text=str(int(float(v))))).pack(side="left", expand=True, fill="x", padx=8)

        # ── Cooldown ──────────────────────────────────────────────────────────
        cool_frame = tk.Frame(self, bg=BG)
        cool_frame.pack(padx=PAD, pady=(0, PAD), fill="x")
        tk.Label(cool_frame, text="Cooldown (s):", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side="left")
        self.cooldown_var = tk.DoubleVar(value=0.5)
        self.cool_label = tk.Label(cool_frame, text="0.50", width=4,
                                   font=("Segoe UI", 9, "bold"), bg=BG, fg=HOT)
        self.cool_label.pack(side="right")
        ttk.Scale(cool_frame, from_=0.0, to=5.0, variable=self.cooldown_var, orient="horizontal",
                  command=lambda v: self.cool_label.config(
                      text=f"{float(v):.2f}")).pack(side="left", expand=True, fill="x", padx=8)

        # ── Start / Stop ──────────────────────────────────────────────────────
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(padx=PAD, pady=(0, 8), fill="x")
        self.start_btn = ttk.Button(btn_row, text="▶  Start", command=self._toggle)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(btn_row, text="Reset", command=self._reset).pack(
            side="left", expand=True, fill="x", padx=(4, 0))

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Set both pixels to begin.")
        tk.Label(self, textvariable=self.status_var, font=("Segoe UI", 9),
                 bg=ACC, fg=FG, anchor="w", padx=8, pady=4).pack(fill="x", side="bottom")
        tk.Label(self, text="Press  F6  to toggle anywhere",
                 font=("Segoe UI", 8), bg=BG, fg="#555").pack(side="bottom", pady=(0, 2))

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
        if idx == "click":
            self.click_pos = (x, y)
            self.click_coord_lbl.config(text=f"({x}, {y})", fg="#eaeaea")
            self.status_var.set(f"Click position set → ({x}, {y})")
        else:
            self.pixels[idx] = (x, y)
            self.coord_labels[idx].config(text=f"({x}, {y})", fg="#eaeaea")
            self.status_var.set(f"Pixel {chr(65+idx)} set → ({x}, {y})")

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
            x1, x2 = min(start["x"], event.x), max(start["x"], event.x)
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
        self.scan_area_lbl.config(text=f"x: {x1}–{x2}  ({x2 - x1}px wide)", fg="#eaeaea")
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
        if None in self.pixels or self.click_pos is None:
            missing = []
            if self.pixels[0] is None: missing.append("Leaf Area")
            if self.pixels[1] is None: missing.append("Chopping Bar")
            if self.click_pos is None:  missing.append("Click Position")
            messagebox.showwarning("Not ready", f"Please set: {', '.join(missing)}")
            return
        self.running = True
        self.click_count = 0
        self.start_btn.config(text="■  Stop")
        self.status_var.set("Monitoring…  (F6 or Stop button to halt)")
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _stop(self):
        self.running = False
        self.start_btn.config(text="▶  Start")
        self.status_var.set(f"Stopped.  Total clicks: {self.click_count}")

    def _monitor_loop(self):
        ay = self.pixels[0][1]
        by = self.pixels[1][1]
        cx, cy = self.click_pos
        screen_width = pyautogui.size().width

        last_click    = 0
        last_x_a      = None
        SEARCH_RADIUS = 120
        was_in_zone   = False
        prev_x_a      = None
        smoothed_vel  = 0.0
        VEL_ALPHA     = 0.4

        with _mss_lib.mss() as sct:
            while self.running:
                try:
                    cooldown = self.cooldown_var.get()
                    inset    = self.zone_inset_var.get()
                    x_start, x_end = self.scan_area if self.scan_area else (0, screen_width)

                    # ── Leaf scan (Pixel A) — local search first ───────────────
                    if last_x_a is not None:
                        ls = max(x_start, last_x_a - SEARCH_RADIUS)
                        le = min(x_end,   last_x_a + SEARCH_RADIUS)
                    else:
                        ls, le = x_start, x_end

                    shot_a = sct.grab({"left": ls, "top": ay, "width": max(1, le - ls), "height": 1})
                    rel_a  = _scan_row(shot_a.raw, shot_a.width, PIXEL_A_COLOR, COLOR_TOLERANCE)
                    if rel_a is None and (ls != x_start or le != x_end):
                        shot_a = sct.grab({"left": x_start, "top": ay, "width": max(1, x_end - x_start), "height": 1})
                        rel_a  = _scan_row(shot_a.raw, shot_a.width, PIXEL_A_COLOR, COLOR_TOLERANCE)
                        x_a = (x_start + rel_a) if rel_a is not None else None
                    else:
                        x_a = (ls + rel_a) if rel_a is not None else None

                    if x_a is not None:
                        last_x_a = x_a

                    # ── Velocity tracking ─────────────────────────────────────
                    if x_a is not None:
                        if prev_x_a is not None:
                            raw_vel      = x_a - prev_x_a
                            smoothed_vel = VEL_ALPHA * raw_vel + (1 - VEL_ALPHA) * smoothed_vel
                        prev_x_a = x_a
                    else:
                        prev_x_a     = None
                        smoothed_vel = 0.0

                    # ── Zone scan (Pixel B) — always full range ────────────────
                    shot_b = sct.grab({"left": x_start, "top": by, "width": max(1, x_end - x_start), "height": 1})
                    xl, xr = _scan_zone(shot_b.raw, shot_b.width, PIXEL_B_COLOR, COLOR_TOLERANCE)
                    x_b_left  = (x_start + xl) if xl is not None else None
                    x_b_right = (x_start + xr) if xr is not None else None

                    # ── Zone check (actual + predicted) ───────────────────────
                    lookahead = self.lookahead_var.get()
                    if x_a is None or x_b_left is None:
                        missing = ("Pixel A: not found  " if x_a is None else "") + \
                                  ("Pixel B zone: not found" if x_b_left is None else "")
                        self.after(0, lambda m=missing: self.status_var.set(f"Monitoring…  {m}"))
                        in_zone_actual = in_zone_predicted = False
                    else:
                        eff_left  = x_b_left  + inset
                        eff_right = x_b_right - inset
                        predicted_x       = x_a + smoothed_vel * lookahead
                        in_zone_actual    = eff_left <= x_a         <= eff_right
                        in_zone_predicted = eff_left <= predicted_x <= eff_right
                        self.after(0, lambda xa=x_a, px=round(predicted_x), bl=eff_left, br=eff_right, z=in_zone_predicted:
                            self.status_var.set(f"Monitoring…  A@x={xa}→{px}  zone=[{bl}–{br}]  {'✓ IN ZONE' if z else '✗ out'}"))

                    # ── Fire decision ─────────────────────────────────────────
                    now = time.time()
                    if self.edge_trigger_var.get():
                        if x_a is not None and x_b_left is not None:
                            fire        = in_zone_predicted and not was_in_zone and (now - last_click) >= cooldown
                            was_in_zone = in_zone_actual
                        else:
                            fire = False
                    else:
                        fire = in_zone_predicted and (now - last_click) >= cooldown

                    if fire:
                        pyautogui.click(cx, cy)
                        last_click = now
                        self.click_count += 1
                        self.after(0, lambda n=self.click_count, xa=x_a:
                            self.status_var.set(f"✅ Click #{n}  (A@x={xa})"))

                except Exception as e:
                    self.after(0, lambda err=e: self.status_var.set(f"Error: {err}"))
                    break

    # ── Misc ───────────────────────────────────────────────────────────────────

    def _reset(self):
        self._stop()
        self.pixels    = [None, None]
        self.scan_area = None
        self.click_pos = None
        for i in range(2):
            self.coord_labels[i].config(text="Not set", fg="#aaa")
        self.scan_area_lbl.config(text="Full screen width", fg="#888")
        self.click_coord_lbl.config(text="Not set", fg="#aaa")
        self.status_var.set("Reset.  Set both pixels to begin.")

    def _on_close(self):
        self.running = False
        keyboard.unhook_all()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
