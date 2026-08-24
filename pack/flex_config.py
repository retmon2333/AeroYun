import json
import os
import tempfile
from pathlib import Path
from typing import Any


class FlexConfig:
    """
    健壮的 Python 配置管理器 (V2 版本)
    新增 append_config 追加写入方法。
    """

    def __init__(self, filepath: str | Path):
        self.filepath = Path(filepath)
        self._config_cache: dict[str, Any] = {}
        print(f"[*] FlexConfig 初始化完成。目标路径: {self.filepath.absolute()}")

    def read_config(self, quiet: bool = False) -> dict[str, Any]:
        """
        方法1：读取配置文件。
        :param quiet: 如果为 True，则不打印读取细节（用于追加操作时减少冗余刷屏）
        """
        if not quiet:
            print(f"\n[->] 开始读取配置文件: {self.filepath.name} ...")

        if not self.filepath.exists():
            if not quiet: print(f"[!] 警告: 文件不存在。将返回空字典并等待首次写入。")
            return {}

        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                self._config_cache = json.load(f)

            if not quiet:
                print(f"[+] 读取成功！共加载 {len(self._config_cache)} 个顶级配置项。")
                # self._print_type_details(self._config_cache)
            return self._config_cache

        except json.JSONDecodeError as e:
            print(f"[X] 严重错误: 配置文件数据损坏或格式不合法！")
            print(f"    -> 解析报错位置: 行 {e.lineno} 列 {e.colno}, 错误信息: {e.msg}")
            return {}
        except Exception as e:
            print(f"[X] 未知 I/O 异常: {e}")
            return {}

    def write_config(self, data: dict[str, Any], quiet: bool = False) -> bool:
        """
        方法2：写入配置文件（原子级覆盖）。
        """
        if not quiet:
            print(f"\n[<-] 开始写入配置文件: {self.filepath.name} ...")

        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path_str = tempfile.mkstemp(dir=self.filepath.parent, prefix="._cfg_tmp_", text=True)
        temp_path = Path(temp_path_str)

        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, self.filepath)

            if not quiet: print(f"[+] 写入成功！配置文件已安全落盘。")
            self._config_cache = data
            return True

        except TypeError as e:
            print(f"[X] 写入失败: 数据中包含无法被序列化的对象。错误: {e}")
            if temp_path.exists(): temp_path.unlink()
            return False
        except Exception as e:
            print(f"[X] 写入失败: 未知系统错误 {e}")
            if temp_path.exists(): temp_path.unlink()
            return False

    def append_config(self, key: str, value: Any) -> bool:
        """
        方法3：追加配置项。
        传入键和值，安全地追加到现有配置中。如果键已存在，则覆盖。
        """
        print(f"\n[++] 开始执行追加操作: 目标键名 [{key}]")

        # 1. 强制读取硬盘上的最新配置，防止多线程/多进程下的数据脏读
        current_data = self.read_config(quiet=True)

        # 2. 判断是新增还是覆盖，用于详细打印
        is_update = key in current_data
        action_type = "覆盖更新" if is_update else "全新追加"

        if is_update:
            old_val = current_data[key]
            print(f"    -> 动作: [{action_type}]")
            print(f"    -> 旧值: {old_val} (类型: {type(old_val).__name__})")
            print(f"    -> 新值: {value} (类型: {type(value).__name__})")
        else:
            print(f"    -> 动作: [{action_type}]")
            print(f"    -> 写入值: {value} (类型: {type(value).__name__})")

        # 3. 将新键值对装入配置
        current_data[key] = value

        # 4. 调用原子写入保存，完成追加
        success = self.write_config(current_data, quiet=True)
        if success:
            print(f"[+] 追加成功！当前配置总项数: {len(current_data)}")
        return success

    @staticmethod
    def _print_type_details(data: dict[str, Any], indent: int = 0) -> None:
        """递归打印数据及其 Python 数据类型"""
        space = " " * indent
        for key, value in data.items():
            type_str = type(value).__name__
            if isinstance(value, dict):
                print(f"{space}- [{type_str}] {key}:")
                FlexConfig._print_type_details(value, indent + 4)
            elif isinstance(value, list):
                print(f"{space}- [{type_str}] {key}: (长度: {len(value)})")
            else:
                val_str = str(value)
                if len(val_str) > 50: val_str = val_str[:47] + "..."
                print(f"{space}- [{type_str}] {key}: {val_str}")


# ==========================================
# 🚀 追加功能测试用例
# ==========================================
if __name__ == "__main__":
    pass
