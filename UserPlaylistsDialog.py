from PyQt5.QtWidgets import QListWidgetItem, QMainWindow, QShortcut, QMenu, QAction  # 确保引入
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon, QPixmap, QKeySequence
# 如果你编译了 XML，引入下面这句。
from window.ui_user import Ui_UserInfoWindow
from pack.network import *
from pack import qss


class UserPlaylistsDialog(QMainWindow, Ui_UserInfoWindow):
    """解耦的用户个人信息子窗口"""
    # 自定义信号：当用户双击歌单时，向主窗口发送歌单 ID
    playlist_double_clicked_signal = pyqtSignal(str)
    daily_recommend_signal = pyqtSignal(str)

    def __init__(self, session, cache_dir, user_data, address):
        super().__init__()
        self.setupUi(self)
        self.session = session
        self.cache_dir = cache_dir
        self.user_data = user_data  # 包含 uid, nickname, avatar_path
        self.address = address

        # 防止被 Python 垃圾回收的线程池
        self._thread_pool_refs = set()

        self.shortcut_close = QShortcut(QKeySequence("Esc"), self)
        self.shortcut_close.activated.connect(self.close)

        self.init_ui()
        self.fetch_playlists()

    def init_ui(self):
        """初始化暗黑主题与 QListWidget 渲染策略"""
        # 1. 设置头像和昵称
        self.label_nickname.setText(self.user_data.get("nickname", "未知"))
        avatar_path = self.user_data.get("avatar_path")
        if avatar_path and os.path.exists(avatar_path):
            self.label_avatar.setPixmap(QPixmap(avatar_path))

        # 2. 配置 QListWidget 支持大图标显示
        self.listWidget_playlists.setIconSize(QSize(45, 45))  # 左侧图标变大
        self.listWidget_playlists.setSpacing(2)

        # 3. 绑定双击事件
        self.listWidget_playlists.itemDoubleClicked.connect(self.on_item_double_clicked)
        # ====== 👑 绑定与初始化推荐按钮 ======
        self.btn_today_rec.clicked.connect(lambda: self.on_recommend_clicked(""))
        # 初始时将历史按钮禁用，防误触，等待后台拉取
        self.btn_history_rec.setEnabled(False)
        self.fetch_history_dates()
        # 4. 应用与主程序一致的暗黑粉色 QSS 主题
        self.setStyleSheet(qss.get_dynamic_qss())
        qss.adjust_window_size_by_dpi(self, self.width(), self.height(), design_scale=1.5)
        qss.set_os_titlebar_theme(self, True)

    # ====== 👑 [新增] 静默获取历史日推日期 ======
    def fetch_history_dates(self):
        worker = NetworkWorker(self.session, self.address)
        worker.history_dates_finished.connect(self.setup_history_menu)
        worker.prepare_get_history_dates()
        self._thread_pool_refs.add(worker)
        worker.finished.connect(lambda: self._thread_pool_refs.discard(worker))
        worker.start()

    # ====== 👑 [新增] 动态生成按钮的下拉菜单 ======
    def setup_history_menu(self, dates):
        if not dates:
            # 如果没有数据(非黑胶VIP或记录为空)
            self.btn_history_rec.setText("🕒 无历史日推")
            self.btn_history_rec.setToolTip("黑胶VIP可查看近期5次历史记录")
            return

        self.btn_history_rec.setText("🕒 历史日推 ▾")
        self.btn_history_rec.setEnabled(True)

        # 创建下拉菜单并应用透明属性（支持QSS圆角）
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_TranslucentBackground)

        for date_str in dates:
            action = QAction(f"📅 {date_str}", self)
            action.triggered.connect(lambda checked, d=date_str: self.on_recommend_clicked(d))
            menu.addAction(action)

        # 优雅挂载：QPushButton 原生支持 setMenu，点击后自动弹出
        self.btn_history_rec.setMenu(menu)

    def fetch_playlists(self):
        """向后台请求数据"""
        uid = self.user_data.get("uid")
        if not uid:
            return

        self.label_title.setText("正在加载歌单...")
        worker = NetworkWorker(self.session, self.address)
        worker.user_playlists_finished.connect(self.render_playlists)
        worker.prepare_get_user_playlists(uid, self.cache_dir)

        self._thread_pool_refs.add(worker)
        worker.finished.connect(lambda: self._thread_pool_refs.discard(worker))
        worker.start()

    def render_playlists(self, playlists_data):
        """将数据渲染进 QListWidget，实现图文混排"""
        self.label_title.setText(f"我的歌单 ({len(playlists_data)}个) ")
        for pl in playlists_data:
            # 文本格式：使用 \n 实现双行显示 (歌单名 + 歌曲数量)
            display_text = f"{pl['name']}\n{pl['count']} 首歌曲"

            item = QListWidgetItem(display_text)
            if pl["cover_path"] and os.path.exists(pl["cover_path"]):
                item.setIcon(QIcon(pl["cover_path"]))

            # 💡 将歌单的真实 ID 隐藏绑定在节点里，双击时方便提取
            item.setData(Qt.UserRole, pl["id"])

            self.listWidget_playlists.addItem(item)

    def on_item_double_clicked(self, item):
        """双击歌单事件处理"""
        playlist_id = item.data(Qt.UserRole)
        # 发射自定义信号，通知主窗口
        self.playlist_double_clicked_signal.emit(str(playlist_id))
        # 搜索后自动关闭当前子窗口
        self.close()

    def on_recommend_clicked(self, date_str):
        """统一分发日推点击事件，并关闭窗口"""
        self.daily_recommend_signal.emit(date_str)
        self.close()
