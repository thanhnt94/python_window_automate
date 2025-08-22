# app_manager.py
# --- VERSION 9.0 (Snapshot Management):
# - Integrated a snapshot caching system directly into the AppManager.
# - New methods `cache_snapshot`, `get_from_snapshot`, and `clear_snapshot_cache`
#   allow for high-performance interaction with multiple elements on a static screen.
# - Snapshots are automatically cleared during lifecycle events (launch, kill, etc.)
#   to prevent stale element references.
# - This promotes a cleaner coding pattern by centralizing state management
#   within the AppManager instance.

import logging
import subprocess
import time
import psutil
import os
import sys
import shlex
from typing import Dict, Any, Optional, List, Callable, Literal

# --- Import project modules ---
try:
    from core_controller import UIController, WindowNotFoundError, AmbiguousElementError, UISnapshot
    from ui_notifier import StatusNotifier
    from image_automation import ImageController
    from pywinauto.controls.uiawrapper import UIAWrapper
    from core_logic import get_property_value
except ImportError as e:
    # Fallback for different project structures
    try:
        from .core_controller import UIController, WindowNotFoundError, AmbiguousElementError, UISnapshot
        from .ui_notifier import StatusNotifier
        from .image_automation import ImageController
        from pywinauto.controls.uiawrapper import UIAWrapper
        from .core_logic import get_property_value
    except ImportError:
        raise ImportError(
            "Could not import a core module. "
            "Please ensure your script is run from a directory where the modules "
            f"(core_controller, etc.) are accessible. Original error: {e}"
        )


class AppManager:
    """
    Manages the lifecycle and interaction for a specific application,
    with built-in caching for the main window and UI snapshots to boost performance.
    """
    def __init__(self, name: str, command_line: str, main_window_spec: Dict[str, Any], 
                 controller: Optional[UIController] = None, 
                 notifier: Optional[StatusNotifier] = None,
                 image_controller: Optional[ImageController] = None,
                 timeout: int = 30):
        self.name: str = name
        self.command: str = command_line
        self.main_window_spec: Dict[str, Any] = main_window_spec
        self.process: Optional[psutil.Process] = None
        self.pid: Optional[int] = None
        self.logger = logging.getLogger(f"AppManager({self.name})")
        
        self.default_timeout = timeout
        
        self.controller = controller if controller else UIController()
        self.notifier = notifier
        self.image_controller = image_controller
        
        # --- Caching attributes ---
        self._cached_window: Optional[UIAWrapper] = None
        self._snapshot_cache: Dict[str, UISnapshot] = {}
        
        log_msg = f"AppManager for '{self.name}' initialized. Window and Snapshot caching is ENABLED."
        self.logger.info(log_msg)

    # ======================================================================
    #                 PUBLIC API - LIFECYCLE MANAGEMENT
    # ======================================================================

    def launch(self, wait_ready: bool = True, timeout: Optional[int] = None) -> bool:
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
        except FileNotFoundError:
            error_msg = f"Critical error launching '{self.name}': The executable was not found. Please check the path in the command: {self.command}"
            self._emit_event(error_msg, style='error', duration=0)
            self.logger.error(error_msg, exc_info=True)
            self.process = None
            self.pid = None
            return False
        except Exception as e:
            self._emit_event(f"Critical error launching '{self.name}': {e}", style='error', duration=0)
            self.logger.error(f"Failed to launch '{self.name}': {e}", exc_info=True)
            self.process = None
            self.pid = None
            return False

    def attach(self, timeout: Optional[int] = None, on_conflict: Literal['fail', 'newest', 'oldest', 'relaunch', 'launch_new'] = 'fail', attach_timeout: int = 3) -> bool:
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
                self.logger.info(f"No instances found, proceeding with launch as per policy '{on_conflict}'.")
                return self.launch(wait_ready=True, timeout=launch_timeout)
            return False

        target_window = None
        if len(candidates) == 1:
            self.logger.info("Found 1 unique instance.")
            target_window = candidates[0]
        else:
            self.logger.warning(f"Found {len(candidates)} conflicting instances of '{self.name}'. Applying policy '{on_conflict}'.")
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
                self.logger.info(f"Successfully attached and cached window for PID {self.pid}.")
                self._emit_event(f"Successfully attached to '{self.name}' (PID {self.pid}).", style='success')
                return True
            except psutil.NoSuchProcess:
                self._emit_event(f"Error: Window exists but process {self.pid} has disappeared.", style='error')
                self.pid = None
                return False
        return False

    def close(self, timeout: Optional[int] = None) -> bool:
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
        self.clear_all_caches()
        if not self.pid or not psutil.pid_exists(self.pid):
            self.logger.info(f"'{self.name}' is not running or PID is unknown. No action needed.")
            return

        self._emit_event(f"Force-closing '{self.name}' (PID: {self.pid})...", style='warning', duration=5)
        try:
            parent = psutil.Process(self.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
            self._emit_event(f"Force-closed '{self.name}'.", style='success')
        except psutil.NoSuchProcess:
            self.logger.warning(f"Process with PID {self.pid} no longer exists.")
        except Exception as e:
            self._emit_event(f"Error force-closing '{self.name}': {e}", style='error')
        finally:
            self.process = None
            self.pid = None

    # ======================================================================
    #                  PUBLIC API - INTERACTION & STATE
    # ======================================================================

    def activate(self, timeout: Optional[int] = None) -> bool:
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

    def get_window(self, timeout: Optional[int] = None, **kwargs) -> Optional[UIAWrapper]:
        """
        Finds and returns the main window object, using a cache for performance.
        """
        if self._cached_window:
            try:
                if self._cached_window.is_visible():
                    self.logger.debug("Returning valid cached window.")
                    return self._cached_window
            except Exception:
                self.logger.warning("Cached window seems to be stale. Re-scanning.")
                self.clear_window_cache()
        
        self.logger.info("No valid cached window. Scanning for main window...")
        timeout = timeout if timeout is not None else self.default_timeout
        try:
            window = self.controller.find_element(
                window_spec=self.main_window_spec, 
                timeout=timeout,
                **kwargs
            )
            if window:
                self._cached_window = window
                self.pid = window.process_id()
            return window
        except (WindowNotFoundError, AmbiguousElementError) as e:
            self.logger.warning(f"Could not get unique window for '{self.name}': {e}")
            return None

    def clear_window_cache(self):
        """Manually clears the cached main window object."""
        if self._cached_window:
            self.logger.info("Main window cache has been cleared.")
            self._cached_window = None

    def clear_snapshot_cache(self, snapshot_name: Optional[str] = None):
        """
        Clears the snapshot cache. If a name is provided, only that
        snapshot is cleared. Otherwise, all snapshots are cleared.
        """
        if snapshot_name:
            if snapshot_name in self._snapshot_cache:
                del self._snapshot_cache[snapshot_name]
                self.logger.info(f"Snapshot '{snapshot_name}' has been cleared.")
        else:
            if self._snapshot_cache:
                self._snapshot_cache.clear()
                self.logger.info("All snapshots have been cleared.")

    def clear_all_caches(self):
        """Clears both the main window cache and all snapshot caches."""
        self.clear_window_cache()
        self.clear_snapshot_cache()

    def get_title(self, timeout: Optional[int] = None) -> Optional[str]:
        window = self.get_window(timeout)
        return window.window_text() if window else None

    def is_running(self) -> bool:
        if self.pid and psutil.pid_exists(self.pid):
            try:
                p = psutil.Process(self.pid)
                if self.command:
                    expected_exe = os.path.basename(shlex.split(self.command)[0].strip('"'))
                    if p.name().lower() == expected_exe.lower():
                        return True
                else:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
                return False
        return False

    def is_window_ready(self, timeout: Optional[int] = None) -> bool:
        self._emit_event(f"Checking for '{self.name}' window...", style='process')
        is_ready = self.get_window(timeout) is not None
        if is_ready:
            self._emit_event(f"'{self.name}' window is ready.", style='success')
        else:
            self._emit_event(f"Could not find '{self.name}' window.", style='warning')
        return is_ready

    # ======================================================================
    #                 PUBLIC API - AUTOMATION ACTIONS
    # ======================================================================

    def find_element(self, element_spec: Dict[str, Any], timeout: Optional[int] = None, **kwargs) -> Optional[UIAWrapper]:
        """Finds a single element within the application's main window."""
        self._emit_event('info', f"Finding element in '{self.name}'...")
        window = self.get_window(timeout=timeout)
        if not window:
            raise WindowNotFoundError(f"Cannot find element: Main window for '{self.name}' not found.")
        
        return self.controller.find_element(
            window_spec={'win32_handle': window.handle},
            element_spec=element_spec,
            timeout=timeout,
            **kwargs
        )

    def cache_snapshot(self, snapshot_name: str, elements_map: Dict[str, Dict[str, Any]], timeout: Optional[int] = None, **kwargs) -> bool:
        """
        Scans the main window for a set of elements and caches them as a named snapshot.
        Returns True if the snapshot was created successfully, False otherwise.
        """
        self._emit_event('process', f"Caching snapshot '{snapshot_name}' for '{self.name}'...")
        window = self.get_window(timeout=timeout)
        if not window:
            raise WindowNotFoundError(f"Cannot create snapshot: Main window for '{self.name}' not found.")

        snapshot = self.controller.create_snapshot(
            window_spec={'win32_handle': window.handle},
            elements_map=elements_map,
            timeout=timeout,
            **kwargs
        )
        
        if snapshot and snapshot.found_elements:
            self._snapshot_cache[snapshot_name] = snapshot
            self._emit_event('success', f"Snapshot '{snapshot_name}' cached with {len(snapshot.found_elements)} elements.")
            return True
        else:
            self._emit_event('warning', f"Snapshot '{snapshot_name}' could not be created or found no elements.")
            return False

    def get_from_snapshot(self, snapshot_name: str, element_key: str) -> Optional[UIAWrapper]:
        """
        Retrieves a cached element from a named snapshot.
        This is much faster than finding the element again.
        """
        snapshot = self._snapshot_cache.get(snapshot_name)
        if not snapshot:
            self.logger.warning(f"Snapshot '{snapshot_name}' not found in cache. Use `cache_snapshot` first.")
            return None
        
        # The snapshot's __getitem__ handles self-healing
        element = snapshot[element_key]
        if not element:
            self.logger.warning(f"Element '{element_key}' not found or is stale in snapshot '{snapshot_name}'.")
        
        return element

    def run_action(self, element_spec: Dict[str, Any], action: str, timeout: Optional[int] = None, **kwargs) -> bool:
        """Runs an action on an element within the application's main window."""
        timeout = timeout if timeout is not None else self.default_timeout
        return self.controller.run_action(
            window_spec=self.main_window_spec,
            element_spec=element_spec,
            action=action,
            timeout=timeout,
            auto_activate=True,
            **kwargs
        )

    def check_exists(self, element_spec: Dict[str, Any], timeout: Optional[int] = None, **kwargs) -> bool:
        """Checks if an element exists within the application's main window."""
        timeout = timeout if timeout is not None else self.default_timeout
        return self.controller.check_exists(
            window_spec=self.main_window_spec,
            element_spec=element_spec,
            timeout=timeout,
            **kwargs
        )

    def get_property(self, element_spec: Dict[str, Any], property_name: str, timeout: Optional[int] = None, **kwargs) -> Any:
        """Gets a property from an element within the application's main window."""
        timeout = timeout if timeout is not None else self.default_timeout
        return self.controller.get_property(
            window_spec=self.main_window_spec,
            element_spec=element_spec,
            property_name=property_name,
            timeout=timeout,
            **kwargs
        )
        
    def image_run_action(self, image_target: Any, action: str, timeout: Optional[int] = None, **kwargs) -> bool:
        if not self.image_controller:
            raise RuntimeError("ImageController is not available for this AppManager instance.")
        
        window = self.get_window(timeout=5)
        if not window:
            self._emit_event(f"Cannot perform image action: main window '{self.name}' not found.", style='error')
            return False
            
        kwargs['region'] = window.rectangle().to_tuple()
        
        return self.image_controller.image_action(
            image_target=image_target,
            action=action,
            timeout=timeout,
            **kwargs
        )

    # ======================================================================
    #                      INTERNAL (PRIVATE) METHODS
    # ======================================================================

    def _emit_event(self, text: str, style: str = 'info', duration: Optional[int] = 3):
        """Internal helper to send notifications and log messages."""
        self.logger.info(text)
        if self.notifier:
            self.notifier.update_status(text=text, style=style, duration=duration)

# Dummy implementation for standalone testing
class DummyNotifier:
    def update_status(self, *args, **kwargs):
        print(f"[NOTIFY] {kwargs.get('style', 'info').upper()}: {kwargs.get('text')}")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    print("--- AppManager v9.0 Demo ---")
    
    # Setup
    notepad_spec = {'pwa_class_name': 'Notepad'}
    notepad_app = AppManager(
        name="Notepad",
        command_line="notepad.exe",
        main_window_spec=notepad_spec,
        notifier=DummyNotifier()
    )

    try:
        if notepad_app.launch():
            print("Notepad launched.")
            time.sleep(1)
            
            # 1. Define the elements on the "File" menu
            file_menu_elements = {
                'file_menu': {'pwa_title': 'File', 'pwa_control_type': 'MenuItem'},
                'edit_menu': {'pwa_title': 'Edit', 'pwa_control_type': 'MenuItem'}
            }
            
            # 2. Cache the snapshot (1 scan)
            if notepad_app.cache_snapshot('main_menu', file_menu_elements):
                
                # 3. Get elements from the snapshot (instant)
                file_item = notepad_app.get_from_snapshot('main_menu', 'file_menu')
                edit_item = notepad_app.get_from_snapshot('main_menu', 'edit_menu')

                if file_item and edit_item:
                    print("Successfully retrieved menu items from snapshot.")
                    
                    # 4. Interact with the cached elements
                    notepad_app.controller.run_action(target=file_item, action='click')
                    time.sleep(1)
                    notepad_app.controller.run_action(target=edit_item, action='click')
                    time.sleep(1)

            # 5. Fallback to normal find_element
            edit_spec = {'pwa_control_type': 'Edit'}
            notepad_app.run_action(edit_spec, "type_keys:Snapshot demo complete!")
            time.sleep(2)

    except Exception as e:
        print(f"An error occurred during the demo: {e}")
    finally:
        print("Closing Notepad...")
        notepad_app.close(timeout=5)
        if notepad_app.is_running():
            notepad_app.kill()
        print("--- Demo Finished ---")
