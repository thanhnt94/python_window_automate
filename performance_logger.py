# performance_logger.py
# Module này chịu trách nhiệm thiết lập một hệ thống ghi log (logging) tập trung.
# Log sẽ được ghi đồng thời ra console và file `performance.log` để phân tích hiệu suất.

import logging
import sys
from logging.handlers import RotatingFileHandler

def setup_logger():
    """
    Thiết lập logger chính cho ứng dụng.

    Hàm này cấu hình một logger có tên 'PerformanceLogger' để ghi lại thông tin
    vào cả console và một file log có tên 'performance.log'. File log sẽ tự động
    xoay vòng khi đạt kích thước 5MB và giữ lại 3 file backup.

    Định dạng log bao gồm thời gian, cấp độ, module, tên hàm, số dòng và nội dung log.
    """
    # Lấy logger gốc, tránh tạo logger trùng lặp nếu hàm này được gọi nhiều lần
    logger = logging.getLogger('PerformanceLogger')
    
    # Nếu logger đã được cấu hình (đã có handlers), không làm gì cả
    if logger.hasHandlers():
        return logger

    # Đặt cấp độ log thấp nhất là DEBUG để bắt tất cả các thông điệp
    logger.setLevel(logging.DEBUG)

    # --- Định dạng cho log ---
    # Bao gồm thời gian, cấp độ, tên module, tên hàm, số dòng, và nội dung
    log_formatter = logging.Formatter(
        '%(asctime)s - [%(levelname)s] - %(module)s.%(funcName)s:%(lineno)d - %(message)s'
    )

    # --- Handler để ghi log ra file `performance.log` ---
    # Sử dụng RotatingFileHandler để file log không bị quá lớn
    # maxBytes=5*1024*1024 tương đương 5MB
    # backupCount=3 sẽ giữ lại 3 file log cũ (performance.log.1, .2, .3)
    file_handler = RotatingFileHandler(
        'performance.log', 
        maxBytes=5*1024*1024, 
        backupCount=3,
        encoding='utf-8' # Đảm bảo hỗ trợ ký tự Unicode
    )
    file_handler.setFormatter(log_formatter)
    
    # --- Handler để in log ra console (màn hình terminal) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)

    # Thêm các handler vào logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logger.info("Hệ thống ghi log hiệu suất đã được khởi tạo.")
    
    return logger

# Có thể gọi trực tiếp để kiểm tra
if __name__ == '__main__':
    # Thiết lập logger
    perf_logger = setup_logger()
    
    # Ví dụ cách sử dụng
    perf_logger.debug("Đây là một thông điệp debug.")
    perf_logger.info("Đây là một thông điệp thông tin (info).")
    perf_logger.warning("Đây là một cảnh báo (warning).")
    perf_logger.error("Đây là một thông báo lỗi (error).")
    
    print("\nKiểm tra file 'performance.log' đã được tạo trong cùng thư mục.")
