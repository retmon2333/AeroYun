import os
import sys
import re
import concurrent.futures
import bisect  # 新增：用于高效的歌词时间戳二分查找
import time

from PyQt5.QtGui import QDesktopServices, QIcon, QKeySequence
from UserPlaylistsDialog import UserPlaylistsDialog
from pack.flex_config import FlexConfig
# 导入你用 pyuic5 生成的 UI 界面类
from window.ui_main import Ui_MainWindow
from pack.AutoCookieSession import *

# ================= 引入 PyQt5 核心组件 =================
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QAbstractItemView, QMenu,
    QAction, QHeaderView, QStyledItemDelegate, QStyle, QCompleter, QVBoxLayout, QShortcut, QSizePolicy
)
from PyQt5.QtCore import QUrl, QEvent, QStringListModel, QTimer
from PyQt5.QtGui import QStandardItemModel, QStandardItem
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from pack.network import *
from pack import qss
from SettingDialog import SettingDialog
from pack.ScrollingLyricWidget import *
from pack.CoverBackgroundWidget import *
from pack.util import *
from pack.RotatingCoverWidget import *
from pack.sys_cfg import cloud_config
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, error


# ================= 3. 高并发文件 IO 与 ID3 注入线程池 =================
class DownloadFileWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    progress_process_signal = pyqtSignal(bool, int, int)

    def __init__(self, download_dir, download_tasks, max_workers=5):
        super().__init__()
        self.download_dir = download_dir
        self.download_tasks = download_tasks
        self.max_workers = max_workers

    def sanitize_filename(self, name):
        # 1. 过滤 Windows 非法字符
        cleaned = re.sub(r'[\/:*?"<>|]', '_', name)
        # 2. 替换换行符等不可见控制字符
        cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', cleaned)
        # 3. 👑 核心：剥除字符串两端的空格和句号 (Windows 底层最怕这个)
        cleaned = cleaned.strip(' .')

        # 4. 👑 核心：防止超长文件名导致操作系统的 PathTooLongException
        # 取文件名(无后缀)和后缀，确保整体不超过 200 个字符
        if len(cleaned) > 200:
            name_part, ext_part = os.path.splitext(cleaned)
            cleaned = name_part[:(200 - len(ext_part))] + ext_part

        # 兜底：如果清理完了变空了，给个默认名字
        return cleaned if cleaned else "未知歌曲_下载"

    def embed_id3_tags(self, file_path, task):
        """👑 核心引擎：音频标签与封面物理注入（全格式兼容）"""
        ext = str(task.get('ext', '')).lower()

        try:
            # 1. 优先下载封面图片 (提前拿到二进制流，提高容错)
            cover_data = None
            cover_url = task.get('cover_url')
            if cover_url:
                img_res = requests.get(cover_url, headers=get_headers(), timeout=10)
                if img_res.status_code == 200:
                    cover_data = img_res.content

            # ================= 🎧 MP3 格式处理 =================
            if ext in ['mp3', 'mpeg']:
                audio = MP3(file_path, ID3=ID3)
                # 检查并初始化头部
                if audio.tags is None:
                    audio.add_tags()

                # 写入基础文本信息
                audio.tags.add(TIT2(encoding=3, text=task.get('name', '')))
                audio.tags.add(TPE1(encoding=3, text=task.get('artist', '')))
                audio.tags.add(TALB(encoding=3, text=task.get('album', '')))

                # 写入封面图
                if cover_data:
                    audio.tags.add(
                        APIC(
                            encoding=3,  # 必须是3，代表 UTF-8
                            mime='image/jpeg',
                            type=3,  # 3 代表正面封面 (Front Cover)
                            desc='Cover',
                            data=cover_data
                        )
                    )
                # 👑 [核心修复]：强制要求 Mutagen 保存为 ID3v2.3 版本！
                # 这样 Windows 系统右键属性、文件图标以及各大车载机身才能完美识别图片。
                audio.save(v2_version=3)

            # ================= 💽 FLAC 格式处理 =================
            elif ext == 'flac':
                from mutagen.flac import FLAC, Picture
                audio = FLAC(file_path)

                # FLAC 的文本标签可以直接按字典写入
                audio['title'] = task.get('name', '')
                audio['artist'] = task.get('artist', '')
                audio['album'] = task.get('album', '')

                if cover_data:
                    # FLAC 拥有自己独立的图片块体系
                    pic = Picture()
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Cover"
                    pic.data = cover_data
                    audio.clear_pictures()  # 稳妥起见：清除网易云可能附带的残缺封面
                    audio.add_picture(pic)

                audio.save()
            else:
                print(f"[DEBUG LOG] 暂不支持为 {ext} 格式写入标签，已跳过。")
                return

            print(f"[DEBUG LOG] ✅ 标签与封面写入成功: {task['filename']}")

        except Exception as e:
            # 如果依然报错，这个 Print 能够帮你精准看到报错栈
            print(f"[DEBUG LOG] ❌ 标签写入异常 [{task['filename']}]: {e}")

    def download_single_file(self, task):
        url = task.get("url")
        filename = self.sanitize_filename(task.get("filename"))
        file_path = os.path.join(self.download_dir, filename)
        try:
            with requests.get(url, stream=True, headers=get_headers(), timeout=30) as res:
                if res.status_code in [200, 206]:
                    with open(file_path, 'wb') as f:
                        for chunk in res.iter_content(chunk_size=1024 * 64):  # 提升缓冲区大小到 64KB 提速
                            if chunk:
                                f.write(chunk)

                    # 👑 音频落地后，立刻拦截并触发 ID3 标签注入引擎！
                    self.embed_id3_tags(file_path, task)

                    return True, filename, ""
                else:
                    return False, filename, f"HTTP {res.status_code}"
        except Exception as e:
            return False, filename, str(e)

    def run(self):
        total = len(self.download_tasks)
        completed, failed = 0, 0
        self.progress_signal.emit(f"已启动下载引擎(并发:{self.max_workers})，处理任务数: {total} ...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {executor.submit(self.download_single_file, task): task for task in self.download_tasks}
            for future in concurrent.futures.as_completed(future_to_task):
                success, filename, err_msg = future.result()
                if success:
                    completed += 1
                    self.progress_signal.emit(f"成功保存并注入封面: {filename}")
                    self.progress_process_signal.emit(True, total, completed + failed)
                else:
                    failed += 1
                    self.progress_signal.emit(f"下载失败 [{filename}]: {err_msg}")

        self.finished_signal.emit(f"✅ 批量任务结束！成功: {completed} 首，失败: {failed} 首")
        self.progress_process_signal.emit(False, 0, 0)


class NoFocusDelegate(QStyledItemDelegate):
    """自定义表格绘制代理：彻底抹除虚线框，并绘制播放高亮层"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playing_row = -1  # 初始状态：没有正在播放的行

    def set_playing_row(self, row):
        """提供给主窗口的接口，用于更新当前正在播放的行"""
        self.playing_row = row

    def paint(self, painter, option, index):
        # 1. 抹除系统默认虚线焦点框
        option.state &= ~QStyle.State_HasFocus

        # 👑 2. 核心：如果该行是正在播放的行
        if index.row() == self.playing_row:
            # 【深度思考逻辑】：如果这行正被用户选中，把优先展示权让给“选中高亮色”
            # 只有在没有被选中时，才显示“正在播放的浅灰底色”，层次分明！
            if not (option.state & QStyle.State_Selected):
                painter.save()
                # 绘制浅灰色透明底色 (RGB:255,255,255, Alpha: 25)
                # 这种带透明度的白色在暗黑主题下会呈现出非常高级的“磨砂浅灰”效果
                bg_color = QColor(255, 117, 140, 20)
                painter.fillRect(option.rect, bg_color)
                painter.restore()

        # 3. 继续完成正常的文本、红字版权判定和背景绘制
        super().paint(painter, option, index)


# ================= 4. PyQt5 主窗体与播放器实现 =================
class MusicDownloaderApp(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # 目录系统构建
        self.exe_dir = PathManager.get_exe_dir()
        self.cfg_dir = os.path.join(self.exe_dir, "cfg")
        self.download_dir = os.path.join(self.exe_dir, "download")
        self.cache_dir = os.path.join(self.exe_dir, "cache")
        self.cover_cache_dir = os.path.join(self.cache_dir, "album_covers")
        self.audio_cache_dir = os.path.join(self.cache_dir, "audio")
        for d in [self.cfg_dir, self.download_dir, self.cache_dir, self.cover_cache_dir, self.audio_cache_dir]:
            os.makedirs(d, exist_ok=True)

        self.cookie_file_path = os.path.join(self.cfg_dir, "cookies.txt")
        self.session = AutoCookieSession(file_path=self.cookie_file_path)

        # ====== [新增]：防脏数据与并发冲突的核心变量 ======
        self.search_epoch = 0  # 搜索纪元（每次搜索+1）
        self.cover_workers = []  # 存放正在运行的封面下载线程
        # ====== 💡 新增：L1 内存缓存字典，避免重复解析已显示的图标 ======
        self.icon_ram_cache = {}
        # 👑 [核心新增]：维护一个完整的 JSON 歌单累加器，防止分页覆盖
        self.current_raw_playlist = []
        self.current_keywords = ""
        self.current_offset = 0
        self.is_loading = False
        self.has_more = True
        self._thread_pool_refs = set()

        # 播放器核心变量
        self.player = QMediaPlayer()
        self.current_play_index = -1
        # ====== 💡 [新增]：播放准备互斥锁 ======
        self.play_epoch = 0
        self.suggest_epoch = 0
        # =======================================
        self.current_user_data = None  # 💡 新增：用于存储当前登录用户的信息
        self.user_window = None  # 💡 新增：子窗口引用防销毁
        self.setting_window = None
        self.parsed_lyrics = {}
        self.sorted_lyric_times = []
        self.server = ''
        # 👑 [新增] 初始化配置管理器
        self.config = cloud_config(os.path.join(self.cfg_dir, "config.json"))
        self.init_config()
        self.init_logic()
        self.init_player_ui()

        # ====== [新增 1]：在UI初始化完成后，立刻发起后台登录检测 ======
        self.check_initial_login_status()

    # ====== [新增 2]：发起检测请求的方法 ======
    def check_initial_login_status(self):
        """开机自动检测当前 Cookie 的登录状态"""
        self.status_lbl.setText("正在检测用户登录状态...")
        worker = NetworkWorker(self.session, self.server)
        # 绑定信号到下方的渲染函数
        worker.login_status_finished.connect(self.update_user_profile_ui)
        worker.error_occurred.connect(lambda e: print(f"[DEBUG LOG] 登录检测异常: {e}"))
        worker.prepare_check_login(self.cache_dir)

        self._track_thread(worker)
        worker.start()

    # ====== [新增 3]：接收数据并渲染界面的槽函数 ======
    def update_user_profile_ui(self, user_data):
        """将后台解析到的昵称和头像渲染到 UI 上"""
        self.current_user_data = user_data  # 💡 保存起来供子窗口使用
        nickname = user_data.get("nickname", "未登录")
        avatar_path = user_data.get("avatar_path", "")
        uid = user_data.get("uid")  # 拿到用户UID
        # ====== [修改]：你的UI文件中新加了 user_info_btn ======

        self.user_info_btn.setText(f"👤 {nickname}")
        # 2. 渲染左下角头像
        if avatar_path and os.path.exists(avatar_path):
            if self.current_play_index == -1:
                pixmap = QPixmap(avatar_path)
                # 👑 核心修复：直接把原图丢给动态引擎，无需手动裁剪！
                self.label_cover.set_pixmap(pixmap)

        print(f"[DEBUG LOG] 登录状态检查完成，当前用户: {nickname}")
        self.status_lbl.setText("系统就绪。")
        # 👑 [新增] 如果已登录，立刻在后台静默拉取该用户的歌单以备右键菜单使用！
        if uid:
            worker = NetworkWorker(self.session, self.server)
            worker.user_playlists_finished.connect(self.store_my_created_playlists)
            worker.prepare_get_user_playlists(uid, self.cache_dir)
            self._track_thread(worker)
            worker.start()

    # 👑 [新增] 专属槽函数：过滤并存储用户自建的歌单
    def store_my_created_playlists(self, playlists_data):
        uid = self.current_user_data.get("uid")
        # 核心过滤逻辑：只保留自己创建的歌单，别人的收藏歌单没权限添加！
        self.my_created_playlists = [pl for pl in playlists_data if pl.get("creator_id") == uid]
        print(f"[DEBUG LOG] ✅ 后台静默缓存 [我创建的歌单] 完毕，共 {len(self.my_created_playlists)} 个。")

    def init_logic(self):
        def reset_window():
            # ==================== 👑 [新增] 智能应用窗口尺寸 ====================
            self.setStyleSheet(qss.get_dynamic_qss())
            qss.set_os_titlebar_theme(self, True)
            saved_w = self.config.cache_cfg.get("window_width", 0)
            saved_h = self.config.cache_cfg.get("window_height", 0)

            if saved_w > 0 and saved_h > 0:
                # 场景 A：如果配置里有记录，直接精准恢复用户上一次手动拉拽的尺寸
                self.resize(saved_w, saved_h)
                print(f"[DEBUG LOG] 🪟 从记忆配置恢复窗口尺寸: {saved_w}x{saved_h}")
            else:
                # 场景 B：如果全新安装/从未记录过，则触发基于 150% 比例的反向动态计算引擎
                qss.adjust_window_size_by_dpi(self, self.width(), self.height(), design_scale=1.5)
                print("[DEBUG LOG] 🪟 初次运行，触发 DPI 自动适配窗口尺寸")
            # 1. 获取当前程序所在的主屏幕可用区域 (自动扣除了任务栏)
            screen_geo = QApplication.instance().primaryScreen().availableGeometry()
            # 2. 计算完美的居中坐标：(屏幕宽度 - 窗口宽度) / 2
            center_x = (screen_geo.width() - self.width()) // 2
            center_y = (screen_geo.height() - self.height()) // 2
            # 3. 将窗口移动到该坐标点
            self.move(center_x, center_y)
            # ====================================================================

        reset_window()
        # 👑 [核心修复]：防止超长歌名撑爆主窗口
        # 将 status_lbl 的水平尺寸策略设为 Ignored (忽略文字本身所需宽度)
        self.status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        # 👑 [绝命 Bug 修复]：彻底禁用表格点击排序，防止 UI 行号与内存 JSON 数组发生错位脱节！
        self.tableView.setSortingEnabled(False)
        # ========================================================
        self.table_model = QStandardItemModel()
        self.table_model.setHorizontalHeaderLabels(["歌曲名", "专辑名", "作者", "歌曲 ID", "MV ID"])
        self.tableView.setModel(self.table_model)

        # ====== [新增]：优化表格尺寸，使其完美容纳封面图片 ======
        # 统一设置行高为 60（图片大约 50x50，留点边距）
        self.tableView.verticalHeader().setDefaultSectionSize(35)
        # 告诉 TableView 里面 Icon 的统一大小
        self.tableView.setIconSize(QSize(30, 30))
        # ====== 💡 新增：实例化并保存代理对象的引用 ======
        self.table_delegate = NoFocusDelegate(self.tableView)
        self.tableView.setItemDelegate(self.table_delegate)

        # ====== 💡 核心修复：开启表格文本自动换行 ======
        self.tableView.setWordWrap(True)
        # ====== 重新精细化控制列宽分配 ======
        header = self.tableView.horizontalHeader()
        # 1. 歌曲ID(3)、MVID(4) 数据极短且固定，保留按内容自适应
        short_cols = [3, 4]
        for col in short_cols:
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # 2. 歌曲名(0)、专辑名(1)、作者(2) 都可能很长，让它们共同平分剩余空间！
        # 此时有了边界压迫，配合 setWordWrap(True)，超长的作者名字就会乖乖换行了。
        stretch_cols = [0, 1, 2]
        for col in stretch_cols:
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        # ==========================================

        self.tableView.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableView.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.tableView.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tableView.customContextMenuRequested.connect(self.show_context_menu)
        self.tableView.doubleClicked.connect(self.play_selected_song_from_table)

        self.tableView.verticalScrollBar().valueChanged.connect(self.handle_scroll_pagination)

        self.search_btn.clicked.connect(self.perform_new_search)
        self.search_input.returnPressed.connect(self.perform_new_search)
        self.user_info_btn.clicked.connect(self.open_user_window)
        self.user_info_btn.setToolTip("个人账号信息(需登录)")
        self.setting_btn.clicked.connect(self.open_setting_window)

        # ==================== [新增] 搜索建议核心逻辑 ====================
        # 1. 初始化防抖定时器 (防止频繁请求封IP)
        self.suggest_timer = QTimer(self)
        self.suggest_timer.setSingleShot(True)
        self.suggest_timer.timeout.connect(self.fetch_search_suggestions)
        # 2. 初始化自动完成器模型
        self.suggest_model = QStringListModel()
        self.completer = QCompleter(self.suggest_model, self)
        # 【核心设置】：设为 UnfilteredPopupCompletion，因为网易云API已经帮我们把联想词过滤好了
        # 我们只需要原封不动地展示 API 返回的结果即可
        self.completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        # 3. 将 Completer 挂载到现有的输入框上
        self.search_input.setCompleter(self.completer)
        # ====== 💡 [核心修正]：为独立的下拉弹窗穿上暗黑外套，并彻底拔除虚线框 ======
        # 获取 Completer 内部自带的 QListView 弹窗对象
        popup = self.completer.popup()
        # 1. 强行应用我们绝美的暗黑 QSS 样式表
        popup.setStyleSheet(qss.DARK_QSS)
        # 2. 复用你为 TableView 写的无焦点代理，一刀切除烦人的系统焦点虚线框！
        popup.setItemDelegate(NoFocusDelegate(popup))
        # 4. 监听输入框文本变化
        self.search_input.textChanged.connect(self.on_search_text_changed)
        # 5. 监听 Completer 的选中事件（鼠标点击 或 键盘回车）
        self.completer.activated.connect(self.on_suggest_selected)
        # ===============================================================

        # ==================== 👑 [新增] 注入高性能独立背景图层 ====================
        # 1. 实例化我们的画板，并将它交给 centralwidget 托管
        self.table_bg_widget = CoverBackgroundWidget(self.centralwidget, self.config.cache_cfg.get('widget_opacity'),
                                                     self.config.cache_cfg.get('widget_blur_radius'))
        # 2. 赋予“幽灵属性”：让鼠标点击完全穿透它，不影响任何操作
        self.table_bg_widget.setAttribute(Qt.WA_TransparentForMouseEvents)
        # 3. 将其沉降到 Z 轴的最底层 (垫底)
        self.table_bg_widget.lower()

        # 4. 剥离 TableView 本身的背景，使其变透明，从而漏出我们下面绝美的画板
        self.tableView.setStyleSheet("""
                    QTableView { background-color: transparent; }
                    QTableView::viewport { background-color: transparent; }
                """)

        # 5. 让主窗口监听 TableView 的事件，以便于尺寸同步
        self.tableView.installEventFilter(self)
        # ==================== 👑 [新增] 状态同步守护引擎 ====================
        self.pending_restore_position = 0  # 内存中的待恢复位置标记

        self.sync_state_timer = QTimer(self)
        self.sync_state_timer.timeout.connect(self._sync_playback_state_to_disk)
        self.sync_state_timer.start(5000)  # 每隔 5 秒在后台静默检查并备份一次
        # ===============================================================

    def init_player_ui(self):
        self.progressBar_download.hide()
        # ==================== 👑 [新增] 替换左下角封面引擎 ====================
        # 1. 绝不破坏原有的 UI 布局树！直接让 Qt Designer 生成的 label_cover 充当容器
        self.original_cover_container = self.label_cover
        self.original_cover_container.clear()  # 清空原本的静态图
        # 2. 给这个容器加上贴边的极简布局
        cover_layout = QVBoxLayout(self.original_cover_container)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        # 3. 实例化动态引擎，并用 self.label_cover 重新指向它 (这样后续代码都不用改)
        self.label_cover = RotatingCoverWidget(self.original_cover_container,
                                               self.config.cache_cfg.get('rotate_album', False))
        cover_layout.addWidget(self.label_cover)
        # ====================================================================
        # ====== 💡 核心修复：重新分配底部播放器的水平宽度比例 ======
        # horizontalLayout_4 是底部那一整条的总体布局
        self.horizontalLayout_4.setStretch(0, 0)  # 索引0(左侧封面)：不需要拉伸，保持 65x65
        self.horizontalLayout_4.setStretch(1, 1)  # 索引1(中间信息与进度条)：分配权重为 1，强行霸占所有剩余空间！
        self.horizontalLayout_4.setStretch(2, 0)  # 索引2(右侧控制区)：权重为 0，被中间区域往右侧死死挤压，保持紧凑

        # 使用 Windows 矢量图标代码
        self.btn_prev.setText("\uE892")  # 完美对齐的 |◀
        self.btn_play.setText("\uE768")  # 完美对齐的 ▶
        self.btn_next.setText("\uE893")  # 完美对齐的 ▶|

        # ==================== 🛠️ 核心修改：释放歌词物理边界 ====================
        self.horizontalLayout_title.removeWidget(self.label_song_info)
        self.horizontalLayout_title.removeWidget(self.label_lyric)
        self.horizontalLayout_title.removeWidget(self.label_time)

        self.label_lyric.deleteLater()
        self.label_lyric = ScrollingLyricWidget()

        self.horizontalLayout_title.addWidget(self.label_song_info)
        # 👑 核心修复：删除 addStretch！直接给 label_lyric 分配权重 1
        # 这样它会霸占中间所有的空余区域，宽度瞬间来到 500px 以上！再也不会提前换行！
        self.horizontalLayout_title.addWidget(self.label_lyric, 1)
        self.horizontalLayout_title.addWidget(self.label_time)

        self.label_song_info.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # ===================================================================

        # 保持你后续的事件监听和信号绑定不变
        self.slider_progress.installEventFilter(self)
        self.player.positionChanged.connect(self.update_player_position)
        self.player.durationChanged.connect(self.update_player_duration)
        self.player.stateChanged.connect(self.update_play_btn_state)
        QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_play_pause)

        self.player.mediaStatusChanged.connect(self.handle_media_status)

        self.btn_play.clicked.connect(self.toggle_play_pause)
        self.btn_prev.clicked.connect(self.play_prev_song)
        self.btn_prev.setToolTip("上一首")
        self.btn_play.setToolTip("播放/暂停")
        self.btn_next.setToolTip("下一首")
        self.btn_next.clicked.connect(self.play_next_song)

        # ====== 💡 新增：音量条逻辑绑定 ======
        # 1. 默认设置音量为 50 (与UI中设定的一致)
        self.player.setVolume(50)
        # 2. 绑定滑块的值变化信号，直接传给播放器的 setVolume 方法
        self.slider_volume.valueChanged.connect(self.player.setVolume)
        # 3. 为音量条安装事件过滤器，支持鼠标“点击直接跳到该音量”
        self.slider_volume.installEventFilter(self)

    def init_config(self):
        # 1. 定义全量配置的缺省参数模板 (配置字典树)
        default_config = {
            'default_keyworlds': 'Tokyo Soft Dance',  # 无last_playlist时默认搜索的
            'default_position': 16300,
            'server': 'http://127.0.0.1:3000',
            'auto_play': True,
            'volume': 30,
            'last_playlist': [],
            'last_play_index': -1,
            'last_position': 0,
            'last_keyword': "",
            'last_search_mode': "搜索歌曲",
            'rotate_album': False,
            'is_daily_recommend': False,  # 👑 [新增] 每日推荐模式标记，用于断点恢复
            # 显示界面
            'widget_opacity': 0.15,
            'widget_blur_radius': 5,
            # 👑 [新增] 窗口宽高记忆标记 (0代表初次运行)
            'window_width': 0,
            'window_height': 0
        }

        # 补全缺省配置
        for key, default_value in default_config.items():
            if key not in self.config.cache_cfg:
                self.config.cache_cfg[key] = default_value
                self.config.set_dirty()  # 发现缺失，打上脏标记

        # 如果有新配置生成，执行一次全量刷盘
        if self.config.get_dirty():
            self.config.flush_config_to_disk()

        self.server = self.config.cache_cfg.get('server')
        print(f"✅ 内存配置字典装载完毕，服务器节点: {self.server}")

        cfg = self.config.cache_cfg
        if not cfg.get("last_playlist", []):
            # 仅读取歌单判断是否为空
            self.current_keywords = default_config["default_keyworlds"]
            self.config.set_config('last_position', default_config['default_position'])
            self.config.set_config('last_play_index', 0)
            self.config.flush_config_to_disk()
            # 提前搜索加载默认歌单
            self.execute_network_search(self.restore_last_state)
        else:
            # 延时触发状态恢复 (防止 UI 还没画完就卡住)
            QTimer.singleShot(500, self.restore_last_state)

    def restore_last_state(self):
        cfg = self.config.update()
        # 👑 [新增] 提取状态标识
        self.is_daily_recommend_mode = cfg.get("is_daily_recommend", False)
        last_playlist = cfg.get("last_playlist", [])

        last_play_index = cfg.get("last_play_index", -1)
        auto_play = cfg.get("auto_play", False)

        # 👑 [核心新增]：恢复历史音量，拒绝开机突脸爆炸音量
        saved_vol = cfg.get("volume", 50)
        self.slider_volume.setValue(saved_vol)
        self.player.setVolume(saved_vol)

        # 👑 [核心新增]：提取记忆的播放进度，挂载到全局，等待装载就绪
        self.pending_restore_position = cfg.get("last_position", 0)
        print(f'pending_restore_position {self.pending_restore_position}')

        # 1. 恢复列表数据
        if last_playlist:
            print("[DEBUG LOG] 🌀 发现历史播放列表，正在恢复...")
            self.current_raw_playlist = last_playlist.copy()
            # 👑 [核心修复 1]：更新纪元并清退历史封面线程，防止串台
            self.search_epoch += 1
            for worker in self.cover_workers:
                worker.cancel()
            self.cover_workers.clear()

            # 👑 [核心修复 2]：完美还原当时的搜索上下文，保证滚动分页正常工作！
            self.current_keywords = cfg.get("last_keyword", "")
            self.search_combo.setCurrentText(cfg.get("last_search_mode", "搜索歌曲"))
            self.search_input.setText(self.current_keywords)

            self.table_model.removeRows(0, self.table_model.rowCount())
            self._render_songs_to_table(last_playlist)
            # 👑 [核心修复] 如果当时关机前是在听每日推荐，这里恢复时也要禁止它往下滚动！
            self.has_more = not self.is_daily_recommend_mode
            self.has_more = True  # 假定可以继续滚动

            # 如果没有记录播放位置，但是此时有列表就从开始播放
            if last_play_index == -1:
                last_play_index = 0
                self.pending_restore_position = 0
            # 2. 如果存在上一次的播放索引，先在 UI 上选中它
            if 0 <= last_play_index < self.table_model.rowCount():
                self.current_play_index = last_play_index
                # 👑 [恢复高亮]：告诉代理，这就是那首要继续播放的歌！
                if hasattr(self, 'table_delegate'):
                    self.table_delegate.set_playing_row(last_play_index)
                self.tableView.selectRow(last_play_index)
                # 获取该行第 0 列的模型索引
                scroll_index = self.table_model.index(last_play_index, 0)
                # 强行命令 TableView 滚动到该索引，并且策略设为：尽可能使其处于视图正中间！
                self.tableView.scrollTo(scroll_index, QAbstractItemView.PositionAtCenter)
                # 3. 如果开启了自动播放，触发播放！
                if auto_play:
                    print(f"[DEBUG LOG] ▶️ 自动播放已开启，准备播放第 {last_play_index} 首歌...")
                    # 延时 500ms 触发，给封面下载子线程一点缓冲时间，体验更佳
                    QTimer.singleShot(500, lambda: self.trigger_play(last_play_index))

    def eventFilter(self, source, event):
        if source == self.tableView and event.type() in (QEvent.Resize, QEvent.Move):
            self.table_bg_widget.setGeometry(self.tableView.geometry())

        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if source in (self.slider_progress, self.slider_volume):
                val = source.minimum() + (
                        source.maximum() - source.minimum()
                ) * event.x() / source.width()
                source.setValue(int(val))

                if source == self.slider_progress:
                    # 👑 [保险丝]：如果根本没有音频，或者总时长为空，绝对禁止向下游调用 setPosition！
                    if self.player.mediaStatus() not in (QMediaPlayer.NoMedia, QMediaPlayer.InvalidMedia) \
                            and self.player.duration() > 0:
                        self.player.setPosition(int(val))

                return False
        return super().eventFilter(source, event)

    def _track_thread(self, thread_obj):
        self._thread_pool_refs.add(thread_obj)
        thread_obj.finished.connect(lambda: self._thread_pool_refs.discard(thread_obj))
        thread_obj.finished.connect(thread_obj.deleteLater)

    def on_search_text_changed(self, text):
        """当输入框内容改变时触发"""
        # 只有在“搜索歌曲”模式下才开启搜索建议
        if self.search_combo.currentText() != "搜索歌曲":
            return

        keyword = text.strip()
        if not keyword:
            self.suggest_model.setStringList([])  # 文本为空时清空下拉框
            return

        # 每次打字都重新启动定时器。只有当用户停下键盘 400 毫秒后，才会触发 timeout
        self.suggest_timer.start(400)

    def fetch_search_suggestions(self):
        keyword = self.search_input.text().strip()
        if not keyword: return

        # 👑 发起请求前，建议纪元+1
        self.suggest_epoch += 1
        current_epoch = self.suggest_epoch

        worker = NetworkWorker(self.session, self.server)
        # 用 lambda 闭包把纪元封印传过去
        worker.search_suggest_finished.connect(
            lambda suggest_list, epoch=current_epoch: self.update_search_suggestions(suggest_list, epoch)
        )
        worker.prepare_search_suggest(keyword)
        self._track_thread(worker)
        worker.start()

    def update_search_suggestions(self, suggest_list, epoch):
        """接收网络线程返回的数据并刷新下拉框"""
        # 👑 [幽灵拦截]：如果返回的纪元不是最新的，直接丢弃，绝不覆盖当前正确的 UI！
        if epoch != self.suggest_epoch:
            return
        # 防脏数据校验：如果网络请求返回时，用户已经把输入框清空了，则不显示
        if not self.search_input.text().strip():
            self.suggest_model.setStringList([])
            return

        # 更新下拉列表的数据模型
        self.suggest_model.setStringList(suggest_list)

        # 核心逻辑：如果当前还有焦点，且有数据，主动展开弹窗
        if suggest_list and self.search_input.hasFocus():
            self.completer.complete()

    def on_suggest_selected(self, text):
        """当用户在下拉列表中用鼠标点击或按回车选中某一项时触发"""
        # QCompleter 会自动把选中的 text 填入 search_input 中，并自动收起弹窗！
        # 我们只需要直接触发真正的搜索方法即可：
        print(f"[DEBUG LOG] 用户选中了搜索建议: {text}")
        self.perform_new_search()

    # ================= 数据检索模块 =================

    def perform_new_search(self):
        # ================== 💡 [核心新增]：拦截幽灵弹窗 ==================
        # 1. 只要发起了正式搜索，立刻掐死搜索建议的防抖定时器
        if hasattr(self, 'suggest_timer') and self.suggest_timer.isActive():
            self.suggest_timer.stop()
        # 2. 强行隐藏可能还没来得及收回的弹窗
        if hasattr(self, 'completer') and self.completer.popup().isVisible():
            self.completer.popup().hide()
        # ==============================================================
        self.current_keywords = self.search_input.text().strip()
        if not self.current_keywords:
            return
        # 👑 [新增] 退出每日推荐模式
        self.is_daily_recommend_mode = False
        self.config.set_config("is_daily_recommend", False)
        # ====== 💡 [核心防御]：纪元更新与线程清退 ======
        self.search_epoch += 1  # 检索纪元+1

        # 👑 [绝命 Bug 修复]：播放纪元也必须强制+1！
        # 瞬间废弃掉后台可能还在加载的上一批歌曲，防止它们渲染时读取到空表格闪退！
        if not hasattr(self, 'play_epoch'):
            self.play_epoch = 0
        self.play_epoch += 1

        for worker in self.cover_workers:
            worker.cancel()
        self.cover_workers.clear()
        # ===============================================
        # 👑 [核心修复]：全新搜索时，彻底清空累加器，并重置硬盘里的播放索引
        self.current_raw_playlist.clear()
        self.config.set_config("last_play_index", -1)
        self.current_play_index = -1  # 保持同步
        # 👑 [抹除高亮]：清空高亮行号，并强制刷新
        if hasattr(self, 'table_delegate'):
            self.table_delegate.set_playing_row(-1)
            self.tableView.viewport().update()
        self.table_model.removeRows(0, self.table_model.rowCount())
        self.current_offset = 0
        self.has_more = True
        self.execute_network_search()

    def execute_network_search(self, post_callback=None):
        if self.is_loading or not self.has_more: return
        self.is_loading = True
        self.status_lbl.setText("正在连接云端提取数据...")
        worker = NetworkWorker(self.session, self.server)
        worker.error_occurred.connect(self.handle_network_error)
        worker.search_finished.connect(self.parse_search_result)

        def _cb():
            pass

        worker.post_callback.connect(_cb if post_callback is None else post_callback)

        # ====== 💡 [核心修改]：增加“搜索歌手ID”的分发，完美衔接你写好的滚动分页 ======
        current_mode = self.search_combo.currentText()
        if current_mode == "搜索歌曲":
            worker.prepare_search_general(self.current_keywords, 1, self.current_offset)
        elif current_mode == "搜索歌单":
            worker.prepare_search_general(self.current_keywords, 1000, self.current_offset)
        elif current_mode == "搜索歌手ID":
            worker.prepare_search_artist(self.current_keywords, self.current_offset)
        else:
            worker.prepare_search_playlist(self.current_keywords, self.current_offset)
        # ====================================================================
        self._track_thread(worker)
        worker.start()

    # 5. [修改] parse_search_result()，处理接口数据并保护 ComboBox
    def parse_search_result(self, response_data, mode_str):
        self.is_loading = False
        if response_data.get("code") != 200:
            return

        # ====== 👑 核心融合：兼容今日与历史日推结构的细微差异 ======
        if mode_str in ("daily_recommend", "history_recommend"):
            data_node = response_data.get("data", {})
            # 历史记录里通常叫 songs，今日推荐通常叫 dailySongs，双保险获取
            songs_list = data_node.get("dailySongs", []) or data_node.get("songs", [])

            if not songs_list and isinstance(data_node, list):
                songs_list = data_node

        elif mode_str == "song":
            songs_list = response_data.get("result", {}).get("songs", [])
        # elif mode_str == "song_list_search":
        #     self.song_list_window = SonglistsWindow(response_data.get("result", {}).get("songs", []))
        #     self.song_list_window.show()
        #     return
        else:
            songs_list = response_data.get("songs", [])

        if not songs_list:
            return

        # 只要是推荐类，统统阻断分页
        if mode_str not in ("daily_recommend", "history_recommend"):
            self.has_more = len(songs_list) > 0
        else:
            self.has_more = False

        self._render_songs_to_table(songs_list)
        self.current_raw_playlist.extend(songs_list)

        # 将歌曲存入内存配置
        self.config.set_config("last_playlist", self.current_raw_playlist)

        if mode_str not in ("daily_recommend", "history_recommend"):
            self.config.set_config("last_keyword", self.current_keywords)
            self.config.set_config("last_search_mode", self.search_combo.currentText())

        self.config.flush_config_to_disk()

    # ==================== 👑 [新增] 独立的表格渲染引擎 ====================
    def _render_songs_to_table(self, songs_list):
        """负责将标准 JSON 歌单数组渲染进 QTableView"""
        cover_download_tasks = []
        self.tableView.setUpdatesEnabled(False)
        for song in songs_list:
            song_id = str(song.get("id", ""))
            song_name = song.get("name", "未知")
            artists = song.get("ar", song.get("artists", []))
            artist_name = "/".join([art.get("name", "") for art in artists]) if artists else "未知"

            album = song.get("al", song.get("album", {}))
            album_name = album.get("name", "") if album else "单曲/未知专辑"

            mvid = song.get("mv", 0)
            album_id = str(album.get("id", "0"))
            raw_pic_url = album.get("picUrl", "")

            item_song = QStandardItem(song_name)
            item_album = QStandardItem(album_name)
            item_id = QStandardItem(song_id)
            item_mvid = QStandardItem(str(mvid) if mvid else "-")

            item_artist = QStandardItem(artist_name)
            # ==================== 👑 补全：版权及播放权限深度判定 ====================
            privilege = song.get("privilege", {})
            is_no_copyright = False

            if privilege:
                if privilege.get("st", 0) < 0 or privilege.get("pl", 0) == 0:
                    is_no_copyright = True
            elif song.get("st", 0) < 0:
                is_no_copyright = True

            if is_no_copyright:
                from PyQt5.QtGui import QBrush, QColor  # 确保导入
                red_brush = QBrush(QColor("#ff4d4f"))
                for item in (item_song, item_album, item_artist, item_id, item_mvid):
                    item.setForeground(red_brush)
                    item.setToolTip("该歌曲暂无版权或无VIP权限，可能无法正常播放或下载")
            # =========================================================================

            current_row = self.table_model.rowCount()

            # 封面下载与 L1 缓存判定
            if raw_pic_url:
                cache_key = album_id if album_id != "0" else f"single_{song_id}"
                if cache_key in self.icon_ram_cache:
                    item_album.setIcon(self.icon_ram_cache[cache_key])
                else:
                    cover_download_tasks.append({
                        'row': current_row,
                        'url': f"{raw_pic_url}?param=100y100",
                        'album_id': cache_key
                    })

            self.table_model.appendRow([item_song, item_album, item_artist, item_id, item_mvid])
        self.tableView.setUpdatesEnabled(True)
        self.current_offset += len(songs_list)
        self.status_lbl.setText(f"列表现有 {self.table_model.rowCount()} 首歌曲。")

        if cover_download_tasks:
            worker = AlbumCoverWorker(cover_download_tasks, self.cover_cache_dir, self.search_epoch)
            worker.cover_ready_signal.connect(self.on_single_cover_ready)
            self._track_thread(worker)
            self.cover_workers.append(worker)
            worker.finished.connect(lambda w=worker: self.cover_workers.remove(w) if w in self.cover_workers else None)
            worker.start()

    # ====== 💡 [修复]：极速渲染与 L1 缓存写入 ======
    def on_single_cover_ready(self, row_index, qimage, album_id, epoch):
        """主线程只负责将现成的 QImage 转换为 QIcon 并贴在表格上，耗时微秒级"""

        if epoch != self.search_epoch:
            return

        if row_index < self.table_model.rowCount():
            item = self.table_model.item(row_index, 1)  # 1 代表专辑名列

            if item and not qimage.isNull():
                # QPixmap.fromImage 底层直接是一次内存到显存的拷贝，速度极快
                pixmap = QPixmap.fromImage(qimage)
                icon = QIcon(pixmap)
                # 写入 L1 内存缓存，下次搜到这首专辑的歌，连子线程都不用进了！
                self.icon_ram_cache[album_id] = icon
                # 渲染到界面
                item.setIcon(icon)

    def handle_scroll_pagination(self, value):
        """通用无限触底滚动分页（同时支持歌曲和歌单模式）"""
        # 👑 [新增] 如果处于每日推荐模式，直接物理阻断滚动请求！
        if getattr(self, 'is_daily_recommend_mode', False):
            return
        scrollbar = self.tableView.verticalScrollBar()
        # 当滚动条滑动到最底部 (value == maximum)，且列表非空 (value > 0)
        if value == scrollbar.maximum() and value > 0:
            # 必须满足：云端还有更多数据，且当前没有正在加载（防抖防重复请求）
            if self.has_more and not self.is_loading:
                print(f"[DEBUG LOG] 🛑 检测到列表触底，准备加载下一页... 当前 offset: {self.current_offset}")
                self.execute_network_search()

    def handle_network_error(self, err_msg):
        self.is_loading = False
        QMessageBox.critical(self, "错误", err_msg)

    # ================= 播放器核心逻辑 =================

    def play_selected_song_from_table(self, index):
        self.trigger_play(index.row())

    def play_prev_song(self):
        row_count = self.table_model.rowCount()
        if row_count > 0:
            # 👑 如果还没开始播放，按 0 计算，这样减 1 才会精准跳到最后一首歌
            idx = 0 if self.current_play_index == -1 else self.current_play_index
            target_index = (idx - 1 + row_count) % row_count
            self.trigger_play(target_index)

    def play_next_song(self):
        row_count = self.table_model.rowCount()
        if row_count > 0:
            target_index = (self.current_play_index + 1) % row_count
            self.trigger_play(target_index)

    def toggle_play_pause(self):
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        elif self.player.state() == QMediaPlayer.PausedState:
            self.player.play()
        else:
            indexes = self.tableView.selectionModel().selectedRows()
            if indexes:
                self.trigger_play(indexes[0].row())

    def trigger_play(self, target_index):
        if target_index < 0 or target_index >= self.table_model.rowCount():
            return

        # 👑 [核心机制]：每次点击产生新纪元，废弃“布尔锁”，完美支持狂点切歌！
        if not hasattr(self, 'play_epoch'):
            self.play_epoch = 0
        self.play_epoch += 1
        current_epoch = self.play_epoch

        # 获取即将播放的歌曲名，用于在状态栏给个文字反馈，但绝不改变底部大 UI
        s_name = self.table_model.item(target_index, 0).text()
        s_id = self.table_model.item(target_index, 3).text()

        # [反馈] 只在状态栏提示，当前正在播放的歌词、封面依然正常运行！
        self.status_lbl.setText(f"⏳ 正在后台缓冲: {s_name} ...")

        worker = NetworkWorker(self.session, self.server)

        # 👑 [核心魔法] 利用 lambda 闭包，把 current_epoch 和 target_index 封印带走
        worker.play_info_finished.connect(
            lambda data, epoch=current_epoch, tidx=target_index: self.start_media_play(data, epoch, tidx)
        )
        worker.error_occurred.connect(
            lambda e, epoch=current_epoch: self.handle_play_error(e, epoch)
        )
        worker.task_progress_update.connect(lambda msg: self.status_lbl.setText(msg))

        worker.prepare_play_song(s_id, self.cache_dir, self.audio_cache_dir)
        self._track_thread(worker)
        worker.start()

    def start_media_play(self, data, epoch, target_index):
        # 👑 [幽灵拦截] 如果缓冲这首歌时，用户早就不耐烦切到另一首歌了，这批旧数据直接扔进垃圾桶！
        if epoch != getattr(self, 'play_epoch', -1):
            print(f"[DEBUG LOG] 🗑️ 拦截到过期媒体流回调(epoch={epoch})，已静默抛弃。")
            return

        if not data["url"]:
            # 因为之前没切走 UI，失败了只用弹个窗即可，上一首歌完全不受影响！
            QMessageBox.warning(self, "播放失败", "❌ 无可用播放源(可能因版权限制)")
            self.status_lbl.setText("缓冲失败：无可用播放源。")
            return

        # ==================== 👑 [数据就绪，开始瞬间切换 UI 与配置] ====================

        # 1. 处理进度重置
        if target_index != self.config.cache_cfg.get("last_play_index", -1):
            self.pending_restore_position = 0
            self.config.set_config("last_position", 0)

        # 2. 只有此时，才真正承认这首歌“正在播放”
        self.current_play_index = target_index
        self.config.set_config("last_play_index", self.current_play_index)
        # =======================================================
        # 👑 [绝佳 UX：播放底色渲染]
        # 通知画笔当前正在播放哪一行，并强制表格视口瞬间刷新！
        if hasattr(self, 'table_delegate'):
            self.table_delegate.set_playing_row(target_index)
            self.tableView.viewport().update()
        # =======================================================
        # 3. 瞬间切换底部文字
        s_name = self.table_model.item(target_index, 0).text()
        a_name = self.table_model.item(target_index, 2).text()

        s_name_short = self.safe_truncate(s_name, max_len=14)
        a_name_short = self.safe_truncate(a_name, max_len=10)
        self.label_song_info.setText(f"{s_name_short} - {a_name_short}")
        self.label_song_info.setToolTip(f"{s_name} - {a_name}")

        # 4. 瞬间切换封面 (主图与底层画板)
        if data["cover_path"] and os.path.exists(data["cover_path"]):
            pixmap = QPixmap(data["cover_path"])
            self.label_cover.set_pixmap(pixmap)
            self.table_bg_widget.set_pixmap(pixmap)
        else:
            album_item = self.table_model.item(target_index, 1)
            if album_item and not album_item.icon().isNull():
                thumb_pixmap = album_item.icon().pixmap(150, 150)
                self.label_cover.set_pixmap(thumb_pixmap)
                self.table_bg_widget.set_pixmap(thumb_pixmap)
            else:
                self.label_cover.set_pixmap(QPixmap())
                self.table_bg_widget.set_pixmap(QPixmap())

        # 5. 解析并瞬间装载歌词
        self.parse_lrc(data["lyrics"])

        # 6. 打断旧音频，挂载新音频并轰出声音
        media_url = data["url"]
        if media_url.startswith("http://") or media_url.startswith("https://"):
            qurl = QUrl(media_url)
        else:
            qurl = QUrl.fromLocalFile(media_url)

        self.player.setMedia(QMediaContent(qurl))
        self.player.play()

    # 💡 别忘了顺手重写报错处理，同样带上纪元校验
    def handle_play_error(self, e, epoch):
        if epoch != getattr(self, 'play_epoch', -1):
            return
        # 不更改底部 UI，只在状态栏安静地提示失败
        self.status_lbl.setText(f"❌ 加载失败: {self.safe_truncate(str(e))}")
        print(f"[DEBUG LOG] ❌ 播放缓冲失败: {e}")

    def parse_lrc(self, lrc_text):
        self.parsed_lyrics.clear()
        self.sorted_lyric_times.clear()
        if not lrc_text:
            self.label_lyric.setText("纯音乐 / 暂无歌词")  # 💡 修改这里
            return

        pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)')
        for line in lrc_text.split('\n'):
            match = pattern.match(line)
            if match:
                m, s, ms, text = match.groups()
                ms_val = int(ms) if len(ms) == 3 else int(ms) * 10
                time_ms = int(m) * 60000 + int(s) * 1000 + ms_val
                if text.strip():
                    self.parsed_lyrics[time_ms] = text.strip()

        self.sorted_lyric_times = sorted(self.parsed_lyrics.keys())

        # ====== 👑 将解析好的数据整套喂给动画歌词引擎 ======
        self.label_lyric.set_lyrics(self.parsed_lyrics, self.sorted_lyric_times)

    def update_player_position(self, position):
        """刷新进度条与同步动画歌词引擎"""
        # 👑 防御黑科技：如果底层总时长还没拿到，拒绝任何进度条的更新与重绘，防止滑块乱跳！
        if self.player.duration() <= 0:
            return
        self.slider_progress.blockSignals(True)
        self.slider_progress.setValue(position)
        self.slider_progress.blockSignals(False)
        self.update_time_label()

        if self.sorted_lyric_times:
            # 二分查找当前播放时间的歌词索引
            idx = bisect.bisect_right(self.sorted_lyric_times, position) - 1
            # 💡 直接将索引喂给我们的独立动画引擎，它会自动处理滚动与变色！
            self.label_lyric.set_current_index(idx)

    def update_player_duration(self, duration):
        if duration > 0:
            self.slider_progress.setRange(0, duration)

            # ==================== 👑 核心修复：时机后置 ====================
            # 只有当获取到真实的媒体总时长后，才允许跳跃进度！
            # 此时 slider_progress 的最大值 (Range) 已经被完全撑开，
            # 再执行 setPosition，绝对不会出现“先满后空”的视觉 Bug！
            if getattr(self, 'pending_restore_position', 0) > 0:
                print(f"[DEBUG LOG] ⏱️ 媒体时长就绪，触发断点续播，跳转至: {self.pending_restore_position} ms")
                self.player.setPosition(self.pending_restore_position)

                # 消耗掉进度标记，防止播放过程中乱跳
                self.pending_restore_position = 0
            # ===============================================================

        self.update_time_label()

    def update_time_label(self):
        pos = self.player.position() // 1000
        dur = self.player.duration() // 1000
        self.label_time.setText(f"{pos // 60:02d}:{pos % 60:02d} / {dur // 60:02d}:{dur % 60:02d}")

    def update_play_btn_state(self, state):
        self.label_cover.changed_status(state == QMediaPlayer.PlayingState)
        if state == QMediaPlayer.PlayingState:
            print("播放")
            self.btn_play.setText("\uE769")  # 完美对齐的 || (暂停)
        else:
            print("暂停")
            self.btn_play.setText("\uE768")  # ▶ (播放)

    # ================= 右键菜单与下载 =================

    def handle_media_status(self, status):
        """监听播放器状态：处理自动切歌 与 异步媒体装载劫持"""
        if status == QMediaPlayer.EndOfMedia:
            print("[DEBUG LOG] 🎵 当前歌曲播放完毕，准备自动播放下一首...")
            self.play_next_song()

    def show_context_menu(self, pos):
        indexes = self.tableView.selectionModel().selectedRows()
        if not indexes:
            return
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_TranslucentBackground)  # 开启透明，允许圆角生效
        play_action = QAction("立即播放", self)
        play_action.triggered.connect(lambda: self.play_selected_song_from_table(indexes[0]))
        menu.addAction(play_action)

        # ==================== 🎬 [新增] 播放 MV 功能 ====================
        target_row = indexes[0].row()
        # 👑 [绝佳优化]：直接从内存中的原始 JSON 列表拿数据，完全解耦 UI！
        song_data = self.current_raw_playlist[target_row]
        mvid = song_data.get("mv", 0)
        artist_list_data = song_data.get("ar", song_data.get("artists", []))

        if mvid:  # 如果 mvid 存在且不为 0
            mv_menu = QMenu("🎬 播放 MV", self)
            try:
                # 【按需调用】仅在右键且有 MV 时，才同步请求一次详情接口
                res = self.session.get(f"{self.server}/mv/detail", params={"mvid": mvid}, timeout=3).json()
                if res.get("code") == 200:
                    brs = res.get("data", {}).get("brs", [])
                    for br_info in brs:
                        br = br_info.get("br")
                        action = QAction(f"{br}P 清晰度", self)
                        # 注意 lambda 的闭包陷阱，必须用默认参数 m=mvid, b=br 锁住当前循环的值
                        action.triggered.connect(lambda checked, m=mvid, b=br: self.play_mv(m, b))
                        mv_menu.addAction(action)

                    if not brs:
                        no_mv_action = QAction("暂无清晰度信息", self)
                        no_mv_action.setEnabled(False)
                        mv_menu.addAction(no_mv_action)
            except Exception as e:
                err_action = QAction("获取信息失败", self)
                err_action.setEnabled(False)
                mv_menu.addAction(err_action)
            menu.addMenu(mv_menu)
        # ==============================================================

        # ==================== 🎤 [新增] 查看歌手单曲 功能 ====================
        if artist_list_data:
            artist_menu = QMenu("查看歌手单曲", self)
            for art in artist_list_data:
                art_id = art["id"]
                art_name = art["name"]
                if art_id:
                    action = QAction(f"{art_name}", self)
                    action.triggered.connect(lambda checked, aid=art_id: self.search_artist_songs(aid))
                    artist_menu.addAction(action)

            menu.addMenu(artist_menu)
        # ==================================================================
        # ==================== 📁 👑 [新增] 添加到歌单 功能 ====================
        # 前提条件：用户已登录，且后台已经成功拉取到了自己创建的歌单
        if hasattr(self, 'my_created_playlists') and self.my_created_playlists:
            menu.addSeparator()  # 加一条漂亮的分割线
            add_playlist_menu = QMenu("添加到歌单 ➕", self)

            for pl in self.my_created_playlists:
                # 截断太长的歌单名，防止菜单过宽
                display_name = self.safe_truncate(pl['name'], 20)
                action = QAction(display_name, self)
                # 使用 lambda 闭包锁定当前歌单 ID
                action.triggered.connect(lambda checked, pid=pl['id']: self.execute_add_to_playlist(pid))

                # 如果本地有缓存封面，顺手给菜单加上小图标体验更好
                if pl["cover_path"] and os.path.exists(pl["cover_path"]):
                    action.setIcon(QIcon(pl["cover_path"]))

                add_playlist_menu.addAction(action)

            menu.addMenu(add_playlist_menu)
        # =================================================================
        menu.addSeparator()

        dl_single = QAction("下载选定单曲", self)
        dl_single.triggered.connect(self.trigger_download_single)
        dl_all = QAction("下载当前列表全部", self)
        dl_all.triggered.connect(self.trigger_download_all)
        menu.addAction(dl_single)
        menu.addAction(dl_all)
        menu.exec_(self.tableView.viewport().mapToGlobal(pos))

    # 👑 [新增] 执行添加到歌单逻辑
    def execute_add_to_playlist(self, playlist_id):
        indexes = self.tableView.selectionModel().selectedRows()
        if not indexes: return

        # 1. 批量收集选中的所有歌曲ID (支持用户按住 Shift/Ctrl 多选批量添加)
        song_ids = []
        for idx in indexes:
            row = idx.row()
            s_id = self.table_model.item(row, 3).text()
            song_ids.append(s_id)

        tracks_str = ",".join(song_ids)  # 逗号拼接

        # 2. 发起网络请求
        self.status_lbl.setText(f"正在将 {len(song_ids)} 首歌曲添加到歌单...")
        worker = NetworkWorker(self.session, self.server)
        worker.add_playlist_finished.connect(self.on_add_playlist_finished)
        worker.prepare_add_to_playlist(playlist_id, tracks_str)
        self._track_thread(worker)
        worker.start()

    # 👑 [新增] 接收添加结果的槽函数
    def on_add_playlist_finished(self, success, msg):
        if success:
            self.status_lbl.setText(msg)
            # 你也可以用一个小气泡或者提示，不打扰用户
            # QMessageBox.information(self, "成功", msg)
        else:
            self.status_lbl.setText("添加取消或失败。")
            QMessageBox.warning(self, "提示", msg)

    def search_artist_songs(self, artist_id):
        """接收右键菜单传回的歌手ID，自动在主窗口发起搜索"""
        print(f"[DEBUG LOG] 接收到歌手 ID: {artist_id}，准备检索全部歌曲...")
        # 2. 自动将下拉框切换为“搜索歌手ID”
        self.search_combo.setCurrentText("搜索歌手ID")
        # 3. 将 歌手ID 填入输入框（因为 execute_network_search 会读取 current_keywords 作为参数）
        self.search_input.setText(str(artist_id))
        # 4. 自动模拟点击“搜索”按钮发起网络请求，这会重置表格和分页 offset
        self.perform_new_search()

    def play_mv(self, mvid, br):
        """请求 MV 链接并通过 m3u 播放列表强制调用本地播放器"""
        self.status_lbl.setText(f"正在获取 MV(ID:{mvid}) {br}P 播放链接...")
        try:
            res = self.session.get(f"{self.server}/mv/url", params={"id": mvid, "r": br}, timeout=5).json()
            if res.get("code") == 200 and res.get("data", {}).get("url"):
                mv_url = res["data"]["url"]
                print(f"[DEBUG LOG] 成功获取 MV 链接: {mv_url}")

                # ================= 核心黑科技 =================
                # 在 cache 目录创建一个临时的 .m3u 音视频播放列表文件
                m3u_path = os.path.join(self.cache_dir, "temp_mv.m3u")
                with open(m3u_path, "w", encoding="utf-8") as f:
                    f.write("#EXTM3U\n")
                    f.write(f"#EXTINF:-1, 网易云 MV - {mvid}\n")
                    f.write(f"{mv_url}\n")  # 把 MP4 链接塞进播放列表

                # 让操作系统打开这个 .m3u 文件，系统会自动调起默认的视频播放器！
                QDesktopServices.openUrl(QUrl.fromLocalFile(m3u_path))
                # ============================================

                self.status_lbl.setText(f"已唤起系统默认播放器播放 {br}P MV。")
            else:
                QMessageBox.warning(self, "错误", "无法获取到有效 MV 链接")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"获取 MV 链接发生异常: {e}")

    def _extract_ids_and_map(self, row_indices):
        song_ids, id_map = [], {}
        for row in row_indices:
            s_name = self.table_model.item(row, 0).text()
            a_name = self.table_model.item(row, 2).text()
            s_id = self.table_model.item(row, 3).text()

            # 👑 [深度增强]：直接从内存原始 JSON 穿透获取高清封面！
            song_data = self.current_raw_playlist[row]
            cover_url = ""
            album_data = song_data.get("al", song_data.get("album", {}))
            if album_data and album_data.get("picUrl"):
                # 获取 500x500 的高清封面用于内嵌，不要用原图（几MB太大了）
                cover_url = f"{album_data.get('picUrl')}?param=500y500"

            song_ids.append(s_id)
            id_map[s_id] = {
                "name": s_name,
                "artist": a_name,
                "cover_url": cover_url,  # 新增封面字段
                "album": album_data.get("name", "未知专辑")  # 顺手把专辑名也加上
            }
        return song_ids, id_map

    def trigger_download_single(self):
        indexes = self.tableView.selectionModel().selectedRows()
        if not indexes:
            return
        song_ids, id_map = self._extract_ids_and_map([indexes[0].row()])
        self._start_url_fetch_worker(song_ids, id_map)

    def trigger_download_all(self):
        row_count = self.table_model.rowCount()
        if row_count == 0: return
        song_ids, id_map = self._extract_ids_and_map(range(row_count))
        self._start_url_fetch_worker(song_ids, id_map)

    def _start_url_fetch_worker(self, song_ids, id_map):
        self.status_lbl.setText(f"正在申请 {len(song_ids)} 首歌曲的下载链接...")
        worker = NetworkWorker(self.session, self.server)
        worker.batch_url_finished.connect(self.process_batch_download_urls)
        worker.error_occurred.connect(self.handle_network_error)
        worker.prepare_download_songs(song_ids, id_map)
        self._track_thread(worker)
        worker.start()

    def process_batch_download_urls(self, url_data_list, id_map):
        download_tasks = []
        for item in url_data_list:
            s_id = str(item.get("id"))
            url = item.get("url")
            if not url or s_id not in id_map: continue

            raw_type = item.get("type")
            file_ext = raw_type.lower() if isinstance(raw_type, str) else "mp3"
            if not file_ext and "." in url.split('?')[0]:
                file_ext = url.split('?')[0].split('.')[-1]

            meta = id_map[s_id]
            filename = f"{meta['name']} - {meta['artist']}.{file_ext}"

            # 👑 [深度增强]：构建全维度的下载任务字典
            download_tasks.append({
                "url": url,
                "filename": filename,
                "name": meta['name'],
                "artist": meta['artist'],
                "album": meta['album'],
                "cover_url": meta['cover_url'],
                "ext": file_ext
            })

        if not download_tasks:
            QMessageBox.warning(self, "警告", "选定的歌曲没有解析到合规链接。")
            return
        self.start_file_download_threads(download_tasks)

    def start_file_download_threads(self, task_list):
        dl_worker = DownloadFileWorker(self.download_dir, task_list)
        dl_worker.progress_signal.connect(lambda msg: self.status_lbl.setText(msg))
        dl_worker.finished_signal.connect(self.on_download_finished)
        dl_worker.progress_process_signal.connect(self.on_download_process)
        self._track_thread(dl_worker)
        dl_worker.start()

    def on_download_process(self, is_show, max_num, value):
        if is_show:
            self.progressBar_download.show()
            self.user_info_btn.setEnabled(False)
            self.search_btn.setEnabled(False)
            self.setting_btn.setEnabled(False)
        else:
            self.progressBar_download.hide()
            self.user_info_btn.setEnabled(True)
            self.search_btn.setEnabled(True)
            self.setting_btn.setEnabled(True)
        if max_num > 0:
            self.progressBar_download.setMaximum(max_num)
            self.progressBar_download.setValue(value)

    def on_download_finished(self, msg):
        os.startfile(self.download_dir)
        self.status_lbl.setText(msg)

    def safe_truncate(self, text, max_len=38):
        """智能截断超长文本，防止强行撑开 UI 界面"""
        if not text: return ""
        # 如果包含中英文混合，38个字符通常是安全的边界
        if len(text) > max_len:
            return text[:max_len - 2] + "..."
        return text

    def open_setting_window(self):
        self.setting_window = SettingDialog(
            self.session,
            self.cookie_file_path,
            self
        )
        self.setting_window.update_server_addr.connect(self.update_server)
        self.setting_window.setWindowModality(Qt.ApplicationModal)
        self.setting_window.show()

    def update_server(self, new):
        self.server = new

    def open_user_window(self):
        """打开用户空间独立子窗口"""
        if not self.current_user_data or not self.current_user_data.get("uid"):
            QMessageBox.warning(self, "提示", "您尚未登录，请先在设置中配置 网易云音乐Cookie。")
            return

        # 实例化子窗口 (传入会话参数实现完全解耦)
        self.user_window = UserPlaylistsDialog(
            self.session, self.cache_dir, self.current_user_data, self.server
        )
        # 连接子窗口发出的双击信号，触发本类的搜索逻辑
        self.user_window.setWindowModality(Qt.ApplicationModal)
        self.user_window.playlist_double_clicked_signal.connect(self.search_playlist_from_user)
        # 👑 [新增] 接收每日推荐信号
        self.user_window.daily_recommend_signal.connect(self.fetch_daily_recommendations)
        self.user_window.show()

    # 3. [新增] 独立的每日推荐数据获取方法
    def fetch_daily_recommendations(self, date_str=""):
        """获取每日推荐，完全独立于普通搜索状态"""
        print(f"[DEBUG LOG] 准备获取日推，日期参数: {date_str if date_str else '今日'}")
        # 👑 核心：开启每日推荐模式并打上脏数据标记准备刷盘
        self.is_daily_recommend_mode = True
        self.config.set_config("is_daily_recommend", True)

        # 纪元更新与清理 (复用安全切歌逻辑)
        self.search_epoch += 1
        if not hasattr(self, 'play_epoch'): self.play_epoch = 0
        self.play_epoch += 1
        for worker in self.cover_workers: worker.cancel()
        self.cover_workers.clear()

        # 清理旧数据与高亮状态
        self.current_raw_playlist.clear()
        self.config.set_config("last_play_index", -1)
        self.current_play_index = -1
        if hasattr(self, 'table_delegate'):
            self.table_delegate.set_playing_row(-1)
            self.tableView.viewport().update()
        self.table_model.removeRows(0, self.table_model.rowCount())

        # 👑 核心：关闭分页偏移，并彻底切断 has_more，防止触底重载
        self.current_offset = 0
        self.has_more = False
        self.is_loading = True

        worker = NetworkWorker(self.session, self.server)
        worker.error_occurred.connect(self.handle_network_error)
        worker.search_finished.connect(self.parse_search_result)

        # 👑 [核心分发]：根据有无日期参数决定调用哪个接口
        if not date_str:
            self.status_lbl.setText("正在连接云端获取今日推荐...")
            worker.prepare_get_daily_recommend()
        else:
            self.status_lbl.setText(f"正在连接云端获取 {date_str} 历史推荐...")
            worker.prepare_get_history_recommend_detail(date_str)

        self._track_thread(worker)
        worker.start()

    def search_playlist_from_user(self, playlist_id):
        """接收子窗口传回的歌单ID，自动在主窗口发起搜索"""
        print(f"[DEBUG LOG] 接收到子窗口传回的歌单 ID: {playlist_id}，准备搜索...")

        # 1. 自动将下拉框切换为“搜索歌单模式”
        self.search_combo.setCurrentText("搜索歌单ID")
        # 2. 将 ID 填入输入框
        self.search_input.setText(str(playlist_id))
        # 3. 自动模拟点击“搜索”按钮发起网络请求
        self.perform_new_search()

    # ==================== 👑 [新增] 断点续播守护引擎 ====================
    def _sync_playback_state_to_disk(self):
        """状态同步引擎：每 5 秒自动检测内存差异并刷盘"""
        if self.current_play_index >= 0 and self.player.state() in (
                QMediaPlayer.PlayingState, QMediaPlayer.PausedState):
            # 1. [音量]：利用 set_config 的内置等值判断，直接传！
            self.config.set_config("volume", self.slider_volume.value())
            # 2. [进度]：利用外层的阈值判断，阻断微小的时间变化产生的无效脏标记
            current_pos = self.player.position()
            if abs(self.config.cache_cfg.get("last_position", 0) - current_pos) > 2000:
                self.config.set_config("last_position", current_pos)
        self.config.set_config('rotate_album', self.label_cover.rotat)
        # 3. [触发]：内部自带脏标记放行机制，无脑调用即可
        self.config.flush_config_to_disk()

    def closeEvent(self, event):
        """👑 窗口关闭时的终极拦截：结算最后状态并安全刷盘"""
        self._sync_playback_state_to_disk()  # 把此时的播放位置录入内存
        self.config.flush_config_to_disk()  # 强行把可能存在的内存更改刷入硬盘！
        event.accept()

    # =================================================================

    # ==================== 👑 [新增] 动态监听窗口尺寸变化 ====================
    def resizeEvent(self, event):
        # 1. 必须先调用父类方法，保证底层正常重绘
        super().resizeEvent(event)
        # 3. 将最新宽高写入内存配置 (依靠你写好的 _config_dirty 机制，绝对不会卡顿！)
        self.config.set_config("window_width", self.width())
        self.config.set_config("window_height", self.height())
        print(self.width(), self.height())


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    window = MusicDownloaderApp()
    window.show()
    sys.exit(app.exec_())
