# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "commentjson>=0.9.0",
# ]
# ///
"""zed_lsp_manager.py: 通用 Zed 项目级 LSP 配置管理与脚手架工具

功能特性：
1. 新项目极简脚手架：若当前目录未创建 .zed/settings.json，提供一键初始化并写入规范模板；
2. 100% 注释与格式保真（JSONC 手术刀式原位更新）：
   - 读取时解析语义，修改时采用精准文本补丁；
   - 彻底杜绝全量 json.dumps 抹平用户原有中文注释、空行与自定义设置；
3. 一键批量全关：将 languages 下所有条目的 enable_language_server 置为 false，极致节省内存；
4. 选择开启语言：选择单项或多项语言置为 true，其余为 false；
5. 覆盖式单槽记忆：设置批量开启时，自动将选中的语言列表覆盖写入专属 AppData 存储，
   新项目可一键套用记忆瞬间秒建环境；
6. 架构与合规：
   - 严格遵循仓库《AGENTS.md》数据文件强约束，数据落盘于 %APPDATA%/language_projects/zed_lsp_manager/；
   - 严格遵循《CLI 工具开发标准》（Core Service 与 Shell 解耦，支持 --json 与 --schema 自描述）；
   - 无参数运行时自动进入交互式终端数字菜单。
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import commentjson

# ==============================================================================
# 0. 全局常量与环境适配
# ==============================================================================

VERSION = "1.1.0"

# Windows 终端 UTF-8 编码防乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 支持的通用语言及其默认配置与别名
DEFAULT_LANGUAGE_SPECS: Dict[str, Dict[str, Any]] = {
    "Python": {
        "aliases": ["py", "python"],
        "language_servers": ["basedpyright"],
        "default_enabled": True,
        "description": "Python (基于 basedpyright 单文件只读轻量索引)",
    },
    "TypeScript": {
        "aliases": ["ts", "typescript"],
        "language_servers": ["vtsls"],
        "default_enabled": False,
        "description": "TypeScript (基于 vtsls)",
    },
    "TSX": {
        "aliases": ["tsx"],
        "language_servers": ["vtsls"],
        "default_enabled": False,
        "description": "TSX (React/Vue JSX)",
    },
    "JavaScript": {
        "aliases": ["js", "javascript", "jsx"],
        "language_servers": ["vtsls"],
        "default_enabled": False,
        "description": "JavaScript (基于 vtsls)",
    },
    "Rust": {
        "aliases": ["rs", "rust"],
        "language_servers": ["rust-analyzer"],
        "default_enabled": False,
        "description": "Rust (基于 rust-analyzer)",
    },
    "Go": {
        "aliases": ["go", "golang"],
        "language_servers": ["gopls"],
        "default_enabled": False,
        "description": "Go (基于 gopls)",
    },
}

# 默认新项目规范初始模板（带丰富中文注释）
DEFAULT_PROJECT_TEMPLATE_JSONC = """{
  // Zed 项目级配置：白名单模式开启语言服务器（配合全局 enable_language_server: false）
  // 目标是在保留跳转与补全的前提下，尽可能减少内存占用与后台索引抖动
  "languages": {
    // Python：basedpyright 跳转，仅对打开文件做语法诊断
    "Python": {
      "enable_language_server": true,
      "language_servers": ["basedpyright"]
    },
    // TypeScript / TSX / JavaScript：vtsls 提供跳转
    "TypeScript": { "enable_language_server": false, "language_servers": ["vtsls"] },
    "TSX": { "enable_language_server": false, "language_servers": ["vtsls"] },
    "JavaScript": { "enable_language_server": false, "language_servers": ["vtsls"] },
    // Rust：rust-analyzer 跳转
    "Rust": { "enable_language_server": false, "language_servers": ["rust-analyzer"] },
    // Go：gopls 跳转
    "Go": { "enable_language_server": false, "language_servers": ["gopls"] }
  },
  "lsp": {
    // basedpyright：彻底关闭类型全量推断，仅分析当前打开文件
    "basedpyright": {
      "settings": {
        "basedpyright.analysis.diagnosticMode": "openFilesOnly",
        "basedpyright.analysis.autoSearchPaths": false
      }
    },
    // vtsls：设置堆上限为 2GB，禁止在非 node 仓库盲目下载类型包
    "vtsls": {
      "settings": {
        "typescript": {
          "tsserver": { "maxTsServerMemory": 2048 },
          "disableAutomaticTypeAcquisition": true
        },
        "javascript": {
          "tsserver": { "maxTsServerMemory": 2048 },
          "disableAutomaticTypeAcquisition": true
        }
      }
    }
  }
}
"""


# ==============================================================================
# 1. 核心业务服务层 (ZedLspService)
# ==============================================================================

class ZedLspService:
    """Zed 项目级 LSP 管理核心服务层，与命令行解析及终端交互彻底解耦。"""

    def __init__(self, project_dir: Optional[Path] = None):
        self.project_dir = (project_dir or Path.cwd()).resolve()
        self.zed_dir = self.project_dir / ".zed"
        self.settings_file = self.zed_dir / "settings.json"

    # --- 路径与数据合规管理 (遵从 AGENTS.md) ---

    @staticmethod
    def get_data_dir() -> Path:
        """获取项目专属数据存储目录（严格遵守 AGENTS.md 数据强约束）。"""
        app_data = os.environ.get("APPDATA")
        if app_data:
            base_dir = Path(app_data) / "language_projects" / "zed_lsp_manager"
        else:
            base_dir = Path.home() / ".language_projects" / "zed_lsp_manager"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    @classmethod
    def get_memory_file(cls) -> Path:
        """获取单槽覆盖式记忆文件路径。"""
        return cls.get_data_dir() / "lsp-batch-memory.json"

    # --- 原子写入工具 ---

    @staticmethod
    def write_atomic(file_path: Path, content: str) -> None:
        """尽力原子写入文本，杜绝崩溃破坏文件，适配 Windows 句柄占用降级。"""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = file_path.with_name(f"{file_path.name}.tmp.{os.getpid()}")
        tmp.write_text(content, encoding="utf-8")
        try:
            os.replace(tmp, file_path)
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EPERM):
                raise
            # Windows 下目标被锁无法原子替换时原地回写
            file_path.write_text(content, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # --- 项目检测与配置读取 ---

    def is_configured(self) -> bool:
        """判断当前项目是否已初始化 .zed/settings.json。"""
        return self.settings_file.is_file()

    def read_raw_text(self) -> str:
        """读取 settings.json 原始文本。"""
        if not self.is_configured():
            return ""
        return self.settings_file.read_text(encoding="utf-8")

    def read_settings_dict(self) -> Dict[str, Any]:
        """读取当前项目的 settings.json（容错处理 JSONC 注释转为字典供检索）。"""
        raw = self.read_raw_text().strip()
        if not raw:
            return {}
        try:
            data = commentjson.loads(raw)
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            try:
                return json.loads(raw)
            except Exception:
                return {}

    # --- 语言规范化与解析 ---

    @staticmethod
    def canonical_language_name(name_or_alias: str) -> Optional[str]:
        """将用户输入或别名转换为官方规范语言名称。"""
        query = name_or_alias.strip().lower()
        for canonical, spec in DEFAULT_LANGUAGE_SPECS.items():
            if canonical.lower() == query:
                return canonical
            if query in [a.lower() for a in spec.get("aliases", [])]:
                return canonical
        if query:
            return name_or_alias.strip()
        return None

    # --- 状态扫描 ---

    def get_status(self) -> Dict[str, Any]:
        """提取当前项目的完整 LSP 状态概览。"""
        configured = self.is_configured()
        settings = self.read_settings_dict()
        languages_block = settings.get("languages", {})
        if not isinstance(languages_block, dict):
            languages_block = {}

        lang_status: Dict[str, Dict[str, Any]] = {}
        # 先列出默认支持的语言
        for canonical, spec in DEFAULT_LANGUAGE_SPECS.items():
            entry = languages_block.get(canonical)
            enabled = False
            servers = spec.get("language_servers", [])
            if isinstance(entry, dict):
                enabled = bool(entry.get("enable_language_server", False))
                if "language_servers" in entry and isinstance(entry["language_servers"], list):
                    servers = entry["language_servers"]
            lang_status[canonical] = {
                "enabled": enabled,
                "servers": servers,
                "description": spec.get("description", ""),
                "is_default_spec": True,
            }

        # 扫描用户自建的其他语言条目
        for k, v in languages_block.items():
            if k not in lang_status and isinstance(v, dict):
                lang_status[k] = {
                    "enabled": bool(v.get("enable_language_server", False)),
                    "servers": v.get("language_servers", []),
                    "description": "自定义语言",
                    "is_default_spec": False,
                }

        preset = self.load_memory_preset()
        return {
            "project_dir": str(self.project_dir),
            "configured": configured,
            "settings_path": str(self.settings_file),
            "languages": lang_status,
            "memory_preset": preset,
        }

    # ==========================================================================
    # 核心创新：JSONC 注释保真补丁引擎 (In-place JSONC Patcher)
    # ==========================================================================

    @staticmethod
    def patch_language_in_text(raw_text: str, lang: str, enabled: bool) -> Tuple[str, bool]:
        """在 JSONC 原始文本中精准定位指定语言块并原位替换 enable_language_server。

        100% 保留该语言段落前后所有的注释、换行、字段顺序以及排版。
        返回: (更新后的文本, 是否匹配并替换成功)
        """
        val_str = "true" if enabled else "false"

        # 1. 尝试匹配已有的语言块中已存在的 enable_language_server 字段
        # 例如: "Go": { ... "enable_language_server": true ... }
        # [^}]*? 保证非贪婪锁定在当前对象的花括号内
        pattern_existing = (
            rf'("{re.escape(lang)}"\s*:\s*\{{[^}}]*?"enable_language_server"\s*:\s*)(true|false)'
        )
        if re.search(pattern_existing, raw_text, flags=re.DOTALL):
            new_text = re.sub(
                pattern_existing,
                rf"\g<1>{val_str}",
                raw_text,
                count=1,
                flags=re.DOTALL,
            )
            return new_text, True

        # 2. 如果存在该语言块，但里面缺失 enable_language_server 键
        pattern_missing_key = rf'("{re.escape(lang)}"\s*:\s*\{{)'
        if re.search(pattern_missing_key, raw_text):
            replacement = rf'\g<1>\n      "enable_language_server": {val_str},'
            new_text = re.sub(pattern_missing_key, replacement, raw_text, count=1)
            return new_text, True

        return raw_text, False

    @classmethod
    def insert_new_language_entry(cls, raw_text: str, lang: str, enabled: bool) -> str:
        """当文件中完全没有该语言时，在 languages 块开头优雅插入新条目并保留注释。"""
        val_str = "true" if enabled else "false"
        servers = ["vtsls"]
        if lang in DEFAULT_LANGUAGE_SPECS:
            servers = DEFAULT_LANGUAGE_SPECS[lang].get("language_servers", [])
        servers_json = json.dumps(servers)

        entry_text = f'    "{lang}": {{ "enable_language_server": {val_str}, "language_servers": {servers_json} }},\n'

        # 定位 "languages": { 并紧接着插入
        pattern_langs = r'("languages"\s*:\s*\{)'
        if re.search(pattern_langs, raw_text):
            return re.sub(pattern_langs, rf"\g<1>\n{entry_text}", raw_text, count=1)

        # 若连 "languages" 块都没有，安全插入到根对象的起始位置
        return raw_text.replace("{\n", f"{{\n  \"languages\": {{\n{entry_text}  }},\n", 1)

    def apply_batch_patch(self, desired_states: Dict[str, bool]) -> None:
        """对 settings.json 原始文本执行批量的保真补丁并原子写回。"""
        if not self.is_configured():
            # 新项目直接创建自带丰富注释的标准模版
            raw_text = DEFAULT_PROJECT_TEMPLATE_JSONC
        else:
            raw_text = self.read_raw_text()
            if not raw_text.strip():
                raw_text = DEFAULT_PROJECT_TEMPLATE_JSONC

        updated_text = raw_text
        for lang_name, enabled in desired_states.items():
            updated_text, found = self.patch_language_in_text(updated_text, lang_name, enabled)
            if not found:
                # 语言尚未在原文本中定义，安全补全该条目
                updated_text = self.insert_new_language_entry(updated_text, lang_name, enabled)

        self.write_atomic(self.settings_file, updated_text)

    # --- 核心操作分发 ---

    def init_project(self, initial_enabled: Optional[List[str]] = None) -> Dict[str, Any]:
        """初始化项目：新项目写入带丰富注释的规范模版；已存在项目补全必要字段。"""
        if not self.is_configured():
            enabled_set = set(initial_enabled) if initial_enabled is not None else {"Python"}
            # 基于默认注释模版，根据 initial_enabled 逐一补丁
            desired: Dict[str, bool] = {}
            for canonical in DEFAULT_LANGUAGE_SPECS:
                desired[canonical] = canonical in enabled_set
            self.apply_batch_patch(desired)
            return self.get_status()

        # 已有配置：只确保默认语言条目齐全，绝不清除已有注释
        current = self.get_status()
        existing_langs = current["languages"]
        desired: Dict[str, bool] = {}
        for canonical in DEFAULT_LANGUAGE_SPECS:
            if canonical in existing_langs:
                desired[canonical] = existing_langs[canonical]["enabled"]
            else:
                desired[canonical] = False
        self.apply_batch_patch(desired)
        return self.get_status()

    def disable_all(self) -> Dict[str, Any]:
        """一键全关所有语言的 LSP（100% 原位保留所有注释）。"""
        status = self.get_status()
        all_langs = list(status["languages"].keys())
        desired = {lang: False for lang in all_langs}
        self.apply_batch_patch(desired)
        return self.get_status()

    def enable_selected(self, target_languages: List[str], save_memory: bool = True) -> Dict[str, Any]:
        """勾选启用指定的单/多语言，其余已有/已知语言置为 false（100% 原位保留注释）。"""
        canon_set: Set[str] = set()
        for item in target_languages:
            c = self.canonical_language_name(item)
            if c:
                canon_set.add(c)

        status = self.get_status()
        all_known = set(status["languages"].keys()) | set(DEFAULT_LANGUAGE_SPECS.keys()) | canon_set

        desired: Dict[str, bool] = {}
        for lang_name in all_known:
            desired[lang_name] = lang_name in canon_set

        self.apply_batch_patch(desired)

        if save_memory and canon_set:
            self.save_memory_preset(list(canon_set))
        return self.get_status()

    # --- 覆盖式单槽记忆管理 ---

    @classmethod
    def load_memory_preset(cls) -> List[str]:
        """读取全局唯一单槽记忆预设。"""
        mem_file = cls.get_memory_file()
        if not mem_file.is_file():
            return ["Python"]
        try:
            data = json.loads(mem_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "enabled_languages" in data:
                val = data["enabled_languages"]
                if isinstance(val, list):
                    return [str(x) for x in val]
        except Exception:
            pass
        return ["Python"]

    @classmethod
    def save_memory_preset(cls, languages: List[str]) -> None:
        """覆盖式保存全局单槽记忆预设（严格只保留一份）。"""
        mem_file = cls.get_memory_file()
        payload = {
            "enabled_languages": sorted(list(set(languages))),
            "updated_at": Path(__file__).name,
        }
        cls.write_atomic(mem_file, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    def apply_memory_preset(self) -> Dict[str, Any]:
        """一键套用上次记忆的语言组合（新项目自动建项，已有注释绝不破坏）。"""
        preset = self.load_memory_preset()
        return self.enable_selected(preset, save_memory=False)


# ==============================================================================
# 2. CLI Schema 契约元数据
# ==============================================================================

def get_cli_schema() -> Dict[str, Any]:
    """导出 CLI 静态契约规范 JSON Schema，满足《CLI 工具开发标准》一等公民要求。"""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ZedLspManagerCLI",
        "description": "通用 Zed 项目级 LSP 配置管理与初始化工具（支持 100% JSONC 注释保真）",
        "type": "object",
        "commands": {
            "--status": {"description": "查看当前项目 LSP 开启状态及记忆预设"},
            "--init": {"description": "初始化或补全 .zed/settings.json 规范模板（自带丰富中文注释）"},
            "--disable-all": {"description": "一键全关当前项目所有语言的 LSP（原位保留所有已有注释）"},
            "--enable": {
                "description": "启用指定语言服务器（以逗号分隔，如 'py,ts' 或 'Python,TSX'），并自动覆盖记忆",
                "type": "string",
            },
            "--apply-preset": {"description": "一键套用上次保存的单槽批量记忆预设（新项目自动建项）"},
            "--json": {"description": "以机器可读的标准 JSON 格式输出执行结果"},
            "--schema": {"description": "导出命令行的静态自描述 JSON Schema 契约"},
            "--version": {"description": "显示版本信息"},
        },
        "response_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["ok", "error"]},
                "data": {
                    "type": "object",
                    "properties": {
                        "project_dir": {"type": "string"},
                        "configured": {"type": "boolean"},
                        "settings_path": {"type": "string"},
                        "languages": {"type": "object"},
                        "memory_preset": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "message": {"type": "string"},
            },
            "required": ["status"],
        },
    }


# ==============================================================================
# 3. 交互式控制台菜单 (Interactive Console)
# ==============================================================================

def render_interactive_ui(service: ZedLspService) -> None:
    """无参数时的交互式数字菜单体验。"""
    while True:
        status = service.get_status()
        is_conf = status["configured"]
        langs: Dict[str, Dict[str, Any]] = status["languages"]
        preset: List[str] = status["memory_preset"]

        print("\n" + "=" * 64)
        print(f"  Zed LSP Manager v{VERSION} - 项目级配置与脚手架 (注释 100% 保真)")
        print("=" * 64)
        print(f"项目路径: {status['project_dir']}")
        if not is_conf:
            print("项目状态: [✨ 新项目：尚未创建 .zed/settings.json]")
        else:
            print(f"配置文件: {status['settings_path']}")

        print("\n当前各语言 LSP 开关状态:")
        print("-" * 64)
        indexed_langs: List[str] = list(langs.keys())
        for idx, lang_name in enumerate(indexed_langs, 1):
            info = langs[lang_name]
            mark = "[x] 已开启" if info["enabled"] else "[ ] 已关闭"
            servers = ", ".join(info["servers"])
            desc = info["description"]
            print(f"  {idx:2d}. {mark:10s} {lang_name:<12s} ({desc} -> {servers})")

        print("-" * 64)
        print(f"全局单槽覆盖记忆: [{', '.join(preset)}]")
        print("=" * 64)
        print("请选择操作:")
        if not is_conf:
            print("  1. ✨ 初始化创建 .zed 配置 (带规范注释标准模板)")
            print("  2. ⚡ 一键套用记忆预设 (新项目直接一键建好并开启对应语言)")
        else:
            print("  1. 🔄 补齐默认语言缺失项 (绝不破坏已有注释与排版)")
            print("  2. ⚡ 套用记忆预设开启对应语言")
        print("  3. 🎯 选择开启指定语言 (数字编号或名称，自动覆盖最新记忆)")
        print("  4. 🛑 一键全关所有语言服务器 (极度节省内存)")
        print("  5. 📄 查看当前 settings.json 文件内容 (验证注释完整性)")
        print("  0. 🚪 退出")
        print("=" * 64)

        try:
            choice = input("请输入选项编号: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出。")
            break

        if choice == "0":
            print("再见！")
            break
        elif choice == "1":
            service.init_project()
            print(">> ✅ .zed/settings.json 初始化/对齐完成（已有注释 100% 完整保留）！")
        elif choice == "2":
            service.apply_memory_preset()
            print(f">> ✅ 已套用记忆预设 [{', '.join(preset)}]，配置已更新（注释完整保留）！")
        elif choice == "3":
            print("\n请输入要开启的语言（支持数字编号如 '1,2' 或别名如 'py,ts'，留空表示全关）：")
            try:
                selected_input = input("语言列表: ").strip()
            except (KeyboardInterrupt, EOFError):
                continue
            if not selected_input:
                service.disable_all()
                print(">> ✅ 输入为空，已全部关闭。")
                continue

            target_list: List[str] = []
            parts = [p.strip() for p in re.split(r"[,;\s]+", selected_input) if p.strip()]
            for p in parts:
                if p.isdigit():
                    idx = int(p) - 1
                    if 0 <= idx < len(indexed_langs):
                        target_list.append(indexed_langs[idx])
                else:
                    canon = service.canonical_language_name(p)
                    if canon:
                        target_list.append(canon)

            if not target_list:
                print(">> ⚠️ 未识别到有效语言名称，请重试。")
            else:
                service.enable_selected(target_list, save_memory=True)
                print(f">> ✅ 已成功开启 [{', '.join(target_list)}]，并已覆盖写入最新单槽记忆（注释完全保留）！")
        elif choice == "4":
            service.disable_all()
            print(">> ✅ 已一键关闭所有语言服务器，内存开销已释放（注释完全保留）！")
        elif choice == "5":
            if not service.is_configured():
                print(">> ⚠️ 尚未创建 .zed/settings.json。")
            else:
                print("\n--- .zed/settings.json (保留全部注释) ---")
                print(service.settings_file.read_text(encoding="utf-8"))
                print("------------------------------------------")
        else:
            print(">> ⚠️ 无效选项，请输入正确的菜单编号。")


# ==============================================================================
# 4. 主程序入口与命令行分发
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="通用 Zed 项目级 LSP 配置管理器（支持新项目极速建项、一键全关、单槽批量记忆、JSONC 注释 100% 保真）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--status", action="store_true", help="查看当前项目 LSP 开启状态与记忆预设")
    parser.add_argument("--init", action="store_true", help="初始化创建或补全规范的 .zed/settings.json")
    parser.add_argument("--disable-all", action="store_true", help="一键全关当前项目所有语言的 LSP（原位保留注释）")
    parser.add_argument("--enable", metavar="LANGS", help="开启指定语言（以逗号分隔，如 py,ts），自动覆盖最新记忆")
    parser.add_argument("--apply-preset", action="store_true", help="套用上次保存的单槽批量记忆预设（新项目自动建项）")
    parser.add_argument("--project-dir", metavar="DIR", help="指定目标项目根目录（默认为当前工作目录）")
    parser.add_argument("--json", action="store_true", help="输出机器可读的标准结构化 JSON")
    parser.add_argument("--schema", action="store_true", help="输出静态 JSON Schema 契约定义")
    parser.add_argument("--version", action="version", version=f"zed_lsp_manager {VERSION}")

    # 若无任何参数传入，直接进入交互式控制台菜单
    if len(sys.argv) == 1:
        service = ZedLspService()
        render_interactive_ui(service)
        return 0

    args = parser.parse_args()

    # 1. 处理静态 Schema 输出
    if args.schema:
        schema = get_cli_schema()
        print(json.dumps(schema, indent=2, ensure_ascii=False))
        return 0

    target_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()
    service = ZedLspService(project_dir=target_dir)

    result_data: Optional[Dict[str, Any]] = None
    message = ""

    # 2. 调度执行各项业务
    if args.init:
        result_data = service.init_project()
        message = "已完成 .zed/settings.json 项目规范初始化（注释完整保留）"
    elif args.disable_all:
        result_data = service.disable_all()
        message = "已一键关闭所有语言服务器（注释完整保留）"
    elif args.enable:
        raw_langs = [x.strip() for x in args.enable.split(",") if x.strip()]
        result_data = service.enable_selected(raw_langs, save_memory=True)
        message = f"已开启指定语言 [{args.enable}] 并更新记忆（注释完整保留）"
    elif args.apply_preset:
        result_data = service.apply_memory_preset()
        preset = service.load_memory_preset()
        message = f"已套用记忆预设 [{', '.join(preset)}]（注释完整保留）"
    elif args.status:
        result_data = service.get_status()
        message = "查询状态成功"
    else:
        result_data = service.get_status()
        message = "未指定操作指令，返回当前状态"

    # 3. 输出格式分发
    if args.json:
        output_payload = {
            "status": "ok",
            "message": message,
            "data": result_data,
        }
        print(json.dumps(output_payload, indent=2, ensure_ascii=False))
    else:
        print(f">> {message}")
        if result_data:
            print(f"项目: {result_data.get('project_dir')}")
            print(f"配置就绪: {result_data.get('configured')}")
            active = [
                k for k, v in result_data.get("languages", {}).items() if v.get("enabled")
            ]
            print(f"当前开启语言: {active if active else '无 (全部关闭)'}")
            print(f"记忆预设: {result_data.get('memory_preset')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
