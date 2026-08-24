from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF  # 💡 新增了 QPointF
from PyQt5.QtGui import QPainter, QPainterPath, QPixmap


class RotatingCoverWidget(QWidget):
    """
    👑 高性能动态黑胶封面渲染引擎
    支持高分屏(High-DPI)、动态无缝切变(圆角方块 <-> 旋转圆盘)、60FPS 硬件加速旋转
    """

    def __init__(self, parent=None, rotat=False):
        super().__init__(parent)
        self.raw_pixmap = QPixmap()
        self.scaled_pixmap = QPixmap()

        self.rotat = rotat
        self.is_rotating = False
        self.angle = 0.0

        # 60FPS 丝滑旋转定时器 (1000ms / 60 ≈ 16ms)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._update_rotation)

        # 鼠标指针变成小手，提示用户可点击
        self.setCursor(Qt.PointingHandCursor)
        self.timer.start()

    def _update_rotation(self):
        """更新旋转角度"""
        if self.is_rotating:
            # 👑 修复 1：使用浮点取模，防止精度溢出
            self.angle = (self.angle + 0.4) % 360.0

            # 👑 修复 2：底层黑科技！永远避开绝对的 0.0 度
            # 强迫 Qt 引擎永远开启抗锯齿矩阵插值通道，杜绝整数贴图跳变！
            if self.angle == 0.0:
                self.angle = 0.001

            self.update()

    def set_pixmap(self, pixmap):
        """对外接口：灌入原始高清大图"""
        if pixmap is None:
            pixmap = QPixmap()

        self.raw_pixmap = pixmap
        self._update_scaled_pixmap()
        self.update()

    def _update_scaled_pixmap(self):
        """高分屏完美缩放引擎：在这里一次性搞定高清缩放，不用每次重绘都算"""
        if self.raw_pixmap.isNull():
            self.scaled_pixmap = QPixmap()
            return

        dpr = self.devicePixelRatioF()
        pw = int(self.width() * dpr)
        ph = int(self.height() * dpr)

        if pw <= 0 or ph <= 0:
            return

        self.scaled_pixmap = self.raw_pixmap.scaled(
            pw, ph, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        self.scaled_pixmap.setDevicePixelRatio(dpr)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小改变时，重新生成对应物理分辨率的图
        self._update_scaled_pixmap()

    def mousePressEvent(self, event):
        """单击切换形态与动画状态"""
        if event.button() == Qt.LeftButton:
            self.rotat = not self.rotat
            self.update()

    def changed_status(self, is_run):
        self.is_rotating = is_run

    def paintEvent(self, event):
        if self.scaled_pixmap.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = QRectF(self.rect())
        center = rect.center()
        path = QPainterPath()

        if self.rotat:
            # 变身黑胶唱片
            radius = min(rect.width(), rect.height()) / 2.0
            path.addEllipse(center, radius, radius)
        else:
            # 变回静态圆角正方形
            path.addRoundedRect(rect, 10, 10)

        painter.setClipPath(path)

        if self.rotat:
            painter.translate(center)
            painter.rotate(self.angle)
            painter.translate(-center)

        dpr = self.scaled_pixmap.devicePixelRatio()
        pw = self.scaled_pixmap.width() / dpr
        ph = self.scaled_pixmap.height() / dpr

        # 👑 修复 3：绝对禁止使用 int() 强转！保留小数级别浮点精度
        x = (rect.width() - pw) / 2.0
        y = (rect.height() - ph) / 2.0

        # 👑 修复 4：传入 QPointF 而不是分别传入 x, y，激活 Qt 浮点级渲染
        painter.drawPixmap(QPointF(x, y), self.scaled_pixmap)
        painter.end()
