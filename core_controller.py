# functions/core_controller.py
# Lõi điều phối các hoạt động tự động hóa UI.
# --- VERSION 15.0 (Single Scan Mode & Timeout Consistency):
# - Đã điều chỉnh _find_with_retry để hỗ trợ chế độ "quét một lần duy nhất"
#   khi timeout=0 và retry_interval=0 được truyền vào.
# - Đảm bảo tham số 'timeout' được truyền nhất quán từ get_next_state
#   xuống check_exists và các hàm tìm kiếm cấp thấp hơn.
# - Loại bỏ lỗi "Hết thời gian chờ. Không tìm thấy..." khi quá trình tìm kiếm
#   bị hủy bỏ sớm do thời gian chờ nội bộ quá ngắn.

import logging
import time
import threading
import sys
import os
from datetime import datetime

# --- Thư viện yêu cầu ---
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
    print(f"Lỗi: Không thể import thư viện, vui lòng cài đặt: {e}")
    sys.exit(1)

# --- Cấu hình logging ---
perf_logger = logging.getLogger('PerformanceLogger')
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

# --- Import các thành phần đã tái cấu trúc ---
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
        print("LỖI NGHIÊM TRỌNG: 'core_logic.py', 'ui_notifier.py', hoặc 'ui_control_panel.py' phải nằm trong cùng thư mục.")
        class AutomationState:
            def is_stopped(self): return False
            def is_paused(self): return False
        sys.exit(1)


# --- Các định nghĩa Exception tùy chỉnh ---
class UIActionError(Exception): pass
class WindowNotFoundError(UIActionError): pass
class ElementNotFoundFromWindowError(UIActionError): pass
class AmbiguousElementError(UIActionError): pass
class WaitTimeoutError(UIActionError): pass

# --- Lớp UISnapshot ---
class UISnapshot:
    """Quản lý một "ảnh chụp" của các element UI."""
    DEFAULT_HEAL_TIMEOUT_CAP = 2.0

    def __init__(self, name, controller_instance, creation_timeout):
        self.name = name
        self._elements = {}
        self._recipes = {}
        self._controller = controller_instance
        self._creation_timeout = creation_timeout
        self.logger = logging.getLogger(f"UISnapshot({self.name})")

    def _add_element(self, key, element, parent_window=None, spec=None):
        """
        Mô tả:
        Thêm một element vào snapshot.
        Nếu parent_window và spec được cung cấp, nó sẽ tạo một 'recipe' để tự phục hồi.
        Nếu không, element được thêm vào mà không có khả năng tự phục hồi.
        """
        self._elements[key] = element
        if parent_window and spec:
            self._recipes[key] = {'parent': parent_window, 'spec': spec}
        else:
            self._recipes[key] = None # Đánh dấu là không thể tự phục hồi

    def __getitem__(self, key):
        element = self._elements.get(key)
        try:
            if element and element.is_visible():
                return element
        except Exception:
            pass

        self.logger.warning(f"Element '{key}' trong snapshot '{self.name}' đã cũ. Đang cố gắng tự phục hồi...")
        recipe = self._recipes.get(key)
        if not recipe:
            self.logger.warning(f"Element '{key}' không có 'recipe' để tự phục hồi. Không thể khôi phục.")
            return None

        parent_window = recipe['parent']
        element_spec = recipe['spec']
        try:
            if not parent_window or not parent_window.is_visible():
                return None
            heal_timeout = min(self._creation_timeout, self.DEFAULT_HEAL_TIMEOUT_CAP)
            healed_element = self._controller.find_element(
                window_spec={'win32_handle': parent_window.handle},
                element_spec=element_spec,
                timeout=heal_timeout
            )
            if healed_element:
                self._elements[key] = healed_element
                return healed_element
            else:
                return None
        except Exception:
            return None

    @property
    def found_elements(self):
        return self._elements

def create_notifier_callback(notifier_instance):
    if not notifier_instance or not isinstance(notifier_instance, StatusNotifier):
        return None
    def event_handler(event_type, message, **kwargs):
        notifier_instance.update_status(text=message, style=event_type, duration=kwargs.get('duration'))
    return event_handler

DEFAULT_CONTROLLER_CONFIG = {
    'backend': 'uia', 'human_interruption_detection': False, 'human_cooldown_period': 5,
    'secure_mode': False, 'default_timeout': 10, 'default_retry_interval': 0.5,
    'log_level': 'info'
}

class UIController:
    """
    Điều phối các hoạt động tự động hóa UI.
    """
    GETTABLE_PROPERTIES = {'text', 'texts', 'value', 'is_toggled'}.union(core_logic.SUPPORTED_FILTER_KEYS)
    BACKGROUND_SAFE_ACTIONS = {'set_text', 'send_message_text'}
    SENSITIVE_ACTIONS = {'paste_text', 'type_keys', 'set_text'}
    VALID_ACTIONS = {action['name'] for action in core_logic.ACTION_DEFINITIONS}.union({'mouse_scroll'})

    def __init__(self,
                 notifier=None,
                 event_callback=None,
                 automation_state=None,
                 **kwargs):
        """
        Mô tả:
        Khởi tạo UIController.
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
        finder_log_callback = None
        if log_level == 'debug':
            def finder_logger(level, message):
                self.logger.debug(f"[ElementFinder] L:{level} - M:{message}")
            finder_log_callback = finder_logger

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

    def find_element(self, window_spec,
                     element_spec=None,
                     timeout=None,
                     retry_interval=None,
                     **kwargs):
        """
        Mô tả:
        Tìm một element duy nhất dựa trên bộ lọc (spec). Hỗ trợ thuộc tính 'child_path'
        để điều hướng đến các element con.
        """
        timeout = timeout if timeout is not None else self.config['default_timeout']
        retry_interval = retry_interval if retry_interval is not None else self.config['default_retry_interval']
        try:
            # Tìm cửa sổ gốc
            window = self._find_with_retry(
                self.desktop, window_spec, timeout, retry_interval, WindowNotFoundError,
                AmbiguousElementError, "window", **kwargs
            )

            # Nếu không có element_spec, trả về cửa sổ
            if not element_spec:
                return window

            # Tách child_path ra khỏi spec chính
            spec_to_find = element_spec.copy()
            child_path = spec_to_find.pop('child_path', None)

            # Xử lý các trường hợp tìm kiếm phức tạp khác
            search_root = window
            if 'search_root_spec' in spec_to_find:
                container_spec = spec_to_find.pop('search_root_spec')
                search_root = self.find_element(window_spec, container_spec, timeout, retry_interval, **kwargs)
                if not search_root:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element container.")

            # Tìm element cha (base element)
            base_element = self._find_with_retry(
                search_root, spec_to_find, timeout, retry_interval, ElementNotFoundFromWindowError,
                AmbiguousElementError, f"element trong '{search_root.window_text()}'", **kwargs
            )

            if not base_element:
                return None

            # Nếu có child_path, bắt đầu điều hướng từ element cha
            if child_path:
                return self._traverse_child_path(base_element, child_path)
            else:
                return base_element

        except (WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError) as e:
            self.logger.warning(f"Không thể tìm thấy element duy nhất: {e}")
            return None

    def get_next_state(self, cases, timeout, description=None):
        """
        Mô tả:
        Chờ cho đến khi một trong các trạng thái UI được định nghĩa xuất hiện.
        """
        display_message = description or "Đang chờ trạng thái UI kế tiếp..."
        self._emit_event('process', display_message)

        start_time = time.time()
        while time.time() - start_time < timeout:
            remaining_timeout = timeout - (time.time() - start_time)
            # Đảm bảo remaining_timeout không âm
            if remaining_timeout < 0:
                remaining_timeout = 0 

            for state_key, specs in cases.items():
                window_spec = specs.get("window_spec")
                element_spec = specs.get("element_spec")

                if not window_spec:
                    continue

                # Truyền remaining_timeout xuống check_exists
                if self.check_exists(window_spec=window_spec, element_spec=element_spec, timeout=remaining_timeout, log_output=False):
                    self._emit_event('success', f"Đã phát hiện trạng thái '{state_key}'.")
                    return state_key

            time.sleep(self.config['default_retry_interval'])

        self._emit_event('warning', f"Hết thời gian chờ: Không phát hiện được trạng thái nào sau {timeout} giây.")
        return "timeout"

    def run_action(self, action,
                   target=None,
                   window_spec=None,
                   element_spec=None,
                   timeout=None,
                   auto_activate=True,
                   retry_interval=None,
                   description=None,
                   notify_style='info',
                   delay_before=0,
                   delay_after=0,
                   scroll_if_needed=False,
                   scroll_container_spec=None,
                   scroll_direction='down',
                   scroll_amount=1,
                   scroll_max_attempts=20,
                   raise_on_failure=False,
                   **kwargs):
        """
        Mô tả:
        Thực hiện một hành động trên một element. Nếu raise_on_failure=True,
        sẽ tự động báo lỗi (raise exception) khi thất bại.
        """
        log_action = action
        if self.config['secure_mode'] and ':' in action:
            command, _ = action.split(':', 1)
            if command.lower().strip() in self.SENSITIVE_ACTIONS:
                log_action = f"{command}:********"
        display_message = description or f"Đang thực hiện tác vụ: {log_action}"
        self._emit_event(notify_style if description else 'info', display_message)

        try:
            self._wait_for_user_idle()

            target_element = None
            if target:
                if not isinstance(target, UIAWrapper):
                    raise UIActionError("Target được cung cấp không phải là một element UI hợp lệ.")
                target_element = target
            else:
                if not window_spec:
                    raise ValueError("Phải cung cấp 'window_spec' và 'element_spec'.")
                target_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not target_element:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element mục tiêu ban đầu.")

            # Tự động cuộn nếu cần
            if scroll_if_needed and not target_element.is_visible():
                self._emit_event('process', f"'{target_element.window_text()}' is not visible. Scrolling to find it...")

                scroll_container = None
                if scroll_container_spec:
                    scroll_container = self.find_element(window_spec, scroll_container_spec, timeout=5)
                    if not scroll_container:
                        raise UIActionError("Could not find the specified scroll container.")

                is_found = self._scroll_to_find_element(
                    target_element=target_element,
                    scroll_container=scroll_container,
                    direction=scroll_direction,
                    amount=scroll_amount,
                    max_attempts=scroll_max_attempts
                )

                if not is_found:
                    raise UIActionError(f"Could not make '{target_element.window_text()}' visible even after scrolling.")
                else:
                    self._emit_event('success', f"Found '{target_element.window_text()}' after scrolling.")


            if delay_before > 0:
                time.sleep(delay_before)

            command = action.split(':', 1)[0].lower().strip()
            if command not in self.BACKGROUND_SAFE_ACTIONS:
                self._handle_activation(target_element, command, auto_activate)

            self._execute_action_safely(target_element, action)

            if delay_after > 0:
                time.sleep(delay_after)

            self._emit_event('success', f"Thành công: {display_message}")
            return True
        except (UIActionError, WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError, ValueError) as e:
            self.logger.error(f"Lỗi khi thực hiện '{display_message}': {e}", exc_info=False)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            if raise_on_failure:
                raise e
            return False
        except Exception as e:
            self.logger.critical(f"Lỗi không mong muốn khi thực hiện '{display_message}': {e}", exc_info=True)
            self._emit_event('error', f"Thất bại: {display_message}")
            self.take_error_screenshot()
            if raise_on_failure:
                raise e
            return False

    def wait_for_state(self, state_spec,
                       target=None,
                       window_spec=None,
                       element_spec=None,
                       timeout=None,
                       retry_interval=None,
                       description=None,
                       **kwargs):
        """
        Mô tả:
        Chờ một element đạt được trạng thái mong muốn.
        """
        effective_timeout = timeout if timeout is not None else self.config['default_timeout']
        effective_retry = retry_interval if retry_interval is not None else self.config['default_retry_interval']
        display_message = description or f"Đang chờ trạng thái: {state_spec}"
        self._emit_event('process', display_message)

        try:
            monitor_element = None
            if target:
                if not isinstance(target, UIAWrapper):
                    raise UIActionError("Target được cung cấp không phải là một element UI hợp lệ.")
                monitor_element = target
            else:
                if not window_spec:
                    raise ValueError("Phải cung cấp 'window_spec' và 'element_spec'.")
                monitor_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not monitor_element:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element để theo dõi trạng thái.")

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

            raise WaitTimeoutError(f"Hết thời gian chờ sau {effective_timeout}s.")

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

    def get_property(self, property_name,
                     target=None,
                     window_spec=None,
                     element_spec=None,
                     timeout=None,
                     retry_interval=None,
                     description=None,
                     notify_style='info',
                     **kwargs):
        """
        Mô tả:
        Lấy giá trị của một thuộc tính từ một element.
        """
        display_message = description or f"Đang lấy thuộc tính '{property_name}'"
        self._emit_event(notify_style if description else 'info', display_message)
        if property_name not in self.GETTABLE_PROPERTIES:
            raise ValueError(f"Thuộc tính '{property_name}' không được hỗ trợ.")

        try:
            self._wait_for_user_idle()

            target_element = None
            if target:
                if not isinstance(target, UIAWrapper):
                    raise UIActionError("Target không phải là element UI hợp lệ.")
                target_element = target
            else:
                if not window_spec:
                    raise ValueError("Phải cung cấp 'window_spec' và 'element_spec'.")
                target_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not target_element:
                    raise ElementNotFoundFromWindowError("Không thể tìm thấy element mục tiêu.")

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

    def check_exists(self, target=None,
                     window_spec=None,
                     element_spec=None,
                     timeout=None,
                     retry_interval=None,
                     log_output=True,
                     **kwargs):
        """
        Mô tả:
        Kiểm tra xem một element có tồn tại hay không.
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
                # Sử dụng thời gian chờ được truyền vào, thay vì giá trị cố định
                return self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                ) is not None
        except (UIActionError, ValueError) as e:
            if log_output:
                self.logger.error(f"Lỗi trong quá trình check_exists: {e}")
            return False
        except Exception as e:
            if log_output:
                self.logger.error(f"Lỗi không mong muốn trong quá trình check_exists: {e}", exc_info=True)
                self._emit_event('error', f"Lỗi không mong muốn xảy ra trong quá trình kiểm tra: {e}")
            return False

    def create_snapshot(self, window_spec,
                        elements_map,
                        timeout=None,
                        retry_interval=None,
                        **kwargs):
        """
        Mô tả:
        Tạo một ảnh chụp (snapshot) của nhiều element trong một cửa sổ.
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
            except (ElementNotFoundFromWindowError, AmbiguousElementError):
                pass
        self._emit_event('success', f"Đã tạo snapshot. Tìm thấy {found_count}/{len(elements_map)} elements.")
        return snapshot

    def close(self):
        """Đóng UIController."""
        self.logger.info("Đang đóng UIController...")

    def _find_with_retry(self, search_root,
                         spec,
                         timeout,
                         retry_interval,
                         not_found_exception,
                         ambiguous_exception,
                         entity_name,
                         log_output=True,
                         **kwargs):
        """
        Mô tả:
        Tìm kiếm một element hoặc cửa sổ với cơ chế thử lại.
        Hàm này hỗ trợ chế độ "quét một lần duy nhất" nếu timeout=0 và retry_interval=0.
        """
        start_time = time.time()

        # THAY ĐỔI MỚI: Xử lý trường hợp timeout=0 và retry_interval=0 để chỉ quét một lần
        if timeout == 0 and retry_interval == 0:
            # Thực hiện một lần quét duy nhất. Cung cấp một timeout nhỏ cho finder.find
            # để nó có đủ thời gian thực hiện việc quét và lọc ban đầu mà không bị ngắt ngay lập tức.
            candidates = self.finder.find(search_root, spec, timeout=0.1, **kwargs) 

            if len(candidates) == 1:
                return candidates[0]
            elif len(candidates) > 1:
                details = [f"'{c.window_text()}'" for c in candidates[:5]]
                raise ambiguous_exception(f"Tìm thấy {len(candidates)} {entity_name} không rõ ràng. Chi tiết: {details}")
            else:
                if log_output:
                    self.logger.warning(f"Không tìm thấy {entity_name} duy nhất trong lần quét đầu tiên.\n--> Bộ lọc đã sử dụng: {spec}")
                raise not_found_exception(f"Không tìm thấy {entity_name} duy nhất trong lần quét đầu tiên.\n--> Bộ lọc đã sử dụng: {spec}")


        # Logic thử lại thông thường nếu timeout hoặc retry_interval > 0
        while True:
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

            remaining_timeout = start_time + timeout - time.time()
            if remaining_timeout <= 0:
                if log_output:
                    self.logger.warning(f"Hết thời gian chờ. Không tìm thấy {entity_name} duy nhất.\n--> Bộ lọc đã sử dụng: {spec}")
                raise not_found_exception(f"Hết thời gian chờ. Không tìm thấy {entity_name} duy nhất.\n--> Bộ lọc đã sử dụng: {spec}")

            # Truyền remaining_timeout xuống finder.find
            candidates = self.finder.find(search_root, spec, timeout=remaining_timeout, **kwargs)

            if len(candidates) == 1:
                return candidates[0]
            elif len(candidates) > 1:
                details = [f"'{c.window_text()}'" for c in candidates[:5]]
                raise ambiguous_exception(f"Tìm thấy {len(candidates)} {entity_name} không rõ ràng. Chi tiết: {details}")

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

    def _emit_event(self, event_type, message, **kwargs):
        """Gửi một sự kiện để ghi log và hiển thị thông báo."""
        log_levels = {"info": logging.INFO, "success": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "process": logging.DEBUG, "debug": logging.DEBUG}
        self.logger.log(log_levels.get(event_type, logging.INFO), message)
        if self.event_callback and callable(self.event_callback):
            try:
                self.event_callback(event_type, message, **kwargs)
            except Exception as e:
                self.logger.error(f"Lỗi khi thực thi event_callback: {e}")

    def _wait_for_user_idle(self):
        """Kiểm tra và chờ cho đến khi người dùng không còn hoạt động."""
        if self.activity_listener:
            self.activity_listener.wait_for_user_idle()

    def _handle_activation(self, target_element, command, auto_activate):
        """Kích hoạt cửa sổ của element mục tiêu."""
        try:
            top_window = core_logic.get_top_level_window(target_element)
            if top_window and (not top_window.is_active() or top_window.is_minimized()):
                if auto_activate:
                    top_window.maximize()
                    time.sleep(0.5)
                else:
                    raise UIActionError(f"Cửa sổ '{top_window.window_text()}' không hoạt động.")
        except NoPatternInterfaceError:
            self.logger.warning(f"Element '{target_element.window_text()}' không hỗ trợ WindowPattern.")
        except Exception as e:
            self.logger.error(f"Lỗi không mong muốn xảy ra trong quá trình kích hoạt: {e}", exc_info=True)

    def _scroll_to_find_element(self, target_element, scroll_container, direction, amount, max_attempts):
        """
        Mô tả:
        Thực hiện cuộn một cách an toàn để tìm một element.
        Hàm này đảm bảo `human_activity_listener` không bị kích hoạt nhầm.
        """
        # Xác định vị trí và hướng cuộn
        scroll_coords = None
        if scroll_container:
            rect = scroll_container.rectangle()
            scroll_coords = rect.mid_point()

        # Chuyển hướng thành wheel_dist cho pywinauto
        wheel_dist = 0
        if direction == 'down':
            wheel_dist = -amount
        elif direction == 'up':
            wheel_dist = amount
        # Lưu ý: pywinauto chỉ hỗ trợ cuộn dọc. Cuộn ngang cần logic khác.
        if direction in ['left', 'right']:
            self.logger.warning("Cuộn ngang chưa được hỗ trợ trực tiếp. Bỏ qua.")
            return target_element.is_visible()

        # Bắt đầu vòng lặp cuộn
        for i in range(max_attempts):
            if target_element.is_visible():
                return True # Đã tìm thấy

            self.logger.warning(f"Scrolling {direction} (Attempt {i+1})...")

            # --- VÙNG AN TOÀN ---
            # Bật cờ báo hiệu bot đang hoạt động trước khi cuộn
            with self._bot_acting_lock:
                self._is_bot_acting[0] = True
            try:
                # Di chuyển chuột đến vùng cuộn nếu có
                if scroll_coords:
                    pywinauto_mouse.move(coords=(scroll_coords.x, scroll_coords.y))
                # Thực hiện hành động cuộn
                pywinauto_mouse.scroll(coords=scroll_coords, wheel_dist=wheel_dist)
            finally:
                # Tắt cờ báo hiệu bot đã hoạt động xong
                with self._bot_acting_lock:
                    self._is_bot_acting[0] = False
            # --- KẾT THÚC VÙNG AN TOÀN ---

            time.sleep(0.3) # Chờ giao diện cập nhật sau khi cuộn

        # Nếu hết vòng lặp mà không thấy, kiểm tra lần cuối
        return target_element.is_visible()

    def _traverse_child_path(self, parent_element, path):
        """
        Mô tả:
        Điều hướng từ một element cha đến một element con/cháu theo một đường dẫn cho trước.
        Sử dụng chỉ số bắt đầu từ 1.
        """
        if not isinstance(path, list) or not all(isinstance(i, int) and i != 0 for i in path):
            raise ValueError("child_path phải là một danh sách các số nguyên khác 0.")

        current_element = parent_element
        # Lặp qua từng bước trong đường dẫn
        for i, step_index in enumerate(path):
            children = current_element.children()
            num_children = len(children)
            
            # Chuyển đổi chỉ số 1-based thành 0-based
            # Hỗ trợ cả số âm (đếm từ cuối)
            zero_based_index = step_index - 1 if step_index > 0 else step_index

            # Kiểm tra xem chỉ số có hợp lệ không
            if not (-num_children <= zero_based_index < num_children):
                path_so_far = " -> ".join(map(str, path[:i+1]))
                raise ElementNotFoundFromWindowError(
                    f"Không tìm thấy con tại vị trí {step_index}. "
                    f"Element chỉ có {num_children} con. (Đường dẫn: {path_so_far})"
                )
            
            # Di chuyển đến element con tiếp theo
            current_element = children[zero_based_index]
        
        return current_element


    def _execute_action_safely(self, element, action_str):
        """Thực hiện hành động trong một luồng an toàn."""
        with self._bot_acting_lock:
            self._is_bot_acting[0] = True
        try:
            self._execute_action(element, action_str)
        finally:
            with self._bot_acting_lock:
                self._is_bot_acting[0] = False

    def _execute_action(self, element, action_str):
        """
        Mô tả:
        Thực hiện một hành động cụ thể trên element.
        """
        parts = action_str.split(':', 1)
        command = parts[0].lower().strip()
        value = parts[1] if len(parts) > 1 else None

        try:
            if command not in self.VALID_ACTIONS:
                raise ValueError(f"Hành động '{command}' không được hỗ trợ.")

            if command in ['click', 'double_click', 'right_click', 'select']:
                try:
                    element.scroll_into_view()
                    time.sleep(0.2)
                except Exception as e:
                    self.logger.warning(f"Không thể cuộn element vào khung nhìn: {e}")

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
                parts = value.split(',')
                direction = parts[0].strip().lower()
                amount = int(parts[1]) if len(parts) > 1 else 1
                element.scroll(direction, amount)
            elif command == 'mouse_scroll':
                value = value if value else "down"
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
            raise UIActionError(f"Thực thi hành động '{action_str}' thất bại. Lỗi gốc: {e}") from e
