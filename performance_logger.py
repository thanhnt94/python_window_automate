# functions/performance_logger.py
# Module này chịu trách nhiệm thiết lập và quản lý hệ thống ghi log tập trung.
# VERSION 3.0: Thêm hàm set_log_level để thay đổi cấp độ log linh hoạt.

import logging
import sys
from logging.handlers import RotatingFileHandler

# Biến toàn cục để lưu trữ các handler, giúp việc thay đổi level dễ dàng hơn
_file_handler = None
_console_handler = None

def initialize_logging(level='info'):
    """
    Mô tả:
    Hàm này khởi tạo và cấu hình hệ thống ghi log tập trung cho toàn bộ ứng dụng.
    Nó nên được gọi một lần khi bắt đầu một quy trình chính.

    Hoạt động:
    1. Xóa tất cả các cấu hình log (handler) đang tồn tại để tránh log bị lặp.
    2. Dựa vào tham số 'level', nó sẽ quyết định cấp độ log (DEBUG, INFO) hoặc vô hiệu hóa log ('none').
    3. Thiết lập hai bộ xử lý (handler):
        - Một để ghi log ra file 'performance.log', có cơ chế tự xoay vòng khi file đạt 5MB.
        - Một để in log ra console (màn hình terminal).
    4. Áp dụng một định dạng chung cho tất cả các log để dễ dàng theo dõi.
    """
    global _file_handler, _console_handler
    
    root_logger = logging.getLogger()
    
    if root_logger.hasHandlers():
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    if level.lower() == 'none':
        root_logger.addHandler(logging.NullHandler())
        logging.info("Hệ thống ghi log đã được TẮT.")
        return

    log_level_map = {
        'debug': logging.DEBUG,
        'info': logging.INFO
    }
    effective_level = log_level_map.get(level.lower(), logging.INFO)
    root_logger.setLevel(effective_level)

    log_formatter = logging.Formatter(
        '%(asctime)s - [%(levelname)s] - %(name)s.%(funcName)s:%(lineno)d - %(message)s'
    )

    _file_handler = RotatingFileHandler(
        'performance.log', 
        maxBytes=5*1024*1024, 
        backupCount=3,
        encoding='utf-8'
    )
    _file_handler.setFormatter(log_formatter)
    
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setFormatter(log_formatter)

    root_logger.addHandler(_file_handler)
    root_logger.addHandler(_console_handler)
    
    logging.info(f"Hệ thống ghi log đã được khởi tạo với cấp độ '{level.upper()}'.")

def set_log_level(level):
    """
    Mô tả:
    Hàm này cho phép thay đổi cấp độ log của hệ thống một cách linh hoạt khi chương trình đang chạy.
    Rất hữu ích để bật chế độ debug khi gặp lỗi.

    Hoạt động:
    - Tìm logger gốc và các handler đã được tạo.
    - Thay đổi thuộc tính 'level' của chúng thành cấp độ mới được chỉ định.
    """
    log_level_map = {
        'debug': logging.DEBUG,
        'info': logging.INFO
    }
    new_level = log_level_map.get(level.lower())
    
    if not new_level:
        logging.warning(f"Cấp độ log không hợp lệ: '{level}'. Không thay đổi.")
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(new_level)

    # Thay đổi level trên các handler đã được lưu trữ
    if _file_handler:
        _file_handler.setLevel(new_level)
    if _console_handler:
        _console_handler.setLevel(new_level)
        
    logging.info(f"Đã thay đổi cấp độ log thành '{level.upper()}'.")

# Khối chạy thử nghiệm để kiểm tra
if __name__ == '__main__':
    # 1. Bắt đầu với chế độ INFO
    print("--- 1. Bắt đầu ở chế độ INFO ---")
    initialize_logging(level='info')
    logging.debug("Câu lệnh debug này sẽ không hiển thị.")
    logging.info("Chương trình đang chạy ở chế độ Info.")

    # 2. Gặp lỗi và chuyển sang chế độ DEBUG
    print("\n--- 2. Gặp lỗi, chuyển sang DEBUG ---")
    set_log_level('debug')
    logging.debug("Bây giờ câu lệnh debug này đã hiển thị, giúp cho việc truy vết lỗi.")
    logging.info("Thông tin chi tiết về lỗi...")

    # 3. Gỡ lỗi xong, quay lại chế độ INFO
    print("\n--- 3. Gỡ lỗi xong, quay lại INFO ---")
    set_log_level('info')
    logging.debug("Câu lệnh debug này lại bị ẩn đi.")
    logging.info("Chương trình tiếp tục chạy bình thường.")

    print("\nKiểm tra file 'performance.log' để xem kết quả ghi log.")
