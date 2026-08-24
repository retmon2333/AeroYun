import ctypes
import sys

from PyQt5.QtWidgets import QApplication

DARK_QSS = """
        /* ================= 1. 全局基础设置 ================= */
        QWidget {
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            color: #dbdee1; /* 全局亮灰色字体 */
            font-size: %%FONT_SIZE%%px; /* 💡 修改：使用占位符等待动态注入 */
        }
        QMainWindow, QDialog, QMessageBox {
            background-color: #2b2d31; /* 顶级窗口背景：深空灰 */
        }
        QToolTip {
            background-color: #1e1f22;
            color: #ffffff;
            border: 1px solid #ff758c; /* 提示框粉色描边 */
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* ================= 2. 文本输入与数值调整框 ================= */
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
            background-color: #1e1f22; 
            border: 1px solid #1e1f22;
            border-radius: 6px;
            padding: 6px 12px;
           
            color: #dbdee1;
            selection-background-color: #ff758c;
            selection-color: #ffffff;
        }
        QLineEdit:focus, QTextEdit:focus, QSpinBox:focus {
            border: 1px solid #ff758c; /* 聚焦时边框发光变粉色 */
            background-color: #2b2d31;
        }
        QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled {
            background-color: #202225;
            color: #6d6f78;
        }

        /* 步进器按钮 (上下箭头) */
        QSpinBox::up-button, QDoubleSpinBox::up-button, 
        QSpinBox::down-button, QDoubleSpinBox::down-button {
            background-color: transparent;
            border-radius: 4px;
            width: 16px;
        }
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {
            background-color: #2b2d31;
        }

        /* ================= 3. 下拉菜单 (ComboBox) ================= */
        QComboBox {
            background-color: #1e1f22; 
            border: 1px solid #1e1f22;
            border-radius: 6px;
            padding: 6px 12px;
        }
        QComboBox:hover, QComboBox:focus {
            border: 1px solid #ff758c; 
            background-color: #2b2d31;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QComboBox::down-arrow {
            /* 配合暗色主题的隐形下拉箭头，利用系统原生重绘 */
            width: 10px; height: 10px;
        }
        QComboBox QAbstractItemView {
            background-color: #1e1f22;           
            color: #dbdee1;                      
            border: 1px solid #2b2d31;           
            selection-background-color: #ff758c; 
            selection-color: #ffffff;            
            outline: none;
            border-radius: 6px;
            padding: 4px;
        }
        QComboBox QAbstractItemView::item {
            border-radius: 4px;
            min-height: 28px;
            padding-left: 5px;
        }

        /* ================= 4. 各种按钮 (Buttons) ================= */
        QPushButton, QToolButton {
            background-color: #1e1f22;
            border: 1px solid #1e1f22;
            border-radius: 6px;
            padding: 6px 14px;
            color: #dbdee1;
        }
        QPushButton:hover, QToolButton:hover {
            border: 1px solid #ff758c; 
            color: #ff758c;
            background-color: #2b2d31;
        }
        QPushButton:pressed, QToolButton:pressed {
            background-color: #d6556e; /* 💡 稍微变暗的粉色，产生按压反馈 */
            border: 1px solid #d6556e;
            color: #ffffff;
        }
        QPushButton:disabled {
            background-color: #202225;
            color: #6d6f78;
            border: none;
        }

        /* 独立定制：搜索按钮与大播放按钮 (纯实心粉色，视觉中心) */
        QPushButton#search_btn, QPushButton#btn_play {
            background-color: #ff758c;
            color: #ffffff;
            border: none;
            font-weight: bold;
        }
        QPushButton#search_btn:hover, QPushButton#btn_play:hover {
            background-color: #ff8fa3; /* 悬浮时变亮 */
        }
        /* 👑 核心修复：添加专属的 pressed 状态 */
        QPushButton#search_btn:pressed, QPushButton#btn_play:pressed {
            background-color: #d6556e; /* 按压时颜色下沉变暗 */
            color: #e0e0e0;            /* 字体稍微变灰，增强物理按压感 */
        }

        /* ================= 5. 数据视图 (Table / List / Tree) ================= */
        QTableView, QListView, QTreeView, QListWidget, QTreeWidget {
            background-color: #1e1f22; 
            border: 1px solid #1e1f22;
            border-radius: 8px;
            gridline-color: #2b2d31; 
            selection-background-color: #ff758c; 
            selection-color: #ffffff;            
            outline: none;
            padding: 2px;
        }
        QListView::item, QTreeView::item {
            padding: 6px;
            border-radius: 4px;
        }
        QListView::item:hover, QTreeView::item:hover {
            background-color: #2b2d31;
        }
        QListView::item:selected, QTreeView::item:selected {
            background-color: #ff758c;
            color: #ffffff;
        }
        /* 核心：设置选中行背景透明度 */
        QTableView::item:selected {
            background-color: rgba(255, 117, 140, 150); /* 50 为透明度，取值 0-255 */
            selection-background-color: rgba(255, 117, 140, 150);
        }
        
       /* ================= 5. 数据视图 (Table / List / Tree) 附加透明表头 ================= */
        /* 整个表头的底层容器全透，把渲染权交给底层毛玻璃 */
        QHeaderView {
            background-color: transparent; 
            border: none;
        }

        /* 顶部列标题和左侧行标题的单元格 */
        QHeaderView::section {
            background-color: rgba(43, 45, 49, 160); /* 👑 半透明的深空灰 (#2b2d31) */
            color: #80848e; 
            border: none;
            /* 👑 极弱的透明白边，模拟玻璃切割质感 */
            border-right: 1px solid rgba(255, 255, 255, 10); 
            border-bottom: 1px solid rgba(255, 255, 255, 10);
            padding: 6px 10px;
            font-weight: bold;
        }
        
        /* 鼠标悬浮在表头时稍微亮一点点 */
        QHeaderView::section:hover {
            background-color: rgba(64, 66, 73, 180); 
            color: #dbdee1;
        }

        /* 左上角行与列交界处的那个小方块 */
        QTableView QTableCornerButton::section {
            background-color: rgba(43, 45, 49, 160);
            border: none;
            border-right: 1px solid rgba(255, 255, 255, 10);
            border-bottom: 1px solid rgba(255, 255, 255, 10);
        }

        /* ================= 6. 滚动条 (全局半透明悬浮) ================= */
        /* 垂直滚动条 */
        QScrollBar:vertical {
            border: none;
            background: transparent; /* 👑 轨道全透 */
            width: 8px;
            margin: 0px;
            border-radius: 4px;  
        }
        /* 滑块主体 */
        QScrollBar::handle:vertical {
            background: rgba(64, 66, 73, 150); /* 👑 半透明的深灰 */
            min-height: 30px;
            border-radius: 4px;
        }
        /* 鼠标悬浮滑块时变粉色且加深不透明度 */
        QScrollBar::handle:vertical:hover {
            background: rgba(255, 117, 140, 200); 
        }
        /* 上下空白区域全透 */
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent; 
        }
        /* 隐藏顶部和底部的上下箭头按钮 */
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none; 
            background: transparent;
            height: 0px;
        }

        /* 水平滚动条 (同理) */
        QScrollBar:horizontal {
            border: none;
            background: transparent; 
            height: 8px;
            margin: 0px;
            border-radius: 4px;  
        }
        QScrollBar::handle:horizontal {
            background: rgba(64, 66, 73, 150);
            min-width: 30px;
            border-radius: 4px;
        }
        QScrollBar::handle:horizontal:hover {
            background: rgba(255, 117, 140, 200); 
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: transparent; 
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            border: none; 
            background: transparent;
            width: 0px;
        }

        /* ================= 7. 标签页 (Tab Widget) ================= */
        QTabWidget::pane {
            border: 1px solid #1e1f22;
            background-color: #1e1f22;
            border-radius: 6px;
            top: -1px; /* 掩盖底部边框 */
        }
        QTabBar::tab {
            background-color: #2b2d31;
            color: #80848e;
            padding: 8px 20px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            margin-right: 2px;
            border: 1px solid transparent;
        }
        QTabBar::tab:hover {
            color: #dbdee1;
            background-color: #1e1f22;
        }
        QTabBar::tab:selected {
            background-color: #1e1f22;
            color: #ff758c;
            font-weight: bold;
            border-bottom: 2px solid #ff758c; /* 底部粉色指示条 */
        }

        /* ================= 8. 单选框与复选框 (Checkbox / Radio) ================= */
        QCheckBox, QRadioButton {
            spacing: 8px;
            color: #dbdee1;
        }
        QCheckBox::indicator, QRadioButton::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1px solid #404249;
            background-color: #1e1f22;
        }
        QRadioButton::indicator {
            border-radius: 8px; /* Radio 是圆的 */
        }
        QCheckBox::indicator:hover, QRadioButton::indicator:hover {
            border: 1px solid #ff758c;
        }
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {
            background-color: #ff758c;
            border: 1px solid #ff758c;
        }

        /* ================= 9. 进度条 (ProgressBar) ================= */
        QProgressBar {
            background-color: #1e1f22;
            border-radius: 6px;
            text-align: center;
            color: #ffffff;
            font-weight: bold;
        }
        QProgressBar::chunk {
            background-color: #ff758c;
            border-radius: 6px;
        }

        /* ================= 10. 底部播放器控制台定制 ================= */
        QFrame#frame_player {
            background-color: #2b2d31; 
            border-top: 1px solid #1e1f22;
            border-radius: 0px;
        }

        /* 播放进度条 */
        QSlider::groove:horizontal {
            border-radius: 5px;
            height: 4px;
            background: #404249; 
        }
        QSlider::handle:horizontal {
            background: #ffffff; 
            width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;
        }
        QSlider::sub-page:horizontal {
            background: #ff758c; 
            border-radius: 3px;
        }

        /* 控制按钮 */
        QPushButton#btn_play {
        font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets", sans-serif;
            font-size: 18px; border-radius: 22px;
        }
        /* 前进后退按钮的专属逻辑 */
        QPushButton#btn_prev, QPushButton#btn_next {
        font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets", sans-serif;
            border-radius: 18px;
            color: #dbdee1;
            border: none;
            background: transparent;
            font-size: 14px;
        }
        QPushButton#btn_prev:hover, QPushButton#btn_next:hover {
            color: #ff758c; 
            background-color: #1e1f22; /* 悬浮时浮现底色 */
        }
        /* 👑 核心修复：前进后退按钮的按压状态 */
        QPushButton#btn_prev:pressed, QPushButton#btn_next:pressed {
            color: #d6556e;            /* 图标颜色变暗 */
            background-color: #17181a; /* 背景色比深空灰更黑一点 */
        }

        /* ================= 11. 右键菜单 (Context Menu) ================= */
        QMenu {
            background-color: #2b2d31; 
            color: #dbdee1;            
            border: 1px solid #1e1f22; 
            border-radius: 8px;        
            padding: 4px 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 6px 12px 6px 12px;
            border-radius: 4px;        
            margin: 2px 4px;
        }
        QMenu::item:selected {
            background-color: #ff758c; 
            color: #ffffff;
            font-weight: bold;
        }
        QMenu::item:!enabled {
            color: #6d6f78; 
            background-color: transparent;
        }
        QMenu::separator {
            height: 1px;
            background: #1e1f22;
            margin: 4px 8px;
        }
        QMenu::right-arrow {
            width: 12px;
            height: 12px;
        }
        
        """


def set_os_titlebar_theme(window, is_dark: bool):
    """调用 Windows 原生 API 强制切换系统标题栏的暗/亮模式 (极度优雅)"""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1 if is_dark else 0)
        # 20 适用于 Win11 和较新的 Win10
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        # 19 适用于早期的 Win10
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except Exception as e:
        print(f"系统标题栏主题切换略过: {e}")


# 2. 新增：动态计算并渲染 QSS 的核心引擎
def get_dynamic_qss(base_font_size=11):
    """
    根据当前主屏幕的 DPI 缩放比例，动态计算最合适的字号并注入到 QSS 中。
    :param base_font_size: 100% 缩放比例下的基础字号（默认 11px）
    """
    # 获取当前的 QApplication 实例
    app = QApplication.instance()
    if not app:
        return DARK_QSS.replace("%%FONT_SIZE%%", str(base_font_size))
    # 获取主屏幕
    screen = app.primaryScreen()
    # 获取逻辑 DPI (Windows 在 100% 缩放时，标准 DPI 为 96)
    logical_dpi = screen.logicalDotsPerInch()
    # 计算当前系统的缩放比例 (例如 120 DPI / 96 = 1.25 即 125% 缩放)
    scale_ratio = logical_dpi / 96.0
    # ================= 深度思考：防止双重缩放的黑科技 =================
    # 如果系统开启了 Qt.AA_EnableHighDpiScaling，Qt 内部已经对界面的物理长宽做了拉伸。
    # 为了避免字体被“计算一次+底层拉伸一次”导致双倍巨大化，
    # 我们可以根据 devicePixelRatio() 来做动态衰减补偿。
    # 但最稳妥的办法是：直接将基础字号乘上我们计算出的缩放比，并做好上下限控制。
    # ================================================================

    dynamic_font_size = round(base_font_size * scale_ratio)
    # 安全边界：限制最小 10px，最大 18px，防止极端分辨率下字体崩溃
    dynamic_font_size = max(10, min(dynamic_font_size, 18))
    print(
        f"[DEBUG LOG] 🖥️ 屏幕 DPI: {logical_dpi:.0f}, 缩放比: {scale_ratio:.2f}x, 动态全局字号: {dynamic_font_size}px")
    # 替换占位符并返回最终的 QSS 字符串
    return DARK_QSS.replace("%%FONT_SIZE%%", str(dynamic_font_size))


def adjust_window_size_by_dpi(window, designed_width, designed_height, design_scale=1.5):
    """
    根据当前屏幕 DPI 与可用分辨率执行【反向动态调整】，并强制施加安全边界。
    """
    app = QApplication.instance()
    if not app:
        return

    # 获取当前主屏幕对象
    screen = app.primaryScreen()
    logical_dpi = screen.logicalDotsPerInch()

    # Windows 标准 100% 缩放的 DPI 是 96
    current_scale = logical_dpi / 96.0

    # 1. 基础反向缩放计算
    target_width = int(designed_width * (design_scale / current_scale))
    target_height = int(designed_height * (design_scale / current_scale))

    # ==================== 👑 [深度排雷] 物理分辨率安全边界 ====================
    # availableGeometry 获取的是刨除 Windows 底部任务栏后的【真实可用逻辑面积】
    available_geometry = screen.availableGeometry()
    screen_w = available_geometry.width()
    screen_h = available_geometry.height()

    # 设定最高安全阈值：窗口最大不允许超过屏幕宽度的 65%，高度的 75%
    # (你可以根据 UI 设计的主观视觉需求微调这两个比例参数)
    max_width = int(screen_w * 0.65)
    max_height = int(screen_h * 0.75)

    # 防溢出机制：取“公式计算结果”与“屏幕安全边界”的最小值
    final_width = min(target_width, max_width)
    final_height = min(target_height, max_height)

    print(f"[DEBUG LOG] 🖥️ 系统环境: 缩放 {current_scale}x, 可用逻辑空间: {screen_w}x{screen_h}")
    print(f"[DEBUG LOG] 🪟 原始公式计算: {target_width}x{target_height}")
    print(f"[DEBUG LOG] 🛡️ 边界修正裁切: {final_width}x{final_height}")

    # 2. 强制重置窗口尺寸
    window.resize(final_width, final_height)

    # 3. 尺寸变动后，必须重新计算中心点并强制居中，否则窗口会偏移
    rect = window.frameGeometry()
    center_point = available_geometry.center()
    rect.moveCenter(center_point)
    window.move(rect.topLeft())