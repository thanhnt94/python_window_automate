# functions/app_manager.py
# Quản lý vòng đời và tương tác cho một ứng dụng cụ thể.
# VERSION 13.0 (Manual Snapshot Addition):
# - Thêm hàm mới `add_to_snapshot` để cho phép chủ động thêm một element
#   đã tìm thấy vào bộ nhớ cache.

import logging
import subprocess
import time
import psutil
import os
import sys
import shlex

# --- Import các module của dự án ---
try:
    from .core_controller import UIController, WindowNotFoundError, AmbiguousElementError, UISnapshot
    from .ui_notifier import StatusNotifier
    from .image_automation import ImageController
    from pywinauto.controls.uiawrapper import UIAWrapper
    from .core_logic import get_property_value
except ImportError:
    # Fallback cho các cấu trúc thư mục khác nhau
    try:
        from core_controller import UIController, WindowNotFoundError, AmbiguousElementError, UISnapshot
        from ui_notifier import StatusNotifier
        from image_automation import ImageController
        from pywinauto.controls.uiawrapper import UIAWrapper
        from core_logic import get_property_value
    except ImportError as e:
        raise ImportError(f"Không thể import module cốt lõi. Lỗi: {e}")


class AppManager:
    """
    Mô tả:
    Quản lý vòng đời và tương tác cho một ứng dụng cụ thể.
    Cơ chế cache cửa sổ mặc định được TẮT để đảm bảo sự ổn định.
    """
    def __init__(self, name, command_line, main_window_spec, 
                 controller=None, notifier=None,
                 image_controller=None, timeout=30, enable_window_cache=False):
        self.name = name
        self.command = command_line
        self.main_window_spec = main_window_spec
        self.process = None
        self.pid = None
        self.logger = logging.getLogger(f"AppManager({self.name})")
        
        self.default_timeout = timeout
        
        self.controller = controller if controller else UIController()
        self.notifier = notifier
        self.image_controller = image_controller
        
        self.enable_window_cache = enable_window_cache
        self._cached_window = None
        self._snapshot_cache = {}
        
        cache_status = "ENABLED" if self.enable_window_cache else "DISABLED"
        log_msg = f"AppManager for '{self.name}' initialized. Window Caching is {cache_status} by default."
        self.logger.info(log_msg)

    def launch(self, wait_ready=True, timeout=None):
        """Khởi chạy ứng dụng."""
        self.clear_all_caches()
        timeout = timeout if timeout is not None else self.default_timeout
        if self.is_running():
            self._emit_event(f"'{self.name}' is already running (PID {self.pid}). Skipping launch.", style='info')
            return True

        self._emit_event(f"Launching '{self.name}'...", style='process')
        try:
            command_list = shlex.split(self.command)
            process_handle = subprocess.Popen(command_list)
            self.pid = process_handle.pid
            self.process = psutil.Process(self.pid)
            self.logger.info(f"'{self.name}' process started with PID: {self.pid}")

            if wait_ready:
                if self.is_window_ready(timeout):
                    self._emit_event(f"'{self.name}' launched successfully.", style='success')
                    return True
                else:
                    self._emit_event(f"Error: '{self.name}' window did not appear after {timeout}s.", style='error', duration=0)
                    self.kill()
                    return False
            return True
        except Exception as e:
            self._emit_event(f"Critical error launching '{self.name}': {e}", style='error', duration=0)
            self.logger.error(f"Failed to launch '{self.name}': {e}", exc_info=True)
            self.process = None
            self.pid = None
            return False

    def attach(self, timeout=None, on_conflict='fail', attach_timeout=3):
        """Gắn vào một tiến trình ứng dụng đang chạy."""
        self.clear_all_caches()
        launch_timeout = timeout if timeout is not None else self.default_timeout
        self._emit_event(f"Attempting to attach to '{self.name}' (policy: {on_conflict})...", style='process')
        if self.is_running():
            self._emit_event(f"Already attached to '{self.name}' (PID {self.pid}).", style='info')
            return True
        
        start_time = time.time()
        candidates = []
        while time.time() - start_time < attach_timeout:
            try:
                candidates = self.controller.finder.find(self.controller.desktop, self.main_window_spec)
                if candidates: break
            except Exception as e:
                self.logger.error(f"Error finding candidates during attach: {e}")
                return False
            time.sleep(0.5)
        
        if not candidates:
            self._emit_event(f"Timeout: No instances of '{self.name}' found after {attach_timeout}s.", style='warning')
            if on_conflict in ['relaunch', 'launch_new']:
                return self.launch(wait_ready=True, timeout=launch_timeout)
            return False

        target_window = None
        if len(candidates) == 1:
            target_window = candidates[0]
        else:
            self.logger.warning(f"Found {len(candidates)} conflicting instances of '{self.name}'.")
            if on_conflict == 'fail':
                self._emit_event(f"Error: Multiple '{self.name}' windows found.", style='error')
                return False
            elif on_conflict == 'launch_new':
                return self.launch(wait_ready=True, timeout=launch_timeout)
            elif on_conflict == 'relaunch':
                self._emit_event("Closing conflicting windows...", style='warning')
                for win in candidates:
                    try: psutil.Process(win.process_id()).kill()
                    except Exception as e: self.logger.error(f"Failed to kill process for conflicting window: {e}")
                time.sleep(1)
                return self.launch(wait_ready=True, timeout=launch_timeout)
            elif on_conflict in ['newest', 'oldest']:
                candidates.sort(key=lambda w: get_property_value(w, 'proc_create_time'), reverse=(on_conflict == 'newest'))
                target_window = candidates[0]
                self._emit_event(f"Selected the {on_conflict} window.", style='info')

        if target_window:
            self.pid = target_window.process_id()
            try:
                self.process = psutil.Process(self.pid)
                self._cached_window = target_window
                self._emit_event(f"Successfully attached to '{self.name}' (PID {self.pid}).", style='success')
                return True
            except psutil.NoSuchProcess:
                self._emit_event(f"Error: Window exists but process {self.pid} has disappeared.", style='error')
                self.pid = None
                return False
        return False
    
    def close(self, timeout=None):
        """Cố gắng đóng ứng dụng một cách nhẹ nhàng."""
        timeout = timeout if timeout is not None else self.default_timeout
        self._emit_event(f"Attempting to close '{self.name}'...", style='process')
        window = self.get_window(timeout=1)
        if window:
            try:
                window.close()
                end_time = time.time() + timeout
                while window.is_visible() and time.time() < end_time:
                    time.sleep(0.5)
                
                if not window.is_visible():
                    self._emit_event(f"'{self.name}' closed successfully.", style='success')
                    self.clear_all_caches()
                    return True
                else:
                    self._emit_event(f"'{self.name}' did not close after {timeout}s.", style='warning')
                    self.kill()
                    return False
            except Exception as e:
                self._emit_event(f"Error closing '{self.name}': {e}", style='error')
                return False
        else:
            self._emit_event(f"'{self.name}' window not found to close.", style='warning')
            return True

    def kill(self):
        """Buộc đóng ứng dụng."""
        self.clear_all_caches()
        if not self.pid or not psutil.pid_exists(self.pid):
            return
        self._emit_event(f"Force-closing '{self.name}' (PID: {self.pid})...", style='warning', duration=5)
        try:
            parent = psutil.Process(self.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        finally:
            self.process = None
            self.pid = None
    
    def activate(self, timeout=None):
        """Kích hoạt (focus) cửa sổ ứng dụng."""
        timeout = timeout if timeout is not None else self.default_timeout
        self._emit_event(f"Activating '{self.name}' window...", style='process')
        window = self.get_window(timeout)
        if window:
            try:
                if window.is_minimized():
                    window.maximize()
                window.set_focus()
                return True
            except Exception as e:
                 self.logger.error(f"An unexpected error occurred during activation for '{self.name}': {e}")
                 return False
        self._emit_event(f"Could not activate '{self.name}' window.", style='error')
        return False

    def get_window(self, timeout=None):
        """
        Mô tả:
        Tìm và trả về đối tượng cửa sổ chính.
        Hành vi của nó phụ thuộc vào cờ 'enable_window_cache'.
        """
        if not self.enable_window_cache:
            self.logger.debug("Window cache is disabled. Performing a live scan.")
            self._cached_window = None 
            return self._scan_for_window(timeout)

        if self._cached_window:
            try:
                if self._cached_window.is_visible():
                    self.logger.debug("Returning valid cached window.")
                    return self._cached_window
            except Exception:
                self.logger.warning("Cached window seems to be stale. Re-scanning.")
        
        self._cached_window = self._scan_for_window(timeout)
        return self._cached_window

    def _scan_for_window(self, timeout=None):
        """Hàm nội bộ để thực hiện việc quét cửa sổ thực tế."""
        self.logger.info("Scanning for main window...")
        timeout = timeout if timeout is not None else self.default_timeout
        try:
            window = self.controller.find_element(
                window_spec=self.main_window_spec, 
                timeout=timeout
            )
            if window:
                self.pid = window.process_id()
            return window
        except (WindowNotFoundError, AmbiguousElementError) as e:
            self.logger.warning(f"Could not get unique window for '{self.name}': {e}")
            return None

    def cache_window(self, timeout=None):
        """
        Mô tả:
        Chủ động quét và lưu đối tượng cửa sổ vào bộ nhớ cache.
        Hữu ích khi bạn biết mình đang ở một màn hình tĩnh và muốn tăng tốc.
        """
        self.logger.info(f"Manually caching window for '{self.name}'...")
        self._cached_window = self._scan_for_window(timeout)
        if self._cached_window:
            self._emit_event(f"Window for '{self.name}' has been cached successfully.", style='success')
            return True
        else:
            self._emit_event(f"Failed to cache window for '{self.name}'.", style='warning')
            return False

    def clear_window_cache(self):
        """Xóa đối tượng cửa sổ đã được lưu trong cache."""
        if self._cached_window:
            self.logger.info("Main window cache has been cleared.")
            self._cached_window = None

    def clear_snapshot_cache(self, snapshot_name=None):
        """Xóa cache của snapshot UI."""
        if snapshot_name:
            if snapshot_name in self._snapshot_cache:
                del self._snapshot_cache[snapshot_name]
        else:
            if self._snapshot_cache:
                self._snapshot_cache.clear()

    def clear_all_caches(self):
        """Xóa tất cả các loại cache."""
        self.clear_window_cache()
        self.clear_snapshot_cache()

    def get_title(self, timeout=None):
        """Lấy tiêu đề của cửa sổ chính."""
        window = self.get_window(timeout)
        return window.window_text() if window else None

    def is_running(self):
        """Kiểm tra xem tiến trình ứng dụng có đang chạy không."""
        if self.pid and psutil.pid_exists(self.pid):
            return True
        return False

    def is_window_ready(self, timeout=None):
        """Kiểm tra xem cửa sổ chính đã sẵn sàng chưa."""
        self._emit_event(f"Checking for '{self.name}' window...", style='process')
        is_ready = self.get_window(timeout) is not None
        if is_ready:
            self._emit_event(f"'{self.name}' window is ready.", style='success')
        else:
            self._emit_event(f"Could not find '{self.name}' window.", style='warning')
        return is_ready

    def find_element(self, element_spec, timeout=None, **kwargs):
        """Tìm một element duy nhất bên trong cửa sổ chính của ứng dụng."""
        force_rescan = kwargs.pop('force_rescan', False)
        if force_rescan:
            self.clear_window_cache()

        window = self.get_window(timeout=timeout)
        if not window:
            raise WindowNotFoundError(f"Cannot find element: Main window for '{self.name}' not found.")
        return self.controller.find_element(window_spec={'win32_handle': window.handle}, element_spec=element_spec, timeout=timeout, **kwargs)

    def run_action(self, element_spec, action, timeout=None, raise_on_failure=False, **kwargs):
        """
        Chạy một hành động trên một element bên trong cửa sổ chính.
        Truyền tham số 'raise_on_failure' xuống controller.
        """
        force_rescan = kwargs.pop('force_rescan', False)
        if force_rescan:
            self.clear_window_cache()
            
        window = self.get_window(timeout=timeout)
        if not window:
             raise WindowNotFoundError(f"Action failed: Main window for '{self.name}' not found.")
        
        return self.controller.run_action(
            window_spec={'win32_handle': window.handle}, 
            element_spec=element_spec, 
            action=action, 
            timeout=timeout, 
            auto_activate=True, 
            raise_on_failure=raise_on_failure,
            **kwargs
        )

    def check_exists(self, element_spec, timeout=None, **kwargs):
        """Kiểm tra sự tồn tại của một element bên trong cửa sổ chính."""
        force_rescan = kwargs.pop('force_rescan', False)
        if force_rescan:
            self.clear_window_cache()

        window = self.get_window(timeout=timeout)
        if not window:
            return False
        return self.controller.check_exists(window_spec={'win32_handle': window.handle}, element_spec=element_spec, timeout=timeout, **kwargs)

    def get_property(self, element_spec, property_name, timeout=None, **kwargs):
        """Lấy một thuộc tính từ một element bên trong cửa sổ chính."""
        force_rescan = kwargs.pop('force_rescan', False)
        if force_rescan:
            self.clear_window_cache()

        window = self.get_window(timeout)
        if not window:
             raise WindowNotFoundError(f"Get property failed: Main window for '{self.name}' not found.")
        return self.controller.get_property(window_spec={'win32_handle': window.handle}, element_spec=element_spec, property_name=property_name, timeout=timeout, **kwargs)
        
    def cache_snapshot(self, snapshot_name, elements_map, timeout=None, **kwargs):
        """Tạo và lưu cache một snapshot của các elements trên màn hình."""
        self._emit_event('process', f"Caching snapshot '{snapshot_name}' for '{self.name}'...")
        window = self.get_window(timeout=timeout)
        if not window:
            raise WindowNotFoundError(f"Cannot create snapshot: Main window for '{self.name}' not found.")

        snapshot = self.controller.create_snapshot(window_spec={'win32_handle': window.handle}, elements_map=elements_map, timeout=timeout, **kwargs)
        
        if snapshot and snapshot.found_elements:
            self._snapshot_cache[snapshot_name] = snapshot
            self._emit_event('success', f"Snapshot '{snapshot_name}' cached with {len(snapshot.found_elements)} elements.")
            return True
        else:
            self._emit_event('warning', f"Snapshot '{snapshot_name}' could not be created or found no elements.")
            return False

    def add_to_snapshot(self, snapshot_name, element_key, element_object):
        """
        Mô tả:
        Chủ động thêm một element đã được tìm thấy vào một snapshot.
        Tự động tạo snapshot nếu nó chưa tồn tại.
        """
        if not self._snapshot_cache.get(snapshot_name):
            self.logger.info(f"Creating new snapshot '{snapshot_name}' on-the-fly.")
            snapshot = UISnapshot(snapshot_name, self.controller, self.default_timeout)
            self._snapshot_cache[snapshot_name] = snapshot
        
        snapshot = self._snapshot_cache[snapshot_name]
        # Thêm thủ công, không cần recipe để tự phục hồi
        snapshot._add_element(key=element_key, element=element_object)
        self._emit_event(f"Element '{element_key}' manually added to snapshot '{snapshot_name}'.", style='success')

    def get_from_snapshot(self, snapshot_name, element_key):
        """Lấy một element từ snapshot đã được cache."""
        snapshot = self._snapshot_cache.get(snapshot_name)
        if not snapshot:
            self.logger.warning(f"Snapshot '{snapshot_name}' not found.")
            return None
        return snapshot[element_key]

    def image_run_action(self, image_target, action, timeout=None, **kwargs):
        """Thực hiện hành động dựa trên nhận diện hình ảnh."""
        if not self.image_controller:
            raise RuntimeError("ImageController is not available.")
        
        window = self.get_window(timeout=5)
        if not window: return False
        kwargs['region'] = window.rectangle().to_tuple()
        
        return self.image_controller.image_action(image_target=image_target, action=action, timeout=timeout, **kwargs)

    def _emit_event(self, text, style='info', duration=3):
        """Gửi thông báo và ghi log."""
        self.logger.info(text)
        if self.notifier:
            self.notifier.update_status(text=text, style=style, duration=duration)
