# tool_explorer.py
# A standalone and embeddable tool for full window element scanning.
# --- VERSION 11.0 (Finalization & Advanced Caching):
# - Translated all UI components, messages, and comments into English.
# - Implemented an advanced caching mechanism ("Cached Scanning") for
#   "Full Load" mode using IUIAutomationCacheRequest.
# - This new method prefetches a batch of properties for all elements in a
#   single COM call, dramatically speeding up the full scan process by
#   reducing thousands of individual calls.
# - The "Quick Scan" (Lazy Load) mode remains the default for initial speed.

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

# ======================================================================
#                   SCANNER LOGIC CLASS (BACKEND)
# ======================================================================
class FullScanner:
    def __init__(self):
        self.desktop = Desktop(backend='uia')
        try:
            self.uia = comtypes.client.CreateObject(UIA.CUIAutomation)
            self.tree_walker = self.uia.ControlViewWalker
        except (OSError, comtypes.COMError):
            raise

    def get_all_windows(self):
        start_time = time.perf_counter()
        windows = self.desktop.windows()
        all_windows_data = []
        for win in windows:
            try:
                if win.is_visible():
                    info = core_logic.get_all_properties(win, self.uia, self.tree_walker)
                    info['pwa_object'] = win
                    info['sys_unique_id'] = id(win.element_info.element)
                    all_windows_data.append(info)
            except Exception:
                pass
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        return all_windows_data, duration

    def get_all_elements_from_window(self, window_pwa_object, max_depth=None, full_load=False, basic_keys=None):
        if not window_pwa_object:
            return [], 0
        
        start_time = time.perf_counter()
        all_elements_data = []
        root_com_element = window_pwa_object.element_info.element
        
        if full_load:
            # Chế độ quét toàn bộ được tối ưu hóa
            cache_request = self.uia.CreateCacheRequest()
            for prop_id in [UIA.UIA_NamePropertyId, UIA.UIA_AutomationIdPropertyId, UIA.UIA_ClassNamePropertyId, UIA.UIA_ControlTypePropertyId, UIA.UIA_IsEnabledPropertyId, UIA.UIA_IsOffscreenPropertyId, UIA.UIA_BoundingRectanglePropertyId, UIA.UIA_ProcessIdPropertyId, UIA.UIA_NativeWindowHandlePropertyId]:
                cache_request.AddProperty(prop_id)
            
            self._walk_element_tree_cached(root_com_element, 0, all_elements_data, max_depth, cache_request)
        else:
            # Chế độ quét nhanh như cũ
            self._walk_element_tree_lazy(root_com_element, 0, all_elements_data, max_depth, basic_keys or [])
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        return all_elements_data, duration

    def _walk_element_tree_lazy(self, element_com, level, all_elements_data, max_depth, basic_keys):
        if element_com is None or (max_depth is not None and level > max_depth):
            return
        try:
            element_pwa = UIAWrapper(UIAElementInfo(element_com))
            element_data = {}
            for key in basic_keys:
                value = core_logic.get_property_value(element_pwa, key, self.uia, self.tree_walker)
                if value or value is False or value == 0:
                    element_data[key] = value
            element_data['sys_is_partial'] = True

            if element_data:
                element_data['pwa_object'] = element_pwa
                element_data['sys_unique_id'] = id(element_com)
                all_elements_data.append(element_data)

            child = self.tree_walker.GetFirstChildElement(element_com)
            while child:
                self._walk_element_tree_lazy(child, level + 1, all_elements_data, max_depth, basic_keys)
                try:
                    child = self.tree_walker.GetNextSiblingElement(child)
                except comtypes.COMError:
                    break
        except Exception:
            pass

    def _walk_element_tree_cached(self, element_com, level, all_elements_data, max_depth, cache_request):
        if element_com is None or (max_depth is not None and level > max_depth):
            return
        try:
            # Tải element và các thuộc tính đã yêu cầu vào cache
            updated_element_com = element_com.BuildUpdatedCache(cache_request)
            element_pwa = UIAWrapper(UIAElementInfo(updated_element_com))
            
            # Lấy thông tin từ cache, nhanh hơn nhiều
            element_data = {
                'rel_level': level,
                'pwa_title': updated_element_com.Cached.Name,
                'pwa_auto_id': updated_element_com.Cached.AutomationId,
                'pwa_class_name': updated_element_com.Cached.ClassName,
                'pwa_control_type': core_logic._CONTROL_TYPE_ID_TO_NAME.get(updated_element_com.Cached.ControlType, 'Unknown'),
                'state_is_enabled': updated_element_com.Cached.IsEnabled,
                'state_is_visible': not updated_element_com.Cached.IsOffscreen,
                'win32_handle': updated_element_com.Cached.NativeWindowHandle,
            }
            element_data['pwa_object'] = element_pwa
            element_data['sys_unique_id'] = id(element_com)
            all_elements_data.append(element_data)

            child = self.tree_walker.GetFirstChildElement(element_com)
            while child:
                self._walk_element_tree_cached(child, level + 1, all_elements_data, max_depth, cache_request)
                try:
                    child = self.tree_walker.GetNextSiblingElement(child)
                except comtypes.COMError:
                    break
        except Exception:
            pass

# ======================================================================
#                   GUI CLASS (Embeddable Frame)
# ======================================================================
class ExplorerTab(ttk.Frame):
    def __init__(self, parent, suite_app=None, status_label_widget=None, is_run_from_suite=False):
        super().__init__(parent)
        self.pack(fill="both", expand=True) 
        
        self.suite_app = suite_app
        self.is_run_from_suite = is_run_from_suite
        self.status_label = status_label_widget
        
        if self.status_label:
            self.progress_bar = ttk.Progressbar(self.status_label.master, mode='indeterminate')
        else:
            self.progress_bar = None

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
        
        if self.selected_element_data.get('sys_is_partial'):
            self.update_status("Explorer: Loading full details for selected element...")
            pwa_object = self.selected_element_data.get('pwa_object')
            if pwa_object:
                full_properties = core_logic.get_all_properties(pwa_object, self.scanner.uia, self.scanner.tree_walker)
                self.selected_element_data.update(full_properties)
                self.selected_element_data.pop('sys_is_partial', None)
                unique_id = self.selected_element_data.get('sys_unique_id')
                for i, item in enumerate(self.element_data_cache):
                    if item.get('sys_unique_id') == unique_id:
                        self.element_data_cache[i] = self.selected_element_data
                        break
            else:
                messagebox.showerror("Error", "Could not find the element object to fetch details.")
                self.update_status("Explorer: Error loading details.")
                return
            self.update_status("Explorer: Full details loaded.")

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
            if self.is_run_from_suite and self.suite_app and hasattr(self.suite_app, 'send_specs_to_debugger'):
                self.suite_app.send_specs_to_debugger(win_spec, elem_spec)
                detail_win.destroy()
            else:
                messagebox.showinfo("Info", "This action is only available when run from the Automation Suite.")

        main_frame = ttk.Frame(detail_win, padding=10)
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(0, weight=1); main_frame.rowconfigure(1, weight=1)
        
        def copy_to_clipboard(content, button):
            detail_win.clipboard_clear(); detail_win.clipboard_append(content); detail_win.update()
            original_text = button.cget("text"); button.config(text="✅")
            detail_win.after(1500, lambda: button.config(text=original_text))

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
        
        if self.is_run_from_suite:
            send_full_btn = ttk.Button(full_btn_frame, text="🚀 Send to Debugger", style="Copy.TButton", command=lambda: send_specs(window_info, cleaned_element_info))
            send_full_btn.pack(side='left', padx=2)
        
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

        if self.is_run_from_suite:
            send_optimal_btn = ttk.Button(optimal_btn_frame, text="🚀 Send to Debugger", style="Copy.TButton", command=lambda: send_specs(optimal_window_spec, optimal_element_spec))
            send_optimal_btn.pack(side='left', padx=2)

    def create_widgets(self,):
        main_paned_window = ttk.PanedWindow(self, orient='vertical')
        main_paned_window.pack(fill="both", expand=True, padx=10, pady=10)
        
        top_frame = ttk.Frame(main_paned_window)
        main_paned_window.add(top_frame, weight=2)
        top_frame.columnconfigure(0, weight=1)
        top_frame.rowconfigure(1, weight=1)

        control_frame = ttk.Frame(top_frame, padding=10)
        control_frame.grid(row=0, column=0, sticky='ew')
        
        self.scan_windows_btn = ttk.Button(control_frame, text="Scan All Windows", command=self.start_scan_windows)
        self.scan_windows_btn.pack(side='left', padx=(0, 10))
        
        self.scan_elements_btn = ttk.Button(control_frame, text="Scan Window's Elements", state="disabled", command=self.start_scan_elements)
        self.scan_elements_btn.pack(side='left', padx=10)

        ttk.Label(control_frame, text="Max Depth:").pack(side='left', padx=(20, 5))
        self.max_depth_var = tk.StringVar()
        self.max_depth_entry = ttk.Entry(control_frame, textvariable=self.max_depth_var, width=5)
        self.max_depth_entry.pack(side='left')
        
        self.full_load_var = tk.BooleanVar(value=False)
        self.full_load_check = ttk.Checkbutton(control_frame, text="Load full details on scan (slower)", variable=self.full_load_var)
        self.full_load_check.pack(side='left', padx=(10, 0))

        self.detail_btn = ttk.Button(control_frame, text="View Element Details", state="disabled", command=self.show_detail_window)
        self.detail_btn.pack(side='right', padx=10)
        
        self.export_btn = ttk.Button(control_frame, text="Export to Excel...", state="disabled", command=self.export_to_excel)
        self.export_btn.pack(side='right', padx=10)

        windows_frame = self.create_windows_list_frame(top_frame)
        windows_frame.grid(row=1, column=0, sticky='nsew')
        
        elements_frame = self.create_elements_list_frame(main_paned_window)
        main_paned_window.add(elements_frame, weight=3)

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

    def start_loading(self, message="Scanning..."):
        if not self.progress_bar: return
        self.update_status(message)
        self.progress_bar.pack(side='left', fill='x', expand=True, padx=10)
        self.progress_bar.start(10)

    def stop_loading(self):
        if not self.progress_bar: return
        self.progress_bar.stop()
        self.progress_bar.pack_forget()

    def _scan_elements_thread(self, max_depth, full_load, basic_keys):
        results, duration = self.scanner.get_all_elements_from_window(
            self.selected_window_data['pwa_object'], max_depth, full_load, basic_keys
        )
        self.element_data_cache = results
        self.after(0, self.populate_elements_tree, results, duration)

    def populate_elements_tree(self, elements, duration):
        self.stop_loading()
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
        
        self.update_status(f"Explorer: Found {len(elements)} elements in {duration:.2f}s.")
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
        self.start_loading("Explorer: Scanning all windows...")
        self.clear_treeview(self.win_tree); self.clear_treeview(self.elem_tree)
        self.window_map.clear(); self.element_map.clear(); self.window_data_cache = []
        threading.Thread(target=self._scan_windows_thread, daemon=True).start()

    def _scan_windows_thread(self):
        windows_data, duration = self.scanner.get_all_windows()
        self.after(0, self.populate_windows_tree, windows_data, duration)

    def populate_windows_tree(self, windows_data, duration):
        self.stop_loading()
        self.clear_treeview(self.win_tree)
        self.window_data_cache = windows_data
        for win_info in windows_data:
            values = (win_info.get('pwa_title'), win_info.get('win32_handle'), win_info.get('proc_name'))
            item_id = self.win_tree.insert("", "end", values=values)
            self.window_map[item_id] = win_info
            
        self.update_status(f"Explorer: Found {len(windows_data)} windows in {duration:.2f}s. Select one to scan for elements.")
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
            
            # Luôn tìm 'pwa_object' để lấy rect, vì nó luôn có sẵn
            pwa_object = self.selected_element_data.get('pwa_object')
            if pwa_object:
                try:
                    self.draw_highlight(pwa_object.rectangle())
                except Exception:
                    pass # Bỏ qua nếu không thể vẽ
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

        max_depth_str = self.max_depth_var.get()
        max_depth = None
        if max_depth_str:
            try:
                max_depth = int(max_depth_str)
                if max_depth < 0:
                    messagebox.showerror("Invalid Input", "Max Depth must be a non-negative number.")
                    return
            except ValueError:
                messagebox.showerror("Invalid Input", "Max Depth must be a valid number.")
                return

        self.scan_windows_btn.config(state="disabled"); self.scan_elements_btn.config(state="disabled")
        self.export_btn.config(state="disabled"); self.detail_btn.config(state="disabled")
        self.start_loading(f"Explorer: Scanning elements of '{self.selected_window_data.get('pwa_title')}'...")
        self.clear_treeview(self.elem_tree); self.element_map.clear()
        
        full_load = self.full_load_var.get()
        
        basic_keys = list(self.ELEMENT_COLUMNS.keys())
        if 'geo_bounding_rect_tuple' not in basic_keys:
            basic_keys.append('geo_bounding_rect_tuple')

        threading.Thread(target=self._scan_elements_thread, args=(max_depth, full_load, basic_keys), daemon=True).start()

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
            export_data = []
            for row in self.element_data_cache:
                new_row = {k: v for k, v in row.items() if not k.startswith('sys_') and k != 'pwa_object'}
                export_data.append(new_row)
            
            self.update_status(f"Explorer: Exporting to {os.path.basename(file_path)}...")
            df = pd.DataFrame(export_data)
            df.to_excel(file_path, index=False, engine='openpyxl')
            self.update_status("Explorer: Excel export successful!")
            messagebox.showinfo("Success", f"Data was successfully saved to:\n{file_path}")
        except Exception as e:
            self.update_status(f"Explorer: Error exporting file: {e}")
            messagebox.showerror("Error", f"Could not save the Excel file.\nError: {e}")

    def draw_highlight(self, rect):
        self.destroy_highlight()
        try:
            # rect giờ là đối tượng Rectangle của pywinauto
            self.highlighter_window = tk.Toplevel(self)
            self.highlighter_window.overrideredirect(True)
            self.highlighter_window.wm_attributes("-topmost", True, "-disabled", True, "-transparentcolor", "white")
            self.highlighter_window.geometry(f'{rect.width()}x{rect.height()}+{rect.left}+{rect.top}')
            canvas = tk.Canvas(self.highlighter_window, bg='white', highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)
            canvas.create_rectangle(2, 2, rect.width() - 2, rect.height() - 2, outline="red", width=4)
            self.highlighter_window.after(2500, self.destroy_highlight)
        except Exception:
            pass

    def destroy_highlight(self):
        if self.highlighter_window and self.highlighter_window.winfo_exists():
            self.highlighter_window.destroy()
        self.highlighter_window = None

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Standalone Window Explorer Tool.")
    parser.add_argument('--from-suite', action='store_true', help='Indicates that the script is run from the Automation Suite.')
    args = parser.parse_args()
    
    root = tk.Tk()
    root.title("Standalone Window Explorer")
    root.geometry("1200x800")
    
    status_frame = ttk.Frame(root, relief='sunken', padding=2)
    status_frame.pack(side='bottom', fill='x')
    
    ttk.Label(status_frame, text="© KNT15083").pack(side='right', padx=5)
    
    status_label = ttk.Label(status_frame, text="Ready (Standalone Mode)")
    status_label.pack(side='left', padx=5)
    
    app_frame = ExplorerTab(root, status_label_widget=status_label, is_run_from_suite=args.from_suite)
    
    root.mainloop()
