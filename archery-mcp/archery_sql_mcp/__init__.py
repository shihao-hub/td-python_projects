"""Archery SQL 查询 MCP 工具（单模块实现）。

面向 Archery（Django）SQL 审核查询平台的只读查询封装：
- 自动登录（Django CSRF 协议：csrftoken cookie + csrfmiddlewaretoken 表单）
- 会话缓存（内存 + 磁盘），失效自动重登并重放请求
- 两步异步查询（POST /query/ 入队 → GET /queryresult/ 轮询至终态）

双模式运行：
- 无参数启动 → MCP stdio server（任何 MCP host 可接入）
- 子命令启动 → CLI：init / check / query

配置优先级：环境变量 > 配置文件（~/.archery-mcp/config.json，多 profile）> 内置默认。
密码仅存本地配置文件（init 时明示风险），日志与工具返回值中永不出现密码 / sessionid。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

VERSION = "0.4.0"

# ── 配置文件与会话缓存位置（可用 ARCHERY_CONFIG_DIR 整体改根目录） ──────────
_CONFIG_DIR = Path(os.environ.get("ARCHERY_CONFIG_DIR", str(Path.home() / ".archery-mcp")))
CONFIG_FILE = Path(os.environ.get("ARCHERY_CONFIG", str(_CONFIG_DIR / "config.json")))
SESSION_FILE = _CONFIG_DIR / "session.json"

# 内置默认值：公司线上库
_DEFAULTS: dict[str, Any] = {
    "base_url": "https://sql.tec-develop.com",
    "instance_name": "alisg-haiyun-powerdata-pgsql-prod-01",
    "db_name": "creativault_business",
    "redis_instance_name": "alisg-haiyun-powerdata-redis-prod-01",
    "redis_db_name": "0",
    "limit_num": 100,
    "allowed_prefixes": ["select", "with"],
    "poll_interval_s": 1.0,
    "query_timeout_s": 60.0,
    "request_timeout_s": 30.0,
}

MAX_LIMIT = 1000

# Redis 写/管理命令黑名单（客户端预检；平台侧白名单为最终防线）
_REDIS_FORBIDDEN = frozenset({
    "flushall", "flushdb", "del", "unlink", "set", "setex", "setnx", "psetex",
    "mset", "msetnx", "getset", "append", "setrange", "hset", "hsetnx", "hmset",
    "hdel", "lpush", "rpush", "lpushx", "rpushx", "lpop", "rpop", "lset",
    "linsert", "ltrim", "rpoplpush", "lmove", "sadd", "srem", "spop", "smove",
    "sinterstore", "sunionstore", "sdiffstore", "zadd", "zincrby", "zrem",
    "zremrangebyrank", "zremrangebyscore", "incr", "incrby", "incrbyfloat",
    "decr", "decrby", "hincrby", "hincrbyfloat", "expire",
    "pexpire", "expireat", "pexpireat", "persist", "rename", "renamenx",
    "migrate", "move", "copy", "restore", "swapdb", "sort", "getdel",
    "config", "shutdown", "save", "bgsave", "bgrewriteaof", "client",
    "monitor", "debug", "eval", "evalsha", "script", "slowlog", "keys",
    "dbsize", "randomkey", "select", "multi", "exec", "discard",
    "watch", "unwatch", "subscribe", "publish", "pubsub", "cluster",
    "replicaof", "failover", "reset", "function", "module",
})

# 环境变量 → 配置键（优先级最高，不落盘，适合不想存密码的机器）
_ENV_MAP = {
    "ARCHERY_BASE_URL": "base_url",
    "ARCHERY_USERNAME": "username",
    "ARCHERY_PASSWORD": "password",
    "ARCHERY_INSTANCE": "instance_name",
    "ARCHERY_DB": "db_name",
}

_CSRF_INPUT_RE = re.compile(r'name="csrfmiddlewaretoken"[^>]*value="([^"]+)"')


class ArcheryError(Exception):
    """工具级错误，message 面向调用方展示。"""


# ── 配置 ──────────────────────────────────────────────────────────────

def load_profile() -> dict[str, Any]:
    """合并三层配置：环境变量 > 配置文件 profile > 内置默认。"""
    merged = dict(_DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ArcheryError(f"配置文件 JSON 解析失败({CONFIG_FILE}): {e}")
        profile_name = os.environ.get("ARCHERY_PROFILE") or data.get("default_profile")
        profiles = data.get("profiles") or {}
        if profile_name and profile_name in profiles:
            merged.update({k: v for k, v in profiles[profile_name].items() if v not in (None, "")})
        elif profile_name:
            raise ArcheryError(f"profile '{profile_name}' 不存在，可用: {list(profiles) or '无'}")
    for env, key in _ENV_MAP.items():
        val = os.environ.get(env)
        if val:
            merged[key] = val
    if not merged.get("username") or not merged.get("password"):
        raise ArcheryError(
            f"未配置账号密码：请先运行 `archery-sql-mcp init`（写入 {CONFIG_FILE}），"
            "或通过环境变量 ARCHERY_USERNAME / ARCHERY_PASSWORD 注入。"
        )
    return merged


# ── 会话管理：自动登录 + 缓存 + 失效重登 ────────────────────────────────

class SessionManager:
    """Archery 登录态管理。Django session 过期表现为 302 → /login/。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.base = str(cfg["base_url"]).rstrip("/")
        self.sessionid: Optional[str] = None
        self.csrftoken: Optional[str] = None

    # -- 磁盘缓存：冷启动优先复用（Django session 默认两周有效） --
    def _load_cache(self) -> bool:
        try:
            if SESSION_FILE.exists():
                data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                self.sessionid = data.get("sessionid")
                self.csrftoken = data.get("csrftoken")
        except Exception:
            pass
        return bool(self.sessionid)

    def _save_cache(self) -> None:
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(
                json.dumps(
                    {"sessionid": self.sessionid, "csrftoken": self.csrftoken,
                     "logged_in_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass  # 缓存写失败不影响本次会话

    def invalidate(self) -> None:
        self.sessionid = None
        self.csrftoken = None
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    async def ensure(self) -> None:
        if self.sessionid:
            return
        if self._load_cache():
            return
        await self.login()

    async def login(self) -> None:
        """Django CSRF 登录：GET /login/ 取 token → POST /authenticate/ → sessionid。"""
        async with httpx.AsyncClient(
            timeout=self.cfg["request_timeout_s"], follow_redirects=False
        ) as client:
            page = await client.get(f"{self.base}/login/")
            page.raise_for_status()
            m = _CSRF_INPUT_RE.search(page.text)
            form_token = m.group(1) if m else ""
            cookie_csrf = client.cookies.get("csrftoken") or ""
            if not form_token or not cookie_csrf:
                raise ArcheryError("登录页解析失败：未取到 csrfmiddlewaretoken / csrftoken")

            resp = await client.post(
                f"{self.base}/authenticate/",
                data={
                    "username": self.cfg["username"],
                    "password": self.cfg["password"],
                    "csrfmiddlewaretoken": form_token,
                },
                headers={"Referer": f"{self.base}/login/"},
            )
            # /authenticate/ 是 ajax JSON 接口：恒为 HTTP 200，
            # {"status": 0}=成功（sessionid 经 Set-Cookie 下发），status=1 时 msg 为可读错误。
            # 兼容旧式表单行为（302 跳转）作为回退分支。
            payload: Any = None
            try:
                payload = resp.json()
            except Exception:
                payload = None
            if payload is not None:
                if payload.get("status") != 0:
                    raise ArcheryError(f"Archery 登录失败: {payload.get('msg') or payload}")
            else:
                location = resp.headers.get("location", "")
                if resp.status_code not in (301, 302) or "/login" in location:
                    reason = _extract_login_error(resp.text) or (
                        f"HTTP {resp.status_code}"
                        + (f" → {location}" if location else "")
                        + "（用户名/密码错误，或账号被禁用）"
                    )
                    raise ArcheryError(f"Archery 登录失败: {reason}")

            self.sessionid = client.cookies.get("sessionid")
            self.csrftoken = client.cookies.get("csrftoken") or cookie_csrf
            if not self.sessionid:
                raise ArcheryError("登录成功但未取到 sessionid cookie")
            self._save_cache()

    def is_expired(self, resp: httpx.Response) -> bool:
        """会话失效判定：302 重定向到登录页（Archery/Django 的'401'形态）。"""
        if resp.status_code in (301, 302):
            return "login" in (resp.headers.get("location") or "").lower()
        return False

    async def token_info(self, refresh: bool = False) -> dict[str, Any]:
        """确保已登录并返回 token 概要。refresh=True 时强制重登（即'刷新 token'）。

        token（sessionid）以掩码返回：完整值仅存于本机 session.json，
        避免明文凭据进入 AI 对话历史 / 日志。
        """
        if refresh:
            self.invalidate()
            await self.login()
        else:
            await self.ensure()
        sid = self.sessionid or ""
        masked = (sid[:4] + "***" + sid[-4:]) if len(sid) > 8 else "***"
        logged_in_at = ""
        try:
            if SESSION_FILE.exists():
                logged_in_at = json.loads(
                    SESSION_FILE.read_text(encoding="utf-8")
                ).get("logged_in_at", "")
        except Exception:
            pass
        return {
            "logged_in": True,
            "token_masked": masked,
            "logged_in_at": logged_in_at,
            "base_url": self.base,
            "note": f"完整 token 已缓存于本机 {SESSION_FILE}，仅供本机调试使用",
        }


def _extract_login_error(html: str) -> Optional[str]:
    """尽力从登录失败页面提取可读错误（不同 Archery 版本模板不同）。"""
    for pat in (r'class="[^"]*alert[^"]*"[^>]*>([^<]{4,120})<',
                r'<div class="text-danger[^"]*">\s*([^<]{4,120})'):
        m = re.search(pat, html)
        if m:
            return m.group(1).strip()
    return None


# ── 查询客户端：两步异步 + 失效自动重登重放 ────────────────────────────

class ArcheryClient:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.sm = SessionManager(cfg)

    async def _authed(
        self,
        method: str,
        path: str,
        *,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> httpx.Response:
        """带会话的请求；失效自动重登一次并重放（密码错等确定性失败不会循环）。"""
        last_resp: Optional[httpx.Response] = None
        for _attempt in range(2):
            await self.sm.ensure()
            async with httpx.AsyncClient(
                timeout=self.cfg["request_timeout_s"], follow_redirects=False,
                cookies={"csrftoken": self.sm.csrftoken or "",
                         "sessionid": self.sm.sessionid or ""},
            ) as client:
                resp = await client.request(
                    method, f"{self.sm.base}{path}",
                    data=data, params=params,
                    headers={"X-CSRFToken": self.sm.csrftoken or "",
                             "Referer": self.sm.base + "/"},
                )
            if not self.sm.is_expired(resp):
                return resp
            last_resp = resp
            self.sm.invalidate()  # 触发下一轮 ensure() 重新登录
        raise ArcheryError(
            "会话失效且自动重新登录后仍未通过（账号可能被禁用或密码已变更），"
            f"末次响应 HTTP {last_resp.status_code if last_resp else '?'}"
        )

    def _check_sql_allowed(self, sql: str) -> None:
        body = re.sub(r"--[^\n]*", " ", sql)
        body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
        first = body.strip().split(None, 1)[0].lower() if body.strip() else ""
        allowed = [str(p).lower() for p in self.cfg.get("allowed_prefixes", [])]
        if first not in allowed:
            raise ArcheryError(
                f"仅允许 {'/'.join(allowed)} 开头的只读语句，收到: '{first or '(空)'}'"
            )
        # 第二道防线：首词合法但语句体内藏写操作/多语句的绕过形态
        stmt = body.strip().rstrip(";").strip()
        if ";" in stmt:
            raise ArcheryError("仅允许单条语句（检测到分号分隔的多语句）")
        if re.search(r"\bas\s*\(\s*(delete|insert|update|merge)\b", stmt, re.I):
            raise ArcheryError("CTE (WITH ... AS) 内不允许包含写操作")
        if re.search(r"\binto\b", stmt, re.I):
            raise ArcheryError("不允许 SELECT INTO 建表")

    async def query(
        self,
        sql: str,
        *,
        db_name: Optional[str] = None,
        instance_name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        self._check_sql_allowed(sql)
        limit = min(max(int(limit or self.cfg["limit_num"]), 1), MAX_LIMIT)
        started = time.monotonic()

        resp = await self._authed(
            "POST", "/query/",
            data={
                "instance_name": instance_name or self.cfg["instance_name"],
                "db_name": db_name or self.cfg["db_name"],
                "schema_name": "",
                "tb_name": "",
                "sql_content": sql,
                "limit_num": limit,
            },
        )
        try:
            submitted = resp.json()
        except Exception:
            raise ArcheryError(f"/query/ 返回非 JSON (HTTP {resp.status_code})，请联系管理员确认平台版本兼容性")
        if submitted.get("status") != 0:
            raise ArcheryError(f"/query/ 提交失败: {submitted.get('msg') or submitted}")
        data = submitted.get("data") or {}

        def _finish(columns: Any, rows: Any) -> dict[str, Any]:
            return {
                "status": "finished",
                "columns": columns or [],
                "rows": rows or [],
                "row_count": len(rows or []),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }

        # 同步模式（实测 2026-08-28，公司 Archery 版本）：
        # /query/ 直接返回 column_list / rows / error，无 query_id。
        if data.get("column_list") is not None or data.get("rows") is not None:
            if data.get("error"):
                raise ArcheryError(f"SQL 执行错误: {data['error']}")
            return _finish(data.get("column_list"), data.get("rows"))

        # 异步模式（部分 Archery 版本/配置）：提交返回 query_id → 轮询 /queryresult/。
        query_id = data.get("query_id")
        if not query_id:
            raise ArcheryError(f"/query/ 未返回结果也未返回 query_id: {submitted}")

        # 轮询至终态
        interval = float(self.cfg["poll_interval_s"])
        timeout = float(self.cfg["query_timeout_s"])
        while True:
            resp = await self._authed("GET", "/queryresult/", params={"sql_query_id": query_id})
            try:
                payload = resp.json()
            except Exception:
                raise ArcheryError(f"/queryresult/ 返回非 JSON (HTTP {resp.status_code})")
            data = payload.get("data") or {}
            status = data.get("status", "")
            if status == "finished":
                err = data.get("err")
                if err:
                    raise ArcheryError(f"SQL 执行错误: {err}")
                columns = data.get("column_list") or []
                rows = data.get("rows") or []
                return {
                    "status": "finished",
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            if status in ("failed", "aborted"):
                raise ArcheryError(f"查询失败: {data.get('err') or data}")
            if time.monotonic() - started > timeout:
                raise ArcheryError(
                    f"查询超时(>{timeout:.0f}s)，query_id={query_id}，可稍后重试或缩小结果集"
                )
            await asyncio.sleep(interval)

    async def query_redis(
        self,
        command: str,
        *,
        db_name: Optional[str] = None,
        instance_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """只读查询 Redis：command 如 'get key' / 'ttl key' / 'hgetall key' /
        'scan 0 match xxx* count 100'（协议实测：走同一 /query/ 接口，同步返回）。

        实例拓扑：默认实例 alisg-haiyun-powerdata-redis-prod-01 是华为云实例
        （部分服务的缓存 / celery result backend）。creativault 生产 Celery
        broker 与批次锁在阿里云 RDS（Apollo redis_host），未登记到 Archery、
        本工具不可达——队列/unacked/redbeat/批次锁需在后端 pod 内用
        uv run python + setting.redis_url 直查；在默认实例上查这些 key 为空
        不代表业务为空。"""
        cmd = command.strip().rstrip(";").strip()
        first = cmd.split(None, 1)[0].lower() if cmd else ""
        if not first:
            raise ArcheryError("redis 命令不能为空")
        if first in _REDIS_FORBIDDEN:
            raise ArcheryError(f"禁止的 redis 命令: '{first}'（本工具仅只读）")
        if ";" in cmd:
            raise ArcheryError("仅允许单条 redis 命令")

        started = time.monotonic()
        resp = await self._authed(
            "POST", "/query/",
            data={
                "instance_name": instance_name or self.cfg["redis_instance_name"],
                "db_name": db_name or self.cfg["redis_db_name"],
                "schema_name": "",
                "tb_name": "",
                "sql_content": cmd,
                "limit_num": 100,
            },
        )
        try:
            payload = resp.json()
        except Exception:
            raise ArcheryError(f"/query/ 返回非 JSON (HTTP {resp.status_code})")
        if payload.get("status") != 0:
            raise ArcheryError(f"redis 查询失败: {payload.get('msg') or payload}")
        data = payload.get("data") or {}
        if data.get("error"):
            raise ArcheryError(f"redis 执行错误: {data['error']}")
        rows = data.get("rows") or []
        return {
            "status": "finished",
            "columns": data.get("column_list") or ["Result"],
            "rows": rows,
            "row_count": len(rows),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    async def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        """GET 平台接口并解包：status!=0 报错，否则返回 data（无 data 时返回原 payload）。"""
        resp = await self._authed("GET", path, params=params)
        try:
            payload = resp.json()
        except Exception:
            raise ArcheryError(f"{path} 返回非 JSON (HTTP {resp.status_code})")
        if payload.get("status") != 0:
            raise ArcheryError(f"{path} 调用失败: {payload.get('msg') or payload}")
        return payload.get("data", payload)

    async def list_instances(self) -> Any:
        """列出当前账号有读权限(can_read)的所有数据库实例。"""
        # tag_codes[] 是数组参数：tag_codes%5B%5D=can_read
        return await self._get_json(
            "/group/user_all_instances/", params={"tag_codes[]": ["can_read"]}
        )

    async def instance_resources(
        self,
        *,
        instance_name: Optional[str] = None,
        db_name: Optional[str] = None,
        resource_type: str = "schema",
        schema_name: Optional[str] = None,
        tb_name: Optional[str] = None,
    ) -> Any:
        """查询实例资源：resource_type=schema 列 schema；table 列表；column 需 schema_name+tb_name。"""
        params: dict[str, Any] = {
            "instance_name": instance_name or self.cfg["instance_name"],
            "db_name": db_name or self.cfg["db_name"],
            "resource_type": resource_type,
        }
        if schema_name:
            params["schema_name"] = schema_name
        if tb_name:
            params["tb_name"] = tb_name
        return await self._get_json("/instance/instance_resource/", params=params)


# ── CLI ───────────────────────────────────────────────────────────────

def _ensure_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def cmd_init(_args: argparse.Namespace) -> int:
    _ensure_utf8_stdout()
    print(f"将生成配置文件: {CONFIG_FILE}（密码明文存放于本机，不会进入任何 git 仓库）")
    existing: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            print("检测到已有配置，回车保留原值。")
        except Exception:
            existing = {}

    old = existing.get("profiles", {}).get("prod", {})

    def ask(prompt: str, default: str = "", secret: bool = False) -> str:
        suffix = f" [{default}]" if default else ""
        val = (getpass.getpass(prompt + suffix + ": ") if secret
               else input(prompt + suffix + ": ")).strip()
        return val or default

    profile = {
        "base_url": ask("Archery 地址", old.get("base_url", _DEFAULTS["base_url"])),
        "username": ask("用户名", old.get("username", "")),
        "password": ask("密码", old.get("password", ""), secret=True),
        "instance_name": ask("默认实例", old.get("instance_name", _DEFAULTS["instance_name"])),
        "db_name": ask("默认库", old.get("db_name", _DEFAULTS["db_name"])),
    }
    config = {"default_profile": "prod", "profiles": {"prod": profile}}
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print("配置已写入，正在验证登录…")

    ok = asyncio.run(_login_check(profile))
    if ok:
        print("初始化完成 ✓  可运行 `archery-sql-mcp check` 做全链路自检。")
        return 0
    print("登录验证失败：请检查上面的错误，重新运行 init 修正。")
    return 1


async def _login_check(profile: dict[str, Any]) -> bool:
    cfg = dict(_DEFAULTS)
    cfg.update({k: v for k, v in profile.items() if v})
    try:
        sm = SessionManager(cfg)
        await sm.login()
        print(f"登录成功（session 已缓存到 {SESSION_FILE}）")
        return True
    except ArcheryError as e:
        print(f"错误: {e}")
        return False


def cmd_check(_args: argparse.Namespace) -> int:
    _ensure_utf8_stdout()
    try:
        cfg = load_profile()
        result = asyncio.run(ArcheryClient(cfg).query("SELECT 1 AS ok", limit=1))
        print(f"登录 + 查询链路正常 ✓  SELECT 1 → {result['rows']}  耗时 {result['elapsed_ms']}ms")
        return 0
    except ArcheryError as e:
        print(f"错误: {e}")
        return 1


def cmd_query(args: argparse.Namespace) -> int:
    _ensure_utf8_stdout()
    try:
        cfg = load_profile()
        result = asyncio.run(ArcheryClient(cfg).query(
            args.sql, db_name=args.db, instance_name=args.instance, limit=args.limit,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ArcheryError as e:
        print(f"错误: {e}")
        return 1


def _new_mcp_server(name: str):
    """兼容 mcp SDK 1.x/2.x：2.x 中 FastMCP 改名为 MCPServer，用法一致。"""
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
    return Server(name)


def cmd_instances(_args: argparse.Namespace) -> int:
    _ensure_utf8_stdout()
    try:
        cfg = load_profile()
        data = asyncio.run(ArcheryClient(cfg).list_instances())
        print(json.dumps({"instances": data}, ensure_ascii=False, indent=2))
        return 0
    except ArcheryError as e:
        print(f"错误: {e}")
        return 1


# ── 一键注册 MCP 到各 agent 客户端 ────────────────────────────────────

_AGENT_LABELS = {
    "zcode": "ZCode",
    "claude-code": "Claude Code",
    "claude-desktop": "Claude Desktop",
    "codex": "Codex CLI",
    "cursor": "Cursor",
}


def _json_upsert(path: Path, mutate) -> None:
    """读改写 JSON 配置（不存在则从 {} 开始），保留原有其它键。"""
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ArcheryError(f"{path} JSON 解析失败，请手工修复后重试: {e}")
    mutate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _toml_upsert(path: Path, section: str, body_lines: list[str]) -> None:
    """向 TOML 插入/替换一个段（零依赖文本处理）。body 行需自带 `key = value`。"""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    header = f"[{section}]"
    body = "\n".join(body_lines)
    pattern = re.compile(
        r"^\[" + re.escape(section) + r"\][^\n]*\n(?:(?!\n?\[)[^\n]*\n?)*", re.M
    )
    if pattern.search(text):
        # 用 lambda 替换，避免 Windows 路径中的 \U 等被 re 当作模板转义
        text = pattern.sub(lambda _m: header + "\n" + body + "\n", text)
    else:
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + header + "\n" + body + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _entry_json(command: str, timeout_ms: bool) -> dict[str, Any]:
    e = {"type": "stdio", "command": command, "args": ["archery-sql-mcp"]}
    if timeout_ms:  # 仅 zcode 使用该字段
        e["timeoutMs"] = 120000
    return e


def _toml_escape(s: str) -> str:
    """TOML 单引号字面量字符串（Windows 路径无需转义反斜杠）。"""
    return "'" + s.replace("'", "''") + "'"


def install_mcp(
    agents: list[str],
    scope: str,
    portable: Optional[bool],
    home: Optional[Path] = None,
    cwd: Optional[Path] = None,
) -> list[str]:
    """把 archery MCP server 注册进各 agent 配置。返回结果描述行列表。"""
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    uvx = shutil.which("uvx")
    results: list[str] = []
    unknown = [a for a in agents if a not in _AGENT_LABELS]
    if unknown:
        raise ArcheryError(f"未知 agent: {unknown}，可用: {list(_AGENT_LABELS)}")

    for agent in agents:
        # 项目级默认 portable（通常进 git 共享），用户级默认本机绝对路径
        is_portable = portable if portable is not None else (scope == "project")
        if is_portable:
            command = "uvx"
        elif uvx:
            command = uvx
        else:
            results.append(f"[{agent}] 跳过：PATH 中未找到 uvx（可加 --portable 用命令名 uvx）")
            continue

        paths: list[tuple[str, Path]] = []
        if agent == "zcode":
            if scope in ("user", "both"):
                paths.append(("user", home / ".zcode" / "cli" / "config.json"))
            if scope in ("project", "both"):
                paths.append(("project", cwd / ".zcode" / "config.json"))
        elif agent == "claude-code":
            if scope in ("user", "both"):
                paths.append(("user", home / ".claude.json"))
            if scope in ("project", "both"):
                paths.append(("project", cwd / ".mcp.json"))
        elif agent == "claude-desktop":
            if scope == "project":
                results.append("[claude-desktop] 跳过：桌面应用无项目级配置")
                continue
            appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
            paths.append(("user", appdata / "Claude" / "claude_desktop_config.json"))
        elif agent == "codex":
            if scope in ("user", "both"):
                paths.append(("user", home / ".codex" / "config.toml"))
            if scope in ("project", "both"):
                paths.append(("project", cwd / ".codex" / "config.toml"))
        elif agent == "cursor":
            if scope in ("user", "both"):
                paths.append(("user", home / ".cursor" / "mcp.json"))
            if scope in ("project", "both"):
                paths.append(("project", cwd / ".cursor" / "mcp.json"))

        for level, path in paths:
            try:
                if agent == "zcode":
                    _json_upsert(path, lambda d: d.setdefault("mcp", {}).setdefault(
                        "servers", {}).__setitem__(
                        "archery", _entry_json(command, timeout_ms=True)))
                elif agent == "codex":
                    _toml_upsert(path, "mcp_servers.archery", [
                        f"command = {_toml_escape(command)}",
                        'args = ["archery-sql-mcp"]',
                    ])
                else:  # claude-code / claude-desktop / cursor：顶层 mcpServers
                    _json_upsert(path, lambda d: d.setdefault(
                        "mcpServers", {}).__setitem__(
                        "archery", _entry_json(command, timeout_ms=False)))
                results.append(f"[{agent}/{level}] 已写入 {path} (command={command})")
            except ArcheryError as e:
                results.append(f"[{agent}/{level}] 失败: {e}")
    return results


def cmd_install_mcp(args: argparse.Namespace) -> int:
    _ensure_utf8_stdout()
    agents = list(_AGENT_LABELS) if args.agents == "all" else [
        a.strip() for a in args.agents.split(",") if a.strip()
    ]
    try:
        for line in install_mcp(agents, args.scope, args.portable):
            print(line)
        print("完成。重启对应 agent 的会话后生效；配置中的 server 名为 archery。")
        return 0
    except ArcheryError as e:
        print(f"错误: {e}")
        return 1


def cmd_redis(args: argparse.Namespace) -> int:
    _ensure_utf8_stdout()
    try:
        cfg = load_profile()
        result = asyncio.run(ArcheryClient(cfg).query_redis(
            args.cmd, db_name=args.db, instance_name=args.instance,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ArcheryError as e:
        print(f"错误: {e}")
        return 1


def run_mcp() -> None:
    """MCP stdio server 模式（惰性 import，CLI 场景无需安装 mcp 包）。"""
    mcp = _new_mcp_server("archery")

    @mcp.tool()
    async def query(
        sql: str,
        db_name: Optional[str] = None,
        instance_name: Optional[str] = None,
        limit: int = 100,
    ) -> str:
        """查询 Archery SQL 平台上的数据库（只读，仅允许 SELECT / WITH）。

        默认连接线上生产库：实例 alisg-haiyun-powerdata-pgsql-prod-01，
        库 creativault_business。可通过 db_name / instance_name 参数切换。
        返回 JSON：{status, columns, rows, row_count, elapsed_ms}。

        典型用途：排查线上业务数据（任务状态、错误信息、日志表等）。
        """
        try:
            cfg = load_profile()
            result = await ArcheryClient(cfg).query(
                sql, db_name=db_name, instance_name=instance_name, limit=limit,
            )
            return json.dumps(result, ensure_ascii=False)
        except ArcheryError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def query_redis(
        command: str,
        db_name: Optional[str] = None,
        instance_name: Optional[str] = None,
    ) -> str:
        """只读查询线上 Redis（默认实例 alisg-haiyun-powerdata-redis-prod-01，db 0）。

        command 是 redis 命令文本，例如 'get xxx'、'ttl xxx'、'type xxx'、
        'hgetall xxx'、'scan 0 match 前缀* count 100'（遍历 key 用 scan，keys 已被平台禁用）。
        写命令与管理命令一律拒绝；平台侧白名单为最终防线。

        ⚠️ 实例拓扑：默认实例是华为云 alisg-haiyun-powerdata-redis-prod-01（部分服务的
        缓存 / celery result backend），不是 creativault 的 Celery broker。creativault
        生产 broker 与批次锁（lock:light_mail_batch:* 等）在阿里云 RDS（Apollo
        redis_host），未登记到 Archery、本工具查不到——队列长度/unacked/redbeat/
        批次锁需在后端 pod 内 uv run python + setting.redis_url 直查。在默认实例上
        查这些 key 为空不代表业务为空。
        """
        try:
            cfg = load_profile()
            result = await ArcheryClient(cfg).query_redis(
                command, db_name=db_name, instance_name=instance_name,
            )
            return json.dumps(result, ensure_ascii=False)
        except ArcheryError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def get_token() -> str:
        """获取 Archery 登录 token（执行/复用登录，返回会话状态与掩码 token）。

        token 仅以掩码显示，完整值缓存于本机 ~/.archery-mcp/session.json。
        """
        try:
            cfg = load_profile()
            info = await SessionManager(cfg).token_info(refresh=False)
            return json.dumps(info, ensure_ascii=False)
        except ArcheryError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def refresh_token() -> str:
        """强制刷新 Archery 登录 token（清空缓存并重新登录，返回新会话状态）。"""
        try:
            cfg = load_profile()
            info = await SessionManager(cfg).token_info(refresh=True)
            return json.dumps(info, ensure_ascii=False)
        except ArcheryError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def list_instances() -> str:
        """列出你在 Archery 平台上有读权限(can_read)的所有数据库实例。

        用于 query 工具切换 instance_name 前确认实例名。
        """
        try:
            cfg = load_profile()
            data = await ArcheryClient(cfg).list_instances()
            return json.dumps({"instances": data}, ensure_ascii=False)
        except ArcheryError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def instance_resources(
        resource_type: str = "schema",
        db_name: Optional[str] = None,
        instance_name: Optional[str] = None,
        schema_name: Optional[str] = None,
        tb_name: Optional[str] = None,
    ) -> str:
        """查询数据库实例的资源结构，便于编写 SQL 前探查。

        resource_type=schema 列出库下 schema；table 列出表（可配 schema_name）；
        column 列出字段（需 schema_name + tb_name）。
        默认实例 alisg-haiyun-powerdata-pgsql-prod-01 / 库 creativault_business。
        """
        try:
            cfg = load_profile()
            data = await ArcheryClient(cfg).instance_resources(
                instance_name=instance_name, db_name=db_name,
                resource_type=resource_type, schema_name=schema_name, tb_name=tb_name,
            )
            return json.dumps({"resources": data}, ensure_ascii=False)
        except ArcheryError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    mcp.run()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="archery-sql-mcp",
        description="Archery SQL 查询工具（无参数启动 = MCP server；子命令 = CLI）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="交互式初始化账号密码配置并验证登录")
    sub.add_parser("check", help="全链路自检：登录 + SELECT 1")
    sub.add_parser("instances", help="列出有读权限的数据库实例")

    p_install = sub.add_parser(
        "install-mcp",
        help="一键注册 archery MCP 到各 agent 客户端（zcode/claude-code/claude-desktop/codex/cursor）",
    )
    p_install.add_argument(
        "--scope", choices=["user", "project", "both"], default="user",
        help="user=各 agent 用户级配置；project=当前目录项目级配置（默认 user）",
    )
    p_install.add_argument(
        "--agents", default="all",
        help="逗号分隔：zcode,claude-code,claude-desktop,codex,cursor 或 all（默认 all）",
    )
    p_install.add_argument(
        "--portable", action="store_true", default=None,
        help="command 用命令名 uvx 而非本机绝对路径（项目级配置进 git 共享时推荐；默认项目级自动 portable）",
    )

    p_query = sub.add_parser("query", help="执行只读 SQL 查询")
    p_query.add_argument("sql", help="SELECT / WITH 开头的 SQL 语句")
    p_query.add_argument("--db", help="覆盖默认库名")
    p_query.add_argument("--instance", help="覆盖默认实例名")
    p_query.add_argument("--limit", type=int, help=f"返回行数上限（默认 100，最大 {MAX_LIMIT}）")

    p_redis = sub.add_parser("redis", help="只读查询线上 Redis（如 get/scan/hgetall）")
    p_redis.add_argument("cmd", help="redis 命令，如 'scan 0 match xxx*'")
    p_redis.add_argument("--db", help="redis 逻辑库编号（默认 0）")
    p_redis.add_argument("--instance", help="覆盖默认 redis 实例名")

    args = parser.parse_args()
    if args.command is None:
        run_mcp()  # 无参数 → MCP stdio 模式
        return
    handlers = {"init": cmd_init, "check": cmd_check, "query": cmd_query,
                "instances": cmd_instances, "install-mcp": cmd_install_mcp,
                "redis": cmd_redis}
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
