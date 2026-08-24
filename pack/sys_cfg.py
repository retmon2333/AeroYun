from pack.flex_config import FlexConfig


class cloud_config(FlexConfig):

    def __init__(self, config_path):
        super().__init__(config_path)

        self.cache_cfg = {}
        self._config_dirty = False

        self.update()

    def update(self):
        # 发生 1 次物理读取！
        self.cache_cfg = self.read_config(quiet=True) or {}
        return self.cache_cfg

    def get_cache_cfg(self):
        return self.cache_cfg

    def set_config(self, key, value):
        """统一配置修改入口：只修改内存字典，并打上脏标记，绝对不触发真实 IO"""
        # 只有新值与旧值不同时，才进行修改并打上脏标记
        if self.cache_cfg.get(key) != value:
            self.cache_cfg[key] = value
            self._config_dirty = True

    def set_dirty(self):
        self._config_dirty = True

    def get_dirty(self) -> bool:
        return self._config_dirty

    def flush_config_to_disk(self):
        """执行真正的物理写盘（由定时器或关机事件调用）"""
        if self._config_dirty:
            try:
                # 👑 [终极融合] 放弃普通的 json.dump，直接调用你写好的原子写入引擎！
                # 把内存中维护好的全量 sys_config 字典，一次性安全落盘！
                success = self.write_config(self.cache_cfg)
                if success:
                    self._config_dirty = False  # 写完后复位脏标记
                    # print("[DEBUG LOG] 💾 全局配置已通过 FlexConfig 原子写入磁盘")
                else:
                    print("[ERROR] FlexConfig 原子刷盘返回失败！")
            except Exception as e:
                print(f"[ERROR] 配置刷盘出现异常: {e}")
