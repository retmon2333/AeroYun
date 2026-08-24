import os, re
import threading
import requests
from PyQt5.QtCore import QThread, pyqtSignal, QSize
import concurrent.futures
from pack.util import *
from PyQt5.QtGui import QImage
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC


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


# ================= [终极形态]：全异步 I/O 与解码线程 =================
class AlbumCoverWorker(QThread):
    # 信号传回：目标行号, 解码后的 QImage 对象, 专辑ID(用于内存缓存), 搜索纪元
    cover_ready_signal = pyqtSignal(int, QImage, str, int)

    def __init__(self, tasks, cache_dir, epoch, max_workers=5):
        super().__init__()
        self.tasks = tasks
        self.cache_dir = cache_dir
        self.epoch = epoch
        self.is_cancelled = False
        self.max_workers = max_workers

    def cancel(self):
        self.is_cancelled = True

    def _download_single(self, task):
        if self.is_cancelled:
            return

        local_path = os.path.join(self.cache_dir, f"{task['album_id']}.jpg")
        url = task['url']
        try:
            # 【L3 网络层】：如果本地 L2 没有，则发起网络请求
            if not os.path.exists(local_path):
                res = requests.get(url, timeout=5, headers=get_headers())
                if self.is_cancelled:
                    return  # 💡 阻断2：如果下载过程中用户已经切走，直接抛弃不保存！
                if res.status_code == 200:
                    with open(local_path, 'wb') as f:
                        f.write(res.content)
            if self.is_cancelled:
                return  # 💡 阻断3
            # 【L2 磁盘层】：硬盘读取 + 昂贵的 JPEG 解码，统统在子线程完成！
            if os.path.exists(local_path):
                img = QImage(local_path)  # QImage 是线程安全的纯内存数据

                if not img.isNull() and not self.is_cancelled:
                    # 将解码后的 QImage 通过信号发射给主线程
                    self.cover_ready_signal.emit(task['row'], img, task['album_id'], self.epoch)

        except Exception as e:
            print(e)

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for task in self.tasks:
                if self.is_cancelled:
                    break
                futures.append(executor.submit(self._download_single, task))

            for future in concurrent.futures.as_completed(futures):
                if self.is_cancelled:
                    for f in futures:
                        f.cancel()
                    break


class NetworkWorker(QThread):
    search_finished = pyqtSignal(dict, str)
    batch_url_finished = pyqtSignal(list, dict)
    play_info_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    post_callback=pyqtSignal()
    # ====== [新增 1]：定义登录状态返回信号 ======
    login_status_finished = pyqtSignal(dict)
    # ====== [新增 1]：定义获取歌单完毕的信号 ======
    user_playlists_finished = pyqtSignal(list)
    # ====== 💡 [新增]：用于实时回传任务进度状态的信号 ======
    task_progress_update = pyqtSignal(str)
    # ====== [新增]：搜索建议完成信号 ======
    search_suggest_finished = pyqtSignal(list)

    # 👑 [新增] 添加歌单结果信号 (是否成功, 提示信息)
    add_playlist_finished = pyqtSignal(bool, str)
    # 👑 [新增] 历史推荐日期列表信号
    history_dates_finished = pyqtSignal(list)

    def __init__(self, session, address):
        super().__init__()
        self.session = session

        self.base_url = address
        self.task_type = ""
        self.params = {}
        self.extra_data = {}
        self.song_ids = []

    # ====== [新增 2]：准备获取歌单的参数 ======
    def prepare_get_user_playlists(self, uid, cache_dir):
        self.task_type = "get_user_playlists"
        self.params = {"uid": uid}
        self.extra_data = {"cache_dir": cache_dir}

    # ====== [新增 2]：准备获取登录状态的方法 ======
    def prepare_check_login(self, cache_dir):
        self.task_type = "check_login"
        self.extra_data = {"cache_dir": cache_dir}

    # 替换原有的 prepare_search_song 为这个通用的：
    def prepare_search_general(self, keywords, type_code, offset=0):
        self.task_type = "search_general"
        # type=1 单曲, type=1000 歌单
        self.params = {"keywords": keywords, "type": type_code, "limit": 30, "offset": offset}

    def prepare_search_playlist(self, playlist_id, offset=0):
        self.task_type = "search_playlist"
        self.params = {"id": playlist_id, "limit": 50, "offset": offset}

    def prepare_download_songs(self, song_ids, id_map):
        self.task_type = "download_songs"
        self.song_ids = song_ids
        self.extra_data = id_map

    # 💡 增加 audio_cache_dir 参数
    def prepare_play_song(self, song_id, cache_dir, audio_cache_dir):
        self.task_type = "play_song"
        self.song_ids = [song_id]
        self.extra_data = {"cache_dir": cache_dir, "audio_cache_dir": audio_cache_dir}

    # ====== [新增]：准备获取歌手全部单曲的参数 ======
    def prepare_search_artist(self, artist_id, offset=0):
        self.task_type = "search_artist"
        # order: 'hot'按热度, 'time'按时间。limit: 每次50首。offset: 分页偏移量
        self.params = {"id": artist_id, "limit": 50, "offset": offset, "order": "hot"}

    # 2. 增加准备搜索建议的方法
    def prepare_search_suggest(self, keyword):
        self.task_type = "search_suggest"
        self.params = {"keyword": keyword}

    # 1. 在 NetworkWorker 类中新增准备方法
    def prepare_get_daily_recommend(self):
        """准备获取每日推荐歌曲"""
        self.task_type = "get_daily_recommend"

    # 👑 [新增] 准备添加到歌单的参数
    def prepare_add_to_playlist(self, pid, tracks):
        self.task_type = "add_to_playlist"
        self.params = {"op": "add", "pid": pid, "tracks": tracks}

    # 👑 [新增] 准备获取历史日推的日期列表
    def prepare_get_history_dates(self):
        self.task_type = "get_history_dates"

    # 👑 [新增] 准备获取特定历史日期的歌单详情
    def prepare_get_history_recommend_detail(self, date_str):
        self.task_type = "get_history_detail"
        self.params = {"date": date_str}

    def run(self):
        print(f"\n[DEBUG LOG] ========== 异步工作线程开始任务: {self.task_type} ==========")
        try:
            if self.task_type == "search_general":
                res_obj = self.session.get(f"{self.base_url}/cloudsearch", params=self.params, timeout=15)
                mode_str = ''
                type = self.params.get("type")
                if type == 1:
                    mode_str = "song"
                elif type == 1000:
                    mode_str = "song_list_search"
                self.search_finished.emit(res_obj.json(), mode_str)
                self.post_callback.emit()

            elif self.task_type == "search_playlist":
                res_obj = self.session.get(f"{self.base_url}/playlist/track/all", params=self.params, timeout=15)
                self.search_finished.emit(res_obj.json(), "playlist")

            elif self.task_type == "download_songs":
                url = f"{self.base_url}/song/url"
                collected_results = []
                ids_str = ",".join(self.song_ids)
                params = {"id": ids_str, 'br': 192000}
                res_obj = self.session.get(url, params=params, timeout=15)
                res_json = res_obj.json()
                print(res_json)
                if res_json.get("code") == 200 and "data" in res_json:
                    for data_node in res_json.get("data", []):
                        if data_node and data_node.get("url"):
                            collected_results.append(data_node)
                if collected_results:
                    self.batch_url_finished.emit(collected_results, self.extra_data)
                else:
                    self.error_occurred.emit("选定的歌曲均无法获取有效的标准音质下载链接")

            elif self.task_type == "play_song":
                song_id = self.song_ids[0]
                audio_cache_dir = self.extra_data["audio_cache_dir"]
                result_data = {"id": song_id, "url": "", "cover_path": "", "lyrics": ""}

                # ====== 💡 [极简缓存 3]：直接通过歌曲 ID 拼接唯一本地路径 ======
                cached_audio_path = os.path.join(audio_cache_dir, f"{song_id}.mp3")

                # ================= 1. 检查是否存在本地音频缓存 =================
                if os.path.exists(cached_audio_path):
                    self.task_progress_update.emit("⚡ 命中本地音频缓存，极速加载中...")
                    result_data["url"] = cached_audio_path
                else:
                    # ================= 2. 缓存未命中：走网络请求 =================
                    self.task_progress_update.emit("🎵 正在向云端请求常规音源...")
                    res_url = self.session.get(f"{self.base_url}/song/url", params={"id": song_id, "br": 192000},
                                               timeout=10).json()

                    if res_url.get("code") == 200 and res_url.get("data"):
                        result_data["url"] = res_url["data"][0].get("url", "") or ""

                    # ====== 兜底音源匹配机制 ======
                    if not result_data["url"]:
                        self.task_progress_update.emit("⚠️ 常规音源受限，正在启动全网版权音源匹配，请耐心等待...")
                        try:
                            res_match = self.session.get(f"{self.base_url}/song/url/match", params={"id": song_id},
                                                         timeout=15).json()
                            if res_match.get("code") == 200:
                                match_url = res_match.get("data")
                                if match_url and isinstance(match_url, str):
                                    result_data["url"] = match_url
                                    self.task_progress_update.emit("✅ 兜底匹配成功！")
                                else:
                                    self.task_progress_update.emit("❌ 兜底匹配失败：未找到有效源。")
                        except Exception as e:
                            print(f"[DEBUG LOG] 💥 音源匹配接口异常: {e}")

                    # ====== 💡 [核心修改]：成功拿到线上 URL 后，启动后台静默缓存，主线程立刻放行秒播 ======
                    final_url = result_data["url"]
                    if final_url and final_url.startswith("http"):
                        self.task_progress_update.emit("⚡ 获取到音源，正在准备极速播放...")

                        # 1. 定义一个内部函数，专门用于后台干脏活累活
                        def background_cache_task(target_url, s_id, cache_dir):
                            temp_path = os.path.join(cache_dir, f"{s_id}.tmp")
                            final_path = os.path.join(cache_dir, f"{s_id}.mp3")
                            try:
                                res_audio = requests.get(target_url, headers=get_headers(), stream=True,
                                                         timeout=15)
                                if res_audio.status_code in [200, 206]:
                                    with open(temp_path, 'wb') as f:
                                        for chunk in res_audio.iter_content(chunk_size=1024 * 256):
                                            if chunk: f.write(chunk)

                                    if os.path.exists(final_path): os.remove(final_path)
                                    os.rename(temp_path, final_path)
                                    print(f"[DEBUG LOG] ✅ 后台静默缓存完成 (ID:{s_id})")
                                else:
                                    print(f"[DEBUG LOG] ❌ 后台缓存失败, 状态码: {res_audio.status_code}")
                            except Exception as e:
                                print(f"[DEBUG LOG] ❌ 后台缓存异常: {e}")
                                if os.path.exists(temp_path):
                                    os.remove(temp_path)

                        # 2. 引入 threading 开辟不可见子线程
                        cache_thread = threading.Thread(
                            target=background_cache_task,
                            args=(final_url, song_id, audio_cache_dir)
                        )
                        # 设置为守护线程（如果主程序突然关闭，这个后台线程会默默自杀，不留后患）
                        cache_thread.daemon = True
                        cache_thread.start()
                        # ⚠️ 关键：这里我们不再把 result_data["url"] 改成本地硬盘路径了！
                        # 保持它是原生的 http 链接，让下方的流程直接把 http 喂给播放器实现0秒开播！
                # ==============================================================

                # 2. 获取歌曲详情(封面)
                self.task_progress_update.emit("🖼️ 正在获取高清专辑封面...")

                res_detail = self.session.get(f"{self.base_url}/song/detail", params={"ids": song_id},

                                              timeout=10).json()
                if res_detail.get("code") == 200 and res_detail.get("songs"):
                    pic_url = res_detail["songs"][0].get("al", {}).get("picUrl", "")
                    if pic_url:
                        cover_path = os.path.join(self.extra_data["cache_dir"], f"cover_{song_id}.jpg")
                        if not os.path.exists(cover_path):
                            self.task_progress_update.emit("🖼️ 正在下载并缓存专辑封面...")
                            img_data = self.session.get(f"{pic_url}", timeout=10).content
                            with open(cover_path, 'wb') as f:
                                f.write(img_data)
                        result_data["cover_path"] = cover_path

                # 3. 获取歌词
                self.task_progress_update.emit("📝 正在获取并解析动态歌词...")
                res_lyric = self.session.get(f"{self.base_url}/lyric", params={"id": song_id}, timeout=10).json()
                if res_lyric.get("code") == 200:
                    result_data["lyrics"] = res_lyric.get("lrc", {}).get("lyric", "")

                self.task_progress_update.emit(
                    "✅ 媒体流缓冲完毕，即将开始播放。" if result_data["url"] else "❌ 未找到有效源。")
                self.play_info_finished.emit(result_data)

            # ====== [新增 3]：处理登录状态检查的分支 ======
            elif self.task_type == "check_login":
                print("[DEBUG LOG] 正在请求 /login/status 检查用户状态...")
                res_obj = self.session.get(f"{self.base_url}/login/status", timeout=10)
                res_json = res_obj.json()
                print(res_json)

                result_data = {"nickname": "未登录", "avatar_path": "", "uid": None}  # 加入 uid

                # 校验接口是否返回了有效的 user 数据
                if res_json.get("data", {}).get("code") == 200 and res_json.get("data", {}).get("profile"):
                    profile = res_json["data"]["profile"]
                    result_data["nickname"] = profile.get("nickname", "未知用户")
                    result_data["uid"] = profile.get("userId")  # 💡 提取用户ID
                    avatar_url = profile.get("avatarUrl", "")

                    if avatar_url:
                        # 确保头像被缓存下来
                        avatar_path = os.path.join(self.extra_data["cache_dir"], "user_avatar.jpg")
                        # 如果本地没有缓存过这个头像，才去下载，加快软件启动速度
                        if not os.path.exists(avatar_path):
                            img_data = self.session.get(avatar_url, timeout=10).content
                            with open(avatar_path, 'wb') as f:
                                f.write(img_data)
                        result_data["avatar_path"] = avatar_path

                self.login_status_finished.emit(result_data)
            # ====== [新增 4]：处理获取用户歌单分支 ======
            elif self.task_type == "get_user_playlists":
                uid = self.params["uid"]
                print(f"[DEBUG LOG] 正在请求用户 {uid} 的歌单列表...")

                res_obj = self.session.get(f"{self.base_url}/user/playlist/create", params=self.params, timeout=10)
                print(res_obj.text)
                res_json = res_obj.json()
                playlists_data = []

                if res_json.get("code") == 200:
                    raw_playlists = res_json.get("playlist") or res_json.get("data", {}).get("playlist", [])
                    for pl in raw_playlists:
                        pid = pl.get("id")
                        name = pl.get("name")
                        count = pl.get("trackCount")
                        cover_url = pl.get("coverImgUrl")
                        # 👑 [新增] 提取歌单创建者的 ID
                        creator_id = pl.get("creator", {}).get("userId")
                        cover_path = ""
                        # 下载歌单封面 (💡 聪明设计：加了 ?param=100y100 参数，网易云只会返回极小的缩略图，秒下载防卡死！)
                        if cover_url:
                            cover_path = os.path.join(self.extra_data["cache_dir"], f"pl_{pid}.jpg")
                            # 每次都要更新
                            img_data = self.session.get(f"{cover_url}?param=100y100", timeout=5).content
                            with open(cover_path, "wb") as f:
                                f.write(img_data)

                        playlists_data.append({
                            "id": pid, "name": name, "count": count, "cover_path": cover_path, "creator_id": creator_id
                            # 传入创建者ID
                        })

                self.user_playlists_finished.emit(playlists_data)
            # ====== 👑 [新增] 添加到歌单的分支 ======
            elif self.task_type == "add_to_playlist":
                res_obj = self.session.get(f"{self.base_url}/playlist/tracks", params=self.params, timeout=10)
                res_json = res_obj.json()

                # 兼容网易云的各种套娃结构
                code = res_json.get("code")
                body_code = res_json.get("body", {}).get("code") if isinstance(res_json.get("body"), dict) else None

                if code == 200 or body_code == 200:
                    self.add_playlist_finished.emit(True, "✅ 成功添加到歌单！")
                elif code == 502 or body_code == 502:
                    self.add_playlist_finished.emit(False, "⚠️ 部分或全部歌曲已存在于该歌单中！")
                else:
                    error_msg = res_json.get("message") or res_json.get("body", {}).get("message") or "未知错误"
                    self.add_playlist_finished.emit(False, f"❌ 添加失败：{error_msg}")
            # ====== [新增]：处理获取歌手全部单曲分支 ======
            elif self.task_type == "search_artist":
                print(f"[DEBUG LOG] 正在请求歌手单曲, 参数: {self.params}")
                res_obj = self.session.get(f"{self.base_url}/artist/songs", params=self.params, timeout=15)
                # 💡巧妙之处：把 mode_str 设为 "artist"，在主线程解析时，
                # 网易云返回的结构包含 "songs": [...]，这与 playlist 模式完全一致，可以直接复用！
                self.search_finished.emit(res_obj.json(), "artist")

            # 3. 在 run 方法里面，加入 search_suggest 的处理逻辑
            elif self.task_type == "search_suggest":
                # 请求网易云搜索建议接口
                res_obj = self.session.get(f"{self.base_url}/search/suggest/pc", params=self.params, timeout=5)
                res_json = res_obj.json()

                suggests_list = []
                if res_json.get("code") == 200 and "data" in res_json:
                    raw_suggests = res_json["data"].get("suggests", [])
                    # 提取 JSON 中的 keyword 字段
                    for item in raw_suggests:
                        kw = item.get("keyword")
                        if kw:
                            suggests_list.append(kw)

                # 把纯文本的列表发送回主线程
                self.search_suggest_finished.emit(suggests_list)
            # ====== [新增] 获取每日推荐分支 ======
            elif self.task_type == "get_daily_recommend":
                print("[DEBUG LOG] 正在请求每日推荐歌曲...")
                res_obj = self.session.get(f"{self.base_url}/recommend/songs", timeout=15)
                print(res_obj.text)
                # 复用 search_finished 信号，传入特殊的 mode_str 标识
                self.search_finished.emit(res_obj.json(), "daily_recommend")
            # ====== 👑 [新增] 获取历史日推日期分支 ======
            elif self.task_type == "get_history_dates":
                res_obj = self.session.get(f"{self.base_url}/history/recommend/songs", timeout=10)
                res_json = res_obj.json()

                # 安全提取日期列表
                dates = []
                if res_json.get("code") == 200:
                    dates = res_json.get("data", {}).get("dates", [])

                self.history_dates_finished.emit(dates)

            # ====== 👑 [新增] 获取历史日推详情分支 ======
            elif self.task_type == "get_history_detail":
                print(f"[DEBUG LOG] 正在请求 {self.params['date']} 的历史日推...")
                res_obj = self.session.get(f"{self.base_url}/history/recommend/songs/detail", params=self.params,
                                           timeout=15)
                # 复用 search_finished 信号，传入特殊的 history_recommend 标识
                self.search_finished.emit(res_obj.json(), "history_recommend")
        except Exception as e:
            print(f"[DEBUG LOG] 💥 异步线程内部崩溃: {e}")
            self.error_occurred.emit(str(e))
        print(f"[DEBUG LOG] ========================================================\n")
