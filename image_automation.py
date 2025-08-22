# image_automation.py
# --- VERSION 3.0 (Regional Search Optimization):
# - All major public methods (`wait_for_image`, `image_action`) now accept an
#   optional `region` parameter (a tuple of left, top, width, height).
# - If `region` is provided, pyautogui will only take a screenshot of and
#   search within that specific area, dramatically improving performance.
# - If `region` is None, it defaults to searching the full screen as before.
# - Coordinate calculations are now correctly adjusted based on the region's offset.

import os
import time
import logging
import pyautogui
import pyperclip
from collections import defaultdict
from datetime import datetime
import sys
import math
import threading
from typing import Optional, List, Dict, Any, Callable, Tuple

# --- Pynput for human activity detection ---
try:
    from pynput import mouse, keyboard
except ImportError:
    logging.warning("Pynput not found. Human activity detection will be disabled. Install with: pip install pynput")
    mouse = None
    keyboard = None

# --- Project Modules ---
try:
    # This import is no longer needed as there are no direct dependencies
    # from .ui_toolkit import ImageUtils 
    from .ui_notifier import StatusNotifier
    from .ui_control_panel import AutomationState
except ImportError:
    # Fallback for standalone execution
    try:
        from ui_notifier import StatusNotifier
        from ui_control_panel import AutomationState
    except ImportError:
        print("CRITICAL ERROR: A required module (ui_notifier, ui_control_panel) could not be found.")
        # Dummy classes to prevent crash
        class StatusNotifier:
            def update_status(self, *args, **kwargs): pass
        class AutomationState:
            def is_stopped(self): return False
            def is_paused(self): return False
        print("Warning: Core UI modules not found. ImageController will have limited functionality.")

# =============================================================================
# --- HUMAN ACTIVITY LISTENER (Simplified version from core_controller) ---
# =============================================================================
class HumanActivityListener:
    """Encapsulates logic for detecting user input to pause automation."""
    def __init__(self, cooldown_period: float):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.cooldown_period = cooldown_period
        self._last_human_activity_time: float = time.time() - cooldown_period
        self._lock = threading.Lock()
        
        self._listener_thread = threading.Thread(target=self._run_listeners, daemon=True)
        self._listener_thread.start()
        self.logger.info("Human activity listener started.")

    def _update_last_activity(self, *args):
        with self._lock:
            self._last_human_activity_time = time.time()

    def get_last_activity_time(self) -> float:
        with self._lock:
            return self._last_human_activity_time

    def _run_listeners(self):
        try:
            if not mouse or not keyboard: return
            with mouse.Listener(on_move=self._update_last_activity, on_click=self._update_last_activity, on_scroll=self._update_last_activity) as m_listener:
                with keyboard.Listener(on_press=self._update_last_activity) as k_listener:
                    m_listener.join()
                    k_listener.join()
        except Exception as e:
            self.logger.error(f"Error in input listener thread: {e}", exc_info=True)

# =============================================================================
# --- IMAGE CONTROLLER CLASS DEFINITION ---
# =============================================================================
class ImageController:
    """
    Class to perform GUI automation tasks based on image recognition.
    Includes methods for waiting for images, calculating coordinates, and executing actions,
    with optional integration for user notifications and control.
    """
    def __init__(self,
                 confidence=0.95,
                 timeout=None,
                 check_interval=0.5,
                 grayscale=False,
                 err_pic_dir=None,
                 scroll=(0, 0),
                 notifier: Optional[StatusNotifier] = None,
                 automation_state: Optional[AutomationState] = None,
                 human_interruption_detection: bool = False,
                 human_cooldown_period: int = 5):
        """Initializes an instance of ImageController."""
        self.confidence = confidence
        self.timeout = timeout
        self.check_interval = check_interval
        self.grayscale = grayscale
        self.err_pic_dir = err_pic_dir
        self.scroll = scroll
        self.notifier = notifier
        self.state = automation_state
        self.human_interruption_enabled = human_interruption_detection and mouse and keyboard
        self.activity_listener = None
        if self.human_interruption_enabled:
            self.activity_listener = HumanActivityListener(cooldown_period=human_cooldown_period)
        log_msg = f"ImageController initialized. Human interruption: {'Enabled' if self.human_interruption_enabled else 'Disabled'}"
        self._emit_event(log_msg, style='debug')

    def _emit_event(self, text: str, style: str = 'info', duration: Optional[int] = 3):
        log_levels = {"info": logging.INFO, "success": logging.INFO, "process": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "debug": logging.DEBUG}
        logging.log(log_levels.get(style, logging.INFO), text)
        if self.notifier:
            try:
                self.notifier.update_status(text, style=style, duration=duration)
            except Exception as e:
                logging.error(f"Failed to emit notification: {e}")

    def _wait_for_user_idle(self):
        if not self.activity_listener:
            return
        is_paused_by_human = False
        while time.time() - self.activity_listener.get_last_activity_time() < self.activity_listener.cooldown_period:
            if not is_paused_by_human:
                self._emit_event(f"User activity detected! Pausing for {self.activity_listener.cooldown_period}s...", style='warning', duration=self.activity_listener.cooldown_period)
                is_paused_by_human = True
            time.sleep(1)
        if is_paused_by_human:
            self._emit_event("User is idle. Resuming automation.", style='success', duration=2)

    def wait_for_image(
        self,
        image_input,
        wait_for='appear',
        region: Optional[Tuple[int, int, int, int]] = None, # NEW: Region parameter
        grayscale=None,
        confidence=None,
        timeout=None,
        scroll=None,
        check_interval=None,
        err_pic_dir=None,
        description: Optional[str] = None
    ):
        grayscale = grayscale if grayscale is not None else self.grayscale
        confidence = confidence if confidence is not None else self.confidence
        timeout = timeout if timeout is not None else self.timeout
        scroll = scroll if scroll is not None else self.scroll
        check_interval = check_interval if check_interval is not None else self.check_interval
        err_pic_dir = err_pic_dir if err_pic_dir is not None else self.err_pic_dir
        
        def _log_screenshot(context_prefix, error=None):
            if err_pic_dir is None: return None
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                sanitized_prefix = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in str(context_prefix))
                error_info = f"_{type(error).__name__}" if error else ""
                if not isinstance(err_pic_dir, str) or not err_pic_dir: logging.error(f"Invalid err_pic_dir: {err_pic_dir}."); return None
                filename = os.path.join(err_pic_dir, f"{sanitized_prefix}_{timestamp}{error_info}.png"); os.makedirs(os.path.dirname(filename), exist_ok=True)
                pyautogui.screenshot(filename); logging.warning(f"Screenshot saved: {filename}"); return filename
            except Exception as e: logging.error(f"Failed to prepare screenshot save path: {e}", exc_info=False); return None
        
        def _process_image_input(img_input):
            image_list, path_to_id_map = [], defaultdict(list)
            if isinstance(img_input, dict):
                for key, value in img_input.items():
                    paths_to_add = []
                    val_to_check = value['path'] if isinstance(value, dict) and 'path' in value else value
                    if isinstance(val_to_check, str): paths_to_add = [val_to_check]
                    elif isinstance(val_to_check, list): paths_to_add = val_to_check
                    else: raise ValueError(f"Dict value for key '{key}' must contain str/list path(s).")
                    for path in paths_to_add:
                        if not isinstance(path, str) or not path: raise ValueError(f"Invalid/empty path for key '{key}'.")
                        abs_path = os.path.abspath(path)
                        if abs_path not in image_list: image_list.append(abs_path)
                        path_to_id_map[abs_path].append(key)
                if any(len(v) > 1 for v in path_to_id_map.values()): raise ValueError(f"Ambiguous dict input. Paths under multiple keys.")
            elif isinstance(img_input, list):
                if not all(isinstance(p, str) and p for p in img_input): raise ValueError("List image_input must contain non-empty string paths.")
                image_list = sorted(list(set(os.path.abspath(p) for p in img_input)))
                for path in image_list: path_to_id_map[path].append(path)
            elif isinstance(img_input, str):
                if not img_input: raise ValueError("String image_input cannot be empty.")
                abs_path = os.path.abspath(img_input); image_list = [abs_path]; path_to_id_map[abs_path].append(abs_path)
            else: raise TypeError(f"Unsupported image_input type: {type(img_input).__name__}.")
            if not image_list: raise ValueError("No valid image paths provided/processed.")
            return image_list, path_to_id_map
        
        def _find_images_on_screen_internal(img_paths, conf, reg_tuple, use_grayscale):
            found_this_cycle = []
            pyautogui_region_validated = None
            if reg_tuple:
                try:
                    # Ensure region is a tuple of 4 integers
                    left, top, width, height = map(int, reg_tuple)
                    assert width > 0 and height > 0
                    pyautogui_region_validated = (left, top, width, height)
                except (ValueError, TypeError, AssertionError) as e:
                    raise ValueError(f"Invalid region format {reg_tuple}. Must be (left, top, width, height). Error: {e}")
            
            # UPDATED: Take screenshot only of the specified region if provided
            current_screenshot = pyautogui.screenshot(region=pyautogui_region_validated)
            
            for img_path in img_paths:
                try:
                    match_generator = pyautogui.locateAll(img_path, current_screenshot, confidence=conf, grayscale=use_grayscale)
                    for box_like in match_generator:
                        l, t, w, h = map(int, box_like)
                        if w <= 0 or h <= 0: continue
                        
                        # UPDATED: Adjust coordinates back to be screen-relative if a region was used
                        if pyautogui_region_validated:
                            final_box = (l + pyautogui_region_validated[0], t + pyautogui_region_validated[1], w, h)
                        else:
                            final_box = (l, t, w, h)
                            
                        found_this_cycle.append({'path': img_path, 'box': final_box})
                except pyautogui.ImageNotFoundException: pass
                except Exception as e: logging.warning(f"Error during locateAll for '{os.path.basename(img_path)}': {e}")
            
            found_this_cycle.sort(key=lambda m: (m['box'][1], m['box'][0])) # Sort by top, then left
            return found_this_cycle

        start_loop_time = time.time()
        last_scroll_time = 0
        try:
            log_input_str = str(image_input)[:100] + "..." if len(str(image_input)) > 100 else str(image_input)
            wait_msg = description or f"Waiting for image to {wait_for}: {log_input_str}"
            self._emit_event(wait_msg, style='process')

            image_list_to_search, path_to_id_map_rev = _process_image_input(image_input)
            if not all(os.path.isfile(p) for p in image_list_to_search):
                raise FileNotFoundError(f"One or more image files not found.")

            while True:
                if self.state:
                    if self.state.is_stopped():
                        self._emit_event("Task stopped by user from control panel.", style='error')
                        return {'status': False, 'wait_mode': wait_for, 'matches': []}
                    is_paused_by_panel = False
                    while self.state.is_paused():
                        if not is_paused_by_panel:
                             self._emit_event("Task paused by user. Waiting for resume...", style='warning', duration=0)
                             is_paused_by_panel = True
                        time.sleep(0.5)
                    if is_paused_by_panel: self._emit_event("Task resumed by user.", style='success')
                
                self._wait_for_user_idle()
                
                if timeout is not None and time.time() - start_loop_time > timeout:
                    self._emit_event(f"Timeout: {wait_msg}", style='error', duration=5)
                    _log_screenshot(f"wait_timeout_{wait_for}", TimeoutError("Timeout"))
                    return {'status': False, 'wait_mode': wait_for, 'matches': []}

                current_matches_found = _find_images_on_screen_internal(image_list_to_search, confidence, region, grayscale)
                found_any = len(current_matches_found) > 0
                condition_met = (wait_for == 'appear' and found_any) or (wait_for == 'disappear' and not found_any)
                
                if condition_met:
                    self._emit_event(f"Success: {wait_msg}", style='success')
                    final_matches_list = []
                    if wait_for == 'appear':
                        for match_raw in current_matches_found:
                            path = match_raw['path']
                            identifier = path_to_id_map_rev.get(path, [path])[0]
                            final_matches_list.append({'identifier': identifier, 'path': path, 'box': match_raw['box']})
                    return {'status': True, 'wait_mode': wait_for, 'matches': final_matches_list}
                else:
                    if wait_for == 'appear' and scroll != (0, 0) and not found_any and (time.time() - last_scroll_time > max(0.1, check_interval)):
                        pyautogui.scroll(int(scroll[0])); time.sleep(max(0, float(scroll[1])))
                        last_scroll_time = time.time()
                    time.sleep(max(0.01, check_interval))

        except (ValueError, FileNotFoundError, TypeError) as e:
            self._emit_event(f"Config/Input Error: {e}", style='error', duration=5)
            _log_screenshot("wait_config_error", e)
            return {'status': False, 'wait_mode': wait_for, 'matches': []}
        except Exception as e:
            self._emit_event(f"Unexpected Error: {e}", style='error', duration=5)
            _log_screenshot("wait_runtime_error", e)
            return {'status': False, 'wait_mode': wait_for, 'matches': []}

    def calculate_coords(
        self,
        wait_result,
        match_selection = 0,
        anchor_point = 'center',
        offset = (0, 0),
        ref_point = None
    ):
        def _select_indices(items_list, selection):
            num_items = len(items_list)
            if num_items == 0: return []
            if isinstance(selection, str):
                selection_lower = selection.lower()
                if selection_lower == 'all': return list(range(num_items))
                elif selection_lower in ['closest', 'farthest']:
                    if ref_point is None: logging.error("Missing 'ref_point' for 'closest'/'farthest'."); return None
                    try: ref_x, ref_y = map(float, ref_point)
                    except (TypeError, ValueError): logging.error(f"Invalid ref_point: {ref_point}"); return None
                    distances = []
                    for i, item in enumerate(items_list):
                        box = item.get('box'); 
                        if not box: continue
                        cx = box[0] + box[2] / 2; cy = box[1] + box[3] / 2
                        distances.append({'dist': math.dist((cx, cy), (ref_x, ref_y)), 'index': i})
                    if not distances: return None
                    best = min(distances, key=lambda x: x['dist']) if selection_lower == 'closest' else max(distances, key=lambda x: x['dist'])
                    return [best['index']]
                else: logging.error(f"Invalid string selection: '{selection}'."); return None
            elif isinstance(selection, int):
                if -num_items <= selection < num_items: return [selection if selection >= 0 else num_items + selection]
                else: logging.error(f"Index {selection} OOB."); return None
            elif isinstance(selection, list):
                valid = {idx if idx >= 0 else num_items + idx for idx in selection if isinstance(idx, int) and -num_items <= idx < num_items}
                return sorted(list(valid)) if valid else None
            else: logging.error(f"Invalid selection type: {type(selection)}."); return None

        if not (isinstance(wait_result, dict) and wait_result.get('status') and isinstance(wait_result.get('matches'), list)):
            logging.error("Invalid or failed wait_result provided."); return None
        
        all_matches = wait_result['matches']
        selected_indices = _select_indices(all_matches, match_selection)
        if selected_indices is None: return None
        
        is_single_mode = isinstance(match_selection, (int, str)) and str(match_selection).lower() != 'all'
        if not selected_indices: return None if is_single_mode else []

        coords_list = []
        for index in selected_indices:
            box = all_matches[index].get('box')
            if not box: continue
            l, t, w, h = box
            base_x, base_y = 0, 0
            if anchor_point == 'center': base_x, base_y = l + w / 2, t + h / 2
            elif anchor_point == 'top_left': base_x, base_y = l, t
            elif anchor_point == 'top_right': base_x, base_y = l + w, t
            elif anchor_point == 'bottom_left': base_x, base_y = l, t + h
            elif anchor_point == 'bottom_right': base_x, base_y = l + w, t + h
            else: logging.error(f"Invalid anchor_point: {anchor_point}"); continue
            final_x = int(round(base_x + offset[0]))
            final_y = int(round(base_y + offset[1]))
            coords_list.append((final_x, final_y))
        
        return coords_list[0] if is_single_mode else coords_list

    def run_action(self, coords, action):
        if coords is None: logging.warning("run_action received None for coords."); return False
        coord_list = [coords] if isinstance(coords, tuple) else coords
        if not isinstance(coord_list, list): logging.error(f"Invalid coords type: {type(coords)}"); return False
        
        overall_success = True
        for i, coord in enumerate(coord_list):
            try:
                if not (isinstance(coord, tuple) and len(coord) == 2): raise ValueError("Invalid coordinate format")
                if callable(action):
                    action(coords=coord)
                elif isinstance(action, str):
                    action_str = action.lower()
                    if action_str != 'move': pyautogui.moveTo(coord, duration=0.15)
                    if action_str == 'move': pyautogui.moveTo(coord, duration=0.2)
                    elif action_str == 'click': pyautogui.click(coord)
                    elif action_str == 'double_click': pyautogui.doubleClick(coord, duration=0.1)
                    elif action_str == 'right_click': pyautogui.rightClick(coord)
            except Exception as e:
                logging.error(f"Action failed for coordinate {coord} (index {i}): {e}")
                overall_success = False
                break
        return overall_success

    def image_action(self, image_target, action, timeout=5, region=None, confidence=None, grayscale=None, check_interval=None, match_selection=0, anchor_point='center', offset=(0, 0), err_pic_dir=None, description=None):
        """
        Waits for an image within an optional region and performs an action.
        """
        desc = description or f"Performing '{action}' on image"
        self._emit_event(f"Starting action: {desc}", style='process')
        
        wait_result = self.wait_for_image(
            image_input=image_target,
            wait_for='appear',
            region=region, # Pass the region to the waiting function
            confidence=confidence,
            grayscale=grayscale,
            timeout=timeout,
            check_interval=check_interval,
            err_pic_dir=err_pic_dir,
            description=f"Finding image for action: {desc}"
        )
        if wait_result and wait_result['status']:
            final_coords = self.calculate_coords(
                wait_result=wait_result,
                match_selection=match_selection,
                anchor_point=anchor_point,
                offset=offset
            )
            if final_coords:
                self._emit_event(f"Coordinates calculated. Running action '{action}'...", style='process')
                return self.run_action(coords=final_coords, action=action)
            else:
                self._emit_event("Failed to calculate coordinates from found image.", style='warning')
                return False
        else:
            return False
