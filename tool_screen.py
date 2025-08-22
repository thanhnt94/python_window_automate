# tool_screen.py
# --- VERSION 1.0

import tkinter as tk
from tkinter import ttk, font, messagebox
import math

try:
    from PIL import Image, ImageGrab, ImageTk
except ImportError:
    print("Error: Pillow library is not installed.")
    print("Please install using: pip install Pillow")
    exit()

# =============================================================================
# CAPTURE WINDOW CLASS (Handles all drawing and interaction logic)
# =============================================================================
class CaptureWindow(tk.Toplevel):
    """
    A dedicated fullscreen window for handling screen capture, drawing, and
    information gathering, optimized for performance.
    """
    def __init__(self, root, mode, screenshot, on_complete_callback):
        super().__init__(root)
        self.mode = mode
        self.original_screenshot = screenshot
        self.on_complete = on_complete_callback

        self.start_x, self.start_y = 0, 0
        self.current_x, self.current_y = 0, 0
        self.is_drawing = False

        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.config(cursor="crosshair")
        self.focus_force()

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.tk_screenshot = ImageTk.PhotoImage(self.original_screenshot)
        overlay_image = Image.new('RGBA', self.original_screenshot.size, (0, 0, 0, 128))
        self.tk_overlay = ImageTk.PhotoImage(overlay_image)

        self._draw_background()

        self.canvas.bind("<ButtonPress-1>", self._on_mouse_press)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_release)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.bind("<Escape>", self._cleanup_and_close)
        self.bind("<Button-3>", self._cleanup_and_close)

    def _draw_background(self):
        self.canvas.create_image(0, 0, image=self.tk_screenshot, anchor='nw', tags="background")
        self.canvas.create_image(0, 0, image=self.tk_overlay, anchor='nw', tags="background")

    def _on_mouse_press(self, event):
        if self.mode == 'point':
            self.on_complete({'type': 'log', 'data': f"({event.x}, {event.y})"})
            self._cleanup_and_close()
        elif self.mode == 'color':
            rgb = self.original_screenshot.getpixel((event.x, event.y))
            hex_code = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}".upper()
            # Pass data as a dictionary for special handling
            color_data = {'rgb': f"({rgb[0]}, {rgb[1]}, {rgb[2]})", 'hex': hex_code}
            self.on_complete({'type': 'log', 'data': color_data, 'result_type': 'color'})
            self._cleanup_and_close()
        else: # ruler, rect
            self.is_drawing = True
            self.start_x, self.start_y = event.x, event.y

    def _on_mouse_drag(self, event):
        if not self.is_drawing: return
        self.current_x, self.current_y = event.x, event.y
        self._update_visuals()

    def _on_mouse_release(self, event):
        if not self.is_drawing: return
        self.is_drawing = False
        
        left, top, right, bottom = self._get_normalized_coords()
        width, height = right - left, bottom - top

        if width < 2 and height < 2:
            self._cleanup_and_close()
            return

        result = ""
        if self.mode == 'ruler':
            distance = math.sqrt(width**2 + height**2)
            result = f"W: {width}px, H: {height}px, D: {distance:.2f}px"
        elif self.mode == 'rect':
            result = f"({left}, {top}, {width}, {height})"
        
        self.on_complete({'type': 'log', 'data': result})
        self._cleanup_and_close()

    def _on_mouse_move(self, event):
        if self.is_drawing: return
        self.current_x, self.current_y = event.x, event.y
        self._update_visuals()

    def _update_visuals(self):
        self.canvas.delete("drawing")
        self._draw_selection_area()
        if self.is_drawing or self.mode in ['point', 'color']:
            self._draw_info_box()
        if self.mode == 'color':
            self._draw_magnifier()

    def _get_normalized_coords(self):
        return (min(self.start_x, self.current_x), min(self.start_y, self.current_y),
                max(self.start_x, self.current_x), max(self.start_y, self.current_y))

    def _draw_selection_area(self):
        if not self.is_drawing: return
        left, top, right, bottom = self._get_normalized_coords()
        if right > left and bottom > top:
            selection_img = self.original_screenshot.crop((left, top, right, bottom))
            self.tk_selection_img = ImageTk.PhotoImage(selection_img)
            self.canvas.create_image(left, top, image=self.tk_selection_img, anchor='nw', tags="drawing")
        
        outline_color = "#007AFF"
        if self.mode == 'ruler':
            self.canvas.create_line(self.start_x, self.start_y, self.current_x, self.current_y, fill=outline_color, width=2, tags="drawing")
        elif self.mode == 'rect':
            self.canvas.create_rectangle(left, top, right, bottom, outline=outline_color, width=2, tags="drawing")

    def _draw_info_box(self):
        text = ""
        if self.is_drawing:
            left, top, right, bottom = self._get_normalized_coords()
            width, height = right - left, bottom - top
            if self.mode == 'ruler':
                dist = math.sqrt(width**2 + height**2)
                text = f"W: {width}px  H: {height}px\nDist: {dist:.1f}px"
            elif self.mode == 'rect':
                text = f"Size: {width}x{height}\nPos: ({left}, {top})"
        else:
            x, y = self.current_x, self.current_y
            if self.mode == 'point': text = f"({x}, {y})"
            elif self.mode == 'color':
                rgb = self.original_screenshot.getpixel((x, y))
                hex_code = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}".upper()
                text = f"({x}, {y})\n{hex_code}"
        
        box_width, box_height = 120, 45
        x_offset, y_offset = 20, 20
        if self.current_x + x_offset + box_width > self.winfo_screenwidth(): x_pos = self.current_x - x_offset - box_width
        else: x_pos = self.current_x + x_offset
        if self.current_y + y_offset + box_height > self.winfo_screenheight(): y_pos = self.current_y - y_offset - box_height
        else: y_pos = self.current_y + y_offset
        self.canvas.create_rectangle(x_pos, y_pos, x_pos + box_width, y_pos + box_height, fill="black", outline="#FFFFFF", width=1, tags="drawing")
        self.canvas.create_text(x_pos + box_width/2, y_pos + box_height/2, text=text, fill="white", font=("Segoe UI", 9, "bold"), justify='center', tags="drawing")

    def _draw_magnifier(self):
        size, zoom = 121, 10
        x_offset, y_offset = 20, 20
        if self.current_x - x_offset - size < 0: mag_x = self.current_x + x_offset
        else: mag_x = self.current_x - x_offset - size
        if self.current_y + y_offset + size > self.winfo_screenheight(): mag_y = self.current_y - y_offset - size
        else: mag_y = self.current_y + y_offset
        half = size // zoom // 2
        box = (self.current_x - half, self.current_y - half, self.current_x + half + 1, self.current_y + half + 1)
        try:
            source_img = self.original_screenshot.crop(box)
            zoomed_img = source_img.resize((size, size), Image.NEAREST)
            self.tk_magnifier_img = ImageTk.PhotoImage(zoomed_img)
            self.canvas.create_image(mag_x, mag_y, image=self.tk_magnifier_img, anchor='nw', tags="drawing")
            self.canvas.create_rectangle(mag_x, mag_y, mag_x + size, mag_y + size, outline="white", width=2, tags="drawing")
            for i in range(0, size, zoom):
                self.canvas.create_line(mag_x + i, mag_y, mag_x + i, mag_y + size, fill="gray50", tags="drawing")
                self.canvas.create_line(mag_x, mag_y + i, mag_x + size, mag_y + i, fill="gray50", tags="drawing")
            center_pixel = size // 2
            self.canvas.create_rectangle(mag_x + center_pixel - zoom//2, mag_y + center_pixel - zoom//2,
                                         mag_x + center_pixel + zoom//2, mag_y + center_pixel + zoom//2,
                                         outline="red", width=2, tags="drawing")
        except ValueError: pass

    def _cleanup_and_close(self, event=None):
        self.master.deiconify()
        self.destroy()

# =============================================================================
# MAIN APPLICATION CLASS (Manages UI and State)
# =============================================================================
class ScreenToolApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Screen Tools v10.0")
        # --- UI FIX: Adjust window geometry for a perfect fit ---
        self.root.geometry("520x250") 
        self.root.resizable(False, False)
        self.root.eval('tk::PlaceWindow . center')
        
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("Tool.TFrame", background="#ECECEC")
        style.configure("History.TFrame", background="#FFFFFF")
        style.configure("Copy.TButton", font=("Segoe UI", 7), padding=(2, 2))
        self.root.configure(bg="#ECECEC")

        # --- 2-COLUMN LAYOUT ---
        # --- UI FIX: Increase toolbar width to show full button text ---
        toolbar_frame = ttk.Frame(self.root, width=190, style="Tool.TFrame")
        toolbar_frame.pack(side="left", fill="y", padx=(10, 0), pady=10)
        toolbar_frame.pack_propagate(False)

        history_container = ttk.Frame(self.root, style="Tool.TFrame")
        history_container.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        # --- TOOLBAR (LEFT COLUMN) ---
        buttons = [
            (" Measure Distance", 'ruler', self.draw_ruler_icon),
            (" Get Region Rect", 'rect', self.draw_region_icon),
            (" Pick Color", 'color', self.draw_color_picker_icon),
            (" Get Point Coord", 'point', self.draw_point_icon)
        ]
        for text, mode, icon_drawer in buttons:
            btn = self.create_custom_button(toolbar_frame, text, lambda m=mode: self.start_capture_mode(m), icon_drawer)
            btn.pack(fill='x', padx=10, pady=5)

        # --- HISTORY (RIGHT COLUMN) ---
        history_frame = ttk.Frame(history_container)
        history_frame.pack(expand=True, fill='both')
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(1, weight=1)

        history_header_frame = ttk.Frame(history_frame)
        history_header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        history_header_frame.columnconfigure(0, weight=1)

        history_label = ttk.Label(history_header_frame, text="Result History", font=("Segoe UI", 10, "bold"))
        history_label.grid(row=0, column=0, sticky="w")
        
        self.clear_canvas = tk.Canvas(history_header_frame, width=20, height=20, cursor="hand2", highlightthickness=0)
        self.clear_canvas.grid(row=0, column=1, sticky="e")
        self.draw_clear_icon(self.clear_canvas)
        self.clear_canvas.bind("<Button-1>", lambda e: self.clear_history())
        
        history_content_frame = ttk.Frame(history_frame, style="History.TFrame", relief="solid", borderwidth=1)
        history_content_frame.grid(row=1, column=0, sticky="nsew")
        history_content_frame.columnconfigure(0, weight=1)
        history_content_frame.rowconfigure(0, weight=1)

        self.history_canvas = tk.Canvas(history_content_frame, bg="white", highlightthickness=0)
        self.history_scrollbar = ttk.Scrollbar(history_content_frame, orient="vertical", command=self.history_canvas.yview)
        self.scrollable_frame = ttk.Frame(self.history_canvas)

        self.scrollable_frame.bind("<Configure>", lambda e: self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all")))
        self.history_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.history_canvas.configure(yscrollcommand=self.history_scrollbar.set)
        
        self.history_canvas.grid(row=0, column=0, sticky="nsew")
        self.history_scrollbar.grid(row=0, column=1, sticky="ns")
        
        self.history_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.history_count = 0

    def _on_mousewheel(self, event):
        self.history_canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def create_custom_button(self, parent, text, command, icon_drawer, state="normal"):
        bg_color = "#FFFFFF" if state == "normal" else "#F0F0F0"
        cursor = "hand2" if state == "normal" else ""
        button_frame = tk.Frame(parent, bd=1, relief="raised", bg=bg_color, cursor=cursor)
        icon_canvas = tk.Canvas(button_frame, width=24, height=24, bg=bg_color, highlightthickness=0)
        icon_canvas.pack(side="left", padx=(10, 5), pady=8)
        icon_drawer(icon_canvas)
        label = tk.Label(button_frame, text=text, font=("Segoe UI", 10, "bold"), bg=bg_color, anchor='w')
        label.pack(side="left", padx=(0, 10), pady=8, fill='x', expand=True)
        if state == "normal":
            for widget in [button_frame, icon_canvas, label]:
                widget.bind("<Button-1>", lambda e, f=button_frame, c=command: self._on_button_press(f, c))
                widget.bind("<ButtonRelease-1>", lambda e, f=button_frame: self._on_button_release(f))
        return button_frame

    def _on_button_press(self, frame, command):
        frame.config(relief="sunken")
        command()

    def _on_button_release(self, frame):
        frame.config(relief="raised")

    # Icon drawing methods
    def draw_ruler_icon(self, canvas):
        canvas.create_line(5, 19, 19, 5, fill="#333333", width=2)
        for i in range(3): canvas.create_line(8+i*4, 16-i*4, 6+i*4, 18-i*4, fill="#333333", width=1)
    def draw_region_icon(self, canvas):
        canvas.create_rectangle(5, 5, 19, 19, outline="#333333", width=2, dash=(2, 2))
    def draw_point_icon(self, canvas):
        canvas.create_line(12, 5, 12, 19, fill="#333333", width=1); canvas.create_line(5, 12, 19, 12, fill="#333333", width=1)
        canvas.create_oval(9, 9, 15, 15, outline="#007AFF", width=2)
    def draw_color_picker_icon(self, canvas):
        canvas.create_line(6, 18, 14, 10, fill="#333333", width=2); canvas.create_oval(12, 8, 18, 14, outline="#333333", width=2)
        canvas.create_line(17, 7, 19, 5, fill="#333333", width=2)
    def draw_clear_icon(self, canvas):
        canvas.create_line(6, 6, 16, 16, fill="gray50", width=2)
        canvas.create_line(6, 16, 16, 6, fill="gray50", width=2)

    def start_capture_mode(self, mode):
        self.root.iconify()
        self.root.after(150, self._create_capture_window, mode)

    def _create_capture_window(self, mode):
        try:
            screenshot = ImageGrab.grab()
            CaptureWindow(self.root, mode, screenshot, self.handle_capture_result)
        except Exception as e:
            messagebox.showerror("Error", f"Could not capture screen: {e}")
            self.root.deiconify()

    def handle_capture_result(self, result):
        if result.get('type') == 'log':
            self.root.deiconify()
            self.root.focus_force()
            self.add_history_entry(result['data'], result.get('result_type', 'generic'))

    def add_history_entry(self, data, result_type='generic'):
        bg_color = "#FFFFFF" if self.history_count % 2 == 0 else "#F5F5F5"
        entry_frame = tk.Frame(self.scrollable_frame, bg=bg_color)
        entry_frame.pack(fill='x', expand=True)
        entry_frame.columnconfigure(0, weight=1)

        if result_type == 'color':
            label_text = f"RGB: {data['rgb']}   Hex: {data['hex']}"
            label = tk.Label(entry_frame, text=label_text, font=("Segoe UI", 9), anchor='w', justify='left', bg=bg_color)
            label.grid(row=0, column=0, sticky='w', padx=5, pady=5)
            
            btn_frame = tk.Frame(entry_frame, bg=bg_color)
            btn_frame.grid(row=0, column=1, sticky='e', padx=5)
            
            rgb_btn = ttk.Button(btn_frame, text="RGB", width=4, style="Copy.TButton", command=lambda t=data['rgb']: self.copy_to_clipboard(t))
            rgb_btn.pack(side='left', padx=(0, 2))
            hex_btn = ttk.Button(btn_frame, text="HEX", width=4, style="Copy.TButton", command=lambda t=data['hex']: self.copy_to_clipboard(t))
            hex_btn.pack(side='left')
        else:
            label = tk.Label(entry_frame, text=data, font=("Segoe UI", 9), wraplength=220, anchor='w', justify='left', bg=bg_color)
            label.grid(row=0, column=0, sticky='w', padx=5, pady=5)
            
            copy_btn = ttk.Button(entry_frame, text="Copy", width=5, style="Copy.TButton", command=lambda t=data: self.copy_to_clipboard(t))
            copy_btn.grid(row=0, column=1, sticky='e', padx=5)
        
        self.history_count += 1
        self.root.after(10, lambda: self.history_canvas.yview_moveto(1.0))

    def copy_to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        
    def clear_history(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.history_count = 0

if __name__ == "__main__":
    root = tk.Tk()
    app = ScreenToolApp(root)
    root.mainloop()
