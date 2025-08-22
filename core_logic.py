# core_logic.py
# --- VERSION 16.4 (Timeout Propagation):
# - Thêm tham số 'timeout' vào ElementFinder.find và _apply_filters để cho phép
#   việc chủ động ngắt tìm kiếm nếu nó vượt quá thời gian cho phép.
# - Điều này sửa lỗi nghiêm trọng khiến các tác vụ tìm kiếm bị chặn và
#   vượt quá thời gian chờ đã được thiết lập.

import logging
import re
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Tuple, Set

# --- Required Libraries ---
try:
    import psutil
    import win32gui
    import win32process
    import win32con
    import comtypes
    from comtypes.gen import UIAutomationClient as UIA
    from pywinauto import uia_defines
    from pywinauto.findwindows import ElementNotFoundError
    from pywinauto.controls.uiawrapper import UIAWrapper
except ImportError as e:
    print(f"Error importing libraries: {e}")
    print("Suggestion: pip install psutil pywinauto comtypes")
    exit()

# Initialize logger for this module
logger = logging.getLogger(__name__)
# Lấy logger hiệu suất đã được cấu hình
perf_logger = logging.getLogger('PerformanceLogger')


# ======================================================================
#                         CENTRAL DEFINITIONS
# ======================================================================

# --- Definitions for search modifiers and pre-filtering ---
NATIVE_PREFILTER_KEYS: Set[str] = {'pwa_title', 'pwa_auto_id', 'pwa_control_type', 'pwa_class_name', 'proc_name'}
RELATIONAL_KEYS: Set[str] = {'ancestor'}
POSITIONAL_KEYS: Set[str] = {'within_rect', 'to_right_of', 'to_left_of', 'above', 'below'}
ADVANCED_SEARCH_KEYS: Set[str] = RELATIONAL_KEYS.union(POSITIONAL_KEYS)

# --- Property Definitions (Parameters for filtering) ---
PARAMETER_DEFINITIONS: Dict[str, str] = {
    # --- Native Prefilter Keys (Fastest) ---
    "pwa_title": "The visible text/name of the element (most important).",
    "pwa_auto_id": "Automation ID, a unique identifier for the element within the application.",
    "pwa_control_type": "The control type of the element (e.g., Button, Edit, Tree).",
    "pwa_class_name": "The Win32 class name of the element (useful for legacy apps).",
    "proc_name": "The name of the process (e.g., 'notepad.exe'). Used for fast top-level window filtering.",
    
    # --- Other Filterable Properties ---
    "pwa_framework_id": "The framework that created the element (e.g., UIA, Win32, WPF).",
    "win32_handle": "The handle (unique ID) of the window managed by Windows.",
    "win32_styles": "The style flags of the window (in hex).",
    "win32_extended_styles": "The extended style flags of the window (in hex).",
    "state_is_visible": "Visibility state (True if visible).",
    "state_is_enabled": "Interaction state (True if enabled).",
    "state_is_active": "Active state (True if it is the focused window/element).",
    "state_is_minimized": "Minimized state (True if the window is minimized).",
    "state_is_maximized": "Maximized state (True if the window is maximized).",
    "state_is_focusable": "Focusable state (True if it can receive keyboard focus).",
    "state_is_password": "Password field state (True if it is a password input).",
    "state_is_offscreen": "Off-screen state (True if it is outside the visible screen area).",
    "state_is_content_element": "Is a content element, not just a decorative control.",
    "state_is_control_element": "Is an interactable control element (opposite of content).",
    "geo_bounding_rect_tuple": "The coordinate tuple (Left, Top, Right, Bottom) of the element.",
    "geo_center_point": "The center point coordinates of the element.",
    "proc_pid": "Process ID (ID of the process that owns the window).",
    "proc_thread_id": "Thread ID (ID of the thread that owns the window).",
    "proc_path": "The full path to the process's executable file.",
    "proc_cmdline": "The command line used to launch the process.",
    "proc_create_time": "The creation time of the process (as a timestamp or string).",
    "proc_username": "The username that launched the process.",
    "rel_level": "The depth level of the element in the UI tree (0 is the root).",
    "rel_parent_handle": "The handle of the parent window (if any, 0 is the Desktop).",
    "rel_parent_title": "The name/title of the parent element.",
    "rel_labeled_by": "The name of the label element associated with this element.",
    "rel_child_count": "The number of direct child elements.",
    "uia_value": "The value of the element if it supports ValuePattern.",
    "uia_toggle_state": "The state of the element if it supports TogglePattern (On, Off, Indeterminate).",
    "uia_expand_state": "The state if it supports ExpandCollapsePattern (Collapsed, Expanded, LeafNode).",
    "uia_selection_items": "The currently selected items if it supports SelectionPattern.",
    "uia_range_value_info": "Information (Min, Max, Value) if it supports RangeValuePattern.",
    "uia_grid_cell_info": "Information (Row, Col, RowSpan, ColSpan) if it supports GridItemPattern.",
    "uia_table_row_headers": "The headers of the row if it supports TableItemPattern.",
    "ancestor": "A spec to find a parent/ancestor element.",
    "within_rect": "A tuple (left, top, right, bottom) to search within.",
    "to_right_of": "A spec for an anchor element to the left.",
    "to_left_of": "A spec for an anchor element to the right.",
    "above": "A spec for an anchor element below.",
    "below": "A spec for an anchor element above.",
    "sys_unique_id": "Internal unique ID for the COM object.",
    "sys_parent_id": "Internal unique ID for the parent's COM object.",
    "pwa_object": "Internal pywinauto wrapper object."
}

# --- Operator Definitions ---
STRING_OPERATORS: Set[str] = {'equals', 'iequals', 'contains', 'icontains', 'in', 'regex',
                              'not_equals', 'not_iequals', 'not_contains', 'not_icontains'}
NUMERIC_OPERATORS: Set[str] = {'>', '>=', '<', '<='}
OPERATOR_DEFINITIONS: List[Dict[str, str]] = [
    {'category': 'String', 'name': 'equals', 'example': "'pwa_title': ('equals', 'File Explorer')", 'desc': "Matches the exact string (case-sensitive)."},
    {'category': 'String', 'name': 'iequals', 'example': "'pwa_title': ('iequals', 'file explorer')", 'desc': "Matches the exact string (case-insensitive)."},
    {'category': 'String', 'name': 'contains', 'example': "'pwa_title': ('contains', 'Explorer')", 'desc': "Checks if the string contains the substring (case-sensitive)."},
    {'category': 'String', 'name': 'icontains', 'example': "'pwa_title': ('icontains', 'explorer')", 'desc': "Checks if the string contains the substring (case-insensitive)."},
    {'category': 'String', 'name': 'in', 'example': "'proc_name': ('in', ['explorer.exe', 'notepad.exe'])", 'desc': "Checks if the value is in a list of strings."},
    {'category': 'String', 'name': 'regex', 'example': "'pwa_title': ('regex', r'File.*')", 'desc': "Matches using a regular expression."},
    {'category': 'String', 'name': 'not_equals', 'example': "'pwa_title': ('not_equals', 'Calculator')", 'desc': "Value is not exactly equal."},
    {'category': 'String', 'name': 'not_iequals', 'example': "'pwa_class_name': ('not_iequals', 'Chrome')", 'desc': "Value does not contain the substring (case-insensitive)."},
    {'category': 'String', 'name': 'not_contains', 'example': "'pwa_class_name': ('not_contains', 'Chrome')", 'desc': "Value does not contain the substring (case-sensitive)."},
    {'category': 'String', 'name': 'not_icontains', 'example': "'pwa_class_name': ('not_icontains', 'Chrome')", 'desc': "Value does not contain the substring (case-insensitive)."},
    {'category': 'Numeric', 'name': '>', 'example': "'rel_child_count': ('>', 5)", 'desc': "Greater than."},
    {'category': 'Numeric', 'name': '>=', 'example': "'rel_child_count': ('>=', 5)", 'desc': "Greater than or equal to."},
    {'category': 'Numeric', 'name': '<', 'example': "'win32_handle': ('<', 100000)", 'desc': "Less than."},
    {'category': 'Numeric', 'name': '<=', 'example': "'rel_level': ('<=', 3)", 'desc': "Less than or equal to."},
]

# --- Action Definitions ---
ACTION_DEFINITIONS: List[Dict[str, str]] = [
    {'category': 'Mouse', 'name': 'click', 'example': "action='click'", 'desc': "Performs a standard left-click."},
    {'category': 'Mouse', 'name': 'double_click', 'example': "action='double_click'", 'desc': "Performs a double left-click."},
    {'category': 'Mouse', 'name': 'right_click', 'example': "action='right_click'", 'desc': "Performs a right-click."},
    {'category': 'Keyboard', 'name': 'type_keys', 'example': "action='type_keys:Hello World!{ENTER}'", 'desc': "Types a string of text. Supports special keys like {ENTER}, {TAB}, etc."},
    {'category': 'Keyboard', 'name': 'set_text', 'example': "action='set_text:New text value'", 'desc': "Sets the text of an edit control directly. Faster than typing."},
    {'category': 'Keyboard', 'name': 'paste_text', 'example': "action='paste_text:Text from clipboard'", 'desc': "Pastes text from the clipboard (Ctrl+V)."},
    {'category': 'Keyboard', 'name': 'send_message_text', 'example': "action='send_message_text:Background text'", 'desc': "Sets text using Windows messages. Works even if window is not active."},
    {'category': 'State', 'name': 'focus', 'example': "action='focus'", 'desc': "Sets the keyboard focus to the element."},
    {'category': 'State', 'name': 'invoke', 'example': "action='invoke'", 'desc': "Invokes the default action of an element (like pressing a button)."},
    {'category': 'State', 'name': 'toggle', 'example': "action='toggle'", 'desc': "Toggles the state of a checkbox or toggle button."},
    {'category': 'State', 'name': 'select', 'example': "action='select:Item Name'", 'desc': "Selects an item in a list box, combo box, or tab control by its name."},
    {'category': 'Action', 'name': 'scroll', 'example': "action='scroll:down,1'", 'desc': "Scrolls the element. Directions: 'up', 'down', 'left', 'right'. Amount is optional."},
]

# --- Selector Definitions ---
SELECTOR_DEFINITIONS: List[Dict[str, str]] = [
    {'name': 'sort_by_scan_order', 'example': "'sort_by_scan_order': 2", 'desc': "RECOMMENDED. Selects the Nth element found during the scan. Most stable and predictable."},
    {'name': 'sort_by_y_pos', 'example': "'sort_by_y_pos': 1", 'desc': "Sorts elements by their Y coordinate (top to bottom). Use 1 for the topmost element."},
    {'name': 'sort_by_x_pos', 'example': "'sort_by_x_pos': -1", 'desc': "Sorts elements by their X coordinate (left to right). Use -1 for the rightmost element."},
    {'name': 'sort_by_creation_time', 'example': "'sort_by_creation_time': -1", 'desc': "Sorts windows by their creation time. Use -1 for newest, 1 for oldest."},
    {'name': 'sort_by_height', 'example': "'sort_by_height': -1", 'desc': "Sorts elements by their height. Use -1 for the tallest element."},
    {'name': 'sort_by_width', 'example': "'sort_by_width': -1", 'desc': "Sorts elements by their width. Use -1 for the widest element."},
    {'name': 'sort_by_title_length', 'example': "'sort_by_title_length': 1", 'desc': "Sorts elements by the length of their title text. Use 1 for the shortest title."},
    {'name': 'sort_by_child_count', 'example': "'sort_by_child_count': -1", 'desc': "Sorts elements by the number of direct children they have. Use -1 for the one with the most children."},
    {'name': 'z_order_index', 'example': "'z_order_index': 1", 'desc': "Selects an element based on its Z-order (drawing order). Rarely needed."},
]

# --- Property Sets ---
PWA_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('pwa_')}
WIN32_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('win32_')}
STATE_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('state_')}
GEO_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('geo_')}
PROC_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('proc_')}
REL_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('rel_')}
UIA_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('uia_')}
SYS_PROPS: Set[str] = {k for k in PARAMETER_DEFINITIONS if k.startswith('sys_')}

# --- Selectors and Operators ---
SORTING_KEYS: Set[str] = {item['name'] for item in SELECTOR_DEFINITIONS}
VALID_OPERATORS: Set[str] = STRING_OPERATORS.union(NUMERIC_OPERATORS)
SUPPORTED_FILTER_KEYS: Set[str] = PWA_PROPS | WIN32_PROPS | STATE_PROPS | GEO_PROPS | PROC_PROPS | REL_PROPS | UIA_PROPS
_CONTROL_TYPE_ID_TO_NAME: Dict[int, str] = {v: k for k, v in uia_defines.IUIA().known_control_types.items()}
PROC_INFO_CACHE: Dict[int, Dict[str, Any]] = {}

# --- Unchanged Public Utility Functions and Classes ---
def format_spec_to_string(spec_dict: Dict[str, Any], spec_name: str = "spec") -> str:
    """
    Chuyển một từ điển spec thành chuỗi được định dạng đẹp mắt để dễ đọc.
    """
    if not spec_dict: return f"{spec_name} = {{}}"
    dict_to_format = {k: v for k, v in spec_dict.items() if not k.startswith('sys_') and k != 'pwa_object' and (v or v is False or v == 0)}
    if not dict_to_format: return f"{spec_name} = {{}}"
    items_str = [f"    '{k}': {repr(v)}," for k, v in sorted(dict_to_format.items())]
    content = "\n".join(items_str)
    return f"{spec_name} = {{\n{content}\n}}"

def clean_element_spec(window_info: Dict[str, Any], element_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Loại bỏ các thuộc tính trùng lặp từ element_spec mà đã có sẵn trong window_spec.
    """
    if not window_info or not element_info: return element_info
    cleaned_spec = element_info.copy()
    for key, value in list(element_info.items()):
        if key in window_info and window_info[key] == value:
            del cleaned_spec[key]
    return cleaned_spec

def create_optimal_element_spec(selected_element: Dict[str, Any], all_elements_in_context: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Tạo một bộ lọc tối ưu nhất (spec) cho một element dựa trên ngữ cảnh.
    """
    logger.info("--- Building Optimal Element Spec ---")
    if not selected_element: return {}
    def get_matches(spec: Dict[str, Any], elements_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [elem for elem in elements_list if all(elem.get(k) == v for k, v in spec.items())]
    property_combinations = [['pwa_auto_id'], ['pwa_title', 'pwa_control_type'], ['pwa_title'], ['pwa_class_name', 'pwa_control_type'], ['pwa_class_name']]
    best_effort_spec = {}
    min_matches_count = len(all_elements_in_context)
    for combo in property_combinations:
        spec = {}
        is_combo_valid = True
        for prop in combo:
            value = selected_element.get(prop)
            if value is None or (prop != 'pwa_control_type' and not value): is_combo_valid = False; break
            if prop == 'pwa_auto_id' and not (isinstance(value, str) and any(c.isalpha() for c in value) and not value.isdigit()): is_combo_valid = False; break
            spec[prop] = value
        if is_combo_valid:
            matches = get_matches(spec, all_elements_in_context)
            if len(matches) == 1: logger.info(f"Found unique spec with combo {combo}: {spec}"); return spec
            if len(matches) < min_matches_count: min_matches_count = len(matches); best_effort_spec = spec
    final_spec = best_effort_spec
    final_matches = get_matches(final_spec, all_elements_in_context)
    if len(final_matches) > 1:
        try:
            relative_index = next(i for i, match in enumerate(final_matches) if match.get('sys_unique_id') == selected_element.get('sys_unique_id'))
            final_spec['sort_by_scan_order'] = relative_index + 1
            logger.info(f"Spec was ambiguous, added 'sort_by_scan_order': {final_spec}")
        except StopIteration: logger.warning("Could not find selected element in final match list to determine scan order.")
    return final_spec

def create_optimal_window_spec(selected_window: Dict[str, Any], all_windows_on_desktop: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Tạo một bộ lọc tối ưu nhất (spec) cho một cửa sổ dựa trên ngữ cảnh.
    """
    logger.info("--- Building Optimal Window Spec ---")
    if not selected_window: return {}
    base_spec = {}
    proc_name = selected_window.get('proc_name')
    title = selected_window.get('pwa_title')
    if proc_name: base_spec['proc_name'] = proc_name
    if title: base_spec['pwa_title'] = title
    if not base_spec: return {'pwa_class_name': selected_window.get('pwa_class_name')}
    matches = [w for w in all_windows_on_desktop if (not proc_name or w.get('proc_name') == proc_name) and (not title or w.get('pwa_title') == title)]
    if len(matches) > 1:
        logger.warning(f"Found {len(matches)} duplicate windows. Adding 'sort_by_scan_order'.")
        try:
            relative_index = next(i for i, match in enumerate(matches) if match.get('sys_unique_id') == selected_window.get('sys_unique_id'))
            base_spec['sort_by_scan_order'] = relative_index + 1
        except StopIteration: logger.warning("Could not find selected window in match list to determine scan order.")
    return base_spec

def get_process_info(pid: int) -> Dict[str, Any]:
    """
    Lấy thông tin của một process từ PID.
    """
    if pid in PROC_INFO_CACHE: return PROC_INFO_CACHE[pid]
    if pid > 0:
        try:
            p = psutil.Process(pid)
            info = {'proc_name': p.name(), 'proc_path': p.exe(), 'proc_cmdline': ' '.join(p.cmdline()), 'proc_create_time': datetime.fromtimestamp(p.create_time()).strftime('%Y-%m-%d %H:%M:%S'), 'proc_username': p.username()}
            PROC_INFO_CACHE[pid] = info
            return info
        except (psutil.NoSuchProcess, psutil.AccessDenied): pass
    return {}

def get_property_value(pwa_element: UIAWrapper, key: str, uia_instance=None, tree_walker=None) -> Any:
    """
    Lấy giá trị của một thuộc tính từ một element.
    """
    prop = key.lower()
    com_element = getattr(pwa_element.element_info, 'element', getattr(pwa_element, 'element', pwa_element))
    try:
        if prop in PWA_PROPS:
            if prop == 'pwa_title': return pwa_element.window_text()
            if prop == 'pwa_class_name': return pwa_element.class_name()
            if prop == 'pwa_auto_id': return pwa_element.automation_id()
            if prop == 'pwa_control_type': return pwa_element.control_type()
            if prop == 'pwa_framework_id': return pwa_element.framework_id()
        handle = pwa_element.handle
        if handle:
            if prop in WIN32_PROPS:
                if prop == 'win32_handle': return handle
                if prop == 'win32_styles': return win32gui.GetWindowLong(handle, win32con.GWL_STYLE)
                if prop == 'win32_extended_styles': return win32gui.GetWindowLong(handle, win32con.GWL_EXSTYLE)
            if prop == 'proc_thread_id': return win32process.GetWindowThreadProcessId(handle)[0]
            if prop == 'rel_parent_handle': return win32gui.GetParent(handle)
        if prop in STATE_PROPS:
            if prop == 'state_is_visible': return pwa_element.is_visible()
            if prop == 'state_is_enabled': return pwa_element.is_enabled()
            if prop == 'state_is_active': return pwa_element.is_active()
            if prop == 'state_is_minimized': return pwa_element.is_minimized()
            if prop == 'state_is_maximized': return pwa_element.is_maximized()
            if prop == 'state_is_focusable': return pwa_element.is_focusable()
            if prop == 'state_is_password': return pwa_element.is_password()
            if prop == 'state_is_offscreen': return pwa_element.is_offscreen()
            if prop == 'state_is_content_element': return pwa_element.is_content_element()
            if prop == 'state_is_control_element': return pwa_element.is_control_element()
        if prop in GEO_PROPS:
            try:
                rect = pwa_element.rectangle()
                if prop == 'geo_bounding_rect_tuple': return (rect.left, rect.top, rect.right, rect.bottom)
                if prop == 'geo_center_point': return (rect.mid_point().x, rect.mid_point().y)
            except Exception:
                if com_element:
                    try:
                        com_rect = com_element.CurrentBoundingRectangle
                        if prop == 'geo_bounding_rect_tuple': return (com_rect.left, com_rect.top, com_rect.right, com_rect.bottom)
                        if prop == 'geo_center_point': return ((com_rect.left + com_rect.right) // 2, (com_rect.top + com_rect.bottom) // 2)
                    except (comtypes.COMError, AttributeError): return None
        if prop in PROC_PROPS:
            pid = pwa_element.process_id()
            if prop == 'proc_pid': return pid
            return get_process_info(pid).get(prop)
        if prop in REL_PROPS:
            if prop == 'rel_child_count': return len(pwa_element.children())
            parent = pwa_element.parent()
            if prop == 'rel_parent_title': return parent.window_text() if parent else ''
            if prop == 'rel_labeled_by': return pwa_element.labeled_by() if hasattr(pwa_element, 'labeled_by') else ''
            if prop == 'rel_level' and com_element and tree_walker and uia_instance:
                level = 0
                root = uia_instance.GetRootElement()
                if comtypes.client.GetBestInterface(com_element) == comtypes.client.GetBestInterface(root): return 0
                current = com_element
                while True:
                    parent = tree_walker.GetParentElement(current)
                    if not parent: break
                    level += 1
                    if comtypes.client.GetBestInterface(parent) == comtypes.client.GetBestInterface(root): break
                    current = parent
                    if level > 50: break
                return level
        if prop in UIA_PROPS and com_element and uia_instance:
            if prop == 'uia_value':
                pattern = com_element.GetCurrentPattern(UIA.UIA_ValuePatternId)
                if pattern: return pattern.QueryInterface(UIA.IUIAutomationValuePattern).CurrentValue
            if prop == 'uia_toggle_state':
                pattern = com_element.GetCurrentPattern(UIA.UIA_TogglePatternId)
                if pattern: return pattern.QueryInterface(UIA.IUIAutomationTogglePattern).CurrentToggleState.name
            if prop == 'uia_expand_state':
                pattern = com_element.GetCurrentPattern(UIA.UIA_ExpandCollapsePatternId)
                if pattern: return pattern.QueryInterface(UIA.IUIAutomationExpandCollapsePattern).CurrentExpandCollapseState.name
        return None
    except (comtypes.COMError, AttributeError, Exception) as e:
        logger.debug(f"Error getting property '{prop}': {type(e).__name__} - {e}")
        return None

def get_all_properties(pwa_element: UIAWrapper, uia_instance=None, tree_walker=None) -> Dict[str, Any]:
    """
    Lấy tất cả các thuộc tính có sẵn của một element.
    """
    all_props = {}
    for key in SUPPORTED_FILTER_KEYS:
        value = get_property_value(pwa_element, key, uia_instance, tree_walker)
        if value or value is False or value == 0:
            all_props[key] = value
    if 'pwa_title' not in all_props:
        try: all_props['pwa_title'] = pwa_element.window_text()
        except Exception: pass
    if 'pwa_class_name' not in all_props:
        try: all_props['pwa_class_name'] = pwa_element.class_name()
        except Exception: pass
    return all_props

def get_top_level_window(pwa_element: UIAWrapper) -> Optional[UIAWrapper]:
    """
    Tìm cửa sổ cấp cao nhất (top-level) của một element.
    """
    try: return pwa_element.top_level_parent()
    except (AttributeError, RuntimeError): return None

class ElementFinder:
    """
    Thực hiện tìm kiếm các element UI bằng cách kết hợp các phương thức gốc
    của pywinauto và các bộ lọc tùy chỉnh.
    """
    PYWINAUTO_NATIVE_MAP = {
        'pwa_title': 'title',
        'pwa_class_name': 'class_name',
        'pwa_auto_id': 'auto_id',
        'pwa_control_type': 'control_type',
    }
    # --- NEW: Define the priority order for filtering ---
    FILTER_PRIORITY = [
        'pwa_', 'state_', 'win32_', 'geo_', 'proc_', 'rel_', 'uia_'
    ]

    def __init__(self, uia_instance, tree_walker, log_callback: Optional[Callable[[str, Any], None]] = None):
        def dummy_log(level, message): pass
        self.log = log_callback if callable(log_callback) else dummy_log
        self.uia = uia_instance
        self.tree_walker = tree_walker
        self.anchor_cache: Dict[str, UIAWrapper] = {}

    def find(self, search_root: UIAWrapper, spec: Dict[str, Any], timeout: Optional[float] = None, max_depth: Optional[int] = None, search_direction: Optional[str] = None) -> List[UIAWrapper]:
        """
        Tìm kiếm các element dựa trên một bộ lọc (spec).

        Args:
            search_root (UIAWrapper): Element gốc để bắt đầu tìm kiếm.
            spec (Dict[str, Any]): Bộ lọc tìm kiếm.
            timeout (Optional[float]): Thời gian chờ tối đa cho tác vụ tìm kiếm này.
            max_depth (Optional[int]): Độ sâu tối đa để tìm kiếm.
            search_direction (Optional[str]): Hướng tìm kiếm ('forward' hoặc 'backward').

        Returns:
            List[UIAWrapper]: Danh sách các element phù hợp.
        """
        start_time = time.perf_counter()
        
        if 'search_max_depth' in spec:
            if max_depth is None:
                max_depth = spec.pop('search_max_depth', None)
                self.log('INFO', f"Using search depth from spec: max_depth={max_depth}")
            else:
                spec.pop('search_max_depth', None)
                self.log('INFO', f"Using search depth from function argument (overriding spec): max_depth={max_depth}")

        original_spec_for_logging = spec.copy()
        
        self.log('DEBUG', f"Starting find with spec: {original_spec_for_logging}, depth: {max_depth}, direction: {search_direction}, timeout: {timeout}")
        self.anchor_cache.clear()

        ancestor_spec = spec.pop('ancestor', None)
        if ancestor_spec:
            self.log('INFO', f"Ancestor spec found. Finding ancestor first: {ancestor_spec}")
            ancestor_candidates = self.find(search_root, ancestor_spec, timeout=timeout, max_depth=max_depth)
            if not ancestor_candidates:
                self.log('WARNING', "Ancestor not found. Search will fail.")
                return []
            search_root = ancestor_candidates[0]
            self.log('SUCCESS', f"Found ancestor '{search_root.window_text()}'. Searching within it.")

        native_kwargs = {}
        post_filters = {}
        is_top_level_search = hasattr(search_root, 'windows')

        for key, criteria in spec.items():
            if key in self.PYWINAUTO_NATIVE_MAP:
                native_key = self.PYWINAUTO_NATIVE_MAP[key]
                if not isinstance(criteria, tuple):
                    native_kwargs[native_key] = criteria
                    continue
                op, val = criteria
                if op in ('equals', 'iequals'):
                    native_kwargs[native_key] = val
                elif op in ('contains', 'icontains', 'regex') and is_top_level_search:
                    regex_val = val if op == 'regex' else f".*{re.escape(str(val))}.*"
                    native_kwargs[f"{native_key}_re"] = regex_val
                else:
                    post_filters[key] = criteria
            else:
                post_filters[key] = criteria

        self.log('DEBUG', f"Applying native pywinauto filters: {native_kwargs}")

        if is_top_level_search:
            initial_candidates = search_root.windows(**native_kwargs)
            self.log('DEBUG', f"Fetched {len(initial_candidates)} windows using native filters.")
        else:
            self.log('DEBUG', f"Fetching descendants from '{search_root.window_text()}' with depth={max_depth} and native filters.")
            initial_candidates = search_root.descendants(depth=max_depth, **native_kwargs)
            self.log('DEBUG', f"Found {len(initial_candidates)} initial candidates with native filters.")
        
        if search_direction == 'backward':
            initial_candidates.reverse()
            self.log('DEBUG', f"Reversed {len(initial_candidates)} candidates for 'backward' search.")

        filter_spec = {k: v for k, v in post_filters.items() if k not in SORTING_KEYS}
        selector_spec = {k: v for k, v in spec.items() if k in SORTING_KEYS}
        
        if filter_spec:
            self.log('DEBUG', f"Applying post-filters: {filter_spec}")
            filtered_candidates = self._apply_filters(initial_candidates, filter_spec, initial_candidates, start_time, timeout)
        else:
            filtered_candidates = initial_candidates

        self.log('DEBUG', f"{len(filtered_candidates)} candidates remaining after post-filtering.")

        if selector_spec:
            self.log('DEBUG', f"Applying selectors: {selector_spec}")
            final_candidates = self._apply_selectors(filtered_candidates, selector_spec)
        else:
            final_candidates = filtered_candidates
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        spec_str = str(original_spec_for_logging)[:150]
        self.log('TIMER', f"Find operation for spec '{spec_str}...' completed in {duration:.4f}s. Found {len(final_candidates)} item(s).")
        
        self.log('DEBUG', f"Find finished. Found {len(final_candidates)} candidates.")
        return final_candidates

    def _apply_filters(self, elements: List[UIAWrapper], spec: Dict[str, Any], full_context: List[UIAWrapper], start_time: float, timeout: Optional[float]) -> List[UIAWrapper]:
        """
        Áp dụng các bộ lọc tùy chỉnh cho một danh sách các element.
        """
        if not spec: return elements
        
        filtered_elements = []
        advanced_spec = {k: v for k, v in spec.items() if k in ADVANCED_SEARCH_KEYS}
        property_spec = {k: v for k, v in spec.items() if k not in ADVANCED_SEARCH_KEYS}

        # --- NEW: Sort property checks by priority ---
        def get_priority(key):
            for i, prefix in enumerate(self.FILTER_PRIORITY):
                if key.startswith(prefix):
                    return i
            return len(self.FILTER_PRIORITY)
        
        sorted_property_spec = sorted(property_spec.items(), key=lambda item: get_priority(item[0]))
        
        for elem in elements:
            # Ngắt nếu hết thời gian chờ
            if timeout and time.perf_counter() - start_time > timeout:
                self.log('ERROR', f"TIMEOUT: Filtering aborted. Exceeded {timeout}s.")
                return filtered_elements

            prop_cache = {}
            is_match = True
            
            # Check sorted properties first
            for key, criteria in sorted_property_spec:
                if not self._check_condition(elem, key, criteria, prop_cache):
                    is_match = False
                    break
            if not is_match:
                continue
            
            # Check advanced properties last
            for key, criteria in advanced_spec.items():
                if not self._check_advanced_condition(elem, key, criteria, full_context):
                    is_match = False
                    break
            
            if is_match:
                filtered_elements.append(elem)
                
        return filtered_elements

    def _check_condition(self, element: UIAWrapper, key: str, criteria: Any, prop_cache: Dict[str, Any]) -> bool:
        """
        Kiểm tra một điều kiện lọc duy nhất.
        """
        if key in prop_cache: actual_value = prop_cache[key]
        else: actual_value = get_property_value(element, key, self.uia, self.tree_walker); prop_cache[key] = actual_value
        is_operator_syntax = (isinstance(criteria, tuple) and len(criteria) == 2 and str(criteria[0]).lower() in VALID_OPERATORS)
        if is_operator_syntax:
            operator, target_value = criteria
            op = str(operator).lower()
            if actual_value is None: return False
            if op in STRING_OPERATORS:
                str_actual, str_target = str(actual_value), str(target_value)
                if op == 'equals': return str_actual == str_target
                if op == 'iequals': return str_actual.lower() == str_target.lower()
                if op == 'contains': return str_target in str_actual
                if op == 'icontains': return str_target.lower() in str_actual.lower()
                if op == 'in': return str_actual in target_value
                if op == 'regex': return re.search(str_target, str_actual) is not None
                if op == 'not_equals': return str_actual != str_target
                if op == 'not_iequals': return str_actual.lower() != str_target.lower()
                if op == 'not_contains': return str_target not in str_actual
                if op == 'not_icontains': return str_target.lower() not in str_actual.lower()
            if op in NUMERIC_OPERATORS:
                try:
                    num_actual, num_target = float(actual_value), float(target_value)
                    if op == '>': return num_actual > num_target
                    if op == '>=': return num_actual >= num_target
                    if op == '<': return num_actual < num_target
                    if op == '<=': return num_actual <= num_target
                except (ValueError, TypeError): return False
        else: return actual_value == criteria
        return False

    def _check_advanced_condition(self, element: UIAWrapper, key: str, criteria: Any, full_context: List[UIAWrapper]) -> bool:
        """
        Kiểm tra các điều kiện lọc nâng cao (vị trí, quan hệ).
        """
        if key == 'within_rect':
            elem_rect_val = get_property_value(element, 'geo_bounding_rect_tuple')
            if not elem_rect_val: return False
            elem_rect = element.rectangle()
            box_l, box_t, box_r, box_b = criteria
            return (elem_rect.left >= box_l and elem_rect.top >= box_t and elem_rect.right <= box_r and elem_rect.bottom <= box_b)
        if key in POSITIONAL_KEYS:
            anchor_spec = criteria
            anchor_key = str(anchor_spec)
            if anchor_key not in self.anchor_cache:
                anchor_finder = ElementFinder(self.uia, self.tree_walker, self.log)
                anchor_candidates = anchor_finder.find(element.top_level_parent(), anchor_spec)
                if not anchor_candidates: return False
                self.anchor_cache[anchor_key] = anchor_candidates[0]
            anchor_element = self.anchor_cache[anchor_key]
            if not anchor_element or anchor_element == element: return False
            elem_rect = element.rectangle(); anchor_rect = anchor_element.rectangle()
            v_overlap = max(0, min(elem_rect.bottom, anchor_rect.bottom) - max(elem_rect.top, anchor_rect.top))
            h_overlap = max(0, min(elem_rect.right, anchor_rect.right) - max(elem_rect.left, anchor_rect.left))
            if key == 'to_right_of': return elem_rect.left >= anchor_rect.right and v_overlap > 0
            if key == 'to_left_of': return elem_rect.right <= anchor_rect.left and v_overlap > 0
            if key == 'below': return elem_rect.top >= anchor_rect.bottom and h_overlap > 0
            if key == 'above': return elem_rect.bottom <= anchor_rect.top and h_overlap > 0
        return False

    def _apply_selectors(self, candidates: List[UIAWrapper], selectors: Dict[str, Any]) -> List[UIAWrapper]:
        """
        Áp dụng các bộ chọn (selectors) để thu hẹp kết quả.
        """
        if not candidates: return []
        if 'sort_by_scan_order' in selectors:
            index = selectors['sort_by_scan_order']
            self.log('FILTER', f"Selecting by scan order index: {index}")
            final_index = index - 1 if index > 0 else index
            try:
                selected = candidates[final_index]
                self.log('SUCCESS', f"Selected candidate by scan order: '{selected.window_text()}'")
                return [selected]
            except IndexError:
                self.log('ERROR', f"Index selection={final_index} is out of range for {len(candidates)} candidates.")
                return []
        sorted_candidates = list(candidates)
        for key in [k for k in selectors if k != 'z_order_index']:
            index = selectors[key]
            self.log('FILTER', f"Sorting by: '{key}' (Order: {'Descending' if index < 0 else 'Ascending'})")
            sort_key_func = self._get_sort_key_function(key)
            if sort_key_func:
                sorted_candidates.sort(key=lambda e: (sort_key_func(e) is None, sort_key_func(e)), reverse=(index < 0))
        final_index = 0
        if 'z_order_index' in selectors: final_index = selectors['z_order_index']
        elif selectors:
            last_selector_key = list(selectors.keys())[-1]
            final_index = selectors[last_selector_key]
            final_index = final_index - 1 if final_index > 0 else final_index
        self.log('FILTER', f"Selecting item at final index: {final_index}")
        try:
            selected = sorted_candidates[final_index]
            self.log('SUCCESS', f"Selected candidate after sorting: '{selected.window_text()}'")
            return [selected]
        except IndexError:
            self.log('ERROR', f"Index selection={final_index} is out of range for {len(sorted_candidates)} candidates.")
            return []

    def _get_sort_key_function(self, key: str) -> Optional[Callable[[UIAWrapper], Any]]:
        """
        Trả về một hàm để sắp xếp element dựa trên key.
        """
        if key == 'sort_by_creation_time': return lambda e: get_property_value(e, 'proc_create_time') or datetime.min.strftime('%Y-%m-%d %H:%M:%S')
        if key == 'sort_by_title_length': return lambda e: len(get_property_value(e, 'pwa_title') or '')
        if key == 'sort_by_child_count': return lambda e: get_property_value(e, 'rel_child_count') or 0
        if key in ['sort_by_y_pos', 'sort_by_x_pos', 'sort_by_width', 'sort_by_height']:
            def get_rect_prop(elem, prop_key):
                rect = get_property_value(elem, 'geo_bounding_rect_tuple')
                if not rect: return 0
                if prop_key == 'sort_by_y_pos': return rect[1]
                if prop_key == 'sort_by_x_pos': return rect[0]
                if prop_key == 'sort_by_width': return rect[2] - rect[0]
                if prop_key == 'sort_by_height': return rect[3] - rect[1]
            return lambda e: get_rect_prop(e, key)
        return None
