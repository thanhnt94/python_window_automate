# tool_scanner.py
# A standalone tool for interactive UI element inspection using hotkeys.
# --- VERSION 5.6: Corrected the __main__ block to properly launch ScannerApp
# for standalone execution, fixing the 'not defined' error.
# --- VERSION 5.7 (Performance Logging) ---
# - Tích hợp performance logger để đo thời gian quét bằng hotkey.
# --- VERSION 5.8 (Bug Fix) ---
# - Sửa lỗi "height is not defined" trong hàm draw_highlight.

import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, font, messagebox
from ctypes import wintypes

# --- Required Libraries ---
try:
    import win32gui
    import comtypes
    import comtypes.client
    import keyboard
    from comtypes.gen import UIAutomationClient as UIA
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.controls.uiawrapper import UIAWrapper
except ImportError as e:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Missing Library", f"A required library is not installed.\n\nError: {e}")
    sys.exit(1)

# --- Shared Logic Import ---
try:
    import core_logic
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Missing File", "CRITICAL ERROR: 'core_logic.py' must be in the same directory.")
    sys.exit(1)

# Lấy logger hiệu suất đã được cấu hình
perf_logger = logging.getLogger('PerformanceLogger')

# ======================================================================
#                                 CONFIGURATION CONSTANTS
# ======================================================================

HIGHLIGHT_DURATION_MS = 2500
DIALOG_WIDTH = 350
DIALOG_HEIGHT = 700
DIALOG_DEFAULT_GEOMETRY = "-10-10"

ALL_QUICK_SPEC_OPTIONS = [
    "pwa_title", "pwa_auto_id", "pwa_control_type", "pwa_class_name", "pwa_framework_id",
    "win32_handle", "win32_styles", "win32_extended_styles", "state_is_visible",
    "state_is_enabled", "state_is_active", "state_is_minimized", "state_is_maximized",
    "state_is_focusable", "state_is_password", "state_is_offscreen", "state_is_content_element",
    "state_is_control_element", "geo_bounding_rect_tuple", "geo_center_point", "proc_pid",
    "proc_thread_id", "proc_name", "proc_path", "proc_cmdline", "proc_create_time",
    "proc_username", "rel_level", "rel_parent_handle", "rel_parent_title", "rel_labeled_by",
    "rel_child_count", "uia_value", "uia_toggle_state", "uia_expand_state",
    "uia_selection_items", "uia_range_value_info", "uia_grid_cell_info", "uia_table_row_headers"
]
DEFAULT_QUICK_SPEC_OPTIONS = [
    'pwa_auto_id', 'pwa_title', 'pwa_control_type', 'pwa_class_name',
    "rel_level", "rel_parent_title", "rel_labeled_by",
    "proc_name", "proc_path", "proc_cmdline",
    "uia_selection_items", "uia_range_value_info", "uia_grid_cell_info", "uia_table_row_headers"
]

# ======================================================================
#                                 SCANNER LOGIC CLASS
# ======================================================================

class InteractiveScannerLogic:
    def __init__(self, root_gui):
        if UIA is None: raise RuntimeError("UIAutomationClient could not be initialized.")
        self.logger = logging.getLogger(self.__class__.__name__)
        self.root_gui = root_gui
        self.current_element = None
        try:
            self.uia = comtypes.client.CreateObject(UIA.CUIAutomation)
            self.tree_walker = self.uia.ControlViewWalker
        except (OSError, comtypes.COMError) as e:
            self.logger.critical(f"Fatal error initializing COM: {e}", exc_info=True)
            raise

    def _create_full_pwa_wrapper(self, com_element):
        if not com_element: return None
        element_info = UIAElementInfo(com_element)
        return UIAWrapper(element_info)

    def _run_scan_at_cursor(self):
        self.logger.info("Scan request (F8) received.")
        perf_logger.debug("Hotkey F8: Scan at cursor initiated.")
        start_time = time.perf_counter()
        
        self.root_gui.destroy_highlight()
        try:
            cursor_pos = win32gui.GetCursorPos()
            point = wintypes.POINT(cursor_pos[0], cursor_pos[1])
            element_com = self.uia.ElementFromPoint(point)
            if not element_com:
                self.logger.warning("No element found under the cursor.")
                return
            self.current_element = element_com
            self._inspect_element(self.current_element)
        except Exception as e:
            self.logger.error(f"Unexpected error during scan: {e}", exc_info=True)
        finally:
            end_time = time.perf_counter()
            perf_logger.debug(f"Hotkey F8: Scan finished. Duration: {end_time - start_time:.4f}s")


    def _scan_parent_element(self):
        self.logger.info("Scan parent request (F7) received.")
        perf_logger.debug("Hotkey F7: Scan parent initiated.")
        start_time = time.perf_counter()

        if not self.current_element:
            self.logger.warning("No element has been scanned yet. Please press F8 first.")
            return
        try:
            parent = self.tree_walker.GetParentElement(self.current_element)
            if parent:
                self.current_element = parent
                self._inspect_element(self.current_element)
            else:
                self.logger.warning("No valid parent element found.")
        except Exception as e:
            self.logger.error(f"Error scanning parent element: {e}", exc_info=True)
        finally:
            end_time = time.perf_counter()
            perf_logger.debug(f"Hotkey F7: Scan parent finished. Duration: {end_time - start_time:.4f}s")
    
    def _scan_child_element(self):
        self.logger.info("Scan child request (F9) received.")
        perf_logger.debug("Hotkey F9: Scan child initiated.")
        start_time = time.perf_counter()

        if not self.current_element:
            self.logger.warning("No element has been scanned yet. Please press F8 first.")
            return
        try:
            cursor_pos = win32gui.GetCursorPos()
            point = wintypes.POINT(cursor_pos[0], cursor_pos[1])
            found_child = None
            child = self.tree_walker.GetFirstChildElement(self.current_element)
            while child:
                try:
                    child_rect = child.CurrentBoundingRectangle
                    if (child_rect and
                        point.x >= child_rect.left and point.x <= child_rect.right and
                        point.y >= child_rect.top and point.y <= child_rect.bottom):
                        found_child = child
                        break
                    child = self.tree_walker.GetNextSiblingElement(child)
                except comtypes.COMError: break
            if found_child:
                self.logger.info(f"Entering child: '{found_child.CurrentName}'. Updating...")
                self.current_element = found_child
                self._inspect_element(self.current_element)
            else:
                self.logger.warning("No child element found under the cursor.")
        except Exception as e:
            self.logger.error(f"Unexpected error scanning for child element: {e}", exc_info=True)
        finally:
            end_time = time.perf_counter()
            perf_logger.debug(f"Hotkey F9: Scan child finished. Duration: {end_time - start_time:.4f}s")


    def _inspect_element(self, element_com):
        element_pwa = self._create_full_pwa_wrapper(element_com)
        if not element_pwa:
            self.logger.error("Could not create PWA wrapper for the selected element.")
            return
        
        element_details = core_logic.get_all_properties(element_pwa, self.uia, self.tree_walker)
        top_level_window_pwa = core_logic.get_top_level_window(element_pwa)

        window_details = {}
        if top_level_window_pwa:
            window_details = core_logic.get_all_properties(top_level_window_pwa, self.uia, self.tree_walker)
        else:
            self.logger.warning("Could not determine the top-level parent window.")

        coords = element_details.get('geo_bounding_rect_tuple')
        if coords:
            level = element_details.get('rel_level', 0)
            self.root_gui.draw_highlight(element_pwa.rectangle(), level)
        else:
            self.logger.warning("Could not get element coordinates to draw highlight.")

        cleaned_element_details = core_logic.clean_element_spec(window_details, element_details)
        self.root_gui.update_spec_dialog(window_details, element_details, cleaned_element_details)

# ======================================================================
#                                 GUI CLASS
# ======================================================================

class ScannerApp(tk.Toplevel):
    def __init__(self, suite_app=None, quick_spec_keys=None):
        root = suite_app if suite_app else tk.Tk()
        if not suite_app:
            root.withdraw()
        
        super().__init__(root)
        self.suite_app = suite_app
        self.quick_spec_keys = quick_spec_keys
        
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.scanner = InteractiveScannerLogic(self)
        self.highlight_window = None
        self.listener_thread = None
        
        self.last_window_info, self.last_element_info, self.last_cleaned_element_info = {}, {}, {}
        self.last_quick_win_spec, self.last_quick_elem_spec = {}, {}
        
        # Logic to decide which view to show
        if self.quick_spec_keys:
            # Launched from suite, go directly to scanner
            self.title("Interactive Scan Results")
            self.geometry(f"{DIALOG_WIDTH}x{DIALOG_HEIGHT}{DIALOG_DEFAULT_GEOMETRY}")
            self.wm_attributes("-topmost", True)
            self.create_scanner_frame()
            self.scanner_frame.pack(fill="both", expand=True)
            self.run_interactive_scan()
        else:
            # Standalone mode, show config first
            self.title("Interactive Scanner - Configuration")
            self.geometry(f"400x500")
            self.create_config_frame()
            self.config_frame.pack(fill="both", expand=True)

    def create_config_frame(self):
        self.config_frame = ttk.Frame(self, padding=20)
        self.config_vars = {}

        style = ttk.Style(self)
        style.configure("TLabel", font=('Segoe UI', 10))
        style.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=5)
        style.configure("TLabelframe.Label", font=('Segoe UI', 11, 'bold'))

        info_label = ttk.Label(self.config_frame, text="Select properties to include in the 'Quick Spec'.", wraplength=300)
        info_label.pack(pady=(0, 15), fill='x')

        options_container = ttk.LabelFrame(self.config_frame, text="Element Properties")
        options_container.pack(fill="both", expand=True, pady=5)

        canvas = tk.Canvas(options_container)
        scrollbar = ttk.Scrollbar(options_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", _on_mousewheel)

        for option in ALL_QUICK_SPEC_OPTIONS:
            is_default = option in DEFAULT_QUICK_SPEC_OPTIONS
            var = tk.BooleanVar(value=is_default)
            self.config_vars[option] = var
            cb = ttk.Checkbutton(scrollable_frame, text=option, variable=var)
            cb.pack(anchor="w", padx=10, pady=2)
            cb.bind("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        start_button = ttk.Button(self.config_frame, text="Start Scanning", command=self.start_scanning_mode)
        start_button.pack(pady=(15, 0))

    def start_scanning_mode(self):
        selected_keys = [key for key, var in self.config_vars.items() if var.get()]
        if not selected_keys:
            messagebox.showwarning("No Selection", "Please select at least one property for the Quick Spec.")
            return
        
        self.quick_spec_keys = selected_keys
        logging.info(f"Configuration complete. Launching scanner with keys: {self.quick_spec_keys}")

        self.config_frame.destroy()
        self.create_scanner_frame()
        self.scanner_frame.pack(fill="both", expand=True)
        self.title("Interactive Scan Results")
        self.geometry(f"{DIALOG_WIDTH}x{DIALOG_HEIGHT}{DIALOG_DEFAULT_GEOMETRY}")
        self.wm_attributes("-topmost", True)
        
        self.run_interactive_scan()

    def create_scanner_frame(self):
        self.scanner_frame = ttk.Frame(self)
        style = ttk.Style(self)
        style.configure("Copy.TButton", padding=2, font=('Segoe UI', 8))
        
        status_frame = ttk.Frame(self.scanner_frame, relief='sunken', padding=2)
        status_frame.pack(side='bottom', fill='x')
        ttk.Label(status_frame, text="© KNT15083").pack(side='right', padx=5)
        self.status_label = ttk.Label(status_frame, text="Status: Waiting for scan (F8)...")
        self.status_label.pack(side='left', padx=5)

        def copy_to_clipboard(content, button):
            self.clipboard_clear(); self.clipboard_append(content); self.update()
            original_text = button.cget("text"); button.config(text="✅")
            self.after(1500, lambda: button.config(text=original_text))
        
        def send_specs(win_spec, elem_spec):
            if self.suite_app and hasattr(self.suite_app, 'send_specs_to_debugger'):
                self.suite_app.send_specs_to_debugger(win_spec, elem_spec)
                self.on_closing()

        main_frame = ttk.Frame(self.scanner_frame, padding=10)
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(0, weight=1); main_frame.rowconfigure(1, weight=1)

        full_spec_frame = ttk.LabelFrame(main_frame, text="Full Specification (All Properties)", padding=(10, 5))
        full_spec_frame.grid(row=0, column=0, sticky="nsew", pady=5)
        full_spec_frame.columnconfigure(0, weight=1); full_spec_frame.rowconfigure(0, weight=1)
        self.full_text = tk.Text(full_spec_frame, wrap="word", font=("Courier New", 10))
        self.full_text.grid(row=0, column=0, sticky="nsew")
        full_btn_frame = ttk.Frame(full_spec_frame)
        full_btn_frame.place(relx=1.0, rely=0, x=-5, y=-11, anchor='ne')
        self.copy_full_btn = ttk.Button(full_btn_frame, text="📋 Copy All", style="Copy.TButton", command=lambda: copy_to_clipboard(self.full_text.get("1.0", "end-1c"), self.copy_full_btn))
        self.copy_full_btn.pack(side='left', padx=2)
        if self.suite_app:
            self.send_full_btn = ttk.Button(full_btn_frame, text="🚀 Send to Debugger", style="Copy.TButton", command=lambda: send_specs(self.last_window_info, self.last_cleaned_element_info))
            self.send_full_btn.pack(side='left', padx=2)
        
        quick_frame = ttk.LabelFrame(main_frame, text="Recommended Quick Spec", padding=(10, 5))
        quick_frame.grid(row=1, column=0, sticky="nsew", pady=5)
        quick_frame.columnconfigure(0, weight=1); quick_frame.rowconfigure(0, weight=1)
        self.quick_text = tk.Text(quick_frame, wrap="word", font=("Courier New", 10))
        self.quick_text.grid(row=0, column=0, sticky="nsew")
        quick_btn_frame = ttk.Frame(quick_frame)
        quick_btn_frame.place(relx=1.0, rely=0, x=-5, y=-11, anchor='ne')
        self.copy_quick_btn = ttk.Button(quick_btn_frame, text="📋 Copy All", style="Copy.TButton", command=lambda: copy_to_clipboard(self.quick_text.get("1.0", "end-1c"), self.copy_quick_btn))
        self.copy_quick_btn.pack(side='left', padx=2)
        if self.suite_app:
            self.send_quick_btn = ttk.Button(quick_btn_frame, text="🚀 Send to Debugger", style="Copy.TButton", command=lambda: send_specs(self.last_quick_win_spec, self.last_quick_elem_spec))
            self.send_quick_btn.pack(side='left', padx=2)

    def _build_custom_quick_spec(self, info, spec_type='window'):
        spec = {}
        if spec_type == 'window':
            if info.get('proc_name') and info.get('pwa_title'):
                spec['proc_name'] = info['proc_name']
                spec['pwa_title'] = info['pwa_title']
            elif info.get('pwa_title'):
                spec['pwa_title'] = info['pwa_title']
            elif info.get('proc_name'):
                spec['proc_name'] = info['proc_name']
            return spec
        elif spec_type == 'element':
            for key in self.quick_spec_keys:
                if key in info and info[key] is not None:
                    spec[key] = info[key]
            return spec
        return spec

    def run_interactive_scan(self):
        self.listener_thread = threading.Thread(target=self.keyboard_listener_thread, daemon=True)
        self.listener_thread.start()

    def keyboard_listener_thread(self):
        logging.info("Starting hotkey listener: F7 (Parent), F8 (Scan), F9 (Child), ESC (Exit).")
        keyboard.add_hotkey('f8', lambda: self.after(0, self.scanner._run_scan_at_cursor))
        keyboard.add_hotkey('f7', lambda: self.after(0, self.scanner._scan_parent_element))
        keyboard.add_hotkey('f9', lambda: self.after(0, self.scanner._scan_child_element))
        keyboard.wait('esc')
        self.after(0, self.on_closing)

    def on_closing(self):
        logging.info("Exit command received, shutting down scanner.")
        if self.listener_thread:
            keyboard.unhook_all()
        self.destroy_highlight()
        self.destroy()
        if not self.suite_app:
            self.master.quit()

    def update_spec_dialog(self, window_info, element_info, cleaned_element_info):
        if not self.winfo_exists(): return
        
        self.last_window_info = window_info
        self.last_element_info = element_info
        self.last_cleaned_element_info = cleaned_element_info
        
        self.last_quick_win_spec = self._build_custom_quick_spec(window_info, 'window')
        self.last_quick_elem_spec = self._build_custom_quick_spec(cleaned_element_info, 'element')

        level = element_info.get('rel_level', 'N/A')
        proc_name = window_info.get('proc_name', 'Unknown')
        self.status_label.config(text=f"Level: {level} | Process: {proc_name}")

        full_win_str = core_logic.format_spec_to_string(window_info, "window_spec")
        full_elem_str = core_logic.format_spec_to_string(cleaned_element_info, "element_spec")
        full_combined_str = f"{full_win_str}\n\n{full_elem_str}"
        self.full_text.config(state="normal"); self.full_text.delete("1.0", "end"); self.full_text.insert("1.0", full_combined_str); self.full_text.config(state="disabled")
        
        quick_win_str = core_logic.format_spec_to_string(self.last_quick_win_spec, "window_spec")
        quick_elem_str = core_logic.format_spec_to_string(self.last_quick_elem_spec, "element_spec")
        quick_combined_str = f"{quick_win_str}\n\n{quick_elem_str}"
        self.quick_text.config(state="normal"); self.quick_text.delete("1.0", "end"); self.quick_text.insert("1.0", quick_combined_str); self.quick_text.config(state="disabled")
        
    def destroy_highlight(self):
        if self.highlight_window and self.highlight_window.winfo_exists():
            self.highlight_window.destroy()
        self.highlight_window = None

    def draw_highlight(self, rect, level=0):
        self.destroy_highlight()
        try:
            colors = ['#FF0000', '#FF7F00', '#FFFF00', '#00FF00', '#0000FF', '#4B0082', '#9400D3']
            color = colors[level % len(colors)]
            self.highlight_window = tk.Toplevel(self)
            self.highlight_window.overrideredirect(True)
            self.highlight_window.wm_attributes("-topmost", True, "-disabled", True, "-transparentcolor", "white")
            # --- SỬA LỖI ---
            # Sửa lại chuỗi geometry để sử dụng rect.height() và có cú pháp đúng 'widthxheight+x+y'
            self.highlight_window.geometry(f'{rect.width()}x{rect.height()}+{rect.left}+{rect.top}')
            canvas = tk.Canvas(self.highlight_window, bg='white', highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)
            canvas.create_rectangle(2, 2, rect.width()-2, rect.height()-2, outline=color, width=4)
            self.highlight_window.after(HIGHLIGHT_DURATION_MS, self.destroy_highlight)
        except Exception as e:
            logging.error(f"Error drawing highlight rectangle: {e}")

# ======================================================================
#                                 ENTRY POINT
# ======================================================================

if __name__ == "__main__":
    # --- Tích hợp logger hiệu suất cho chế độ chạy độc lập ---
    try:
        import performance_logger
        performance_logger.setup_logger()
    except ImportError:
        # Cấu hình logging cơ bản nếu không có file logger riêng
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
        logging.warning("performance_logger.py not found. Using basic logging.")

    # This block is for running the scanner as a standalone application.
    # It should create an instance of ScannerApp, which will handle its own config.
    app = ScannerApp()
    app.mainloop()
