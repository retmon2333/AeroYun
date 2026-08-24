# 传入cookies创建类，自动合并处理cookies并保存到文件内

import os
import requests
from http.cookiejar import LWPCookieJar
# ====== 💡 新增：导入底层重试与适配器模块 ======
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class AutoCookieSession:
    """
    智能 Cookie 管理与高可用 Requests Session 包装类
    支持自动解析多格式 Cookie、自动维持会话、自动持久化到本地文件。
    内置工业级底层重试机制，抗击网络波动与服务端限流。
    """

    # 💡 新增 max_retries 参数，默认 3 次
    def __init__(self, file_path="cookies.txt", cookies=None, max_retries=3):
        self.file_path = file_path
        self.session = requests.Session()

        # ==================== 👑 核心新增：挂载高可用重试引擎 ====================
        if max_retries > 0:
            retry_strategy = Retry(
                total=max_retries,  # 最大重试次数
                backoff_factor=1,  # 指数退避算法的基数 (重试间隔: 0s, 1s, 2s, 4s...)，防封 IP
                status_forcelist=[429, 500, 502, 503, 504],  # 遇到这些服务端错误码强制重试
                allowed_methods=["HEAD", "GET", "OPTIONS"]  # 安全允许重试的方法 (POST 通常不重试防重复提交)
            )
            # 将重试策略打包成 HTTP 适配器
            adapter = HTTPAdapter(max_retries=retry_strategy)

            # 将适配器挂载到 http 和 https 协议上接管所有底层连接
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)
        # =======================================================================

        # 核心：使用 LWPCookieJar 替换默认的 CookieJar，使其具备文件读写能力
        self.session.cookies = LWPCookieJar(filename=file_path)

        # 1. 尝试从文件加载历史 Cookie
        if os.path.exists(file_path):
            try:
                # ignore_discard/ignore_expires: 强制加载哪怕已过期或本该被丢弃的会话级 Cookie
                self.session.cookies.load(ignore_discard=True, ignore_expires=True)
                print("加载历史cookies")
            except Exception as e:
                print(f"[Warning] 加载历史 Cookie 失败: {e}")

        # 2. 如果传入了新的 cookies，解析并合并入会话
        if cookies:
            parsed_cookies = self._parse_cookies(cookies)
            # 将字典形式的 Cookie 注入到 CookieJar 中
            requests.utils.add_dict_to_cookiejar(self.session.cookies, parsed_cookies)
            self.save_cookies()

    def _parse_cookies(self, raw_cookies):
        """将多种人类/机器可读格式的 cookies 统一解析为字典"""
        cookie_dict = {}
        if isinstance(raw_cookies, str):
            # 处理格式: "key1=value1; key2=value2==" (浏览器 Network 面板直接复制)
            for item in raw_cookies.split(';'):
                if '=' in item:
                    # 【防坑细节 1】必须使用 split('=', 1)，防止 value 中含有 base64 填充的 '=' 导致截断错误
                    key, value = item.strip().split('=', 1)
                    cookie_dict[key.strip()] = value.strip()
        elif isinstance(raw_cookies, dict):
            # 处理格式: 原生字典
            cookie_dict = raw_cookies
        elif isinstance(raw_cookies, list):
            # 处理格式: Selenium / Puppeteer 导出的 List[Dict] 格式
            for item in raw_cookies:
                if isinstance(item, dict) and 'name' in item and 'value' in item:
                    cookie_dict[item['name']] = item['value']
        else:
            raise ValueError("不支持的 Cookie 格式。请提供字符串、字典或包含字典的列表。")
        return cookie_dict

    def save_cookies(self):
        """强制保存当前 Cookies 到文件"""
        # 【防坑细节 2】必须加上两个 ignore 参数，否则无过期时间的 session-cookie 不会被保存
        self.session.cookies.save(ignore_discard=True, ignore_expires=True)

    def request(self, method, url, **kwargs):
        """
        拦截所有请求，由原生 Session 执行后，自动提取服务器 Set-Cookie 并保存。
        💡 注意：这里的 request 会自动走底层的 HTTPAdapter，如果有网络波动，
        它会在底层阻塞并重试。只有在重试成功（或最终失败抛出异常）后，才会继续往下走。
        """
        response = self.session.request(method, url, **kwargs)
        self.save_cookies()
        return response

    # ================= 常用 HTTP 方法的语法糖 =================
    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def put(self, url, **kwargs):
        return self.request('PUT', url, **kwargs)

    # 支持上下文管理器 (with 语法)，确保资源释放
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()
