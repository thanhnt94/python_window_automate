# -*- coding: utf-8 -*-
import os
import io
import time
import datetime
import logging
import pandas as pd
import configparser
from typing import Union, List
from selenium import webdriver
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, InvalidArgumentException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.ie.service import Service as IeService
from selenium.webdriver.ie.options import Options as IeOptions

try:
    import pyperclip
except ImportError:
    logging.warning("Thư viện 'pyperclip' không được tìm thấy. Chức năng nhập text sẽ không hoạt động.")
    logging.warning("Vui lòng cài đặt bằng lệnh: pip install pyperclip")
    pyperclip = None

class SeleniumController:
    # =================================================================
    # SECTION: CẤU HÌNH MẶC ĐỊNH
    # =================================================================
    DEFAULT_CONFIG = {
        'browser_mode': 'chrome', 'url': None, 'headless': False,
        'start_maximized': True, 'timeout': 15,
        'driver_paths': {
            'chrome_driver': None, 'edge_driver': None, 'ie_driver': None,
            'edge_exe': r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        },
        'output_paths': { 'downloads': None, 'screenshots': None }
    }

    # =================================================================
    # SECTION: KHỞI TẠO & DỌN DẸP
    # =================================================================
    def __init__(self, config: Union[dict, str] = None, ui_notifier: object = None):
        user_config = {}
        if isinstance(config, str):
            logging.info(f"Đang đọc cấu hình từ file: {config}")
            user_config = self._load_config_from_file(config)
        elif isinstance(config, dict):
            user_config = config
        
        self.config = self._deep_merge_configs(self.DEFAULT_CONFIG, user_config)
        self.driver: WebDriver = None
        self.ui_notifier = ui_notifier
        self._ensure_output_dirs_exist()

        self._show_notification(f"Đang khởi tạo trình duyệt: {self.config['browser_mode'].upper()}", style='process')
        try:
            browser_mode = self.config['browser_mode'].lower()
            if browser_mode == 'chrome': self.driver = self._create_chrome_driver()
            elif browser_mode == 'edge': self.driver = self._create_edge_driver()
            elif browser_mode == 'iemode': self.driver = self._create_iemode_driver()
            else: raise ValueError(f"Chế độ trình duyệt '{browser_mode}' không được hỗ trợ.")

            self.wait = WebDriverWait(self.driver, self.config['timeout'])
            if self.config['start_maximized']: self.driver.maximize_window()
            
            self._show_notification("Trình duyệt đã khởi tạo thành công.", style='success')
            if self.config['url']:
                self.run_action(None, ('go_to_url', self.config['url']), description=f"Mở trang web ban đầu")
        except Exception as e:
            self._show_notification(f"LỖI KHỞI TẠO: {e}", style='error', duration=0)
            logging.critical(f"LỖI NGHIÊM TRỌNG khi khởi tạo trình duyệt: {e}")
            raise

    def quit(self):
        if self.driver:
            self._show_notification("Đóng trình duyệt...", style='info', duration=2)
            self.driver.quit()
            self.driver = None

    # =================================================================
    # SECTION: HÀM ĐIỀU KHIỂN TRUNG TÂM
    # =================================================================
    def run_action(self, by_locator: tuple, action, description: str = None, screenshot_moment: str = None):
        log_msg = description or f"Thực thi '{action}' trên '{by_locator}'"
        self._show_notification(log_msg, style='process')
        logging.info(log_msg)
        try:
            if screenshot_moment in ['before', 'both']: self.take_screenshot(f"BEFORE_{description or 'action'}")
            
            action_name, *args = action if isinstance(action, (tuple, list)) else (action, [])
            action_map = {
                'click': lambda: self._click(by_locator),
                'enter_text': lambda: self._enter_text_paste(by_locator, args[0]),
                'type_text': lambda: self._enter_text_type(by_locator, args[0]),
                'enter_text_js': lambda: self._enter_text_js(by_locator, args[0]),
                'check_js': lambda: self._check_js(by_locator),
                'hover': lambda: self._hover(by_locator),
                'get_text': lambda: self._get_text(by_locator),
                'get_attribute': lambda: self._get_attribute(by_locator, args[0]),
                'go_to_url': lambda: self._go_to_url(args[0]),
                'execute_script': lambda: self.execute_script(args[0], *args[1:]),
            }
            if action_name not in action_map: raise ValueError(f"Hành động '{action_name}' không được hỗ trợ.")
            
            result = action_map[action_name]()
            
            if screenshot_moment in ['after', 'both']: self.take_screenshot(f"AFTER_{description or 'action'}")
            return result
        except Exception as e:
            error_action_name = action_name if 'action_name' in locals() else 'UNKNOWN'
            logging.error(f"Lỗi khi thực thi '{error_action_name}' trên '{by_locator}': {e}", exc_info=True)
            self.take_screenshot(f"ERROR_{error_action_name}")
            self._show_notification(f"Lỗi: {log_msg}", style='error', duration=5)
            raise

    # =================================================================
    # SECTION: CÁC HÀM TIỆN ÍCH CÔNG KHAI (PUBLIC)
    # =================================================================
    def get_title(self, description: str = None) -> str:
        log_msg = description or "Lấy tiêu đề trang hiện tại"
        logging.info(log_msg)
        return self.driver.title

    def execute_script(self, script: str, *args, description: str = None):
        log_msg = description or f"Thực thi script: {script[:50]}..."
        logging.info(log_msg)
        return self.driver.execute_script(script, *args)

    def take_screenshot(self, file_name_prefix: str, description: str = None) -> str:
        log_msg = description or f"Chụp ảnh màn hình: {file_name_prefix}"
        logging.info(log_msg)
        screenshot_dir = self.config['output_paths'].get('screenshots') or '.'
        safe_prefix = "".join(c for c in file_name_prefix if c.isalnum() or c in (' ', '_')).rstrip()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        full_path = os.path.join(screenshot_dir, f"{safe_prefix}_{timestamp}.png")
        try:
            self.driver.save_screenshot(full_path)
            logging.info(f"📸 Đã chụp màn hình và lưu tại: {full_path}")
            self._show_notification(f"Đã lưu ảnh: {os.path.basename(full_path)}", style='info', duration=2)
            return full_path
        except Exception as e:
            logging.error(f"Không thể chụp màn hình: {e}")
            return ""

    def wait_for_page_load_complete(self, timeout: int = None, description: str = None):
        log_msg = description or "Đang chờ trang tải hoàn tất..."
        logging.info(log_msg)
        wait = self.wait if timeout is None else WebDriverWait(self.driver, timeout)
        wait.until(lambda driver: driver.execute_script('return document.readyState') == 'complete')

    def is_element_displayed(self, by_locator: tuple, description: str = None) -> bool:
        log_msg = description or f"Kiểm tra hiển thị của element: {by_locator}"
        logging.info(log_msg)
        try:
            return self.driver.find_element(*by_locator).is_displayed()
        except NoSuchElementException:
            return False
    
    def get_table_as_dataframe(self, by_locator: tuple, method: str = 'auto', description: str = None) -> pd.DataFrame:
        log_msg = description or f"Trích xuất bảng {by_locator} thành DataFrame"
        logging.info(log_msg)
        try:
            if method == 'auto':
                try: return self._get_table_data_pandas(by_locator)
                except Exception as e:
                    logging.warning(f"Phương pháp 'pandas' thất bại (lỗi: {e}). Tự động chuyển sang 'manual'.")
                    data = self._get_table_data_manual(by_locator)
                    return pd.DataFrame(data)
            elif method == 'pandas': return self._get_table_data_pandas(by_locator)
            elif method == 'manual':
                data = self._get_table_data_manual(by_locator)
                return pd.DataFrame(data)
            else: raise ValueError("Phương pháp phải là 'auto', 'pandas', hoặc 'manual'.")
        except Exception as e:
            logging.error(f"Không thể trích xuất bảng {by_locator}: {e}")
            return pd.DataFrame()

    def wait_for_download_complete(self, filename_pattern: str, timeout: int = None, description: str = None) -> str:
        download_dir = self.config['output_paths'].get('downloads')
        if not download_dir: raise ValueError("Cần chỉ định 'output_paths:downloads' trong config.")
        
        wait_time = timeout or self.config['timeout']
        log_msg = description or f"Đang chờ file '{filename_pattern}' tải xong trong {wait_time}s"
        logging.info(log_msg)
        self._show_notification(log_msg, style='process')
        
        end_time = time.time() + wait_time
        while time.time() < end_time:
            files = [f for f in os.listdir(download_dir) if filename_pattern in f and not f.endswith(('.crdownload', '.tmp'))]
            if files:
                filepath = os.path.join(download_dir, files[0])
                logging.info(f"✅ Đã phát hiện file tải xong: {filepath}")
                self._show_notification(f"File '{files[0]}' đã tải xong.", style='success')
                return filepath
            time.sleep(1)
            
        raise TimeoutException(f"Hết thời gian chờ. Không tìm thấy file '{filename_pattern}' tại '{download_dir}'.")
    
    def get_current_window_handle(self, description: str = None) -> str:
        log_msg = description or "Lấy handle cửa sổ hiện tại"
        logging.info(log_msg)
        return self.driver.current_window_handle
    
    def get_all_window_handles(self, description: str = None) -> List[str]:
        log_msg = description or "Lấy tất cả handle cửa sổ"
        logging.info(log_msg)
        return self.driver.window_handles
    
    def switch_to_window(self, window_handle: str, description: str = None):
        log_msg = description or f"Chuyển sang cửa sổ có handle: {window_handle}"
        logging.info(log_msg)
        self.driver.switch_to.window(window_handle)
            
    def select_browse_window(self, strategy: str, description: str = None, timeout: int = None, old_handles: List[str] = None, **kwargs) -> str:
        """
        Chờ và chuyển sang một cửa sổ dựa trên một chiến lược lựa chọn.
        
        CHẾ ĐỘ AN TOÀN (khuyến nghị): Cung cấp 'old_handles' để chỉ tìm trong các cửa sổ mới.
        CHẾ ĐỘ KHÔNG AN TOÀN: Nếu 'old_handles' không được cung cấp, hàm sẽ quét tất cả các cửa sổ,
        có nguy cơ chọn nhầm cửa sổ cũ nếu tiêu đề trùng lặp.

        :param strategy: Chiến lược tìm cửa sổ ('title', 'content', 'newest').
        :param description: Mô tả cho hành động.
        :param timeout: Thời gian chờ tối đa.
        :param old_handles: (Tùy chọn) Danh sách các window handles trước khi hành động.
        :param kwargs: Các tham số cho chiến lược (ví dụ: title_text, locator, match_mode).
        :return: Handle của cửa sổ mới nếu thành công, None nếu thất bại.
        """
        wait = self.wait if timeout is None else WebDriverWait(self.driver, timeout)
        original_window = self.get_current_window_handle()
        log_msg = description or f"Đang tìm kiếm cửa sổ với chiến lược: '{strategy}'"
        logging.info(log_msg)
        self._show_notification(log_msg, style='process')

        if old_handles is None:
            logging.warning("Thực hiện tìm kiếm cửa sổ ở chế độ KHÔNG AN TOÀN. Kết quả có thể không chính xác nếu có các cửa sổ trùng lặp.")

        try:
            def find_the_right_window(driver):
                # Xác định tập hợp các cửa sổ cần quét
                if old_handles:
                    # Chế độ an toàn: Chờ có cửa sổ mới và chỉ quét nó
                    if len(driver.window_handles) <= len(old_handles):
                        return False # Chưa có cửa sổ mới, chờ tiếp
                    handles_to_scan = set(driver.window_handles) - set(old_handles)
                else:
                    # Chế độ không an toàn: Quét tất cả
                    handles_to_scan = driver.window_handles

                for handle in handles_to_scan:
                    try:
                        driver.switch_to.window(handle)
                        
                        # Nếu chiến lược là newest và đang ở chế độ an toàn, trả về ngay
                        if strategy == 'newest' and old_handles:
                            return handle

                        # Kiểm tra các điều kiện khác
                        if strategy == 'title':
                            title_text = kwargs.get('title_text')
                            if title_text is None: raise ValueError("Chiến lược 'title' yêu cầu 'title_text'.")
                            match_mode = kwargs.get('match_mode', 'contains')
                            current_title = driver.title
                            if current_title:
                               if match_mode == 'contains' and title_text in current_title: return handle
                               if match_mode == 'exact' and title_text == current_title: return handle
                        
                        elif strategy == 'content':
                            locator = kwargs.get('locator')
                            if locator is None: raise ValueError("Chiến lược 'content' yêu cầu 'locator'.")
                            if driver.find_elements(*locator):
                                return handle
                    except Exception:
                        # Bỏ qua nếu có lỗi khi chuyển cửa sổ (ví dụ: cửa sổ vừa bị đóng)
                        continue
                
                # Nếu không tìm thấy, quay về cửa sổ gốc để tránh bị kẹt trong lần lặp tiếp theo
                driver.switch_to.window(original_window)
                return False

            found_handle = wait.until(find_the_right_window)
            
            logging.info(f"Đã tìm thấy và chuyển sang cửa sổ: '{self.get_title()}' (Handle: {found_handle})")
            self._show_notification(f"Đã chọn cửa sổ: {self.get_title()}", style='success')
            return found_handle

        except TimeoutException:
            logging.warning(f"Hết thời gian chờ. Không tìm thấy cửa sổ với chiến lược '{strategy}'.")
            self.switch_to_window(original_window) # Đảm bảo quay về an toàn
            self._show_notification(f"Không tìm thấy cửa sổ ({strategy})", style='warning')
            return None

    def close_current_window_and_switch_back(self, handle_to_switch_back: str, description: str = None):
        log_msg = description or f"Đóng cửa sổ hiện tại và chuyển về: {handle_to_switch_back}"
        logging.info(log_msg)
        self.driver.close()
        self.switch_to_window(handle_to_switch_back)

    # =================================================================
    # SECTION: CÁC HÀM NỘI BỘ (PRIVATE)
    # =================================================================
    def _load_config_from_file(self, filepath: str) -> dict:
        parser = configparser.ConfigParser()
        if not os.path.exists(filepath): raise FileNotFoundError(f"File cấu hình không tồn tại: {filepath}")
        parser.read(filepath, encoding='utf-8')
        
        config_from_file = {}
        if 'Settings' in parser:
            settings = parser['Settings']
            for key in settings: config_from_file[key] = settings.get(key)
            for key in ['headless', 'start_maximized']:
                if key in settings: config_from_file[key] = settings.getboolean(key)
            if 'timeout' in settings: config_from_file['timeout'] = settings.getint('timeout')
        if 'DriverPaths' in parser: config_from_file['driver_paths'] = dict(parser['DriverPaths'])
        if 'OutputPaths' in parser: config_from_file['output_paths'] = dict(parser['OutputPaths'])
        return config_from_file

    def _ensure_output_dirs_exist(self):
        output_paths = self.config.get('output_paths', {})
        for path_key, path_value in output_paths.items():
            if path_value and not os.path.exists(path_value):
                os.makedirs(path_value); logging.info(f"Đã tạo thư mục '{path_key}' tại: {path_value}")

    def _deep_merge_configs(self, default, user):
        merged = default.copy()
        for key, value in user.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key] = self._deep_merge_configs(merged[key], value)
            else: merged[key] = value
        return merged

    def _go_to_url(self, url: str): self.driver.get(url)
    def _find_element(self, by_locator: tuple) -> WebElement: return self.wait.until(EC.visibility_of_element_located(by_locator))
    def _find_elements(self, by_locator: tuple) -> List[WebElement]: return self.wait.until(EC.visibility_of_all_elements_located(by_locator))
    def _click(self, by_locator: tuple): self.wait.until(EC.element_to_be_clickable(by_locator)).click()
    def _get_text(self, by_locator: tuple) -> str: return self._find_element(by_locator).text
    def _get_attribute(self, by_locator: tuple, attr: str) -> str: return self._find_element(by_locator).get_attribute(attr)

    def _enter_text_paste(self, by_locator: tuple, text: str):
        if not pyperclip: raise ImportError("Cần thư viện 'pyperclip'.")
        element = self._find_element(by_locator)
        pyperclip.copy(str(text))
        actions = ActionChains(self.driver)
        actions.click(element).key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL).send_keys(Keys.DELETE)
        actions.key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        time.sleep(0.5)

    def _enter_text_type(self, by_locator: tuple, text: str):
        element = self._find_element(by_locator)
        element.clear()
        element.send_keys(str(text))

    def _enter_text_js(self, by_locator: tuple, text: str):
        element = self._find_element(by_locator)
        self.execute_script("arguments[0].value = arguments[1];", element, str(text))

    def _check_js(self, by_locator: tuple):
        element = self._find_element(by_locator)
        self.execute_script("arguments[0].checked = true;", element)

    def _hover(self, by_locator: tuple):
        element = self._find_element(by_locator)
        ActionChains(self.driver).move_to_element(element).perform()

    def _show_notification(self, message: str, style: str = 'info', duration: int = 3, buttons: list = None):
        if self.ui_notifier:
            try: self.ui_notifier.update_status(message, style=style, duration=duration, buttons=buttons)
            except Exception as e: logging.warning(f"Lỗi Notifier: {e}")

    def _get_table_data_pandas(self, by_locator: tuple) -> pd.DataFrame:
        table_element = self._find_element(by_locator)
        html_content = table_element.get_attribute('outerHTML')
        df_list = pd.read_html(io.StringIO(html_content))
        if not df_list: raise ValueError("Pandas không tìm thấy bảng nào từ HTML của element.")
        return df_list[0]

    def _get_table_data_manual(self, by_locator: tuple) -> dict:
        table = self._find_element(by_locator)
        headers = [header.text.strip() for header in table.find_elements(By.TAG_NAME, "th")]
        if not headers:
            headers = [cell.text.strip() for cell in table.find_elements(By.XPATH, ".//tbody/tr[1]/td")]
        
        data_rows = table.find_elements(By.XPATH, ".//tbody/tr")
        all_rows_data = []
        for row in data_rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) == len(headers):
                all_rows_data.append([cell.text.strip() for cell in cells])
        
        data = {header: [row[i] for row in all_rows_data] for i, header in enumerate(headers)}
        return data

    def _create_chrome_driver(self) -> WebDriver:
        options = ChromeOptions()
        if self.config['headless']:
            options.add_argument("--headless"); options.add_argument("--window-size=1920,1080")
        if self.config['output_paths'].get('downloads'):
            prefs = {"download.default_directory": os.path.abspath(self.config['output_paths']['downloads'])}
            options.add_experimental_option("prefs", prefs)
        
        driver_path = self.config['driver_paths']['chrome_driver']
        service = ChromeService(executable_path=driver_path) if driver_path else None
        return webdriver.Chrome(service=service, options=options)

    def _create_edge_driver(self) -> WebDriver:
        options = EdgeOptions()
        if self.config['headless']:
            options.add_argument("--headless"); options.add_argument("--window-size=1920,1080")
        if self.config['output_paths'].get('downloads'):
            prefs = {"download.default_directory": os.path.abspath(self.config['output_paths']['downloads'])}
            options.add_experimental_option("prefs", prefs)

        driver_path = self.config['driver_paths']['edge_driver']
        service = EdgeService(executable_path=driver_path) if driver_path else None
        return webdriver.Edge(service=service, options=options)

    def _create_iemode_driver(self) -> WebDriver:
        driver_path = self.config['driver_paths']['ie_driver']
        if not driver_path: raise ValueError("Cần 'ie_driver' trong 'driver_paths' cho IE Mode.")
        
        ie_options = IeOptions()
        ie_options.attach_to_edge_chrome = True
        ie_options.edge_executable_path = self.config['driver_paths']['edge_exe']
        ie_options.ignore_protected_mode_settings = True
        ie_options.ignore_zoom_level = True
        
        service = IeService(executable_path=driver_path)
        return webdriver.Ie(service=service, options=ie_options)
