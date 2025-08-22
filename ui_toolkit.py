# ui_toolkit.py
# --- VERSION 2.0 (Public API Refactoring):
# - Updated calls to use the new public methods from UIController
#   (e.g., find_window) instead of internal ones.

import logging
import subprocess
import time
import psutil
from typing import Dict, Any

# --- Import project modules ---
try:
    from .core_controller import UIController, WindowNotFoundError, AmbiguousElementError
except ImportError:
    try:
        from core_controller import UIController, WindowNotFoundError, AmbiguousElementError
    except ImportError:
        print("CRITICAL ERROR: 'core_controller.py' must be in the same directory or package for ui_utils.")
        class UIController:
            def __init__(self, *args, **kwargs): pass
            def check_exists(self, *args, **kwargs): return False
            def run_action(self, *args, **kwargs): return False
            def get_property(self, *args, **kwargs): return None
            def find_window(self, *args, **kwargs): return None
        class WindowNotFoundError(Exception): pass
        class AmbiguousElementError(Exception): pass
        print("Warning: UIController not found. ui_utils will have limited functionality.")

# ======================================================================
#               STATELESS UTILITY FUNCTIONS
# ======================================================================

def launch_app(command_line: str) -> bool:
    """Launches an application in a simple, non-blocking way."""
    logging.info(f"Stateless launch: Executing command '{command_line}'")
    try:
        subprocess.Popen(command_line, shell=True)
        return True
    except Exception as e:
        logging.error(f"Stateless launch failed: {e}", exc_info=True)
        return False

def is_app_running(process_name: str) -> bool:
    """Checks if any process with the given name is running."""
    return any(p.name().lower() == process_name.lower() for p in psutil.process_iter(['name']))

def kill_app(process_name: str):
    """Forcefully terminates all processes matching the given name."""
    if not process_name:
        logging.warning("kill_app called without a process_name. No action taken.")
        return

    killed_count = 0
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'].lower() == process_name.lower():
            try:
                logging.warning(f"Killing process '{proc.info['name']}' with PID {proc.info['pid']}")
                p = psutil.Process(proc.info['pid'])
                for child in p.children(recursive=True):
                    child.kill()
                p.kill()
                killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logging.error(f"Failed to kill process {proc.info['pid']}: {e}")
    
    if killed_count > 0:
        logging.info(f"Successfully killed {killed_count} process(es) named '{process_name}'.")
    else:
        logging.info(f"No running processes named '{process_name}' were found.")

def wait_for_window(window_spec: Dict[str, Any], timeout: int = 30) -> bool:
    """Waits for a unique window matching the spec to appear."""
    logging.info(f"Waiting for window with spec: {window_spec}")
    temp_controller = UIController()
    return temp_controller.check_exists(window_spec=window_spec, timeout=timeout)

def activate_window(window_spec: Dict[str, Any], timeout: int = 10) -> bool:
    """Finds a unique window and brings it to the foreground."""
    logging.info(f"Activating window with spec: {window_spec}")
    temp_controller = UIController()
    try:
        # Sử dụng hàm public mới
        window = temp_controller.find_window(window_spec, timeout, 0.5)
        if window:
            if window.is_minimized():
                window.maximize()
            else:
                window.set_focus()
            logging.info("Window activated successfully.")
            return True
    except (WindowNotFoundError, AmbiguousElementError) as e:
        logging.error(f"Could not activate window: {e}")
    return False

def run_action(window_spec: Dict[str, Any], element_spec: Dict[str, Any], action: str, timeout: int = 30) -> bool:
    """Runs an action on an element in any specified window."""
    logging.info(f"Running action '{action}' on element in window with spec: {window_spec}")
    temp_controller = UIController()
    return temp_controller.run_action(
        window_spec=window_spec,
        element_spec=element_spec,
        action=action,
        timeout=timeout,
        auto_activate=True
    )

def get_property(window_spec: Dict[str, Any], element_spec: Dict[str, Any], property_name: str, timeout: int = 30) -> Any:
    """Gets a property from an element in any specified window."""
    logging.info(f"Getting property '{property_name}' from element in window with spec: {window_spec}")
    temp_controller = UIController()
    return temp_controller.get_property(
        window_spec=window_spec,
        element_spec=element_spec,
        property_name=property_name,
        timeout=timeout
    )
