# core_controller.py
# --- VERSION 9.7 (Fix Timeout Logic):
# - Sửa lỗi nghiêm trọng khiến hàm tìm kiếm bị chặn và vượt quá thời gian
#   chờ được thiết lập.
# - Hàm _find_with_retry giờ đây sẽ tính toán và truyền thời gian còn lại
#   (remaining_timeout) cho ElementFinder.find trong mỗi vòng lặp thử lại.
# - Điều này đảm bảo rằng các tác vụ tìm kiếm sẽ chủ động ngắt nếu chúng
#   quá lâu, giúp tuân thủ chính xác thời gian chờ và cải thiện hiệu suất
#   khi tìm kiếm các element không tồn tại hoặc mất thời gian.

import logging
import time
import threading
import sys
import os
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable, Type, Union

# --- Required Libraries ---
try:
    import win32api
    import win32con
    import pyperclip
    from pynput import mouse, keyboard
    from pywinauto.findwindows import ElementNotFoundError
    from pywinauto import Desktop
    from pywinauto import mouse as pywinauto_mouse
    from pywinauto.controls.uiawrapper import UIAWrapper
    import comtypes
    from comtypes.gen import UIAutomationClient as UIA
    from PIL import ImageGrab
    from pywinauto.uia_defines import NoPatternInterfaceError
except ImportError as e:
    print(f"Error importing libraries, please install: {e}")
    print("Suggestion: pip install pynput pywinauto pyperclip comtypes Pillow")
    sys.exit(1)

# --- Configure logging ---
perf_logger = logging.getLogger('PerformanceLogger')
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

# --- Import refactored components ---
try:
    from . import core_logic
    from .ui_notifier import StatusNotifier
    from .ui_control_panel import AutomationState
    from .human_activity_listener import HumanActivityListener
except ImportError:
    try:
        import core_logic
        from ui_notifier import StatusNotifier
        from ui_control_panel import AutomationState
        from human_activity_listener import HumanActivityListener
    except ImportError:
        print("CRITICAL ERROR: 'core_logic.py', 'ui_notifier.py', or 'ui_control_panel.py' must be in the same directory.")
        class AutomationState:
            def is_stopped(self): return False
            def is_paused(self): return False
        sys.exit(1)


# --- Custom Exception Definitions ---
class UIActionError(Exception): pass
class WindowNotFoundError(UIActionError): pass
class ElementNotFoundFromWindowError(UIActionError): pass
class AmbiguousElementError(UIActionError): pass
class WaitTimeoutError(UIActionError): pass

# --- UISnapshot Class with Self-Healing ---
class UISnapshot:
    """
    Quản lý một "ảnh chụp" (snapshot) của các element UI tại một thời điểm
    cụ thể, kèm theo khả năng "tự phục hồi" (self-healing) nếu một element
    không còn hợp lệ.
    """
    DEFAULT_HEAL_TIMEOUT_CAP = 2.0

    def __init__(self, name: str, controller_instance: 'UIController', creation_timeout: float):
        """
        Khởi tạo một instance của UISnapshot.

        Args:
            name (str): Tên định danh cho snapshot.
            controller_instance (UIController): Tham chiếu đến UIController để
                                                thực hiện việc tự phục hồi.
            creation_timeout (float): Thời gian chờ ban đầu để tạo snapshot.
        """
        self.name = name
        self._elements = {}
        self._recipes = {}
        self._controller = controller_instance
        self._creation_timeout = creation_timeout
        self.logger = logging.getLogger(f"UISnapshot({self.name})")

    def _add_element(self, key: str, element: UIAWrapper, parent_window: UIAWrapper, spec: Dict[str, Any]):
        """
        Thêm một element vào snapshot.

        Args:
            key (str): Khóa định danh cho element.
            element (UIAWrapper): Đối tượng element.
            parent_window (UIAWrapper): Cửa sổ chứa element.
            spec (Dict[str, Any]): Bộ lọc (spec) ban đầu dùng để tìm element.
        """
        self._elements[key] = element
        self._recipes[key] = {'parent': parent_window, 'spec': spec}

    def __getitem__(self, key: str) -> Optional[UIAWrapper]:
        """
        Lấy một element từ snapshot. Tự động phục hồi nếu element bị cũ (stale).

        Args:
            key (str): Khóa định danh của element.

        Returns:
            Optional[UIAWrapper]: Đối tượng element đã được xác thực/phục hồi,
                                  hoặc None nếu không tìm thấy.
        """
        element = self._elements.get(key)
        try:
            # Kiểm tra xem element có còn hợp lệ không
            if element and element.is_visible():
                return element
        except Exception:
            pass

        self.logger.warning(f"Element '{key}' trong snapshot '{self.name}' đã cũ. Đang cố gắng tự phục hồi...")
        recipe = self._recipes.get(key)
        if not recipe:
            self.logger.error(f"Không thể phục hồi '{key}': Không tìm thấy bộ lọc ban đầu.")
            return None

        parent_window = recipe['parent']
        element_spec = recipe['spec']
        try:
            if not parent_window or not parent_window.is_visible():
                self.logger.error(f"Không thể phục hồi '{key}': Cửa sổ gốc không còn hợp lệ.")
                return None
            heal_timeout = min(self._creation_timeout, self.DEFAULT_HEAL_TIMEOUT_CAP)
            healed_element = self._controller.find_element(
                window_spec={'win32_handle': parent_window.handle},
                element_spec=element_spec,
                timeout=heal_timeout
            )
            if healed_element:
                self.logger.info(f"Tự phục hồi thành công cho '{key}'.")
                self._elements[key] = healed_element
                return healed_element
            else:
                self.logger.error(f"Tự phục hồi thất bại cho '{key}': Không thể tìm lại element trong {heal_timeout:.1f}s.")
                return None
        except Exception as e:
            self.logger.error(f"Lỗi không mong muốn xảy ra trong quá trình tự phục hồi cho '{key}': {e}")
            return None

    def __contains__(self, key: str) -> bool:
        """Kiểm tra xem một khóa có tồn tại trong snapshot không."""
        return key in self._elements

    def get(self, key: str, default: Any = None) -> Optional[UIAWrapper]:
        """Lấy một element, trả về giá trị mặc định nếu không tìm thấy."""
        return self[key] if key in self else default

    @property
    def found_elements(self) -> Dict[str, UIAWrapper]:
        """Trả về từ điển của các element được tìm thấy."""
        return self._elements

def create_notifier_callback(notifier_instance: 'StatusNotifier') -> Optional[Callable]:
    """
    Tạo một callback để gửi thông báo từ UIController.
    
    Args:
        notifier_instance (StatusNotifier): Instance của StatusNotifier.
    
    Returns:
        Optional[Callable]: Hàm callback hoặc None nếu không có notifier.
    """
    if not notifier_instance or not isinstance(notifier_instance, StatusNotifier):
        return None
    def event_handler(event_type: str, message: str, **kwargs):
        notifier_instance.update_status(text=message, style=event_type, duration=kwargs.get('duration'))
    return event_handler

DEFAULT_CONTROLLER_CONFIG: Dict[str, Any] = {
    'backend': 'uia', 'human_interruption_detection': False, 'human_cooldown_period': 5,
    'secure_mode': False, 'default_timeout': 10, 'default_retry_interval': 0.5,
    'log_level': 'info'
}

class UIController:
    """
    Điều phối các hoạt động tự động hóa UI bằng cách kết hợp các công cụ cốt lõi.
    Hỗ trợ tìm kiếm, tương tác, kiểm tra trạng thái và xử lý lỗi.
    """
    GETTABLE_PROPERTIES: set = {'text', 'texts', 'value', 'is_toggled'}.union(core_logic.SUPPORTED_FILTER_KEYS)
    BACKGROUND_SAFE_ACTIONS: set = {'set_text', 'send_message_text'}
    SENSITIVE_ACTIONS: set = {'paste_text', 'type_keys', 'set_text'}
    VALID_ACTIONS: set = {action['name'] for action in core_logic.ACTION_DEFINITIONS}.union({'mouse_scroll'})

    def __init__(self, 
                 notifier: Optional['StatusNotifier'] = None, 
                 event_callback: Optional[Callable] = None,
                 automation_state: Optional['AutomationState'] = None,
                 **kwargs):
        """
        Khởi tạo UIController.

        Args:
            notifier (Optional[StatusNotifier]): Instance của StatusNotifier để gửi thông báo.
            event_callback (Optional[Callable]): Hàm callback tùy chỉnh để xử lý sự kiện.
            automation_state (Optional[AutomationState]): Instance để kiểm soát trạng thái
                                                          (tạm dừng/dừng).
            **kwargs: Các tùy chọn cấu hình bổ sung.
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        if event_callback:
            self.event_callback = event_callback
        elif notifier and isinstance(notifier, StatusNotifier):
            self.event_callback = create_notifier_callback(notifier)
        else:
            self.event_callback = None
        
        self.config = {**DEFAULT_CONTROLLER_CONFIG, **kwargs}
        
        self.state = automation_state
        self.desktop = Desktop(backend=self.config['backend'])
        try:
            self.uia = comtypes.client.CreateObject(UIA.CUIAutomation)
            self.tree_walker = self.uia.ControlViewWalker
        except (OSError, comtypes.COMError) as e:
            self.logger.critical(f"Lỗi nghiêm trọng khi khởi tạo COM: {e}", exc_info=True)
            raise

        log_level = self.config.get('log_level', 'info').lower()
        if log_level == 'debug':
            def finder_logger(level, message):
                self.logger.debug(f"[ElementFinder] L:{level} - M:{message}")
            finder_log_callback = finder_logger
            self.logger.info("UIController đã khởi tạo với log_level='debug' (verbose).")
        else:
            def dummy_log(level, message):
                pass
            finder_log_callback = dummy_log
            self.logger.info(f"UIController đã khởi tạo với log_level='{log_level}' (đã ẩn log finder).")

        self.finder = core_logic.ElementFinder(
            uia_instance=self.uia, 
            tree_walker=self.tree_walker, 
            log_callback=finder_log_callback
        )
        
        self._bot_acting_lock = threading.Lock()
        self._is_bot_acting = [False]
        self.activity_listener = None
        if self.config['human_interruption_detection']:
            self.activity_listener = HumanActivityListener(
                cooldown_period=self.config['human_cooldown_period'],
                bot_acting_lock=self._bot_acting_lock,
                is_bot_acting_ref=self._is_bot_acting,
                notifier=notifier
            )
            
    def find_element(self, window_spec: Dict[str, Any], 
                     element_spec: Optional[Dict[str, Any]] = None, 
                     timeout: Optional[float] = None, 
                     retry_interval: Optional[float] = None,
                     **kwargs) -> Optional[UIAWrapper]:
        """
        Tìm một element duy nhất dựa trên bộ lọc (spec).

        Args:
            window_spec (Dict[str, Any]): Bộ lọc để tìm cửa sổ cha.
            element_spec (Optional[Dict[str, Any]]): Bộ lọc để tìm element bên trong cửa sổ.
                                                      Nếu None, sẽ trả về cửa sổ.
            timeout (Optional[float]): Thời gian chờ tối đa.
            retry_interval (Optional[float]): Khoảng thời gian giữa các lần thử lại.

        Returns:
            Optional[UIAWrapper]: Đối tượng element được tìm thấy, hoặc None.
        """
        timeout = timeout if timeout is not None else self.config['default_timeout']
        retry_interval = retry_interval if retry_interval is not None else self.config['default_retry_interval']
        try:
            window = self._find_with_retry(
                self.desktop, window_spec, timeout, retry_interval, WindowNotFoundError, 
                AmbiguousElementError, "window", **kwargs
            )
            if not element_spec: 
                return window

            search_root = window
            spec_to_find = element_spec.copy()

            if 'search_root_spec' in spec_to_find:
                container_spec = spec_to_find.pop('search_root_spec')
                self.logger.info(f"Tìm kiếm có giới hạn: Đang tìm container với spec: {container_spec}")
                
                search_root = self.find_element(
                    window_spec=window_spec,
                    element_spec=container_spec,
                    timeout=timeout,
                    retry_interval=retry_interval,
                    **kwargs
                )

                if not search_root:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element container cho tìm kiếm có giới hạn.")
                self.logger.info(f"Đã tìm thấy container: '{search_root.window_text()}'. Đang tìm mục tiêu bên trong.")
            
            if 'child_at_index' in spec_to_find:
                index = spec_to_find['child_at_index']
                if not isinstance(index, int):
                    raise ValueError(f"'child_at_index' phải là số nguyên, nhưng nhận được {type(index)}.")
                
                self.logger.info(f"Truy cập trực tiếp: Lấy element con tại chỉ số {index} từ '{search_root.window_text()}'.")
                
                try:
                    children = search_root.children()
                    if len(children) > index:
                        return children[index]
                    else:
                        raise ElementNotFoundFromWindowError(f"Không tìm thấy element con tại chỉ số {index}. Container chỉ có {len(children)} element con.")
                except Exception as e:
                    raise UIActionError(f"Không thể lấy element con tại chỉ số {index}: {e}")

            return self._find_with_retry(
                search_root, spec_to_find, timeout, retry_interval, ElementNotFoundFromWindowError, 
                AmbiguousElementError, f"element trong '{search_root.window_text()}'", **kwargs
            )
        except (WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError) as e:
            self.logger.warning(f"Không thể tìm thấy element duy nhất: {e}")
            return None

    def get_next_state(self, cases: Dict[str, Dict[str, Any]], timeout: float, description: Optional[str] = None) -> str:
        """
        Chờ cho đến khi một trong các trạng thái UI được định nghĩa trong `cases` xuất hiện.

        Args:
            cases (Dict[str, Dict[str, Any]]): Một từ điển của các trạng thái cần theo dõi.
            timeout (float): Thời gian chờ tối đa.
            description (Optional[str]): Mô tả cho hành động (hiển thị trên notifier).

        Returns:
            str: Khóa của trạng thái được phát hiện đầu tiên, hoặc "timeout" nếu hết thời gian chờ.
        """
        display_message = description or "Đang chờ trạng thái UI kế tiếp..."
        self._emit_event('process', display_message)
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            for state_key, specs in cases.items():
                window_spec = specs.get("window_spec")
                element_spec = specs.get("element_spec")
                
                if not window_spec:
                    self.logger.warning(f"Bỏ qua trường hợp '{state_key}' vì thiếu 'window_spec'.")
                    continue
                
                if self.check_exists(window_spec=window_spec, element_spec=element_spec, timeout=0.2, log_output=False):
                    self._emit_event('success', f"Đã phát hiện trạng thái '{state_key}'.")
                    return state_key
            
            time.sleep(self.config['default_retry_interval'])
        
        self._emit_event('warning', f"Hết thời gian chờ: Không phát hiện được trạng thái nào sau {timeout} giây.")
        return "timeout"

    def run_action(self, action: str, 
                   target: Optional[UIAWrapper] = None, 
                   window_spec: Optional[Dict[str, Any]] = None, 
                   element_spec: Optional[Dict[str, Any]] = None, 
                   timeout: Optional[float] = None, 
                   auto_activate: bool = False, 
                   retry_interval: Optional[float] = None, 
                   description: Optional[str] = None, 
                   notify_style: str = 'info', 
                   delay_before: float = 0, 
                   delay_after: float = 0,
                   **kwargs) -> bool:
        """
        Thực hiện một hành động (ví dụ: click, type) trên một element.

        Args:
            action (str): Tên hành động và giá trị (ví dụ: 'click', 'type_keys:hello').
            target (Optional[UIAWrapper]): Element đã được tìm thấy từ trước (tùy chọn).
            window_spec (Optional[Dict[str, Any]]): Bộ lọc cửa sổ nếu target không được cung cấp.
            element_spec (Optional[Dict[str, Any]]): Bộ lọc element nếu target không được cung cấp.
            timeout (Optional[float]): Thời gian chờ tối đa.
            auto_activate (bool): Tự động kích hoạt/làm nổi bật cửa sổ nếu cần.
            description (Optional[str]): Mô tả cho hành động (hiển thị trên notifier).
            delay_before (float): Khoảng thời gian chờ trước khi thực hiện hành động.
            delay_after (float): Khoảng thời gian chờ sau khi thực hiện hành động.
            **kwargs: Các tùy chọn bổ sung cho quá trình tìm kiếm.
        """
        log_action = action
        if self.config['secure_mode'] and ':' in action:
            command, _ = action.split(':', 1)
            if command.lower().strip() in self.SENSITIVE_ACTIONS:
                log_action = f"{command}:********"
        display_message = description or f"Đang thực hiện tác vụ: {log_action}"
        verbose = description is None
        self._emit_event(notify_style if description else 'info', display_message)
        
        try:
            self._wait_for_user_idle()
            
            if target:
                if not isinstance(target, UIAWrapper):
                    raise UIActionError("Target được cung cấp không phải là một element UI hợp lệ.")
                target_element = target
                self.logger.info(f"Đang sử dụng target element đã được cung cấp '{target.window_text()}'. Bỏ qua quét.")
            else:
                if not window_spec:
                    raise ValueError("Phải cung cấp 'window_spec' và 'element_spec' khi 'target' không được sử dụng.")
                target_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not target_element:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element mục tiêu thông qua quét.")

            if delay_before > 0:
                self.logger.info(f"Đang chờ {delay_before} giây trước khi thực hiện hành động.")
                time.sleep(delay_before)

            command = action.split(':', 1)[0].lower().strip()
            if command not in self.BACKGROUND_SAFE_ACTIONS:
                self._handle_activation(target_element, command, auto_activate)
            if verbose:
                self._emit_event('process', f"Đang thực hiện hành động '{log_action}'...")
            self._execute_action_safely(target_element, action)
            
            if delay_after > 0:
                self.logger.info(f"Đang chờ {delay_after} giây sau khi thực hiện hành động.")
                time.sleep(delay_after)

            self._emit_event('success', f"Thành công: {display_message}")
            return True
        except (UIActionError, WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError, ValueError) as e:
            self.logger.error(f"Lỗi khi thực hiện '{display_message}': {e}", exc_info=False)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            return False
        except Exception as e:
            self.logger.critical(f"Lỗi không mong muốn khi thực hiện '{display_message}': {e}", exc_info=True)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            return False

    def wait_for_state(self, state_spec: Dict[str, Any], 
                       target: Optional[UIAWrapper] = None, 
                       window_spec: Optional[Dict[str, Any]] = None, 
                       element_spec: Optional[Dict[str, Any]] = None, 
                       timeout: Optional[float] = None, 
                       retry_interval: Optional[float] = None, 
                       description: Optional[str] = None,
                       **kwargs) -> bool:
        """
        Chờ một element đạt được trạng thái mong muốn.

        Args:
            state_spec (Dict[str, Any]): Trạng thái mục tiêu (ví dụ: {'state_is_enabled': True}).
            target (Optional[UIAWrapper]): Element đã được tìm thấy từ trước (tùy chọn).
            window_spec (Optional[Dict[str, Any]]): Bộ lọc cửa sổ nếu target không được cung cấp.
            element_spec (Optional[Dict[str, Any]]): Bộ lọc element nếu target không được cung cấp.
            timeout (Optional[float]): Thời gian chờ tối đa.
            description (Optional[str]): Mô tả cho hành động (hiển thị trên notifier).
            **kwargs: Các tùy chọn bổ sung cho quá trình tìm kiếm.

        Returns:
            bool: True nếu trạng thái đạt được trong thời gian chờ, ngược lại là False.
        """
        effective_timeout = timeout if timeout is not None else self.config['default_timeout']
        effective_retry = retry_interval if retry_interval is not None else self.config['default_retry_interval']
        display_message = description or f"Đang chờ trạng thái: {state_spec}"
        self._emit_event('process', display_message)

        try:
            if target:
                if not isinstance(target, UIAWrapper):
                    raise UIActionError("Target được cung cấp không phải là một element UI hợp lệ.")
                monitor_element = target
            else:
                if not window_spec:
                    raise ValueError("Phải cung cấp 'window_spec' và 'element_spec' khi 'target' không được sử dụng.")
                monitor_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not monitor_element:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element để theo dõi trạng thái.")
            
            self.logger.info(f"Đang theo dõi element '{monitor_element.window_text()}' để thay đổi trạng thái...")
            start_time = time.time()
            while time.time() - start_time < effective_timeout:
                self._wait_for_user_idle()
                all_conditions_met = True
                for key, criteria in state_spec.items():
                    if not self.finder._check_condition(monitor_element, key, criteria, {}):
                        all_conditions_met = False
                        break
                
                if all_conditions_met:
                    self._emit_event('success', f"Thành công: {display_message}")
                    return True
                
                time.sleep(effective_retry)
            
            raise WaitTimeoutError(f"Hết thời gian chờ sau {effective_timeout}s. Element không đạt được trạng thái mong muốn.")

        except (UIActionError, ValueError, WaitTimeoutError) as e:
            self.logger.error(f"Lỗi trong quá trình wait_for_state '{display_message}': {e}", exc_info=False)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            return False
        except Exception as e:
            self.logger.critical(f"Lỗi không mong muốn trong quá trình wait_for_state '{display_message}': {e}", exc_info=True)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            return False

    def get_property(self, property_name: str, 
                     target: Optional[UIAWrapper] = None, 
                     window_spec: Optional[Dict[str, Any]] = None, 
                     element_spec: Optional[Dict[str, Any]] = None, 
                     timeout: Optional[float] = None, 
                     retry_interval: Optional[float] = None, 
                     description: Optional[str] = None, 
                     notify_style: str = 'info',
                     **kwargs) -> Any:
        """
        Lấy giá trị của một thuộc tính từ một element.

        Args:
            property_name (str): Tên của thuộc tính cần lấy.
            target (Optional[UIAWrapper]): Element đã được tìm thấy từ trước (tùy chọn).
            window_spec (Optional[Dict[str, Any]]): Bộ lọc cửa sổ nếu target không được cung cấp.
            element_spec (Optional[Dict[str, Any]]): Bộ lọc element nếu target không được cung cấp.
            timeout (Optional[float]): Thời gian chờ tối đa.
            description (Optional[str]): Mô tả cho hành động (hiển thị trên notifier).
            **kwargs: Các tùy chọn bổ sung cho quá trình tìm kiếm.

        Returns:
            Any: Giá trị của thuộc tính, hoặc None nếu không thành công.
        """
        display_message = description or f"Đang lấy thuộc tính '{property_name}'"
        self._emit_event(notify_style if description else 'info', display_message)
        if property_name not in self.GETTABLE_PROPERTIES:
            raise ValueError(f"Thuộc tính '{property_name}' không được hỗ trợ.")
        
        try:
            self._wait_for_user_idle()
            
            if target:
                if not isinstance(target, UIAWrapper):
                    raise UIActionError("Target được cung cấp không phải là một element UI hợp lệ.")
                target_element = target
                self.logger.info(f"Đang sử dụng target element đã được cung cấp '{target.window_text()}'. Bỏ qua quét.")
            else:
                if not window_spec:
                    raise ValueError("Phải cung cấp 'window_spec' và 'element_spec' khi 'target' không được sử dụng.")
                target_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not target_element:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element mục tiêu thông qua quét.")

            value = core_logic.get_property_value(target_element, property_name, self.uia, self.tree_walker)
            self._emit_event('success', f"Đã lấy thành công thuộc tính '{property_name}'.")
            return value
        except (UIActionError, WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError, ValueError) as e:
            self.logger.error(f"Lỗi khi thực hiện '{display_message}': {e}", exc_info=False)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            return None
        except Exception as e:
            self.logger.critical(f"Lỗi không mong muốn khi thực hiện '{display_message}': {e}", exc_info=True)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            return None

    def check_exists(self, target: Optional[UIAWrapper] = None, 
                     window_spec: Optional[Dict[str, Any]] = None, 
                     element_spec: Optional[Dict[str, Any]] = None, 
                     timeout: Optional[float] = None, 
                     retry_interval: Optional[float] = None, 
                     log_output: bool = True,
                     **kwargs) -> bool:
        """
        Kiểm tra xem một element có tồn tại hay không.

        Args:
            target (Optional[UIAWrapper]): Element đã được tìm thấy từ trước (tùy chọn).
            window_spec (Optional[Dict[str, Any]]): Bộ lọc cửa sổ nếu target không được cung cấp.
            element_spec (Optional[Dict[str, Any]]): Bộ lọc element nếu target không được cung cấp.
            timeout (Optional[float]): Thời gian chờ tối đa.
            log_output (bool): Có ghi log ra console hay không.

        Returns:
            bool: True nếu element tồn tại, ngược lại là False.
        """
        if log_output:
            self._emit_event('info', "Đang kiểm tra sự tồn tại của mục tiêu...")
        try:
            self._wait_for_user_idle()
            if target:
                try:
                    return isinstance(target, UIAWrapper) and target.is_visible()
                except Exception:
                    return False
            else:
                if not window_spec:
                    raise ValueError("Phải cung cấp 'window_spec' khi 'target' không được sử dụng.")
                return self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                ) is not None
        except (WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError): 
            return False
        except (UIActionError, ValueError) as e: 
            if log_output:
                self.logger.error(f"Lỗi trong quá trình check_exists: {e}")
            return False
        except Exception as e:
            if log_output:
                self.logger.error(f"Lỗi không mong muốn trong quá trình check_exists: {e}", exc_info=True)
                self._emit_event('error', f"Lỗi không mong muốn xảy ra trong quá trình kiểm tra: {e}")
            return False

    def create_snapshot(self, window_spec: Dict[str, Any], 
                        elements_map: Dict[str, Dict[str, Any]], 
                        timeout: Optional[float] = None, 
                        retry_interval: Optional[float] = None,
                        **kwargs) -> Optional[UISnapshot]:
        """
        Tạo một ảnh chụp (snapshot) của nhiều element trong một cửa sổ.

        Args:
            window_spec (Dict[str, Any]): Bộ lọc để tìm cửa sổ mục tiêu.
            elements_map (Dict[str, Dict[str, Any]]): Từ điển ánh xạ tên thân thiện
                                                      tới bộ lọc của từng element.
            timeout (Optional[float]): Thời gian chờ tối đa.

        Returns:
            Optional[UISnapshot]: Đối tượng snapshot đã tạo, hoặc None.
        """
        self._emit_event('process', f"Đang tạo snapshot cho cửa sổ '{window_spec.get('pwa_title', '...')}'...")
        effective_timeout = timeout if timeout is not None else self.config['default_timeout']
        snapshot_name = window_spec.get('pwa_title', 'snapshot')
        snapshot = UISnapshot(snapshot_name, self, effective_timeout)
        window = self.find_element(window_spec, timeout=timeout, retry_interval=retry_interval)
        if not window:
            self._emit_event('error', "Tạo snapshot thất bại: Không tìm thấy cửa sổ mục tiêu.")
            return None
        found_count = 0
        for key, spec in elements_map.items():
            try:
                element = self._find_with_retry(
                    window, spec, 0.5, 0.1, ElementNotFoundFromWindowError, 
                    AmbiguousElementError, f"element '{key}'", **kwargs
                )
                if element:
                    snapshot._add_element(key, element, window, spec)
                    found_count += 1
                    self.logger.info(f"  (+) Đã tìm thấy và thêm '{key}' vào snapshot.")
            except (ElementNotFoundFromWindowError, AmbiguousElementError) as e:
                self.logger.warning(f"  (-) Không thể tìm thấy element duy nhất cho '{key}': {e}")
        self._emit_event('success', f"Đã tạo snapshot. Tìm thấy {found_count}/{len(elements_map)} elements.")
        return snapshot

    def close(self):
        """Đóng UIController."""
        self.logger.info("Đang đóng UIController...")

    def _find_with_retry(self, search_root: UIAWrapper, 
                         spec: Dict[str, Any], 
                         timeout: float, 
                         retry_interval: float, 
                         not_found_exception: Type[Exception], 
                         ambiguous_exception: Type[Exception], 
                         entity_name: str,
                         **kwargs) -> UIAWrapper:
        """
        Tìm kiếm một element hoặc cửa sổ với cơ chế thử lại.

        Args:
            search_root (UIAWrapper): Element gốc để bắt đầu tìm kiếm.
            spec (Dict[str, Any]): Bộ lọc tìm kiếm.
            timeout (float): Thời gian chờ tối đa.
            retry_interval (float): Khoảng thời gian giữa các lần thử lại.
            not_found_exception (Type[Exception]): Ngoại lệ ném ra khi không tìm thấy.
            ambiguous_exception (Type[Exception]): Ngoại lệ ném ra khi tìm thấy nhiều kết quả.
            entity_name (str): Tên của thực thể đang tìm kiếm (ví dụ: "window").

        Returns:
            UIAWrapper: Đối tượng element/cửa sổ được tìm thấy.

        Raises:
            not_found_exception: Nếu không tìm thấy kết quả duy nhất sau thời gian chờ.
            ambiguous_exception: Nếu tìm thấy nhiều hơn một kết quả.
        """
        start_time = time.time()
        perf_logger.debug(f"BẮT ĐẦU VÒNG LẶP THỬ LẠI cho '{entity_name}' | Thời gian chờ: {timeout:.2f}s")
        
        retry_count = 0
        while True:
            retry_count += 1
            if self.state:
                if self.state.is_stopped():
                    raise UIActionError("Tác vụ đã bị người dùng dừng lại.")
                is_paused_by_panel = False
                while self.state.is_paused():
                    if not is_paused_by_panel:
                        self._emit_event('warning', "Tác vụ đã tạm dừng. Đang chờ tiếp tục...", duration=0)
                        is_paused_by_panel = True
                    time.sleep(0.5)
                if is_paused_by_panel:
                    self._emit_event('success', "Tác vụ đã tiếp tục.", duration=3)
            
            # --- SỬA LỖI: Truyền thời gian chờ còn lại vào hàm tìm kiếm ---
            remaining_timeout = start_time + timeout - time.time()
            if remaining_timeout <= 0:
                self.logger.warning(f"RETRY_LOOP TIMEOUT cho '{entity_name}' | Thời gian: {time.time() - start_time:.4f}s")
                raise not_found_exception(f"Hết thời gian chờ. Không tìm thấy {entity_name} duy nhất phù hợp với bộ lọc.\n--> Bộ lọc đã sử dụng: {spec}")
            
            candidates = self.finder.find(search_root, spec, timeout=remaining_timeout, **kwargs)
            
            if len(candidates) == 1:
                perf_logger.info(f"VÒNG LẶP THỬ LẠI THÀNH CÔNG cho '{entity_name}' | Số lần thử: {retry_count} | Thời gian: {time.time() - start_time:.4f}s")
                return candidates[0]
            elif len(candidates) > 1:
                details = [f"'{c.window_text()}'" for c in candidates[:5]]
                raise ambiguous_exception(f"Tìm thấy {len(candidates)} {entity_name} không rõ ràng. Chi tiết: {details}")
            
            perf_logger.debug(f"Lần thử {retry_count}: Không tìm thấy '{entity_name}'. Đang thử lại sau {retry_interval}s...")
            time.sleep(retry_interval)

    def take_error_screenshot(self):
        """Chụp màn hình và lưu lại khi có lỗi."""
        try:
            screenshot_dir = "error_screenshots"
            if not os.path.exists(screenshot_dir):
                os.makedirs(screenshot_dir)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(screenshot_dir, f"error_{timestamp}.png")
            ImageGrab.grab().save(filename)
            self._emit_event('warning', f"Đã lưu ảnh chụp màn hình lỗi tại {filename}")
        except Exception as e:
            self._emit_event('error', f"Không thể chụp màn hình: {e}")

    def _emit_event(self, event_type: str, message: str, **kwargs):
        """
        Gửi một sự kiện để ghi log và hiển thị thông báo.

        Args:
            event_type (str): Loại sự kiện ('info', 'success', 'warning', 'error', 'process', 'debug').
            message (str): Nội dung thông báo.
        """
        log_levels = {"info": logging.INFO, "success": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "process": logging.DEBUG, "debug": logging.DEBUG}
        self.logger.log(log_levels.get(event_type, logging.INFO), message)
        if self.event_callback and callable(self.event_callback):
            try: 
                self.event_callback(event_type, message, **kwargs)
            except Exception as e: 
                self.logger.error(f"Lỗi khi thực thi event_callback: {e}")
                
    def _wait_for_user_idle(self):
        """
        Kiểm tra và chờ cho đến khi người dùng không còn hoạt động.
        """
        if self.activity_listener:
            self.activity_listener.wait_for_user_idle()
        
    def _handle_activation(self, target_element: UIAWrapper, command: str, auto_activate: bool):
        """
        Kích hoạt cửa sổ của element mục tiêu.
        """
        try:
            top_window = core_logic.get_top_level_window(target_element)
            if top_window and (not top_window.is_active() or top_window.is_minimized()):
                if auto_activate:
                    self._emit_event('info', f"Đang tự động kích hoạt và tối đa hóa cửa sổ '{top_window.window_text()}'...")
                    top_window.maximize()
                    time.sleep(0.5) 
                else:
                    raise UIActionError(f"Cửa sổ '{top_window.window_text()}' không hoạt động. Hành động '{command}' yêu cầu kích hoạt.")
        except NoPatternInterfaceError:
            self.logger.warning(f"Element '{target_element.window_text()}' hoặc cửa sổ cha của nó không hỗ trợ WindowPattern. Bỏ qua kiểm tra kích hoạt.")
        except Exception as e:
            self.logger.error(f"Lỗi không mong muốn xảy ra trong quá trình kích hoạt: {e}", exc_info=True)
        
    def _execute_action_safely(self, element: UIAWrapper, action_str: str):
        """
        Thực hiện hành động trong một luồng an toàn.
        """
        with self._bot_acting_lock:
            self._is_bot_acting[0] = True
        try:
            self._execute_action(element, action_str)
        finally:
            with self._bot_acting_lock:
                self._is_bot_acting[0] = False
            
    def _execute_action(self, element: UIAWrapper, action_str: str):
        """
        Thực hiện một hành động cụ thể trên element.

        Args:
            element (UIAWrapper): Element mục tiêu.
            action_str (str): Chuỗi hành động.

        Raises:
            UIActionError: Nếu hành động không thực hiện được.
        """
        self.logger.debug(f"Đang thực hiện hành động '{action_str}' trên element '{element.window_text()}'")
        parts = action_str.split(':', 1)
        command = parts[0].lower().strip()
        value = parts[1] if len(parts) > 1 else None
        try:
            if command not in self.VALID_ACTIONS:
                raise ValueError(f"Hành động '{command}' không được hỗ trợ.")
            
            # Kiểm tra các hành động cần cuộn vào khung nhìn
            if command in ['click', 'double_click', 'right_click', 'select']:
                try:
                    element.scroll_into_view()
                    time.sleep(0.2)
                except Exception as e:
                    self.logger.warning(f"Không thể cuộn element vào khung nhìn (có thể không phải lỗi): {e}")

            if command == 'click':
                element.click_input()
            elif command == 'double_click':
                element.double_click_input()
            elif command == 'right_click':
                element.right_click_input()
            elif command == 'focus':
                element.set_focus()
            elif command == 'invoke':
                element.invoke()
            elif command == 'toggle':
                element.toggle()
            elif command == 'scroll':
                if value is None:
                    raise ValueError("Hành động 'scroll' yêu cầu hướng cuộn, ví dụ: 'scroll:down'")
                parts = value.split(',')
                direction = parts[0].strip().lower()
                amount = int(parts[1]) if len(parts) > 1 else 1
                if direction not in ['up', 'down', 'left', 'right']:
                    raise ValueError(f"Hướng cuộn không hợp lệ: '{direction}'")
                element.scroll(direction, amount)
            elif command == 'mouse_scroll':
                if value is None:
                    value = "down"
                parts = value.split(',')
                direction = parts[0].strip().lower()
                wheel_dist = -5 if direction == 'down' else 5
                rect = element.rectangle()
                coords = (rect.mid_point().x, rect.mid_point().y)
                pywinauto_mouse.move(coords=coords)
                time.sleep(0.1)
                pywinauto_mouse.scroll(coords=coords, wheel_dist=wheel_dist)
            elif command in ('select', 'set_text', 'paste_text', 'type_keys', 'send_message_text'):
                if value is None:
                    raise ValueError(f"Hành động '{command}' yêu cầu một giá trị.")
                if command == 'select':
                    element.select(value)
                elif command == 'set_text':
                    element.set_edit_text(value)
                elif command == 'paste_text':
                    pyperclip.copy(value)
                    element.type_keys('^a^v', pause=0.1) 
                elif command == 'type_keys':
                    element.type_keys(value, with_spaces=True, with_newlines=True, pause=0.01)
                elif command == 'send_message_text':
                    if not element.handle:
                        raise UIActionError("'send_message_text' yêu cầu handle cửa sổ.")
                    win32api.SendMessage(element.handle, win32con.WM_SETTEXT, 0, value)
        except Exception as e:
            raise UIActionError(f"Thực thi hành động '{action_str}' thất bại. Lỗi gốc: {type(e).__name__} - {e}") from e
