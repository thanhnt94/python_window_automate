# core_controller.py
# --- VERSION 9.5 (Definitive Scoped Search Fix):
# - Acknowledged user feedback and implemented the correct, robust logic for
#   `search_root_spec` and `child_at_index`.
# - The `find_element` method now correctly resolves the `search_root_spec` into
#   a parent element object *first*.
# - It then uses this resolved object as the search root for subsequent operations,
#   including the fast `child_at_index` lookup.
# - This fixes all previous crashes and provides the intended clean, declarative syntax.

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
except ImportError:
    try:
        import core_logic
        from ui_notifier import StatusNotifier
        from ui_control_panel import AutomationState
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
    DEFAULT_HEAL_TIMEOUT_CAP = 2.0

    def __init__(self, name: str, controller_instance: 'UIController', creation_timeout: float):
        self.name = name
        self._elements: Dict[str, UIAWrapper] = {}
        self._recipes: Dict[str, Dict[str, Any]] = {}
        self._controller = controller_instance
        self._creation_timeout = creation_timeout
        self.logger = logging.getLogger(f"UISnapshot({self.name})")

    def _add_element(self, key: str, element: UIAWrapper, parent_window: UIAWrapper, spec: Dict[str, Any]):
        self._elements[key] = element
        self._recipes[key] = {'parent': parent_window, 'spec': spec}

    def __getitem__(self, key: str) -> Optional[UIAWrapper]:
        element = self._elements.get(key)
        try:
            if element and element.is_visible():
                return element
        except Exception:
            pass

        self.logger.warning(f"Element '{key}' in snapshot '{self.name}' is stale. Attempting to self-heal...")
        recipe = self._recipes.get(key)
        if not recipe:
            self.logger.error(f"Cannot heal '{key}': No recipe found.")
            return None

        parent_window = recipe['parent']
        element_spec = recipe['spec']
        try:
            if not parent_window or not parent_window.is_visible():
                self.logger.error(f"Cannot heal '{key}': The parent window is no longer valid.")
                return None
            heal_timeout = min(self._creation_timeout, self.DEFAULT_HEAL_TIMEOUT_CAP)
            healed_element = self._controller.find_element(
                window_spec={'win32_handle': parent_window.handle},
                element_spec=element_spec,
                timeout=heal_timeout
            )
            if healed_element:
                self.logger.info(f"Self-healing successful for '{key}'.")
                self._elements[key] = healed_element
                return healed_element
            else:
                self.logger.error(f"Self-healing failed for '{key}': Element could not be re-found within {heal_timeout:.1f}s.")
                return None
        except Exception as e:
            self.logger.error(f"An unexpected error occurred during self-healing for '{key}': {e}")
            return None

    def __contains__(self, key: str) -> bool:
        return key in self._elements

    def get(self, key: str, default: Any = None) -> Optional[UIAWrapper]:
        return self[key] if key in self else default

    @property
    def found_elements(self) -> Dict[str, UIAWrapper]:
        return self._elements

class HumanActivityListener:
    def __init__(self, cooldown_period: float, bot_acting_lock: threading.Lock, is_bot_acting_ref: List[bool]):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._last_human_activity_time: float = time.time() - cooldown_period
        self._cooldown_period: float = cooldown_period
        self._bot_acting_lock: threading.Lock = bot_acting_lock
        self._is_bot_acting_ref: List[bool] = is_bot_acting_ref
        self._listener_thread = threading.Thread(target=self._run_listeners, daemon=True)
        self._listener_thread.start()
        self.logger.info("Human activity listener started.")
    def _update_last_activity(self, *args):
        with self._bot_acting_lock:
            if not self._is_bot_acting_ref[0]: self._last_human_activity_time = time.time()
    def _run_listeners(self):
        try:
            with mouse.Listener(on_move=self._update_last_activity, on_click=self._update_last_activity, on_scroll=self._update_last_activity) as m_listener:
                with keyboard.Listener(on_press=self._update_last_activity) as k_listener:
                    m_listener.join()
                    k_listener.join()
        except Exception as e: self.logger.error(f"Error in input listener thread: {e}", exc_info=True)
    def wait_for_user_idle(self, event_emitter_callback: Optional[Callable]):
        is_paused = False
        while time.time() - self._last_human_activity_time < self._cooldown_period:
            if not is_paused:
                if event_emitter_callback: event_emitter_callback('warning', "User activity detected! Pausing automation...")
                is_paused = True
            time.sleep(1)
        if is_paused:
            if event_emitter_callback: event_emitter_callback('success', "User is idle. Resuming automation...", duration=3)

def create_notifier_callback(notifier_instance: 'StatusNotifier') -> Optional[Callable]:
    if not notifier_instance or not isinstance(notifier_instance, StatusNotifier): return None
    def event_handler(event_type: str, message: str, **kwargs):
        notifier_instance.update_status(text=message, style=event_type, duration=kwargs.get('duration'))
    return event_handler

DEFAULT_CONTROLLER_CONFIG: Dict[str, Any] = {
    'backend': 'uia', 'human_interruption_detection': False, 'human_cooldown_period': 5,
    'secure_mode': False, 'default_timeout': 10, 'default_retry_interval': 0.5,
    'log_level': 'info'
}

class UIController:
    GETTABLE_PROPERTIES: set = {'text', 'texts', 'value', 'is_toggled'}.union(core_logic.SUPPORTED_FILTER_KEYS)
    BACKGROUND_SAFE_ACTIONS: set = {'set_text', 'send_message_text'}
    SENSITIVE_ACTIONS: set = {'paste_text', 'type_keys', 'set_text'}
    VALID_ACTIONS: set = {action['name'] for action in core_logic.ACTION_DEFINITIONS}.union({'mouse_scroll'})

    def __init__(self, 
                 notifier: Optional['StatusNotifier'] = None, 
                 event_callback: Optional[Callable] = None,
                 automation_state: Optional['AutomationState'] = None,
                 **kwargs):
        self.logger = logging.getLogger(self.__class__.__name__)
        if event_callback: self.event_callback = event_callback
        elif notifier and isinstance(notifier, StatusNotifier): self.event_callback = create_notifier_callback(notifier)
        else: self.event_callback = None
        
        self.config: Dict[str, Any] = {**DEFAULT_CONTROLLER_CONFIG, **kwargs}
        
        self.state = automation_state
        self.desktop = Desktop(backend=self.config['backend'])
        try:
            self.uia = comtypes.client.CreateObject(UIA.CUIAutomation)
            self.tree_walker = self.uia.ControlViewWalker
        except (OSError, comtypes.COMError) as e:
            self.logger.critical(f"Fatal error initializing COM: {e}", exc_info=True)
            raise

        log_level = self.config.get('log_level', 'info').lower()
        if log_level == 'debug':
            def finder_logger(level, message):
                self.logger.debug(f"[ElementFinder] L:{level} - M:{message}")
            finder_log_callback = finder_logger
            self.logger.info("UIController initialized with log_level='debug' (verbose).")
        else:
            def dummy_log(level, message):
                pass
            finder_log_callback = dummy_log
            self.logger.info(f"UIController initialized with log_level='{log_level}' (suppressed finder logs).")

        self.finder = core_logic.ElementFinder(
            uia_instance=self.uia, 
            tree_walker=self.tree_walker, 
            log_callback=finder_log_callback
        )
        
        self._bot_acting_lock = threading.Lock()
        self._is_bot_acting: List[bool] = [False]
        self.activity_listener: Optional[HumanActivityListener] = None
        if self.config['human_interruption_detection']:
            self.activity_listener = HumanActivityListener(cooldown_period=self.config['human_cooldown_period'], bot_acting_lock=self._bot_acting_lock, is_bot_acting_ref=self._is_bot_acting)

    # ... (Other methods like get_next_state, run_action, etc. are unchanged) ...

    def find_element(self, window_spec: Dict[str, Any], 
                     element_spec: Optional[Dict[str, Any]] = None, 
                     timeout: Optional[float] = None, 
                     retry_interval: Optional[float] = None,
                     **kwargs) -> Optional[UIAWrapper]:
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
                self.logger.info(f"Scoped search: Finding container with spec: {container_spec}")
                
                # SỬA LỖI: Tìm container trước, không quan tâm nó nằm trong window nào
                # vì spec của nó (ví dụ: handle) đã là duy nhất.
                search_root = self.find_element(
                    window_spec=window_spec, # Vẫn dùng window_spec gốc
                    element_spec=container_spec,
                    timeout=timeout,
                    retry_interval=retry_interval,
                    **kwargs
                )

                if not search_root:
                    raise ElementNotFoundFromWindowError("Could not find the container element for scoped search.")
                self.logger.info(f"Container found: '{search_root.window_text()}'. Searching for target inside.")
            
            if 'child_at_index' in spec_to_find:
                index = spec_to_find['child_at_index']
                if not isinstance(index, int):
                    raise ValueError(f"'child_at_index' must be an integer, but got {type(index)}.")
                
                self.logger.info(f"Direct access: Getting child at index {index} from '{search_root.window_text()}'.")
                
                try:
                    children = search_root.children()
                    if len(children) > index:
                        return children[index]
                    else:
                        raise ElementNotFoundFromWindowError(f"Child at index {index} not found. Container only has {len(children)} children.")
                except Exception as e:
                    raise UIActionError(f"Failed to get child at index {index}: {e}")

            return self._find_with_retry(
                search_root, spec_to_find, timeout, retry_interval, ElementNotFoundFromWindowError, 
                AmbiguousElementError, f"element in '{search_root.window_text()}'", **kwargs
            )
        except (WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError) as e:
            self.logger.warning(f"Could not find unique element: {e}")
            return None

    # ... (Rest of the class methods are unchanged) ...
    def get_next_state(self, cases: Dict[str, Dict[str, Any]], timeout: float, description: Optional[str] = None) -> str:
        display_message = description or "Waiting for the next UI state..."
        self._emit_event('process', display_message)
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            for state_key, specs in cases.items():
                window_spec = specs.get("window_spec")
                element_spec = specs.get("element_spec")
                
                if not window_spec:
                    self.logger.warning(f"Skipping case '{state_key}' because 'window_spec' is missing.")
                    continue
                
                if self.check_exists(window_spec=window_spec, element_spec=element_spec, timeout=0.2, log_output=False):
                    self._emit_event('success', f"State '{state_key}' detected.")
                    return state_key
            
            time.sleep(self.config['default_retry_interval'])
        
        self._emit_event('warning', f"Timeout: No expected state was detected after {timeout} seconds.")
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
        log_action = action
        if self.config['secure_mode'] and ':' in action:
            command, _ = action.split(':', 1)
            if command.lower().strip() in self.SENSITIVE_ACTIONS: log_action = f"{command}:********"
        display_message = description or f"Executing task: {log_action}"
        verbose = description is None
        self._emit_event(notify_style if description else 'info', display_message)
        
        try:
            self._wait_for_user_idle()
            
            if target:
                if not isinstance(target, UIAWrapper): raise UIActionError(f"Provided 'target' is not a valid UI element.")
                target_element = target
                self.logger.info(f"Using provided target element '{target.window_text()}'. Skipping scan.")
            else:
                if not window_spec: raise ValueError("Must provide 'window_spec' and 'element_spec' when 'target' is not used.")
                target_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not target_element: raise ElementNotFoundFromWindowError("Could not find the target element via scanning.")

            if delay_before > 0:
                self.logger.info(f"Delaying for {delay_before} second(s) before action.")
                time.sleep(delay_before)

            command = action.split(':', 1)[0].lower().strip()
            if command not in self.BACKGROUND_SAFE_ACTIONS:
                self._handle_activation(target_element, command, auto_activate)
            if verbose: self._emit_event('process', f"Executing action '{log_action}'...")
            self._execute_action_safely(target_element, action)
            
            if delay_after > 0:
                self.logger.info(f"Delaying for {delay_after} second(s) after action.")
                time.sleep(delay_after)

            self._emit_event('success', f"Success: {display_message}")
            return True
        except (UIActionError, WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError, ValueError) as e:
            self.logger.error(f"Error performing '{display_message}': {e}", exc_info=False)
            self._emit_event('error', f"Failed: {display_message}")
            self.take_error_screenshot()
            return False
        except Exception as e:
            self.logger.critical(f"Unexpected error performing '{display_message}': {e}", exc_info=True)
            self._emit_event('error', f"Failed: {display_message}")
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
        effective_timeout = timeout if timeout is not None else self.config['default_timeout']
        effective_retry = retry_interval if retry_interval is not None else self.config['default_retry_interval']
        display_message = description or f"Waiting for state: {state_spec}"
        self._emit_event('process', display_message)

        try:
            if target:
                if not isinstance(target, UIAWrapper): raise UIActionError(f"Provided 'target' is not a valid UI element.")
                monitor_element = target
            else:
                if not window_spec: raise ValueError("Must provide 'window_spec' and 'element_spec' when 'target' is not used.")
                monitor_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not monitor_element: raise ElementNotFoundFromWindowError("Could not find element to monitor state.")
            
            self.logger.info(f"Monitoring element '{monitor_element.window_text()}' for state change...")
            start_time = time.time()
            while time.time() - start_time < effective_timeout:
                self._wait_for_user_idle()
                all_conditions_met = True
                for key, criteria in state_spec.items():
                    if not self.finder._check_condition(monitor_element, key, criteria, {}):
                        all_conditions_met = False
                        break
                
                if all_conditions_met:
                    self._emit_event('success', f"Success: {display_message}")
                    return True
                
                time.sleep(effective_retry)
            
            raise WaitTimeoutError(f"Timeout after {effective_timeout}s. Element did not reach the desired state.")

        except (UIActionError, ValueError, WaitTimeoutError) as e:
            self.logger.error(f"Error during wait_for_state '{display_message}': {e}", exc_info=False)
            self._emit_event('error', f"Failed: {display_message}")
            self.take_error_screenshot()
            return False
        except Exception as e:
            self.logger.critical(f"Unexpected error during wait_for_state '{display_message}': {e}", exc_info=True)
            self._emit_event('error', f"Failed: {display_message}")
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
        display_message = description or f"Getting property '{property_name}'"
        self._emit_event(notify_style if description else 'info', display_message)
        if property_name not in self.GETTABLE_PROPERTIES: raise ValueError(f"Property '{property_name}' is not supported.")
        
        try:
            self._wait_for_user_idle()
            
            if target:
                if not isinstance(target, UIAWrapper): raise UIActionError(f"Provided 'target' is not a valid UI element.")
                target_element = target
                self.logger.info(f"Using provided target element '{target.window_text()}'. Skipping scan.")
            else:
                if not window_spec: raise ValueError("Must provide 'window_spec' and 'element_spec' when 'target' is not used.")
                target_element = self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                )
                if not target_element: raise ElementNotFoundFromWindowError("Could not find the target element via scanning.")

            value = core_logic.get_property_value(target_element, property_name, self.uia, self.tree_walker)
            self._emit_event('success', f"Successfully got property '{property_name}'.")
            return value
        except (UIActionError, WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError, ValueError) as e:
            self.logger.error(f"Error performing '{display_message}': {e}", exc_info=False)
            self._emit_event('error', f"Failed: {display_message}")
            self.take_error_screenshot()
            return None
        except Exception as e:
            self.logger.critical(f"Unexpected error performing '{display_message}': {e}", exc_info=True)
            self._emit_event('error', f"Failed: {display_message}")
            self.take_error_screenshot()
            return None

    def check_exists(self, target: Optional[UIAWrapper] = None, 
                     window_spec: Optional[Dict[str, Any]] = None, 
                     element_spec: Optional[Dict[str, Any]] = None, 
                     timeout: Optional[float] = None, 
                     retry_interval: Optional[float] = None, 
                     log_output: bool = True,
                     **kwargs) -> bool:
        if log_output:
            self._emit_event('info', "Checking for target existence...")
        try:
            self._wait_for_user_idle()
            if target:
                try:
                    return isinstance(target, UIAWrapper) and target.is_visible()
                except Exception:
                    return False
            else:
                if not window_spec: raise ValueError("Must provide 'window_spec' when 'target' is not used.")
                return self.find_element(
                    window_spec, element_spec, timeout, retry_interval, **kwargs
                ) is not None
        except (WindowNotFoundError, ElementNotFoundFromWindowError, AmbiguousElementError): 
            return False
        except (UIActionError, ValueError) as e: 
            if log_output: self.logger.error(f"Error during check_exists: {e}")
            return False
        except Exception as e:
            if log_output:
                self.logger.error(f"Unexpected error during check_exists: {e}", exc_info=True)
                self._emit_event('error', f"An unexpected error occurred during check: {e}")
            return False

    def create_snapshot(self, window_spec: Dict[str, Any], 
                        elements_map: Dict[str, Dict[str, Any]], 
                        timeout: Optional[float] = None, 
                        retry_interval: Optional[float] = None,
                        **kwargs) -> Optional[UISnapshot]:
        self._emit_event('process', f"Creating snapshot for window '{window_spec.get('pwa_title', '...')}'...")
        effective_timeout = timeout if timeout is not None else self.config['default_timeout']
        snapshot_name = window_spec.get('pwa_title', 'snapshot')
        snapshot = UISnapshot(snapshot_name, self, effective_timeout)
        window = self.find_element(window_spec, timeout=timeout, retry_interval=retry_interval)
        if not window:
            self._emit_event('error', "Snapshot failed: Target window not found.")
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
                    self.logger.info(f"  (+) Found and added '{key}' to snapshot.")
            except (ElementNotFoundFromWindowError, AmbiguousElementError) as e:
                self.logger.warning(f"  (-) Could not find unique element for '{key}': {e}")
        self._emit_event('success', f"Snapshot created. Found {found_count}/{len(elements_map)} elements.")
        return snapshot

    def close(self):
        self.logger.info("Closing UIController...")

    def _find_with_retry(self, search_root: UIAWrapper, 
                         spec: Dict[str, Any], 
                         timeout: float, 
                         retry_interval: float, 
                         not_found_exception: Type[Exception], 
                         ambiguous_exception: Type[Exception], 
                         entity_name: str,
                         **kwargs) -> UIAWrapper:
        start_time = time.time()
        perf_logger.debug(f"RETRY_LOOP START for '{entity_name}' | Timeout: {timeout:.2f}s")
        
        retry_count = 0
        while True:
            retry_count += 1
            if self.state:
                if self.state.is_stopped(): raise UIActionError("Task stopped by user.")
                is_paused_by_panel = False
                while self.state.is_paused():
                    if not is_paused_by_panel: self._emit_event('warning', "Task paused. Waiting for resume...", duration=0); is_paused_by_panel = True
                    time.sleep(0.5)
                if is_paused_by_panel: self._emit_event('success', "Task resumed.", duration=3)
            
            candidates = self.finder.find(search_root, spec, **kwargs)
            
            if len(candidates) == 1:
                perf_logger.info(f"RETRY_LOOP SUCCESS for '{entity_name}' | Attempts: {retry_count} | Duration: {time.time() - start_time:.4f}s")
                return candidates[0]
            elif len(candidates) > 1:
                details = [f"'{c.window_text()}'" for c in candidates[:5]]
                raise ambiguous_exception(f"Found {len(candidates)} ambiguous {entity_name}s. Details: {details}")
            
            if time.time() - start_time >= timeout:
                perf_logger.warning(f"RETRY_LOOP TIMEOUT for '{entity_name}' | Duration: {time.time() - start_time:.4f}s")
                raise not_found_exception(f"Timeout. No unique {entity_name} matching spec found.\n--> Spec used: {spec}")
            
            perf_logger.debug(f"Attempt {retry_count}: '{entity_name}' not found. Retrying in {retry_interval}s...")
            time.sleep(retry_interval)

    def take_error_screenshot(self):
        try:
            screenshot_dir = "error_screenshots"
            if not os.path.exists(screenshot_dir): os.makedirs(screenshot_dir)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(screenshot_dir, f"error_{timestamp}.png")
            ImageGrab.grab().save(filename)
            self._emit_event('warning', f"Screenshot saved to {filename}")
        except Exception as e: self._emit_event('error', f"Failed to take screenshot: {e}")

    def _emit_event(self, event_type: str, message: str, **kwargs):
        log_levels = {"info": logging.INFO, "success": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "process": logging.DEBUG, "debug": logging.DEBUG}
        self.logger.log(log_levels.get(event_type, logging.INFO), message)
        if self.event_callback and callable(self.event_callback):
            try: 
                self.event_callback(event_type, message, **kwargs)
            except Exception as e: 
                self.logger.error(f"Error executing event_callback: {e}")
                
    def _wait_for_user_idle(self):
        if self.activity_listener: self.activity_listener.wait_for_user_idle(self._emit_event)
        
    def _handle_activation(self, target_element: UIAWrapper, command: str, auto_activate: bool):
        try:
            top_window = core_logic.get_top_level_window(target_element)
            if top_window and (not top_window.is_active() or top_window.is_minimized()):
                if auto_activate:
                    self._emit_event('info', f"Auto-activating and maximizing window '{top_window.window_text()}'...")
                    top_window.maximize(); time.sleep(0.5) 
                else: raise UIActionError(f"Window '{top_window.window_text()}' is not active. Action '{command}' requires activation.")
        except NoPatternInterfaceError: self.logger.warning(f"Element '{target_element.window_text()}' or its parent does not support WindowPattern. Skipping activation check.")
        except Exception as e: self.logger.error(f"An unexpected error occurred during activation: {e}", exc_info=True)
        
    def _execute_action_safely(self, element: UIAWrapper, action_str: str):
        with self._bot_acting_lock: self._is_bot_acting[0] = True
        try: self._execute_action(element, action_str)
        finally:
            with self._bot_acting_lock: self._is_bot_acting[0] = False
            
    def _execute_action(self, element: UIAWrapper, action_str: str):
        self.logger.debug(f"Executing action '{action_str}' on element '{element.window_text()}'")
        parts = action_str.split(':', 1)
        command = parts[0].lower().strip()
        value = parts[1] if len(parts) > 1 else None
        try:
            if command not in self.VALID_ACTIONS: raise ValueError(f"Action '{command}' is not a supported action.")
            if command in ['click', 'double_click', 'right_click', 'select']:
                try:
                    element.scroll_into_view(); time.sleep(0.2)
                except Exception as e: self.logger.warning(f"Could not scroll element into view (this might be okay): {e}")
            if command == 'click': element.click_input()
            elif command == 'double_click': element.double_click_input()
            elif command == 'right_click': element.right_click_input()
            elif command == 'focus': element.set_focus()
            elif command == 'invoke': element.invoke()
            elif command == 'toggle': element.toggle()
            elif command == 'scroll':
                if value is None: raise ValueError("Scroll action requires a direction, e.g., 'scroll:down'")
                parts = value.split(','); direction = parts[0].strip().lower(); amount = int(parts[1]) if len(parts) > 1 else 1
                if direction not in ['up', 'down', 'left', 'right']: raise ValueError(f"Invalid scroll direction: '{direction}'")
                element.scroll(direction, amount)
            elif command == 'mouse_scroll':
                if value is None: value = "down"
                parts = value.split(','); direction = parts[0].strip().lower(); wheel_dist = -5 if direction == 'down' else 5
                rect = element.rectangle(); coords = (rect.mid_point().x, rect.mid_point().y)
                pywinauto_mouse.move(coords=coords); time.sleep(0.1); pywinauto_mouse.scroll(coords=coords, wheel_dist=wheel_dist)
            elif command in ('select', 'set_text', 'paste_text', 'type_keys', 'send_message_text'):
                if value is None: raise ValueError(f"Action '{command}' requires a value.")
                if command == 'select': element.select(value)
                elif command == 'set_text': element.set_edit_text(value)
                elif command == 'paste_text': pyperclip.copy(value); element.type_keys('^a^v', pause=0.1) 
                elif command == 'type_keys': element.type_keys(value, with_spaces=True, with_newlines=True, pause=0.01)
                elif command == 'send_message_text':
                    if not element.handle: raise UIActionError("'send_message_text' requires a window handle.")
                    win32api.SendMessage(element.handle, win32con.WM_SETTEXT, 0, value)
        except Exception as e:
            raise UIActionError(f"Execution of action '{action_str}' failed. Original error: {type(e).__name__} - {e}") from e
