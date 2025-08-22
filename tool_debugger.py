# tool_debugger.py
# A standalone tool for debugging and testing UI selectors.
# --- VERSION 4.1 (Performance Fix for Window Search):
# - Sửa lỗi hiệu suất khiến việc tìm kiếm cửa sổ đã mở sẵn bị chậm.
# - DebuggerWorker.run_debug_session giờ đây sử dụng timeout=1 và retry_interval=0.1
#   cho việc tìm kiếm cửa sổ, giúp tìm thấy cửa sổ đã chạy rất nhanh mà vẫn có
#   một chút khả năng chịu lỗi.
# - Logic này chỉ áp dụng riêng cho công cụ debugger, không ảnh hưởng đến
#   cơ chế mặc định của UIController.

import tkinter as tk
from tkinter import ttk, scrolledtext, font, messagebox
import threading
import ast
import logging
import sys
import time
import argparse
import inspect

# --- Required Libraries ---
try:
    from pywinauto import Desktop
    import comtypes
    from comtypes.gen import UIAutomationClient as UIA
    from pywinauto.controls.uiawrapper import UIAWrapper
except ImportError as e:
    print(f"Error importing libraries: {e}")
    sys.exit(1)

# --- Shared Logic Import ---
try:
    import core_logic
    import core_controller
except ImportError:
    print("CRITICAL ERROR: 'core_logic.py' and 'core_controller.py' must be in the same directory.")
    sys.exit(1)

# ======================================================================
#                       DEBUGGER LOGIC CLASS
# ======================================================================
class DebuggerWorker:
    """
    Xử lý các tác vụ tìm kiếm và gỡ lỗi trong một luồng nền.
    """
    def __init__(self, log_callback, ui_controller_instance):
        self.log = log_callback
        self.ui_controller = ui_controller_instance

    def run_debug_session(self, window_spec, element_spec, on_complete_callback):
        """
        Thực hiện một phiên gỡ lỗi.

        Args:
            window_spec (dict): Bộ lọc để tìm cửa sổ.
            element_spec (dict): Bộ lọc để tìm element.
            on_complete_callback (function): Hàm callback để xử lý kết quả.
        """
        # Khởi tạo COM cho luồng này
        comtypes.CoInitialize()
        result_bundle = {"results": [], "level": "element", "window_context": []}
        try:
            session_start_time = time.perf_counter()
            self.log('HEADER', f"--- BẮT ĐẦU PHIÊN GỠ LỖI ---")
            
            self.log('INFO', "--- Bước 1: Tìm kiếm CỬA SỔ ---")
            
            # Lấy tất cả các cửa sổ để có ngữ cảnh cho việc tạo spec tối ưu sau này
            all_windows = self.ui_controller.desktop.windows()
            result_bundle["window_context"] = all_windows
            
            # Sử dụng UIController để tìm cửa sổ với thời gian chờ ngắn
            windows = self.ui_controller.find_element(
                window_spec=window_spec,
                timeout=1,
                retry_interval=0.1
            )
            
            if windows:
                # find_element trả về một đối tượng, không phải list
                target_window = windows
                self.log('SUCCESS', f"Tìm thấy 1 cửa sổ duy nhất: '{target_window.window_text()}'")
                
                if element_spec:
                    self.log('INFO', f"--- Bước 2: Tìm kiếm ELEMENT bên trong cửa sổ ---")
                    # Sử dụng UIController để tìm tất cả các element phù hợp
                    elements = self.ui_controller.finder.find(target_window, element_spec)
                    result_bundle["results"] = elements
                    result_bundle["level"] = "element"
                else:
                    result_bundle["results"] = [target_window]
                    result_bundle["level"] = "window"
            else:
                self.log('ERROR', "Không tìm thấy cửa sổ nào phù hợp với tiêu chí.")
                result_bundle["results"] = []
                result_bundle["level"] = "window"
        
            total_duration = time.perf_counter() - session_start_time
            result_bundle['total_duration'] = total_duration

        except Exception as e:
            self.log('ERROR', f"Đã xảy ra lỗi không mong muốn: {e}")
            logging.exception("Full traceback in console:")
        
        finally:
            self.log('HEADER', "--- KẾT THÚC PHIÊN GỠ LỖI ---")
            on_complete_callback(result_bundle)
            # Giải phóng COM cho luồng này
            comtypes.CoUninitialize()

# ======================================================================
#                       GUI CLASS (Embeddable Frame)
# ======================================================================
class DebuggerTab(ttk.Frame):
    """
    Tab gỡ lỗi selector trong Automation Suite.
    """
    def __init__(self, parent, suite_app=None, status_label_widget=None):
        super().__init__(parent)
        self.pack(fill="both", expand=True)
        self.suite_app = suite_app
        self.status_label = status_label_widget or getattr(suite_app, 'status_label', None)
        
        # Khởi tạo UIController một lần cho tab này
        self.controller = core_controller.UIController(log_level='debug')
        self.debugger_worker = DebuggerWorker(self.log_message, self.controller)

        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure("TLabel", font=('Segoe UI', 10))
        style.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=5)
        style.configure("TLabelframe.Label", font=('Segoe UI', 11, 'bold'))
        style.configure("Treeview.Heading", font=('Segoe UI', 10, 'bold'))
        style.configure("Copy.TButton", padding=2, font=('Segoe UI', 8))

        self.highlighter = None
        self.test_thread = None
        self.found_items_map = {}
        self.selected_item = None
        self.selected_item_type = 'element'
        self.last_window_context = []
        self.last_element_context = []
        
        self.create_widgets()

    def create_widgets(self):
        """
        Tạo và bố cục các widget trên giao diện.
        """
        main_paned_window = ttk.PanedWindow(self, orient='vertical')
        main_paned_window.pack(fill="both", expand=True, padx=10, pady=10)

        top_frame = ttk.Frame(main_paned_window)
        main_paned_window.add(top_frame, weight=2)
        top_frame.columnconfigure(0, weight=1)
        top_frame.rowconfigure(1, weight=1)

        input_frame = self.create_input_frame(top_frame)
        input_frame.grid(row=0, column=0, sticky="ew", pady=5)
        
        results_frame = self.create_results_frame(top_frame)
        results_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        log_frame = self.create_log_frame(main_paned_window)
        main_paned_window.add(log_frame, weight=1)

    def create_input_frame(self, parent):
        """
        Tạo khung nhập liệu cho các selector.
        """
        frame = ttk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        
        ttk.Label(frame, text="Window Spec:").grid(row=0, column=0, sticky="nw", padx=5)
        self.window_spec_text = tk.Text(frame, height=5, font=("Courier New", 10), wrap="word")
        self.window_spec_text.grid(row=0, column=1, sticky="ew")
        self.window_spec_text.insert("1.0", "window_spec = {\n    'pwa_title': ('icontains', 'Explorer')\n}")
        
        ttk.Label(frame, text="Element Spec:").grid(row=1, column=0, sticky="nw", padx=5, pady=5)
        self.element_spec_text = tk.Text(frame, height=5, font=("Courier New", 10), wrap="word")
        self.element_spec_text.grid(row=1, column=1, sticky="ew", pady=5)

        control_frame = ttk.Frame(frame)
        control_frame.grid(row=2, column=1, sticky='ew')
        
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(side="left", padx=20)
        
        self.run_button = ttk.Button(button_frame, text="Run Debug", command=self.run_test)
        self.run_button.pack(side="left", padx=(0, 10))
        
        self.get_spec_button = ttk.Button(button_frame, text="Get Full Spec", state="disabled", command=self.show_detail_window)
        self.get_spec_button.pack(side="left", padx=10)
        
        self.clear_button = ttk.Button(button_frame, text="Clear Log", command=self.clear_log)
        self.clear_button.pack(side="left", padx=10)
        
        return frame

    def create_results_frame(self, parent):
        """
        Tạo khung hiển thị kết quả tìm kiếm.
        """
        self.results_labelframe = ttk.LabelFrame(parent, text="Found Results")
        self.results_labelframe.columnconfigure(0, weight=1); self.results_labelframe.rowconfigure(0, weight=1)
        self.results_tree = ttk.Treeview(self.results_labelframe, show="headings")
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar = ttk.Scrollbar(self.results_labelframe, orient="vertical", command=self.results_tree.yview)
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=v_scrollbar.set)
        h_scrollbar = ttk.Scrollbar(self.results_labelframe, orient="horizontal", command=self.results_tree.xview)
        h_scrollbar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.results_tree.configure(xscrollcommand=h_scrollbar.set)
        self.results_tree.bind("<<TreeviewSelect>>", self.on_result_selected)
        return self.results_labelframe

    def create_log_frame(self, parent):
        """
        Tạo khung hiển thị log chi tiết.
        """
        frame = ttk.LabelFrame(parent, text="Detailed Log")
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        self.log_area = scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 10), state="disabled", bg="#2B2B2B")
        self.log_area.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_area.tag_config('TIMER', foreground='#00FFFF', font=("Consolas", 10, "bold")) 
        self.log_area.tag_config('INFO', foreground='#87CEEB'); self.log_area.tag_config('DEBUG', foreground='#D3D3D3'); self.log_area.tag_config('FILTER', foreground='#FFD700'); self.log_area.tag_config('SUCCESS', foreground='#90EE90'); self.log_area.tag_config('ERROR', foreground='#F08080'); self.log_area.tag_config('HEADER', foreground='#FFFFFF', font=("Consolas", 11, "bold", "underline")); self.log_area.tag_config('KEEP', foreground='#76D7C4', font=("Consolas", 10, "bold")); self.log_area.tag_config('DISCARD', foreground='#E59866', font=("Consolas", 10, "bold"))
        return frame

    def update_status(self, text):
        """
        Cập nhật thanh trạng thái.
        """
        if self.status_label:
            self.status_label.config(text=text)

    def log_message(self, level, message):
        """
        Ghi log vào khu vực văn bản và thanh trạng thái.
        """
        self.update_status(f"Debugger: {message if isinstance(message, str) else 'Processing...'}")
        self.log_area.config(state="normal")
        if isinstance(message, list):
            self.log_area.insert(tk.END, f"[{level}] ")
            for text, tag in message: self.log_area.insert(tk.END, text, tag)
            self.log_area.insert(tk.END, "\n")
        else:
            self.log_area.insert(tk.END, f"[{level}] {message}\n", level)
        self.log_area.config(state="disabled")
        self.log_area.see(tk.END)

    def clear_log(self):
        """
        Xóa tất cả log và kết quả hiển thị.
        """
        self.log_area.config(state="normal")
        self.log_area.delete("1.0", tk.END)
        self.log_area.config(state="disabled")
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self.get_spec_button.config(state="disabled")
        self.selected_item = None
        self.update_status("Debugger cleared. Ready for new test.")

    def _extract_and_parse_spec(self, spec_string):
        """
        Trích xuất và phân tích cú pháp chuỗi spec từ Text widget.
        """
        spec_string = spec_string.strip()
        if not spec_string: return {}
        start_brace = spec_string.find('{')
        if start_brace == -1: raise ValueError("Could not find '{' to start dictionary.")
        dict_str = spec_string[start_brace:]
        try:
            parsed_dict = ast.literal_eval(dict_str)
            if isinstance(parsed_dict, dict): return parsed_dict
            else: raise ValueError("Parsed content is not a dictionary.")
        except (ValueError, SyntaxError) as e: raise ValueError(f"Could not parse spec. Error: {e}")

    def run_test(self):
        """
        Bắt đầu phiên gỡ lỗi trong một luồng nền.
        """
        self.clear_log()
        self.run_button.config(state="disabled")
        self.update_status("Debugger: Running test...")
        try:
            win_spec = self._extract_and_parse_spec(self.window_spec_text.get("1.0", "end-1c"))
            elem_spec = self._extract_and_parse_spec(self.element_spec_text.get("1.0", "end-1c"))
        except ValueError as e:
            self.log_message('ERROR', f"Syntax error in spec: {e}")
            self.run_button.config(state="normal")
            self.update_status("Debugger: Error in spec.")
            return
        
        self.test_thread = threading.Thread(target=self.debugger_worker.run_debug_session, args=(win_spec, elem_spec, self.on_test_complete), daemon=True)
        self.test_thread.start()

    def on_test_complete(self, result_bundle):
        """
        Xử lý kết quả sau khi phiên gỡ lỗi hoàn tất.
        """
        self.after(0, self._update_gui_on_test_complete, result_bundle)
        
    def _update_gui_on_test_complete(self, result_bundle):
        """
        Cập nhật giao diện với kết quả từ phiên gỡ lỗi.
        """
        self.run_button.config(state="normal")
        self.found_items_map.clear()
        results = result_bundle.get("results", [])
        search_level = result_bundle.get("level", "element")
        self.last_window_context = result_bundle.get("window_context", [])
        
        if not results:
            self.update_status("Debugger: Test finished. No items found.")
            self.results_labelframe.config(text="Found Results")
        elif len(results) == 1:
            self.update_status(f"Debugger: Test finished. Found 1 unique {search_level}.")
        else:
            self.update_status(f"Debugger: Found {len(results)} ambiguous {search_level}s. Please select one.")

        if search_level == 'window':
            self.results_labelframe.config(text="Found Windows")
            self.configure_treeview_columns(['Title', 'Handle', 'Process Name'])
            for win in results:
                values = (win.window_text(), win.handle, core_logic.get_property_value(win, 'proc_name'))
                item_id = self.results_tree.insert("", "end", values=values)
                self.found_items_map[item_id] = (win, 'window')
        elif search_level == 'element':
            self.results_labelframe.config(text="Found Elements")
            self.configure_treeview_columns(['Title/Name', 'Control Type', 'Automation ID'])
            for elem in results:
                try: title = elem.window_text()[:100] if elem.window_text() else ""
                except Exception: title = "[Error: Failed to get text]"
                try: ctrl_type = elem.control_type()
                except Exception: ctrl_type = "[Error: Failed to get type]"
                try: auto_id = elem.automation_id()
                except Exception: auto_id = "[Error: Failed to get ID]"
                values = (title, ctrl_type, auto_id)
                item_id = self.results_tree.insert("", "end", values=values)
                self.found_items_map[item_id] = (elem, 'element')

        total_duration = result_bundle.get('total_duration')
        if total_duration is not None:
            self.log_message('HEADER', f"--- TỔNG THỜI GIAN: {total_duration:.4f} giây ---")

        if len(results) == 1:
            self.after(100, self._auto_select_first_item)

    def _auto_select_first_item(self):
        """
        Tự động chọn mục đầu tiên nếu chỉ có một kết quả.
        """
        if not self.results_tree.get_children():
            return
        first_item_id = self.results_tree.get_children()[0]
        self.results_tree.selection_set(first_item_id)
        self.results_tree.focus(first_item_id)
        self.on_result_selected(None)

    def configure_treeview_columns(self, column_names):
        """
        Cấu hình các cột của Treeview.
        """
        self.results_tree.config(columns=column_names)
        for name in column_names:
            self.results_tree.heading(name, text=name)
            self.results_tree.column(name, width=150)
        if "Title" in column_names or "Title/Name" in column_names:
            self.results_tree.column(column_names[0], width=400)

    def on_result_selected(self, event):
        """
        Xử lý sự kiện khi một mục trong Treeview được chọn.
        """
        selected_items = self.results_tree.selection()
        if not selected_items: return
        selected_id = selected_items[0]
        item_data = self.found_items_map.get(selected_id)
        if item_data:
            self.selected_item, self.selected_item_type = item_data
            self.highlight_item(self.selected_item)
            self.get_spec_button.config(state="normal")
            try: self.update_status(f"Debugger: Selected '{self.selected_item.window_text()[:50]}...'")
            except Exception: self.update_status("Debugger: Selected an item.")

    def highlight_item(self, item):
        """
        Vẽ một hình chữ nhật highlight lên element đã chọn.
        """
        if self.highlighter: self.highlighter.destroy()
        try: rect = item.rectangle()
        except Exception as e:
            self.log_message('ERROR', f"Could not get coordinates to highlight: {e}")
            return
        self.highlighter = tk.Toplevel(self)
        self.highlighter.overrideredirect(True)
        self.highlighter.wm_attributes("-topmost", True, "-disabled", True, "-transparentcolor", "white")
        self.highlighter.geometry(f'{rect.width()}x{rect.height()}+{rect.left}+{rect.top}')
        canvas = tk.Canvas(self.highlighter, bg='white', highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_rectangle(2, 2, rect.width()-2, rect.height()-2, outline="red", width=4)
        self.highlighter.after(3000, self.highlighter.destroy)

    def show_detail_window(self):
        """
        Hiển thị cửa sổ chi tiết với các spec đầy đủ và tối ưu.
        """
        if not self.selected_item:
            messagebox.showwarning("No Item Selected", "Please select an item from the list.")
            return
        detail_win = tk.Toplevel(self)
        detail_win.title("Specification Details")
        detail_win.geometry("650x700+50+50")
        detail_win.transient(self)
        detail_win.grab_set()
        
        # Lấy thông tin đầy đủ của element đã chọn
        element_pwa = self.selected_item
        window_pwa = core_logic.get_top_level_window(element_pwa)
        
        # Tạo spec tối ưu và đầy đủ
        window_info = core_logic.get_all_properties(window_pwa, self.controller.uia, self.controller.tree_walker)
        element_info = core_logic.get_all_properties(element_pwa, self.controller.uia, self.controller.tree_walker)
        cleaned_element_info = core_logic.clean_element_spec(window_info, element_info)
        
        # Lấy ngữ cảnh tìm kiếm để tạo spec tối ưu
        all_windows_on_desktop_info = [core_logic.get_all_properties(w, self.controller.uia, self.controller.tree_walker) for w in self.last_window_context]
        try:
            all_elements_in_window_info = [core_logic.get_all_properties(e, self.controller.uia, self.controller.tree_walker) for e in window_pwa.descendants()]
        except Exception as e:
            self.log_message("WARNING", f"Could not get all elements in window for optimal spec: {e}. Using a limited context.")
            all_elements_in_window_info = []

        # Tạo spec tối ưu dựa trên ngữ cảnh
        optimal_window_spec = core_logic.create_optimal_window_spec(window_info, all_windows_on_desktop_info)
        optimal_element_spec = core_logic.create_optimal_element_spec(element_info, all_elements_in_window_info)

        def send_specs(win_spec, elem_spec):
            if self.suite_app and hasattr(self.suite_app, 'send_specs_to_debugger'):
                self.suite_app.send_specs_to_debugger(win_spec, elem_spec)
                detail_win.destroy()
        
        def copy_to_clipboard(content, button):
            detail_win.clipboard_clear(); detail_win.clipboard_append(content); detail_win.update()
            original_text = button.cget("text"); button.config(text="✅")
            detail_win.after(1500, lambda: button.config(text=original_text))

        main_frame = ttk.Frame(detail_win, padding=10)
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(0, weight=1); main_frame.rowconfigure(1, weight=1); main_frame.rowconfigure(2, weight=1)
        
        # --- Full Spec Frame ---
        full_win_spec_str = core_logic.format_spec_to_string(window_info, "window_spec")
        full_elem_spec_str = core_logic.format_spec_to_string(cleaned_element_info, "element_spec")
        full_spec_frame = ttk.LabelFrame(main_frame, text="Full Specification", padding=(10, 5))
        full_spec_frame.grid(row=0, column=0, sticky="nsew", pady=5)
        full_spec_frame.columnconfigure(0, weight=1); full_spec_frame.rowconfigure(0, weight=1)
        full_text = tk.Text(full_spec_frame, wrap="word", font=("Courier New", 10))
        full_text.grid(row=0, column=0, sticky="nsew")
        full_text.insert("1.0", f"{full_win_spec_str}\n\n{full_elem_spec_str}"); full_text.config(state="disabled")
        full_btn_frame = ttk.Frame(full_spec_frame)
        full_btn_frame.place(relx=1.0, rely=0, x=-5, y=-11, anchor='ne')
        copy_full_btn = ttk.Button(full_btn_frame, text="📋 Copy All", style="Copy.TButton", command=lambda: copy_to_clipboard(full_text.get("1.0", "end-1c"), copy_full_btn))
        copy_full_btn.pack(side='left', padx=2)
        if self.suite_app and hasattr(self.suite_app, 'send_specs_to_debugger'):
            send_full_btn = ttk.Button(full_btn_frame, text="🚀 Send to Debugger", style="Copy.TButton", command=lambda: send_specs(window_info, cleaned_element_info))
            send_full_btn.pack(side='left', padx=2)
            
        # --- Optimal Spec Frame ---
        optimal_win_str = core_logic.format_spec_to_string(optimal_window_spec, 'window_spec')
        optimal_elem_str = core_logic.format_spec_to_string(optimal_element_spec, 'element_spec')
        combined_optimal_str = f"{optimal_win_str}\n\n{optimal_elem_str}"
        optimal_frame = ttk.LabelFrame(main_frame, text="Optimal Filter Spec (Recommended)", padding=(10, 5))
        optimal_frame.grid(row=1, column=0, sticky="nsew", pady=5)
        optimal_frame.columnconfigure(0, weight=1); optimal_frame.rowconfigure(0, weight=1)
        optimal_text = tk.Text(optimal_frame, wrap="word", font=("Courier New", 10))
        optimal_text.grid(row=0, column=0, sticky="nsew")
        optimal_text.insert("1.0", combined_optimal_str); optimal_text.config(state="disabled")
        optimal_btn_frame = ttk.Frame(optimal_frame)
        optimal_btn_frame.place(relx=1.0, rely=0, x=-5, y=-11, anchor='ne')
        copy_optimal_btn = ttk.Button(optimal_btn_frame, text="📋 Copy All", style="Copy.TButton", command=lambda: copy_to_clipboard(combined_optimal_str, copy_optimal_btn))
        copy_optimal_btn.pack(side='left', padx=2)
        if self.suite_app and hasattr(self.suite_app, 'send_specs_to_debugger'):
            send_optimal_btn = ttk.Button(optimal_btn_frame, text="🚀 Send to Debugger", style="Copy.TButton", command=lambda: send_specs(optimal_window_spec, optimal_element_spec))
            send_optimal_btn.pack(side='left', padx=2)

    def receive_specs(self, window_spec, element_spec):
        """
        Nhận spec từ một công cụ khác và hiển thị chúng.
        """
        self.clear_log()
        win_spec_str = core_logic.format_spec_to_string(window_spec, "window_spec")
        elem_spec_str = core_logic.format_spec_to_string(element_spec, "element_spec")
        
        self.window_spec_text.config(state="normal")
        self.window_spec_text.delete("1.0", "end")
        self.window_spec_text.insert("1.0", win_spec_str)
        
        self.element_spec_text.config(state="normal")
        self.element_spec_text.delete("1.0", "end")
        self.element_spec_text.insert("1.0", elem_spec_str)
        
        self.log_message("INFO", "Received specs from another tool.")

# ======================================================================
#                   ENTRY POINT (for standalone execution)
# ======================================================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
    
    parser = argparse.ArgumentParser(description="Standalone Selector Debugger Tool.")
    parser.add_argument('--window-spec', type=str, help="A Python dictionary string representing the window specification.")
    parser.add_argument('--element-spec', type=str, help="A Python dictionary string representing the element specification.")
    args = parser.parse_args()

    root = tk.Tk()
    root.title("Standalone Selector Debugger")
    root.geometry("950x800")
    
    status_frame = ttk.Frame(root, relief='sunken', padding=2)
    status_frame.pack(side='bottom', fill='x')
    ttk.Label(status_frame, text="© KNT15083").pack(side='right', padx=5)
    status_label = ttk.Label(status_frame, text="Ready (Standalone Mode)")
    status_label.pack(side='left', padx=5)

    app_frame = DebuggerTab(root, status_label_widget=status_label)
    
    if args.window_spec or args.element_spec:
        try:
            win_spec = ast.literal_eval(args.window_spec) if args.window_spec else {}
            elem_spec = ast.literal_eval(args.element_spec) if args.element_spec else {}
            if not isinstance(win_spec, dict) or not isinstance(elem_spec, dict):
                 raise ValueError("Provided specs must be valid dictionaries.")
            app_frame.receive_specs(win_spec, elem_spec)
            app_frame.log_message("INFO", "Loaded specs from command-line arguments.")
        except (ValueError, SyntaxError) as e:
            app_frame.log_message("ERROR", f"Failed to parse spec from command line: {e}")

    root.mainloop()
