# ScrollingLyricWidget.py
import re
from PyQt5.QtCore import Qt, QVariantAnimation, QEasingCurve, QRectF, QSize
from PyQt5.QtGui import QPainter, QColor, QFont, QFontMetrics
from PyQt5.QtWidgets import QWidget


class ScrollingLyricWidget(QWidget):
    """👑 旗舰级平滑滚动动画歌词引擎 (搭载动态流式排版 & 自定义可视行数)"""

    # ==================== 💎 全局类常量 (消灭魔术数字) ====================
    SAFE_RENDER_HEIGHT = 400.0  # 文本测算与渲染的理论无限高边界，防截断
    DEFAULT_WIDTH = 250  # 布局引擎推荐宽度
    DEFAULT_HEIGHT = 40  # 布局引擎推荐高度
    MIN_WIDTH = 50  # 布局引擎极限最小宽度
    MIN_HEIGHT = 40  # 布局引擎极限最小高度
    ANIMATION_DURATION = 350  # 黄金视觉滚动时间 (毫秒)
    LINE_SPACING = 3  # 极限歌词间距 (负数用于吃掉系统字体自带的透明留白)

    # =================================================================

    def __init__(self, parent=None, surrounding_lines=2):
        super().__init__(parent)
        self.surrounding_lines = max(0, surrounding_lines)

        self.lyrics_list = []
        self.current_idx = -1
        self.anim_offset = 0.0
        self.status_text = "随时随地发现好音乐"

        # 核心动画控制器
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(self.ANIMATION_DURATION)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._on_anim_step)

    def set_surrounding_lines(self, lines):
        """暴露给外部的接口，允许随时动态调整显示的行数"""
        self.surrounding_lines = max(0, lines)
        self.update()

    def sizeHint(self):
        return QSize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)

    def minimumSizeHint(self):
        return QSize(self.MIN_WIDTH, self.MIN_HEIGHT)

    def _on_anim_step(self, val):
        self.anim_offset = val
        self.update()

    def setText(self, text):
        self.status_text = re.sub(r'<[^>]+>', '', text)
        self.lyrics_list = []
        self.update()

    def set_lyrics(self, lyrics_dict, sorted_times):
        self.lyrics_list = [lyrics_dict[t] for t in sorted_times]
        self.current_idx = -1
        self.anim_offset = -1.0
        self.update()

    def set_current_index(self, idx):
        if idx == self.current_idx or not self.lyrics_list:
            return

        if self.current_idx != -1 and abs(idx - self.current_idx) > 2:
            self.anim_offset = float(idx)
            self.current_idx = idx
            self.animation.stop()
            self.update()
            return

        self.current_idx = idx
        self.animation.stop()
        self.animation.setStartValue(self.anim_offset)
        self.animation.setEndValue(float(idx))
        self.animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        if not self.lyrics_list:
            is_error = "失败" in self.status_text or "无可用" in self.status_text
            painter.setPen(QColor("#ff758c") if is_error else QColor("#80848e"))
            painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(self.rect(), Qt.AlignCenter, self.status_text)
            return

        font_normal = QFont("Microsoft YaHei", 8)
        font_active = QFont("Microsoft YaHei", 10, QFont.Bold)
        # 👑 引入双引擎测算：同时准备好小字体和大字体的测算尺子
        fm_normal = QFontMetrics(font_normal)
        fm_active = QFontMetrics(font_active)

        center_y = self.height() / 2.0
        W = self.width() - 20

        # ==================== 👑 动态坐标与视口演算矩阵 ====================
        N = self.surrounding_lines
        calc_range = N + 1

        def get_h(idx):
            """真正的次世代排版测算：高度随动画进程丝滑伸缩"""
            if idx < 0 or idx >= len(self.lyrics_list): return 0
            text = self.lyrics_list[idx]

            # 1. 测算它处于小字号(变灰时)的真实物理高度
            rect_n = fm_normal.boundingRect(
                0, 0, int(W), int(self.SAFE_RENDER_HEIGHT),
                Qt.AlignCenter | Qt.TextWordWrap, text
            )
            h_normal = rect_n.height()

            # 2. 测算它处于大字号(激活时)的真实物理高度
            rect_a = fm_active.boundingRect(
                0, 0, int(W), int(self.SAFE_RENDER_HEIGHT),
                Qt.AlignCenter | Qt.TextWordWrap, text
            )
            h_active = rect_a.height()

            # 3. 计算这句歌词此刻的“激活比例” (0.0 到 1.0 之间)
            # 距离中心越近，ratio 越趋近于 1.0
            diff = abs(idx - self.anim_offset)
            ratio = max(0.0, 1.0 - diff)

            # 4. 👑 核心魔法：根据激活比例，动态返回一个平滑过渡的高度！
            # 这样在歌词向上滚动的半秒钟里，它的占位高度会像呼吸一样丝滑缩小！绝对不会发生间距塌陷！
            return h_normal + (h_active - h_normal) * ratio

        base_idx = int(self.anim_offset)
        frac = self.anim_offset - base_idx

        # 1. 锚点坐标推算
        dist_to_next = (get_h(base_idx) + get_h(base_idx + 1)) / 2.0 + self.LINE_SPACING
        y_positions = {base_idx: center_y - frac * dist_to_next}

        # 2. 向上发散推算
        for i in range(base_idx - 1, base_idx - calc_range - 1, -1):
            dist = (get_h(i) + get_h(i + 1)) / 2.0 + self.LINE_SPACING
            y_positions[i] = y_positions[i + 1] - dist

        # 3. 向下发散推算
        for i in range(base_idx + 1, base_idx + calc_range + 1):
            dist = (get_h(i - 1) + get_h(i)) / 2.0 + self.LINE_SPACING
            y_positions[i] = y_positions[i - 1] + dist
        # ===============================================================

        # 开始绘制可见的视口歌词
        for i in range(base_idx - calc_range, base_idx + calc_range + 1):
            if i < 0 or i >= len(self.lyrics_list): continue

            text = self.lyrics_list[i]
            y_center = y_positions[i]

            diff = i - self.anim_offset

            # 视口边缘精准裁剪
            if abs(diff) > N + 0.5:
                continue

                # 自适应透明度渐隐算法
            opacity = 1.0 - min(abs(diff) / (N + 0.5), 1.0)
            painter.setOpacity(opacity)

            if i == self.current_idx:
                painter.setFont(font_active)
                painter.setPen(QColor("#ff758c"))
            else:
                painter.setFont(font_normal)
                painter.setPen(QColor("#6d6f78"))

            # 利用动态推导的安全高度进行绘制，彻底告别魔术数字与边缘截断！
            half_safe_h = self.SAFE_RENDER_HEIGHT / 2.0
            rect = QRectF(10, y_center - half_safe_h, W, self.SAFE_RENDER_HEIGHT)
            painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, text)
