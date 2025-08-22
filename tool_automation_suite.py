# tool_automation_suite.py
# Version 5.1 (Final Usability Tweaks):
# - Replaced the double-click-to-show-example functionality with a dedicated
#   "Show Example" button to avoid conflict with treeview expansion.
# - The button is enabled only when a valid method is selected in the API tree.
# - Updated the default selected properties in the "Interactive Scan" tab
#   based on user feedback for a more practical starting configuration.
# --- VERSION 5.2 (Performance Logging Integration) ---
# - Tích hợp hệ thống ghi log hiệu suất tập trung.
# - Gọi performance_logger.setup_logger() ngay khi khởi động.

import tkinter as tk
from tkinter import ttk, font, messagebox, scrolledtext
import logging
import sys

# --- Tích hợp logger hiệu suất ---
try:
    import performance_logger
except ImportError:
    print("WARNING: performance_logger.py not found. Performance logging will be disabled.")
    performance_logger = None


# --- Import refactored tool components ---
try:
    from tool_explorer import ExplorerTab
    from tool_debugger import DebuggerTab
    from tool_scanner import ScannerApp, ALL_QUICK_SPEC_OPTIONS
    from core_logic import (
        PARAMETER_DEFINITIONS,
        OPERATOR_DEFINITIONS,
        SELECTOR_DEFINITIONS
    )
except ImportError as e:
    print(f"CRITICAL ERROR: Could not import a required tool module: {e}")
    print("Please ensure tool_explorer.py, tool_debugger.py, tool_scanner.py, and core_logic.py are in the same folder.")
    sys.exit(1)

# ======================================================================
#                         EXAMPLE DIALOG
# ======================================================================

class ExampleDialog(tk.Toplevel):
    """A pop-up window to display a code example for a specific method."""
    def __init__(self, parent, title, code_example):
        super().__init__(parent)
        self.title(f"Example: {title}")
        self.geometry("650x500")
        self.transient(parent)
        self.grab_set()

        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        header_label = ttk.Label(main_frame, text=f"Usage Example for `{title}`", font=('Segoe UI', 12, 'bold'))
        header_label.pack(anchor='w', pady=(0, 10))

        self.code_text = scrolledtext.ScrolledText(main_frame, wrap="word", font=("Courier New", 10), height=15)
        self.code_text.pack(fill="both", expand=True)
        self.code_text.insert("1.0", code_example)
        self.code_text.config(state="disabled")

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x", pady=(10, 0))

        self.copy_button = ttk.Button(button_frame, text="Copy Code", command=self._copy_to_clipboard)
        self.copy_button.pack(side="right")

        close_button = ttk.Button(button_frame, text="Close", command=self.destroy)
        close_button.pack(side="right", padx=(0, 10))

    def _copy_to_clipboard(self):
        self.clipboard_clear()
        self.clipboard_append(self.code_text.get("1.0", "end-1c"))
        self.update()
        original_text = self.copy_button.cget("text")
        self.copy_button.config(text="✅ Copied!")
        self.after(2000, lambda: self.copy_button.config(text=original_text))

# ======================================================================
#                         SCANNER TAB
# ======================================================================

# NEW: Updated default properties for the interactive scanner
SCANNER_DEFAULT_QUICK_SPEC = {
    'pwa_title', 'pwa_class_name', 'pwa_control_type',
    'win32_handle',
    'proc_name', 'proc_path', 'proc_username',
    'rel_level', 'rel_parent_title', 'rel_labeled_by',
    'uia_value'
}

class ScannerConfigTab(ttk.Frame):
    """A tab to configure and launch the interactive scanner."""
    def __init__(self, parent, suite_app):
        super().__init__(parent)
        self.suite_app = suite_app
        self.pack(fill="both", expand=True, padx=20, pady=20)
        self.config_vars = {}
        style = ttk.Style(self)
        style.configure("TLabel", font=('Segoe UI', 10))
        style.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=5)
        style.configure("TLabelframe.Label", font=('Segoe UI', 11, 'bold'))
        info_label = ttk.Label(self, text="Select properties for the 'Quick Spec', then launch the scanner.", wraplength=400, justify='center', font=('Segoe UI', 11))
        info_label.pack(pady=(0, 20), fill='x')
        options_container = ttk.LabelFrame(self, text="Element Properties for Quick Spec")
        options_container.pack(fill="both", expand=True, pady=5)
        num_columns = 3
        for i, option in enumerate(ALL_QUICK_SPEC_OPTIONS):
            row, col = i // num_columns, i % num_columns
            # UPDATED: Use the new default set
            is_default = option in SCANNER_DEFAULT_QUICK_SPEC
            var = tk.BooleanVar(value=is_default)
            self.config_vars[option] = var
            cb = ttk.Checkbutton(options_container, text=option, variable=var)
            cb.grid(row=row, column=col, sticky="w", padx=10, pady=4)
        start_button = ttk.Button(self, text="Launch Interactive Scan", command=self.launch_scanner, style="TButton")
        start_button.pack(pady=20, ipady=10, ipadx=20)

    def launch_scanner(self):
        selected_keys = [key for key, var in self.config_vars.items() if var.get()]
        if not selected_keys:
            messagebox.showwarning("No Selection", "Please select at least one property for the Quick Spec.")
            return
        try:
            self.suite_app.withdraw()
            scanner_app = ScannerApp(suite_app=self.suite_app, quick_spec_keys=selected_keys)
            scanner_app.wait_window()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch scanner: {e}")
        finally:
            self.suite_app.deiconify()
            self.suite_app.focus_force()

# ======================================================================
#                         REFERENCE TAB (RESTRUCTURED)
# ======================================================================

class ReferenceTab(ttk.Frame):
    """A restructured, multi-tabbed reference guide for the framework."""
    def __init__(self, parent):
        super().__init__(parent)
        self.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Copy", command=self._copy_cell_value)
        self.clicked_tree, self.clicked_item, self.clicked_column_id = None, None, None

        style = ttk.Style(self)
        style.configure("Ref.TNotebook.Tab", font=('Segoe UI', 9, 'bold'), padding=[8, 4])
        self.notebook = ttk.Notebook(self, style="Ref.TNotebook")
        self.notebook.pack(fill="both", expand=True)

        self.create_selector_ref_tab()
        self.create_api_ref_tab()
        self.create_integration_ref_tab()

    def _show_context_menu(self, event, tree):
        item_id = tree.identify_row(event.y)
        if not item_id: return
        tree.selection_set(item_id)
        self.clicked_tree, self.clicked_item, self.clicked_column_id = tree, item_id, tree.identify_column(event.x)
        self.context_menu.post(event.x_root, event.y_root)

    def _copy_cell_value(self):
        if not all([self.clicked_tree, self.clicked_item, self.clicked_column_id]): return
        try:
            column_index = int(self.clicked_column_id.replace('#', '')) - 1
            if column_index < 0: return
            value_to_copy = self.clicked_tree.item(self.clicked_item).get('values')[column_index]
            if value_to_copy:
                self.clipboard_clear(); self.clipboard_append(str(value_to_copy)); self.update()
        except (ValueError, IndexError) as e: logging.error(f"Error copying from treeview: {e}")

    # --- Sub-Tab 1: Selector Reference ---
    def create_selector_ref_tab(self):
        tab_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab_frame, text=" Selector Reference ")
        main_pane = ttk.PanedWindow(tab_frame, orient='vertical')
        main_pane.pack(fill='both', expand=True)
        params_frame = ttk.LabelFrame(main_pane, text="Parameters (for filtering)")
        main_pane.add(params_frame, weight=3)
        self.create_parameters_table(params_frame)
        self.populate_parameters_data()
        operators_frame = ttk.LabelFrame(main_pane, text="Operators (for comparisons)")
        main_pane.add(operators_frame, weight=2)
        self.create_operators_table(operators_frame)
        self.populate_operators_data()
        selectors_frame = ttk.LabelFrame(main_pane, text="Selectors & Sorting Keys")
        main_pane.add(selectors_frame, weight=2)
        self.create_selectors_table(selectors_frame)
        self.populate_selectors_data()

    # --- Sub-Tab 2: Framework API Reference ---
    def create_api_ref_tab(self):
        tab_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab_frame, text=" Framework API ")
        
        # Main container
        api_container = ttk.Frame(tab_frame)
        api_container.pack(fill='both', expand=True)
        api_container.rowconfigure(1, weight=1)
        api_container.columnconfigure(0, weight=1)

        # Top bar for buttons
        top_bar = ttk.Frame(api_container)
        top_bar.grid(row=0, column=0, sticky='ew', pady=(0, 5))
        
        self.show_example_button = ttk.Button(top_bar, text="Show Example", state="disabled", command=self._show_selected_example)
        self.show_example_button.pack(side='left')

        # Treeview frame
        api_frame = ttk.LabelFrame(api_container, text="Main Classes and Methods")
        api_frame.grid(row=1, column=0, sticky='nsew')
        api_frame.columnconfigure(0, weight=1); api_frame.rowconfigure(0, weight=1)
        
        cols = ("Name", "Type / Default", "Description")
        self.api_tree = ttk.Treeview(api_frame, columns=cols, show="headings")
        self.api_tree.heading("Name", text="Name"); self.api_tree.heading("Type / Default", text="Type / Default"); self.api_tree.heading("Description", text="Description")
        self.api_tree.column("Name", width=250, anchor='w'); self.api_tree.column("Type / Default", width=200, anchor='w'); self.api_tree.column("Description", width=500, anchor='w')
        v_scrollbar = ttk.Scrollbar(api_frame, orient="vertical", command=self.api_tree.yview)
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.api_tree.configure(yscrollcommand=v_scrollbar.set)
        self.api_tree.grid(row=0, column=0, sticky="nsew")
        self.api_tree.bind("<Button-3>", lambda e: self._show_context_menu(e, self.api_tree))
        self.api_tree.bind("<<TreeviewSelect>>", self._on_api_selection_change)
        self.populate_api_data()

    # --- Sub-Tab 3: UI Tools & Integration ---
    def create_integration_ref_tab(self):
        tab_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab_frame, text=" UI Tools & Integration ")
        main_pane = ttk.PanedWindow(tab_frame, orient='vertical')
        main_pane.pack(fill='both', expand=True)
        notifier_frame = ttk.LabelFrame(main_pane, text="StatusNotifier: Real-time User Feedback")
        main_pane.add(notifier_frame, weight=1)
        notifier_text = scrolledtext.ScrolledText(notifier_frame, wrap="word", font=("Courier New", 9), height=10)
        notifier_text.pack(fill="both", expand=True, padx=5, pady=5)
        notifier_text.insert("1.0", API_EXAMPLES["StatusNotifier"])
        notifier_text.config(state="disabled")
        control_panel_frame = ttk.LabelFrame(main_pane, text="AutomationControlPanel: Pause/Stop Control")
        main_pane.add(control_panel_frame, weight=1)
        panel_text = scrolledtext.ScrolledText(control_panel_frame, wrap="word", font=("Courier New", 9), height=10)
        panel_text.pack(fill="both", expand=True, padx=5, pady=5)
        panel_text.insert("1.0", API_EXAMPLES["AutomationControlPanel"])
        panel_text.config(state="disabled")

    # --- Treeview Creation and Population Methods ---
    def create_parameters_table(self, parent):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(0, weight=1)
        self.params_tree = ttk.Treeview(parent, columns=("Parameter", "Description"), show="headings")
        self.params_tree.heading("Parameter", text="Parameter Name"); self.params_tree.heading("Description", text="Description")
        self.params_tree.column("Parameter", width=250, anchor='w'); self.params_tree.column("Description", width=600, anchor='w')
        v_scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.params_tree.yview)
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.params_tree.configure(yscrollcommand=v_scrollbar.set)
        self.params_tree.grid(row=0, column=0, sticky="nsew")
        self.params_tree.bind("<Button-3>", lambda e: self._show_context_menu(e, self.params_tree))

    def populate_parameters_data(self):
        categorized_params = {
            "Search Modifiers": {'max_depth', 'search_direction'},
            "Advanced Search": {'ancestor', 'within_rect', 'to_right_of', 'to_left_of', 'above', 'below'},
            "PWA": "pwa_", "State": "state_", "Geometry": "geo_", "Relational": "rel_",
            "Process": "proc_", "UIA Patterns": "uia_", "WIN32": "win32_",
        }
        params_by_category = {cat: [] for cat in categorized_params}
        uncategorized = []
        all_params = sorted(PARAMETER_DEFINITIONS.items())
        for param, desc in all_params:
            if param.startswith('sys_'): continue
            assigned = False
            for cat_name, matcher in categorized_params.items():
                if isinstance(matcher, set):
                    if param in matcher:
                        params_by_category[cat_name].append((param, desc)); assigned = True; break
                elif isinstance(matcher, str) and param.startswith(matcher):
                    params_by_category[cat_name].append((param, desc)); assigned = True; break
            if not assigned: uncategorized.append((param, desc))
        for cat_name in categorized_params:
            param_list = params_by_category.get(cat_name)
            if not param_list: continue
            category_id = self.params_tree.insert("", "end", values=(f"--- {cat_name} ---", ""), open=False, tags=('category',))
            for param, desc in sorted(param_list):
                self.params_tree.insert(category_id, "end", values=(param, desc))
        if uncategorized:
            category_id = self.params_tree.insert("", "end", values=(f"--- Other Properties ---", ""), open=False, tags=('category',))
            for param, desc in sorted(uncategorized):
                self.params_tree.insert(category_id, "end", values=(param, desc))
        self.params_tree.tag_configure('category', background='#d3d3d3', foreground='black', font=('Segoe UI', 10, 'bold'))

    def create_operators_table(self, parent):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(0, weight=1)
        self.operators_tree = ttk.Treeview(parent, columns=("Operator", "Example", "Description"), show="headings")
        self.operators_tree.heading("Operator", text="Operator"); self.operators_tree.heading("Example", text="Example Usage"); self.operators_tree.heading("Description", text="Description")
        self.operators_tree.column("Operator", width=120, anchor='w'); self.operators_tree.column("Example", width=350, anchor='w'); self.operators_tree.column("Description", width=400, anchor='w')
        v_scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.operators_tree.yview)
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.operators_tree.configure(yscrollcommand=v_scrollbar.set)
        self.operators_tree.grid(row=0, column=0, sticky="nsew")
        self.operators_tree.bind("<Button-3>", lambda e: self._show_context_menu(e, self.operators_tree))

    def populate_operators_data(self):
        categories = {}
        for op in OPERATOR_DEFINITIONS:
            cat = op['category']
            if cat not in categories: categories[cat] = []
            categories[cat].append(op)
        for cat_name, op_list in categories.items():
            category_id = self.operators_tree.insert("", "end", values=(f"--- {cat_name} Operators ---", "", ""), open=False, tags=('category',))
            for op in op_list:
                self.operators_tree.insert(category_id, "end", values=(op['name'], op['example'], op['desc']))
        self.operators_tree.tag_configure('category', background='#d3d3d3', foreground='black', font=('Segoe UI', 10, 'bold'))

    def create_selectors_table(self, parent):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(0, weight=1)
        self.selectors_tree = ttk.Treeview(parent, columns=("Selector", "Example", "Description"), show="headings")
        self.selectors_tree.heading("Selector", text="Selector Key"); self.selectors_tree.heading("Example", text="Example Usage"); self.selectors_tree.heading("Description", text="Description")
        self.selectors_tree.column("Selector", width=180, anchor='w'); self.selectors_tree.column("Example", width=320, anchor='w'); self.selectors_tree.column("Description", width=400, anchor='w')
        v_scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.selectors_tree.yview)
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.selectors_tree.configure(yscrollcommand=v_scrollbar.set)
        self.selectors_tree.grid(row=0, column=0, sticky="nsew")
        self.selectors_tree.bind("<Button-3>", lambda e: self._show_context_menu(e, self.selectors_tree))

    def populate_selectors_data(self):
        self.selectors_tree.tag_configure('recommended', background='#d8e9d8', font=('Segoe UI', 9, 'bold'))
        for selector in SELECTOR_DEFINITIONS:
            tags = ('recommended',) if 'RECOMMENDED' in selector['desc'] else ()
            self.selectors_tree.insert("", "end", values=(selector['name'], selector['example'], selector['desc']), tags=tags)
    
    def populate_api_data(self):
        self.api_tree.tag_configure('class', background='#333', foreground='white', font=('Segoe UI', 10, 'bold'))
        self.api_tree.tag_configure('method', font=('Segoe UI', 9, 'bold'))
        self.api_tree.tag_configure('param', foreground='#555')
        for class_info in API_REFERENCE_DATA:
            class_id = self.api_tree.insert("", "end", values=(class_info["class"], "", ""), open=False, tags=('class',))
            for method_info in class_info["methods"]:
                method_id = self.api_tree.insert(class_id, "end", values=("  " + method_info["name"], "", method_info["desc"]), open=False, tags=('method',))
                for p_name, p_type, p_desc in method_info["params"]:
                    self.api_tree.insert(method_id, "end", values=("    " + p_name, p_type, p_desc), tags=('param',))

    def _on_api_selection_change(self, event):
        selected_items = self.api_tree.selection()
        if not selected_items:
            self.show_example_button.config(state="disabled")
            return
        
        item_id = selected_items[0]
        tags = self.api_tree.item(item_id, "tags")
        
        # Enable button only if a method or its parameter is selected
        if 'method' in tags or 'param' in tags:
            self.show_example_button.config(state="normal")
        else:
            self.show_example_button.config(state="disabled")

    def _show_selected_example(self):
        selected_items = self.api_tree.selection()
        if not selected_items: return
        
        item_id = selected_items[0]
        parent_id = self.api_tree.parent(item_id)
        tags = self.api_tree.item(item_id, "tags")
        
        method_name_raw = ""
        if 'method' in tags:
            method_name_raw = self.api_tree.item(item_id, "values")[0].strip()
        elif 'param' in tags and parent_id:
             method_name_raw = self.api_tree.item(parent_id, "values")[0].strip()

        method_name = method_name_raw.split('(')[0]
        if method_name in API_EXAMPLES:
            ExampleDialog(self, method_name, API_EXAMPLES[method_name])
        else:
            messagebox.showinfo("No Example", f"No detailed example is available for '{method_name}'.")

# ======================================================================
#                         MAIN APPLICATION
# ======================================================================

class AutomationSuiteApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Automation Suite v5.2 (by KNT15083)")
        self.geometry("1200x800")
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure("TNotebook.Tab", font=('Segoe UI', 10, 'bold'), padding=[10, 5])
        style.configure("TButton", padding=5)
        style.configure("TLabelframe.Label", font=('Segoe UI', 11, 'bold'))
        style.configure("Treeview.Heading", font=('Segoe UI', 10, 'bold'))
        self.create_widgets()

    def create_widgets(self):
        status_frame = ttk.Frame(self, relief='sunken', padding=2)
        status_frame.pack(side='bottom', fill='x')
        ttk.Label(status_frame, text="© KNT15083").pack(side='right', padx=5)
        self.status_label = ttk.Label(status_frame, text="Welcome to the Automation Suite!")
        self.status_label.pack(side='left', padx=5)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.explorer_tab = ExplorerTab(self.notebook, suite_app=self)
        self.scanner_tab = ScannerConfigTab(self.notebook, suite_app=self)
        self.debugger_tab = DebuggerTab(self.notebook, suite_app=self)
        self.reference_tab = ReferenceTab(self.notebook)
        self.notebook.add(self.explorer_tab, text=" Window Explorer ")
        self.notebook.add(self.scanner_tab, text=" Interactive Scan ")
        self.notebook.add(self.debugger_tab, text=" Selector Debugger ")
        self.notebook.add(self.reference_tab, text=" All-in-One Reference ")

    def send_specs_to_debugger(self, window_spec, element_spec):
        if self.debugger_tab:
            self.debugger_tab.receive_specs(window_spec, element_spec)
            self.notebook.select(self.debugger_tab)
            self.status_label.config(text="Specifications received in Debugger.")
        else:
            messagebox.showerror("Error", "Debugger tab is not available.")

# ======================================================================
#                         API REFERENCE DATA
# ======================================================================

API_REFERENCE_DATA = [
    {"class": "UIController", "methods": [
        {"name": "find_element(...)", "desc": "Finds a single element and returns its object for reuse.", "params": [
            ("window_spec", "dict", "Spec to find the parent window."),
            ("element_spec", "dict | None", "Spec to find the element within the window."),
            ("timeout", "float | None", "Override default timeout."),
            ("retry_interval", "float | None", "Override default retry interval."),
        ]},
        {"name": "create_snapshot(...)", "desc": "Scans a window once to find multiple elements.", "params": [
            ("window_spec", "dict", "Spec to find the parent window."),
            ("elements_map", "dict", "Map of friendly names to element specs."),
            ("timeout", "float | None", "Override default timeout."),
        ]},
        {"name": "run_action(...)", "desc": "Performs an action. Skips scan if `target` is provided.", "params": [
            ("action", "str", "Action to perform (e.g., 'click', 'type_keys:text')."),
            ("target", "UIAWrapper | None", "A pre-found element object to act upon."),
            ("window_spec", "dict | None", "Used if `target` is not provided."),
            ("element_spec", "dict | None", "Used if `target` is not provided."),
            ("delay_before", "float = 0", "Pause in seconds before the action."),
            ("delay_after", "float = 0", "Pause in seconds after the action."),
        ]},
        {"name": "wait_for_state(...)", "desc": "Waits for an element to reach a specific state.", "params": [
            ("state_spec", "dict", "The desired state (e.g., {'state_is_enabled': True})."),
            ("target", "UIAWrapper | None", "A pre-found element to monitor."),
            ("window_spec", "dict | None", "Used to find the element if `target` is not provided."),
            ("element_spec", "dict | None", "Used to find the element if `target` is not provided."),
            ("timeout", "float | None", "Max time to wait for the state."),
        ]},
    ]},
    {"class": "AppManager", "methods": [
        {"name": "launch(...)", "desc": "Launches an application from a command line.", "params": [
            ("wait_ready", "bool = True", "Wait for the main window to appear after launch."),
            ("timeout", "int | None", "Override default timeout for waiting."),
        ]},
        {"name": "attach(...)", "desc": "Attaches to a running application instance.", "params": [
            ("timeout", "int | None", "Override default timeout."),
            ("on_conflict", "str = 'fail'", "Policy for multiple instances ('newest', 'relaunch', etc.)."),
            ("attach_timeout", "int = 3", "Short timeout to find an existing instance."),
        ]},
    ]},
    {"class": "ImageController", "methods": [
        {"name": "image_action(...)", "desc": "Finds an image and performs an action on it.", "params": [
            ("image_target", "str | list", "Path(s) to the image file(s)."),
            ("action", "str", "Action to perform (e.g., 'click')."),
            ("region", "tuple | None", "Optional (left, top, width, height) area to search in."),
            ("confidence", "float | None", "Override default confidence (0.0 to 1.0)."),
        ]},
    ]}
]

API_EXAMPLES = {
    "find_element": """# Find a single element and store it in a variable for later use.
# This is more efficient than re-scanning for every action.

# Define specs for the window and the target element
window_spec = {'pwa_title': 'File Explorer'}
save_button_spec = {'pwa_title': 'Save', 'pwa_control_type': 'Button'}

# Find the element once
save_button = controller.find_element(
    window_spec=window_spec,
    element_spec=save_button_spec,
    timeout=10
)

# Reuse the found element object for multiple actions without re-scanning
if save_button:
    controller.run_action(target=save_button, action='focus')
    controller.run_action(target=save_button, action='click')""",

    "create_snapshot": """# Create a "snapshot" of a static UI screen to interact with
# multiple elements very quickly. This performs only ONE scan.

# 1. Define all elements of interest on the login screen
login_elements_map = {
    'user_field': {'pwa_auto_id': 'usernameInput'},
    'pass_field': {'pwa_auto_id': 'passwordInput'},
    'login_btn':  {'pwa_title': 'Login', 'pwa_control_type': 'Button'}
}

# 2. Scan the window once to capture all defined elements
login_screen = controller.create_snapshot(
    window_spec={'pwa_title': 'Login Window'},
    elements_map=login_elements_map
)

# 3. Interact with the captured elements instantly
if login_screen:
    controller.run_action(target=login_screen['user_field'], action='type_keys:admin')
    controller.run_action(target=login_screen['pass_field'], action='type_keys:password123')
    controller.run_action(target=login_screen['login_btn'], action='click')""",

    "run_action": """# `run_action` is a versatile method.
# It can either find an element and act, or act on a pre-found `target`.

# --- Method 1: Find and Act (slower, good for single actions) ---
controller.run_action(
    window_spec={'pwa_title': 'Notepad'},
    element_spec={'pwa_control_type': 'Edit'},
    action='type_keys:Hello World!',
    delay_after=0.5  # Wait 0.5s after typing
)

# --- Method 2: Act on a Target (faster, best for multiple actions) ---
# (See `find_element` or `create_snapshot` examples)
# Assume `save_button` is a pre-found element object
controller.run_action(target=save_button, action='click')""",

    "wait_for_state": """# Use `wait_for_state` to synchronize your script with the application's state.
# It's more reliable and efficient than `time.sleep()`.

# Find the process button first
process_button = controller.find_element(
    window_spec={'pwa_title': 'Data Processor'},
    element_spec={'pwa_title': 'Process'}
)

if process_button:
    # Click the button, which starts a long process and disables the button
    controller.run_action(target=process_button, action='click')

    # Now, wait for the button to become enabled again, which indicates
    # that the process is complete.
    print("Waiting for process to complete...")
    success = controller.wait_for_state(
        target=process_button,
        state_spec={'state_is_enabled': True},
        timeout=60  # Wait for up to 60 seconds
    )

    if success:
        print("Process finished successfully!")
    else:
        print("Process timed out or failed.")""",
    
    "StatusNotifier": """# --- How to use StatusNotifier ---
from ui_notifier import StatusNotifier
import time

# 1. Initialize the notifier (usually at the start of your script)
notifier = StatusNotifier()

# 2. Use it to provide feedback throughout your script
notifier.update_status("Starting automation...", style='process')
time.sleep(2)

try:
    # Simulate a successful operation
    notifier.update_status("Data saved successfully!", style='success', duration=5)
    time.sleep(6)

    # Simulate an error
    raise ValueError("Network connection lost")

except Exception as e:
    notifier.update_status(f"An error occurred: {e}", style='error', duration=0) # duration=0 means it stays until dismissed
    time.sleep(10)

finally:
    # 3. Stop the notifier's GUI thread at the end
    notifier.stop()
""",

    "AutomationControlPanel": """# --- How to use the Control Panel ---
from ui_notifier import StatusNotifier
from ui_control_panel import AutomationState, AutomationControlPanel
from core_controller import UIController
import time

# 1. Initialize the state management objects
notifier = StatusNotifier()
automation_state = AutomationState()
control_panel = AutomationControlPanel(automation_state, notifier)

# 2. Pass the `automation_state` to the controller
controller = UIController(
    notifier=notifier,
    automation_state=automation_state
)

# 3. Your automation logic will now be pausable/stoppable
for i in range(10):
    # The controller automatically checks the state before each action
    controller.run_action(
        window_spec={'pwa_title': 'Notepad'},
        element_spec={'pwa_control_type': 'Edit'},
        action=f'type_keys:Line {i+1}\\n',
        description=f"Typing line {i+1}"
    )
    time.sleep(1) # Simulate work

# 4. Clean up at the end
notifier.stop()
control_panel.close()
"""
}


if __name__ == "__main__":
    # --- Kích hoạt logger hiệu suất ---
    if performance_logger:
        performance_logger.setup_logger()
    
    # Cấu hình logging cơ bản cho các module không dùng logger hiệu suất
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
    
    app = AutomationSuiteApp()
    app.mainloop()
