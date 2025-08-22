# tool_explorer.py
# A standalone and embeddable tool for full window element scanning.
# --- VERSION 7.2 (Centralized Logic): Corrected a critical bug where 'sys_unique_id' was not being
# added to window properties. Now calls the centralized 'create_optimal_spec' functions
# from core_logic for consistent behavior.
# --- VERSION 7.3 (Performance Logging) ---
# - Tích hợp performance logger để đo thời gian quét toàn bộ cửa sổ và quét sâu.
# --- VERSION 7.4 (Debugger Integration) ---
# - Thêm nút "Send to Debugger" vào cửa sổ chi tiết element.
# - Nút này chỉ hiển thị khi chạy Explorer bên trong Automation Suite.
# - Cho phép gửi trực tiếp full spec hoặc optimal spec sang tab Debugger.

import logging
import re
import time
import os
import sys
import threading
from tkinter import ttk, font, filedialog, messagebox
import tkinter as tk
import argparse
import subprocess

# --- Required Libraries ---
try:
    import pandas as pd
    import comtypes
    import comtypes.client
    from comtypes.gen import UIAutomationClient as UIA
    from pywinauto import Desktop
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.controls.uiawrapper import UIAWrapper
except ImportError as e:
    print(f"Error importing libraries: {e}")
    sys.exit(1)

# --- Shared Logic Import ---
try:
    import core_logic
except ImportError:
    print("CRITICAL ERROR: 'core_logic.py' must be in the same directory.")
    sys.exit(1)

# Lấy logger hiệu suất đã được cấu hình
perf_logger = logging.getLogger('PerformanceLogger')

# ======================================================================
#                   SCANNER LOGIC CLASS (BACKEND)
# ======================================================================
class FullScanner:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.desktop = Desktop(backend='uia')
        try:
            self.uia = comtypes.client.CreateObject(UIA.CUIAutomation)
            self.tree_walker = self.uia.ControlViewWalker
        except (OSError, comtypes.COMError) as e:
            self.logger.critical(f"Fatal error initializing COM: {e}", exc_info=True)
            raise

    def get_all_windows(self):
        self.logger.info("Starting to scan all windows on the desktop...")
        perf_logger.info("Starting full desktop window scan...")
        start_time = time.perf_counter()
        
        windows = self.desktop.windows()
        all_windows_data = []
        for win in windows:
            try:
                if win.is_visible() and win.window_text():
                    info = core_logic.get_all_properties(win, self.uia, self.tree_walker)
                    info['pwa_object'] = win # Keep the object for later use
                    info['sys_unique_id'] = id(win.element_info.element)
                    all_windows_data.append(info)
            except Exception as e:
                self.logger.warning(f"Could not process window. Error: {e}")
        
        end_time = time.perf_counter()
        self.logger.info(f"Found {len(all_windows_data)} valid windows.")
        perf_logger.info(f"Desktop window scan finished. Found {len(all_windows_data)} windows. Duration: {end_time - start_time:.4f}s")
        return all_windows_data

    def get_all_elements_from_window(self, window_pwa_object):
        if not window_pwa_object:
            self.logger.error("Invalid window object provided.")
            return []
        window_title = window_pwa_object.window_text()
        self.logger.info(f"Starting deep scan for all elements in window: '{window_title}'")
        perf_logger.info(f"Starting deep element scan for window: '{window_title}'")
        start_time = time.perf_counter()

        all_elements_data = []
        root_com_element = window_pwa_object.element_info.element
        self._walk_element_tree(root_com_element, 0, all_elements_data)
        
        end_time = time.perf_counter()
        self.logger.info(f"Scan complete. Collected {len(all_elements_data)} elements.")
        perf_logger.info(f"Deep element scan finished. Found {len(all_elements_data)} elements. Duration: {end_time - start_time:.4f}s")
        return all_elements_data

    def _walk_element_tree(self, element_com, level, all_elements_data, max_depth=25):
        if element_com is None or level > max_depth:
            return
        try:
            element_pwa = UIAWrapper(UIAElementInfo(element_com))
            element_data = core_logic.get_all_properties(element_pwa, self.uia, self.tree_walker)
            if element_data:
                element_data['sys_unique_id'] = id(element_com)
                parent_com = self.tree_walker.GetParentElement(element_com)
                element_data['sys_parent_id'] = id(parent_com) if parent_com else 0
                all_elements_data.append(element_data)

            child = self.tree_walker.GetFirstChildElement(element_com)
            while child:
                self._walk_element_tree(child, level + 1, all_elements_data, max_depth)
                try:
                    child = self.tree_walker.GetNextSiblingElement(child)
                except comtypes.COMError:
                    break
        except Exception as e:
            self.logger.warning(f"Error walking element tree at level {level}: {e}")

# ======================================================================
#                   GUI CLASS (Embeddable Frame)
# ======================================================================
class ExplorerTab(ttk.Frame):
    def __init__(self, parent, suite_app=None, status_label_widget=None, is_run_from_suite=False):
        super().__init__(parent)
        self.pack(fill="both", expand=True) 
        
        self.suite_app = suite_app
        self.is_run_from_suite = is_run_from_suite
        self.status_label = status_label_widget or getattr(suite_app, 'status_label', None)
        self.logger = logging.getLogger(self.__class__.__name__)

        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure("TLabel", font=('Segoe UI', 10))
        style.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=5)
        style.configure("TLabelframe.Label", font=('Segoe UI', 11, 'bold'))
        style.configure("Treeview.Heading", font=('Segoe UI', 10, 'bold'))
        style.configure("Copy.TButton", padding=2, font=('Segoe UI', 8))
        
        self.scanner = FullScanner()
        self.selected_window_data = None
        self.selected_element_data = None
        self.window_data_cache = []
        self.element_data_cache = []
        self.window_map = {}
        self.element_map = {}
        self.highlighter_window = None

        self.ELEMENT_COLUMNS = {
            'rel_level': ('Lvl', 40), 
            'pwa_title': ('Title/Name', 450),
            'pwa_control_type': ('Control Type', 150), 
            'pwa_auto_id': ('Automation ID', 150),
            'pwa_class_name': ('Class Name', 150), 
            'win32_handle': ('Handle', 100),
            'state_is_enabled': ('Enabled', 60), 
            'state_is_visible': ('Visible', 60)
        }
        self.create_widgets()

    def show_detail_window(self):
        if not self.selected_element_data:
            messagebox.showwarning("No Element Selected", "Please select an element from the table below.")
            return
        
        detail_win = tk.Toplevel(self)
        detail_win.title("Element Specification Details")
        detail_win.geometry("650x700+50+50")
        detail_win.transient(self)
        detail_win.grab_set()
        
        window_info = self.selected_window_data
        element_info = self.selected_element_data
        cleaned_element_info = core_logic.clean_element_spec(window_info, element_info)
        
        optimal_element_spec = core_logic.create_optimal_element_spec(element_info, self.element_data_cache)
        optimal_window_spec = core_logic.create_optimal_window_spec(window_info, self.window_data_cache)

        def send_specs(win_spec, elem_spec):
            # Kiểm tra xem có đang chạy trong suite không
            if self.is_run_from_suite and self.suite_app and hasattr(self.suite_app, 'send_specs_to_debugger'):
                self.suite_app.send_specs_to_debugger(win_spec, elem_spec)
                detail_win.destroy()
            else:
                # Nếu chạy độc lập, có thể hiển thị thông báo hoặc không làm gì
                messagebox.showinfo("Info", "This action is only available when run from the Automation Suite.")


        main_frame = ttk.Frame(detail_win, padding=10)
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(0, weight=1); main_frame.rowconfigure(1, weight=1)
        
        def copy_to_clipboard(content, button):
            detail_win.clipboard_clear(); detail_win.clipboard_append(content); detail_win.update()
            original_text = button.cget("text"); button.config(text="✅")
            detail_win.after(1500, lambda: button.config(text=original_text))

        # --- Full Spec Frame ---
        full_win_spec_str = core_logic.format_spec_to_string(window_info, "window_spec")
        full_elem_spec_str = core_logic.format_spec_to_string(cleaned_element_info, "element_spec")
        full_spec_frame = ttk.LabelFrame(main_frame, text="Full Specification (All Properties)", padding=(10, 5))
        full_spec_frame.grid(row=0, column=0, sticky="nsew", pady=5)
        full_spec_frame.columnconfigure(0, weight=1); full_spec_frame.rowconfigure(0, weight=1)
        full_text = tk.Text(full_spec_frame, wrap="word", font=("Courier New", 10))
        full_text.grid(row=0, column=0, sticky="nsew")
        full_text.insert("1.0", f"{full_win_spec_str}\n\n{full_elem_spec_str}"); full_text.config(state="disabled")
        full_btn_frame = ttk.Frame(full_spec_frame)
        full_btn_frame.place(relx=1.0, rely=0, x=-5, y=-11, anchor='ne')
        copy_full_btn = ttk.Button(full_btn_frame, text="📋 Copy All", style="Copy.TButton", command=lambda: copy_to_clipboard(full_text.get("1.0", "end-1c"), copy_full_btn))
        copy_full_btn.pack(side='left', padx=2)
        
        # --- THÊM NÚT SEND TO DEBUGGER ---
        if self.is_run_from_suite:
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

        # --- THÊM NÚT SEND TO DEBUGGER ---
        if self.is_run_from_suite:
            send_optimal_btn = ttk.Button(optimal_btn_frame, text="🚀 Send to Debugger", style="Copy.TButton", command=lambda: send_specs(optimal_window_spec, optimal_element_spec))
            send_optimal_btn.pack(side='left', padx=2)

    def create_widgets(self,):
        top_frame = ttk.Frame(self, padding=10)
        top_frame.pack(side='top', fill='x')
        self.scan_windows_btn = ttk.Button(top_frame, text="Scan All Windows", command=self.start_scan_windows)
        self.scan_windows_btn.pack(side='left', padx=(0, 10))
        self.scan_elements_btn = ttk.Button(top_frame, text="Scan Window's Elements", state="disabled", command=self.start_scan_elements)
        self.scan_elements_btn.pack(side='left', padx=10)
        self.detail_btn = ttk.Button(top_frame, text="View Element Details", state="disabled", command=self.show_detail_window)
        self.detail_btn.pack(side='left', padx=10)
        self.export_btn = ttk.Button(top_frame, text="Export to Excel...", state="disabled", command=self.export_to_excel)
        self.export_btn.pack(side='left', padx=10)
        main_paned_window = ttk.PanedWindow(self, orient='vertical')
        main_paned_window.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        windows_frame = self.create_windows_list_frame(main_paned_window)
        main_paned_window.add(windows_frame, weight=1)
        elements_frame = self.create_elements_list_frame(main_paned_window)
        main_paned_window.add(elements_frame, weight=2)

    def create_windows_list_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Running Windows List")
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        cols = ("title", "handle", "process")
        self.win_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.win_tree.heading("title", text="Title"); self.win_tree.heading("handle", text="Handle"); self.win_tree.heading("process", text="Process Name")
        self.win_tree.column("title", width=500); self.win_tree.column("handle", width=100, anchor='center'); self.win_tree.column("process", width=150)
        self.win_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.win_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.win_tree.configure(yscrollcommand=scrollbar.set)
        self.win_tree.bind("<<TreeviewSelect>>", self.on_window_select)
        return frame

    def create_elements_list_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Elements of Selected Window")
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        column_keys = list(self.ELEMENT_COLUMNS.keys())
        self.elem_tree = ttk.Treeview(frame, columns=column_keys, show="headings")
        for key in column_keys:
            display_name, width = self.ELEMENT_COLUMNS[key]
            anchor = 'center' if key in ['rel_level', 'win32_handle', 'state_is_enabled', 'state_is_visible'] else 'w'
            self.elem_tree.heading(key, text=display_name)
            self.elem_tree.column(key, width=width, anchor=anchor)
        self.elem_tree.grid(row=0, column=0, sticky="nsew")
        y_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.elem_tree.yview)
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        self.elem_tree.configure(yscrollcommand=y_scrollbar.set)
        x_scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=self.elem_tree.xview)
        x_scrollbar.grid(row=1, column=0, sticky="ew")
        self.elem_tree.configure(xscrollcommand=x_scrollbar.set)
        self.elem_tree.bind("<<TreeviewSelect>>", self.on_element_select)
        return frame

    def _scan_elements_thread(self):
        self.element_data_cache = self.scanner.get_all_elements_from_window(self.selected_window_data['pwa_object'])
        self.after(0, self.populate_elements_tree, self.element_data_cache)

    def populate_elements_tree(self, elements):
        self.clear_treeview(self.elem_tree)
        column_keys = list(self.ELEMENT_COLUMNS.keys())
        for elem_info in elements:
            indent = "    " * elem_info.get('rel_level', 0)
            values = []
            for key in column_keys:
                val = elem_info.get(key, '')
                if key == 'pwa_title': val = indent + str(val)
                elif isinstance(val, (list, tuple)): val = str(val)
                values.append(val)
            item_id = self.elem_tree.insert("", "end", values=tuple(values))
            self.element_map[item_id] = elem_info
        self.update_status(f"Explorer: Scan finished! Found {len(elements)} elements.")
        self.scan_windows_btn.config(state="normal"); self.scan_elements_btn.config(state="normal")
        if elements: self.export_btn.config(state="normal")

    def update_status(self, text):
        if self.status_label:
            self.status_label.config(text=text)

    def clear_treeview(self, tree):
        for item in tree.get_children():
            tree.delete(item)

    def start_scan_windows(self):
        self.scan_windows_btn.config(state="disabled"); self.scan_elements_btn.config(state="disabled")
        self.export_btn.config(state="disabled"); self.detail_btn.config(state="disabled")
        self.update_status("Explorer: Scanning all windows...")
        self.clear_treeview(self.win_tree); self.clear_treeview(self.elem_tree)
        self.window_map.clear(); self.element_map.clear(); self.window_data_cache = []
        threading.Thread(target=self._scan_windows_thread, daemon=True).start()

    def _scan_windows_thread(self):
        windows_data = self.scanner.get_all_windows()
        self.after(0, self.populate_windows_tree, windows_data)

    def populate_windows_tree(self, windows_data):
        self.clear_treeview(self.win_tree)
        self.window_data_cache = windows_data
        for win_info in windows_data:
            values = (win_info.get('pwa_title'), win_info.get('win32_handle'), win_info.get('proc_name'))
            item_id = self.win_tree.insert("", "end", values=values)
            self.window_map[item_id] = win_info
        self.update_status(f"Explorer: Found {len(windows_data)} windows. Please select one to scan for elements.")
        self.scan_windows_btn.config(state="normal")

    def on_window_select(self, event):
        selected_items = self.win_tree.selection()
        if not selected_items: return
        self.selected_window_data = self.window_map.get(selected_items[0])
        if self.selected_window_data:
            self.scan_elements_btn.config(state="normal")
            self.detail_btn.config(state="disabled")
            self.update_status(f"Explorer: Selected '{self.selected_window_data.get('pwa_title')}'. Ready to scan elements.")
        else:
            self.scan_elements_btn.config(state="disabled")

    def on_element_select(self, event):
        selected_items = self.elem_tree.selection()
        if not selected_items: return
        self.selected_element_data = self.element_map.get(selected_items[0])
        if self.selected_element_data:
            self.detail_btn.config(state="normal")
            self.update_status("Explorer: Element selected. Ready to view details.")
            rect = self.selected_element_data.get('geo_bounding_rect_tuple')
            if rect:
                self.draw_highlight(rect)
        else:
            self.detail_btn.config(state="disabled")

    def start_scan_elements(self):
        if not self.selected_window_data:
            messagebox.showwarning("No Window Selected", "Please select a window from the list first.")
            return
        
        window_object = self.selected_window_data.get('pwa_object')
        if not window_object:
            messagebox.showerror("Error", "Could not find the window object to scan.")
            return

        self.scan_windows_btn.config(state="disabled"); self.scan_elements_btn.config(state="disabled")
        self.export_btn.config(state="disabled"); self.detail_btn.config(state="disabled")
        self.update_status(f"Explorer: Scanning elements of '{self.selected_window_data.get('pwa_title')}'...")
        self.clear_treeview(self.elem_tree); self.element_map.clear()
        threading.Thread(target=self._scan_elements_thread, daemon=True).start()

    def export_to_excel(self):
        if not self.element_data_cache:
            messagebox.showinfo("No Data", "There is no element data to export.")
            return
        window_title = self.selected_window_data.get('pwa_title', 'ScannedWindow')
        sanitized_title = re.sub(r'[\\/:*?"<>|]', '_', window_title)[:50]
        initial_filename = f"Elements_{sanitized_title}_{time.strftime('%Y%m%d')}.xlsx"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],
            initialfile=initial_filename, title="Save Excel File"
        )
        if not file_path:
            self.update_status("Explorer: Export canceled.")
            return
        try:
            self.update_status(f"Explorer: Exporting to {os.path.basename(file_path)}...")
            df = pd.DataFrame(self.element_data_cache)
            if 'pwa_object' in df.columns:
                df = df.drop(columns=['pwa_object'])
            df.to_excel(file_path, index=False, engine='openpyxl')
            self.update_status("Explorer: Excel export successful!")
            messagebox.showinfo("Success", f"Data was successfully saved to:\n{file_path}")
        except Exception as e:
            self.update_status(f"Explorer: Error exporting file: {e}")
            messagebox.showerror("Error", f"Could not save the Excel file.\nError: {e}")

    def draw_highlight(self, rect_tuple):
        self.destroy_highlight()
        try:
            left, top, right, bottom = rect_tuple
            width = right - left
            height = bottom - top
            self.highlighter_window = tk.Toplevel(self)
            self.highlighter_window.overrideredirect(True)
            self.highlighter_window.wm_attributes("-topmost", True, "-disabled", True, "-transparentcolor", "white")
            self.highlighter_window.geometry(f'{width}x{height}+{left}+{top}')
            canvas = tk.Canvas(self.highlighter_window, bg='white', highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)
            canvas.create_rectangle(2, 2, width - 2, height - 2, outline="red", width=4)
            self.highlighter_window.after(2500, self.destroy_highlight)
        except Exception as e:
            logging.error(f"Error drawing highlight rectangle: {e}")

    def destroy_highlight(self):
        if self.highlighter_window and self.highlighter_window.winfo_exists():
            self.highlighter_window.destroy()
        self.highlighter_window = None

if __name__ == '__main__':
    # --- THÊM LOGIC PARSE ARGUMENT KHI CHẠY ĐỘC LẬP ---
    parser = argparse.ArgumentParser(description="Standalone Window Explorer Tool.")
    parser.add_argument('--from-suite', action='store_true', help='Indicates that the script is run from the Automation Suite.')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
    
    root = tk.Tk()
    root.title("Standalone Window Explorer")
    root.geometry("1200x800")
    
    status_frame = ttk.Frame(root, relief='sunken', padding=2)
    status_frame.pack(side='bottom', fill='x')
    ttk.Label(status_frame, text="© KNT15083").pack(side='right', padx=5)
    status_label = ttk.Label(status_frame, text="Ready (Standalone Mode)")
    status_label.pack(side='left', padx=5)
    
    # Truyền cờ is_run_from_suite vào ExplorerTab
    app_frame = ExplorerTab(root, status_label_widget=status_label, is_run_from_suite=args.from_suite)
    
    root.mainloop()
