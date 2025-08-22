# human_activity_listener.py
# A standalone, reusable module to detect user input and pause automation.
# --- VERSION 1.3 (Final Type Hint Removal):
# - Loại bỏ hoàn toàn các type hint trong hàm __init__ để giải quyết
#   triệt để lỗi không tương thích trong một số môi trường.

import logging
import threading
import time
from typing import Optional, List, Callable

# --- Required Libraries ---
try:
    from pynput import mouse, keyboard
except ImportError:
    logging.warning("Thư viện 'pynput' không được tìm thấy. Tính năng phát hiện hoạt động của người dùng sẽ bị vô hiệu hóa.")
    mouse = None
    keyboard = None

# --- Import project modules (optional dependency) ---
try:
    from ui_notifier import StatusNotifier
except ImportError:
    StatusNotifier = None

# ======================================================================
#                       HUMAN ACTIVITY LISTENER
# ======================================================================

class HumanActivityListener:
    """
    Một lớp độc lập để lắng nghe hoạt động của người dùng (chuột và bàn phím).
    Lớp này giúp tạm dừng quá trình tự động hóa nếu người dùng can thiệp.
    """
    def __init__(self, cooldown_period, bot_acting_lock, 
                 is_bot_acting_ref, notifier=None):
        """
        Khởi tạo bộ lắng nghe hoạt động của con người.

        Args:
            cooldown_period (float): Thời gian chờ sau khi hoạt động của con người
                                     dừng lại trước khi tiếp tục.
            bot_acting_lock (threading.Lock): Khóa để đảm bảo luồng bot không bị
                                              gián đoạn bởi luồng lắng nghe.
            is_bot_acting_ref (List[bool]): Một tham chiếu đến biến cờ để kiểm tra
                                            xem bot có đang thực hiện hành động không.
            notifier (Optional[StatusNotifier]): Một instance của StatusNotifier để
                                                gửi thông báo (tùy chọn).
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self._last_human_activity_time = time.time() - cooldown_period
        self._cooldown_period = cooldown_period
        self._bot_acting_lock = bot_acting_lock
        self._is_bot_acting_ref = is_bot_acting_ref
        self._notifier = notifier

        if not mouse or not keyboard:
            self.logger.warning("pynput không được cài đặt. Bộ lắng nghe sẽ không hoạt động.")
            self._listener_thread = None
        else:
            self.logger.info("Initializing pynput listeners in background thread...")
            self._listener_thread = threading.Thread(target=self._run_listeners, daemon=True)
            self._listener_thread.start()
            self.logger.info("Bộ lắng nghe hoạt động của con người đã được khởi động.")

    def _update_last_activity(self, *args):
        """
        Cập nhật thời gian hoạt động cuối cùng của người dùng.
        Chỉ cập nhật nếu bot không đang thực hiện hành động.
        """
        with self._bot_acting_lock:
            if not self._is_bot_acting_ref[0]:
                self._last_human_activity_time = time.time()

    def _run_listeners(self):
        """Chạy các bộ lắng nghe chuột và bàn phím trong một luồng nền."""
        try:
            with mouse.Listener(on_move=self._update_last_activity, on_click=self._update_last_activity, on_scroll=self._update_last_activity) as m_listener:
                with keyboard.Listener(on_press=self._update_last_activity) as k_listener:
                    m_listener.join()
                    k_listener.join()
        except Exception as e:
            self.logger.error(f"Lỗi trong luồng lắng nghe đầu vào: {e}", exc_info=True)

    def _emit_event(self, event_type, message, **kwargs):
        """
        Gửi thông báo qua notifier nếu có.
        """
        if self._notifier and isinstance(self._notifier, StatusNotifier):
            try:
                self._notifier.update_status(text=message, style=event_type, duration=kwargs.get('duration'))
            except Exception as e:
                self.logger.error(f"Lỗi khi gửi thông báo: {e}")

    def wait_for_user_idle(self):
        """
        Tạm dừng quá trình tự động hóa nếu phát hiện hoạt động của người dùng.
        """
        if not self._listener_thread:
            return

        is_paused = False
        while time.time() - self._last_human_activity_time < self._cooldown_period:
            if not is_paused:
                self._emit_event('warning', f"Phát hiện hoạt động của người dùng! Đang tạm dừng...", duration=0)
                is_paused = True
            time.sleep(1)
        
        if is_paused:
            self._emit_event('success', f"Người dùng đã rảnh. Tiếp tục tự động hóa.", duration=3)
