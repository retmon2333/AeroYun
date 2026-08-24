import os
from PyQt5.QtWidgets import QMainWindow, QShortcut, QMessageBox, QLineEdit
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence

from window.ui_setting import Ui_SettingWindow
from pack import qss
from pack.AutoCookieSession import AutoCookieSession


class SettingDialog(QMainWindow, Ui_SettingWindow):
    update_server_addr = pyqtSignal(str)

    def __init__(self, session, cookie_path, main_app_ref):
        super().__init__()
        self.setupUi(self)
        self.session = session
        self.cookie_file_path = cookie_path
        self.main_app = main_app_ref  # 👑 拿到主窗口的控制权

        # 缓存原始 Cookie，避免保存时进行二次磁盘读取 (优化I/O)
        self._original_cookie = ""

        # 绑定 Esc 键快速关闭
        self.shortcut_close = QShortcut(QKeySequence("Esc"), self)
        self.shortcut_close.activated.connect(self.close)

        self.init_ui()
        self.init_logic()

    def init_ui(self):
        self.setStyleSheet(qss.get_dynamic_qss())
        qss.adjust_window_size_by_dpi(self, self.width(), self.height(), design_scale=1.5)
        qss.set_os_titlebar_theme(self, True)

    def init_logic(self):
        # ================= 1. 👑 读取主窗口的高速内存配置并精准回显 =================
        cfg = self.main_app.config.get_cache_cfg()

        # 回显：自动播放
        self.cb_autoplay.setChecked(cfg.get("auto_play", False))

        # 回显：服务器地址
        current_server = cfg.get("server", "")
        self.server_input.setText(current_server)

        # 回显：已保存的 Cookie (并存入内存避免重复读取)
        if os.path.exists(self.cookie_file_path):
            try:
                with open(self.cookie_file_path, 'r', encoding='utf-8') as f:
                    self._original_cookie = f.read().strip()

                if self._original_cookie:
                    self.cookie_input.setText(self._original_cookie)
                    self.cookie_input.setEchoMode(QLineEdit.PasswordEchoOnEdit)
                    cookie_lines = [c.strip() for c in self._original_cookie.split(';') if c.strip()]

                    safe_lines = []
                    for line in cookie_lines:
                        if len(line) > 65:
                            safe_lines.append(line[:65] + " ...")
                        else:
                            safe_lines.append(line)

                    if len(safe_lines) > 12:
                        safe_lines = safe_lines[:12]
                        safe_lines.append("... (已折叠隐藏)")

                    formatted_tooltip = "\n".join(safe_lines)
                    final_tooltip = f"可将自己网易云音乐账号的Cookies粘贴至此，保存重启即可。\n👇 当前已保存的 Cookies 预览:\n{'-' * 30}\n{formatted_tooltip}"
                    self.cookie_input.setToolTip(final_tooltip)
            except Exception as e:
                print(f"[DEBUG LOG] 读取 Cookie 缓存失败: {e}")

        # 提取渲染参数，转为整型以策安全
        opacity = int(cfg.get('widget_opacity', 0.15) * 100)
        blur_radius = int(cfg.get('widget_blur_radius', 45))

        self.horizontalSlider_opacity.setValue(opacity)
        self.horizontalSlider_blur_radius.setValue(blur_radius)

        # 👑 核心需求：使用 :02d 实现单数字自动补 0，如 1 变 01，10 还是 10
        self.label_opacity.setText(f"背景透明度： {opacity:02d}")
        self.label_blur_radius.setText(f"背景模糊度： {blur_radius:02d}")

        # ================= 2. 信号与槽的绑定 =================
        self.cb_autoplay.stateChanged.connect(self.toggle_autoplay)
        self.save_btn.clicked.connect(self.save_and_apply)
        self.server_input.setToolTip("网易云音乐服务器地址")
        self.horizontalSlider_opacity.valueChanged.connect(self.on_horizontalSlider_opacity)
        self.horizontalSlider_blur_radius.valueChanged.connect(self.on_horizontalSlider_blur_radius)

        self.label_author.setToolTip(
            '精通易语言，按键精灵，notepad IDE编程\n易语言大手子\nCV工程师\n提示词工程师\n超级模块，吊打，遥遥领先，一键过检测\n—————— by:lyx')

    def on_horizontalSlider_opacity(self, v):
        # 👑 动态更新时同样补零
        self.label_opacity.setText(f"背景透明度： {v:02d}")
        f_v = round(v / 100, 2)
        self.main_app.config.set_config('widget_opacity', f_v)
        if hasattr(self.main_app, 'table_bg_widget'):
            self.main_app.table_bg_widget.set_rendering_params(opacity=f_v)

    def on_horizontalSlider_blur_radius(self, v):
        # 👑 动态更新时同样补零
        self.label_blur_radius.setText(f"背景模糊度： {v:02d}")
        self.main_app.config.set_config('widget_blur_radius', v)
        if hasattr(self.main_app, 'table_bg_widget'):
            self.main_app.table_bg_widget.set_rendering_params(blur_radius=v)

    def toggle_autoplay(self, state):
        """复选框：勾选/取消时即刻存入主窗口的内存字典"""
        is_checked = (state == Qt.Checked)
        self.main_app.config.set_config("auto_play", is_checked)

    def save_and_apply(self):
        """通用保存引擎：智能判定变更，按需触发重启"""
        need_restart = False

        # 1. 提取最新输入数据
        new_server = self.server_input.text().strip()
        raw_cookie = self.cookie_input.text().strip()

        # 2. 处理服务器地址的更新
        old_server = self.main_app.config.get_cache_cfg().get("server", "")
        if new_server and new_server != old_server:
            self.main_app.config.set_config("server", new_server)
            self.update_server_addr.emit(new_server)
            print(f"[DEBUG LOG] 服务器地址已变更为: {new_server}")

        # 3. 处理 Cookie 的更新 (👑 优化：直接对比内存缓存，不再读取硬盘)
        if raw_cookie and raw_cookie != self._original_cookie:
            try:
                AutoCookieSession(file_path=self.cookie_file_path, cookies=raw_cookie)
                need_restart = True
            except Exception as e:
                QMessageBox.critical(self, "错误", f"Cookie 解析/保存失败！\n详细信息: {e}")
                return
        self.main_app.config.flush_config_to_disk()
        # 4. 根据变更级别给出相应的反馈
        if need_restart:
            QMessageBox.information(self, "信息", "重新打开后Cookie生效")
            os._exit(0)

        self.close()

    def closeEvent(self, event):
        # 只要窗口关闭，就将内存中的配置安全持久化到硬盘，防止用户丢失 UI 调整
        try:
            self.main_app.config.flush_config_to_disk()
        except Exception as e:
            print(f"[DEBUG LOG] 窗口关闭时配置自动刷盘失败: {e}")
        event.accept()
