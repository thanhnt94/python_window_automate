# functions/human_activity_listener.py
# Module độc lập, có thể tái sử dụng để phát hiện người dùng và tạm dừng tự động hóa.
# VERSION 1.4: Sửa lỗi TypeError do import vòng (circular import).

import logging
import threading
import time

# --- Thư viện yêu cầu ---
try:
    from pynput import mouse, keyboard
except ImportError:
    logging.warning("Thư viện 'pynput' không được tìm thấy. Tính năng phát hiện hoạt động của người dùng sẽ bị vô hiệu hóa.")
    mouse = None
    keyboard = None

# --- Import các module của dự án (phụ thuộc tùy chọn) ---
try:
    # Chúng ta vẫn import để có thể tạo instance, nhưng sẽ không dùng để kiểm tra type
    from .ui_notifier import StatusNotifier
except ImportError:
    try:
        from ui_notifier import StatusNotifier
    except ImportError:
        StatusNotifier = None

# ======================================================================
#                       LỚP HUMAN ACTIVITY LISTENER
# ======================================================================

class HumanActivityListener:
    """
    Mô tả:
    Một lớp độc lập để lắng nghe hoạt động của người dùng (chuột và bàn phím).
    Lớp này giúp tạm dừng quá trình tự động hóa nếu người dùng can thiệp.
    """
    def __init__(self, cooldown_period, bot_acting_lock, 
                 is_bot_acting_ref, notifier=None):
        """
        Mô tả:
        Khởi tạo bộ lắng nghe hoạt động của con người.

        Hoạt động:
        - Lưu lại các tham số cần thiết như thời gian chờ, khóa luồng, và notifier.
        - Kiểm tra xem thư viện 'pynput' có tồn tại không.
        - Nếu có, khởi động một luồng nền để chạy các bộ lắng nghe chuột và bàn phím.
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
        Mô tả:
        Hàm này được gọi mỗi khi có hoạt động từ chuột hoặc bàn phím.
        Nó cập nhật lại thời gian hoạt động cuối cùng của người dùng.

        Hoạt động:
        - Sử dụng khóa luồng để đảm bảo an toàn.
        - Chỉ cập nhật thời gian nếu bot không đang trong quá trình thực hiện một hành động.
        """
        with self._bot_acting_lock:
            # Kiểm tra cờ tham chiếu để xem bot có đang hoạt động không
            if not self._is_bot_acting_ref[0]:
                self._last_human_activity_time = time.time()

    def _run_listeners(self):
        """
        Mô tả:
        Chạy các bộ lắng nghe của pynput trong một luồng nền.
        """
        try:
            with mouse.Listener(on_move=self._update_last_activity, on_click=self._update_last_activity, on_scroll=self._update_last_activity) as m_listener:
                with keyboard.Listener(on_press=self._update_last_activity) as k_listener:
                    m_listener.join()
                    k_listener.join()
        except Exception as e:
            self.logger.error(f"Lỗi trong luồng lắng nghe đầu vào: {e}", exc_info=True)

    def _emit_event(self, event_type, message, **kwargs):
        """
        Mô tả:
        Gửi thông báo qua notifier nếu nó được cung cấp và hợp lệ.
        """
        # --- THAY ĐỔI TẠI ĐÂY ---
        # Thay vì kiểm tra type bằng 'isinstance', chúng ta kiểm tra xem đối tượng
        # có phương thức 'update_status' hay không. Cách này an toàn hơn và tránh lỗi.
        if self._notifier and hasattr(self._notifier, 'update_status'):
            try:
                self._notifier.update_status(text=message, style=event_type, duration=kwargs.get('duration'))
            except Exception as e:
                self.logger.error(f"Lỗi khi gửi thông báo: {e}")

    def wait_for_user_idle(self):
        """
        Mô tả:
        Hàm này là chức năng chính của lớp. Nó sẽ chặn (dừng) chương trình
        nếu phát hiện người dùng đang hoạt động và chỉ tiếp tục khi người dùng đã nghỉ.

        Hoạt động:
        - Kiểm tra liên tục xem thời gian hiện tại đã vượt qua thời gian hoạt động cuối cùng
          cộng với thời gian chờ (cooldown) hay chưa.
        - Nếu người dùng đang hoạt động, nó sẽ gửi thông báo "Đang tạm dừng..."
        - Khi người dùng ngừng hoạt động, nó sẽ gửi thông báo "Tiếp tục" và kết thúc vòng lặp.
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
