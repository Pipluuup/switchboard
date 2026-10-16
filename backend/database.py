"""请求日志与统计（SQLite）。"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

from .paths import data_dir

DB_PATH = os.path.join(data_dir(), "proxy.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    client_key TEXT DEFAULT '',
    client_name TEXT DEFAULT '',
    upstream_id TEXT DEFAULT '',
    upstream_name TEXT DEFAULT '',
    in_protocol TEXT DEFAULT '',
    out_protocol TEXT DEFAULT '',
    request_model TEXT DEFAULT '',
    actual_model TEXT DEFAULT '',
    stream INTEGER DEFAULT 0,
    status INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 1,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    request_body TEXT DEFAULT '',
    response_body TEXT DEFAULT '',
    caveman_saved INTEGER DEFAULT 0,
    voice_injected INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON request_logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_ok ON request_logs(ok);
CREATE INDEX IF NOT EXISTS idx_logs_up ON request_logs(upstream_name);
"""

# 用量滚动窗口：(标识, 展示名, 秒数)
# 5 小时 / 7 天 / 30 天与 OpenCode Go 的额度周期（5h=月 20%、周=50%、月=100%）对齐
USAGE_WINDOWS: list[tuple[str, str, int]] = [
    ("5h", "近 5 小时", 5 * 3600),
    ("7d", "近 7 天", 7 * 86400),
    ("30d", "近 30 天", 30 * 86400),
]


class LogDB:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        # 旧库迁移：补齐后加的详情列与优化统计列
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(request_logs)").fetchall()}
        for col in ("request_body", "response_body"):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE request_logs ADD COLUMN {col} TEXT DEFAULT ''")
        for col in ("caveman_saved", "voice_injected"):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE request_logs ADD COLUMN {col} INTEGER DEFAULT 0")
        self._conn.commit()

    def add(self, **kw: Any) -> None:
        cols = [
            "ts", "client_key", "client_name", "upstream_id", "upstream_name",
            "in_protocol", "out_protocol", "request_model", "actual_model",
            "stream", "status", "ok", "input_tokens", "output_tokens",
            "latency_ms", "error", "request_body", "response_body",
            "caveman_saved", "voice_injected",
        ]
        kw.setdefault("ts", time.time())
        vals = [kw.get(c, 0 if c in {"stream", "status", "ok", "input_tokens",
                                     "output_tokens", "latency_ms",
                                     "caveman_saved", "voice_injected"} else "") for c in cols]
        with self._lock:
            self._conn.execute(
                f"INSERT INTO request_logs ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
                vals,
            )
            self._conn.commit()

    # 列表页用的精简列：不含请求/响应详情大字段，避免一页 50 条拖几 MB
    _LIST_COLS = (
        "id", "ts", "client_name", "upstream_name", "in_protocol", "out_protocol",
        "request_model", "actual_model", "stream", "status", "ok",
        "input_tokens", "output_tokens", "latency_ms", "error",
        "caveman_saved", "voice_injected",
    )

    def query(self, limit: int = 100, offset: int = 0, ok: int | None = None,
              keyword: str = "") -> list[dict]:
        sql = f"SELECT {','.join(self._LIST_COLS)} FROM request_logs WHERE 1=1"
        args: list[Any] = []
        if ok is not None:
            sql += " AND ok=?"
            args.append(ok)
        if keyword:
            sql += " AND (request_model LIKE ? OR upstream_name LIKE ? OR client_name LIKE ? OR error LIKE ?)"
            args.extend([f"%{keyword}%"] * 4)
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get(self, log_id: int) -> dict | None:
        """单条完整日志（含请求/响应详情）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM request_logs WHERE id=?", (log_id,)
            ).fetchone()
        return dict(row) if row else None

    def count(self, ok: int | None = None) -> int:
        sql = "SELECT COUNT(*) FROM request_logs"
        args: list[Any] = []
        if ok is not None:
            sql += " WHERE ok=?"
            args.append(ok)
        with self._lock:
            return int(self._conn.execute(sql, args).fetchone()[0])

    def stats_overview(self) -> dict:
        with self._lock:
            c = self._conn.cursor()
            total = c.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0]
            failed = c.execute("SELECT COUNT(*) FROM request_logs WHERE ok=0").fetchone()[0]
            tin = c.execute("SELECT COALESCE(SUM(input_tokens),0) FROM request_logs").fetchone()[0]
            tout = c.execute("SELECT COALESCE(SUM(output_tokens),0) FROM request_logs").fetchone()[0]
            avg = c.execute("SELECT COALESCE(AVG(latency_ms),0) FROM request_logs WHERE ok=1").fetchone()[0]
            day = c.execute(
                "SELECT COUNT(*) FROM request_logs WHERE ts >= ?", (time.time() - 86400,)
            ).fetchone()[0]
            by_up = c.execute(
                "SELECT upstream_name, COUNT(*) n, SUM(ok) ok_n, "
                "COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout, "
                "COALESCE(AVG(latency_ms),0) avg_ms "
                "FROM request_logs GROUP BY upstream_name ORDER BY n DESC"
            ).fetchall()
            by_model = c.execute(
                "SELECT request_model, COUNT(*) n, COALESCE(SUM(input_tokens),0) tin, "
                "COALESCE(SUM(output_tokens),0) tout FROM request_logs "
                "GROUP BY request_model ORDER BY n DESC LIMIT 10"
            ).fetchall()
        return {
            "total_requests": total,
            "failed_requests": failed,
            "success_rate": round((total - failed) / total * 100, 2) if total else 100.0,
            "total_input_tokens": tin,
            "total_output_tokens": tout,
            "avg_latency_ms": round(avg, 1),
            "requests_24h": day,
            "by_upstream": [dict(r) for r in by_up],
            "by_model": [dict(r) for r in by_model],
        }

    def usage_windows(self, top: int = 20) -> list[dict]:
        """按 5 小时 / 7 天 / 30 天三个滚动窗口聚合用量。

        与 stats_overview 的「历史累计」口径不同：这里只统计窗口内的数据，
        用来观察**近期消耗速度**（例如对照 OpenCode Go 的 5 小时 / 每周 /
        每月额度）。注意窗口长度受日志保留天数限制——若保留 7 天，30 天窗口
        实际只能看到 7 天的数据，因此返回值里带上 retention 供前端提示。
        """
        now = time.time()
        out: list[dict] = []
        with self._lock:
            c = self._conn.cursor()
            for key, label, secs in USAGE_WINDOWS:
                since = now - secs
                row = c.execute(
                    "SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok_n, "
                    "COALESCE(SUM(input_tokens),0) tin, "
                    "COALESCE(SUM(output_tokens),0) tout, "
                    "COALESCE(AVG(CASE WHEN ok=1 THEN latency_ms END),0) avg_ms, "
                    "COALESCE(MIN(ts),0) first_ts, COALESCE(MAX(ts),0) last_ts "
                    "FROM request_logs WHERE ts >= ?",
                    (since,),
                ).fetchone()
                # 按实际打到上游的模型名聚合（映射后的名字才是消耗上游额度的那个）
                by_model = c.execute(
                    "SELECT CASE WHEN actual_model <> '' THEN actual_model "
                    "ELSE request_model END AS model, "
                    "COUNT(*) n, COALESCE(SUM(input_tokens),0) tin, "
                    "COALESCE(SUM(output_tokens),0) tout "
                    "FROM request_logs WHERE ts >= ? GROUP BY model "
                    "ORDER BY (COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0)) DESC "
                    "LIMIT ?",
                    (since, top),
                ).fetchall()
                by_up = c.execute(
                    "SELECT upstream_name, COUNT(*) n, "
                    "COALESCE(SUM(input_tokens),0) tin, "
                    "COALESCE(SUM(output_tokens),0) tout "
                    "FROM request_logs WHERE ts >= ? GROUP BY upstream_name "
                    "ORDER BY (COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0)) DESC",
                    (since,),
                ).fetchall()

                n = int(row["n"] or 0)
                ok_n = int(row["ok_n"] or 0)
                tin = int(row["tin"] or 0)
                tout = int(row["tout"] or 0)
                out.append({
                    "key": key,
                    "label": label,
                    "seconds": secs,
                    "since": since,
                    "requests": n,
                    "ok_requests": ok_n,
                    "failed_requests": n - ok_n,
                    "success_rate": round(ok_n / n * 100, 2) if n else 100.0,
                    "input_tokens": tin,
                    "output_tokens": tout,
                    "total_tokens": tin + tout,
                    "avg_latency_ms": round(float(row["avg_ms"] or 0), 1),
                    "first_ts": float(row["first_ts"] or 0),
                    "last_ts": float(row["last_ts"] or 0),
                    "by_model": [dict(r) for r in by_model],
                    "by_upstream": [dict(r) for r in by_up],
                })
        return out

    def timeline(self, hours: int = 24) -> list[dict]:
        """按小时聚合请求量与成功率。"""
        since = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                "SELECT CAST((ts - ?) / 3600 AS INTEGER) AS bucket, COUNT(*) n, SUM(ok) ok_n, "
                "COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout "
                "FROM request_logs WHERE ts >= ? GROUP BY bucket ORDER BY bucket",
                (since, since),
            ).fetchall()
        return [dict(r) for r in rows]

    def today_tokens(self, client_name: str) -> int:
        since = time.time() - (time.time() % 86400) - 8 * 3600  # 按本地时区近似
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(input_tokens + output_tokens),0) FROM request_logs "
                "WHERE client_name=? AND ts >= ?",
                (client_name, since),
            ).fetchone()
        return int(row[0])

    def cleanup(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM request_logs WHERE ts < ?", (cutoff,))
            self._conn.commit()
        return cur.rowcount

    def clear_all(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM request_logs")
            self._conn.commit()
        return cur.rowcount


db = LogDB()
