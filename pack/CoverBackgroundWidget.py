from PyQt5.QtCore import QRectF, Qt, QTimer
from PyQt5.QtGui import QPixmap, QColor, QPainter, QPainterPath
from PyQt5.QtWidgets import QWidget, QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect


class CoverBackgroundWidget(QWidget):
    """
    👑 高性能毛玻璃背景渲染引擎 (增强版)
    加入动态降维、零模糊跳过、平滑交叉叠化 (Crossfade)，及动态参数调控接口
    """

    def __init__(self, parent=None, opacity=0.15, blur_radius=10, fade_duration=600):
        super().__init__(parent)
        self.raw_pixmap = QPixmap()  # 原始图片(未模糊)
        self.blurred_pixmap = QPixmap()  # 当前使用的模糊图片
        self.old_blurred_pixmap = QPixmap()  # 上一张图(用于交叉叠化过渡)

        # ====== 👑 核心渲染参数 (类内私有化) ======
        self.opacity = opacity  # 默认透明度
        self.blur_radius = blur_radius  # 默认模糊度
        self.fade_duration = fade_duration  # 默认动画时长 (毫秒)
        self.base_color = QColor("#1e1f22")

        # ====== 🎬 交叉叠化动画引擎 ======
        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._update_fade)
        self.transition_progress = 1.0
        self.fade_step = 0.05

    def set_rendering_params(self, opacity=None, blur_radius=None, fade_duration=None):
        """
        👑 动态参数调控接口
        智能判定：若修改了模糊度且当前有封面，将瞬间重新计算毛玻璃并重绘。
        """
        need_reblur = False
        need_update = False

        # 1. 判定透明度变化
        if opacity is not None and opacity != self.opacity:
            self.opacity = opacity
            need_update = True  # 仅需重新呼叫 paintEvent 即可生效

        # 2. 判定动画时长变化
        if fade_duration is not None and fade_duration != self.fade_duration:
            self.fade_duration = fade_duration
            # 动画时长只影响下一次切歌，不需要重绘

        # 3. 判定模糊度变化
        if blur_radius is not None and blur_radius != self.blur_radius:
            self.blur_radius = blur_radius
            need_reblur = True  # 必须进入显卡重新计算高斯模糊

        # 🚀 触发视觉更新引擎
        if need_reblur and not self.raw_pixmap.isNull():
            # 提取原图，按照新的模糊度重新生成毛玻璃
            self.blurred_pixmap = self._apply_blur(self.raw_pixmap)
            need_update = True

        if need_update:
            self.update()  # 触发 UI 瞬间重绘

    def _apply_blur(self, pixmap):
        """核心黑科技：动态降维与高斯模糊 (直接使用类内 blur_radius)"""
        if pixmap.isNull():
            return pixmap

        # 动态计算封面的分辨率并除以 2 (防极小图崩溃，保底 100px)
        target_w = max(100, pixmap.width() // 2)
        target_h = max(100, pixmap.height() // 2)
        scaled = pixmap.scaled(target_w, target_h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

        # 如果模糊度为 0，直接返回缩放后的图，跳过昂贵的模糊计算
        if self.blur_radius <= 0:
            return scaled

        # 调用 Qt 的图形特效引擎进行真·高斯模糊
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(scaled)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(self.blur_radius)
        item.setGraphicsEffect(blur)
        scene.addItem(item)

        # 渲染出模糊后的成品图
        res = QPixmap(scaled.size())
        res.fill(Qt.transparent)
        ptr = QPainter(res)
        scene.render(ptr)
        ptr.end()

        # ⚠️ 【极其重要】：不要写 scene.clear()，让 Python 的 GC 去清理，防止 C++ 底层双重释放崩溃！
        return res

    def set_pixmap(self, pixmap):
        """对外精简接口：只负责塞入新封面，动画与参数由类内部全权接管"""
        self.raw_pixmap = pixmap

        # 1. 把当前的图降级为“旧图”，准备淡出
        if not self.blurred_pixmap.isNull():
            self.old_blurred_pixmap = self.blurred_pixmap
        else:
            self.old_blurred_pixmap = QPixmap()

        # 2. 处理新图，准备淡入
        if not pixmap.isNull():
            self.blurred_pixmap = self._apply_blur(pixmap)
        else:
            self.blurred_pixmap = QPixmap()

        # 3. 启动交叉叠化引擎
        if self.fade_duration > 0:
            self.transition_progress = 0.0
            interval = 1000 // 60  # 约 16ms 一帧 (60FPS)
            # 计算每帧的进度增量
            self.fade_step = interval / self.fade_duration
            self.fade_timer.start(interval)
        else:
            # 如果不需要动画，直接拉满进度，顺手释放内存
            self.transition_progress = 1.0
            self.old_blurred_pixmap = QPixmap()
            self.update()

    def _update_fade(self):
        """动画引擎的滴答函数"""
        self.transition_progress += self.fade_step
        if self.transition_progress >= 1.0:
            self.transition_progress = 1.0
            self.fade_timer.stop()
            # 动画结束，彻底销毁旧图释放显存
            self.old_blurred_pixmap = QPixmap()
        self.update()  # 触发重绘

    def _draw_aspect_fill_pixmap(self, painter, pixmap, current_opacity):
        """内部复用函数：按 Aspect Fill 比例绘制单张图"""
        if pixmap.isNull() or current_opacity <= 0.0:
            return

        painter.setOpacity(current_opacity)
        widget_size = self.size()
        pixmap_size = pixmap.size()

        scale_w = widget_size.width() / pixmap_size.width()
        scale_h = widget_size.height() / pixmap_size.height()
        scale = max(scale_w, scale_h)

        new_w = int(pixmap_size.width() * scale)
        new_h = int(pixmap_size.height() * scale)

        x = (widget_size.width() - new_w) // 2
        y = (widget_size.height() - new_h) // 2

        painter.drawPixmap(x, y, new_w, new_h, pixmap)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # 铺设圆角裁切
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 8, 8)
        painter.setClipPath(path)

        # 铺设暗色兜底
        painter.fillRect(self.rect(), self.base_color)

        # ====== 🎬 交叉叠化核心渲染逻辑 ======
        # 1. 渲染正在淡出的旧图
        if self.transition_progress < 1.0 and not self.old_blurred_pixmap.isNull():
            old_opacity = self.opacity * (1.0 - self.transition_progress)
            self._draw_aspect_fill_pixmap(painter, self.old_blurred_pixmap, old_opacity)

        # 2. 渲染正在淡入的新图
        if not self.blurred_pixmap.isNull():
            new_opacity = self.opacity * self.transition_progress
            self._draw_aspect_fill_pixmap(painter, self.blurred_pixmap, new_opacity)

        painter.end()
