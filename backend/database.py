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
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    request_body TEXT DEFAULT '',
    response_body TEXT DEFAULT '',
    caveman_saved INTEGER DEFAULT 0,
    voice_injected INTEGER DEFAULT 0,
    caveman_stats TEXT DEFAULT '',
    failover_trail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON request_logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_ok ON request_logs(ok);
CREATE INDEX IF NOT EXISTS idx_logs_up ON request_logs(upstream_name);

-- 日聚合表：把明细按「天 × 上游 × 实际模型」压成一行。
-- 明细表受 log_retention_days 清理（默认 7 天），30d 用量窗口因此是残的；
-- 这张表不受保留期影响，窗口长于保留期时改查它。
CREATE TABLE IF NOT EXISTS usage_daily_rollups (
    date TEXT NOT NULL,            -- YYYY-MM-DD（本地时区）
    upstream_id TEXT DEFAULT '',
    upstream_name TEXT DEFAULT '',
    actual_model TEXT DEFAULT '',  -- 映射后的实际模型（消耗额度的是它）
    request_model TEXT DEFAULT '', -- 客户端请求的模型（与实际模型分开才看得见转换关系）
    request_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    avg_latency_ms INTEGER DEFAULT 0,
    PRIMARY KEY (date, upstream_id, actual_model)
);
CREATE INDEX IF NOT EXISTS idx_rollup_date ON usage_daily_rollups(date);
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
        if "caveman_stats" not in cols:
            self._conn.execute("ALTER TABLE request_logs ADD COLUMN caveman_stats TEXT DEFAULT ''")
        for col in ("cache_read_tokens", "cache_write_tokens"):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE request_logs ADD COLUMN {col} INTEGER DEFAULT 0")
        if "failover_trail" not in cols:
            self._conn.execute("ALTER TABLE request_logs ADD COLUMN failover_trail TEXT DEFAULT ''")
        self._conn.commit()

    def add(self, **kw: Any) -> None:
        cols = [
            "ts", "client_key", "client_name", "upstream_id", "upstream_name",
            "in_protocol", "out_protocol", "request_model", "actual_model",
            "stream", "status", "ok", "input_tokens", "output_tokens",
            "cache_read_tokens", "cache_write_tokens",
            "latency_ms", "error", "request_body", "response_body",
            "caveman_saved", "voice_injected", "caveman_stats", "failover_trail",
        ]
        kw.setdefault("ts", time.time())
        vals = [kw.get(c, 0 if c in {"stream", "status", "ok", "input_tokens",
                                     "output_tokens", "cache_read_tokens",
                                     "cache_write_tokens", "latency_ms",
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
        "input_tokens", "output_tokens", "cache_read_tokens", "latency_ms", "error",
        "caveman_saved", "voice_injected", "caveman_stats", "failover_trail",
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
            cread = c.execute(
                "SELECT COALESCE(SUM(cache_read_tokens),0) FROM request_logs").fetchone()[0]
            cwrite = c.execute(
                "SELECT COALESCE(SUM(cache_write_tokens),0) FROM request_logs").fetchone()[0]
            by_up = c.execute(
                "SELECT upstream_name, COUNT(*) n, SUM(ok) ok_n, "
                "COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout, "
                "COALESCE(SUM(cache_read_tokens),0) cin, "
                "COALESCE(SUM(cache_write_tokens),0) cwt, "
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
            "total_cache_read_tokens": int(cread),
            "total_cache_write_tokens": int(cwrite),
            "avg_latency_ms": round(avg, 1),
            "requests_24h": day,
            "by_upstream": [dict(r) for r in by_up],
            "by_model": [dict(r) for r in by_model],
        }

    def usage_windows(self, top: int = 20, retention_days: int = 0) -> list[dict]:
        """按 5 小时 / 7 天 / 30 天三个滚动窗口聚合用量。

        与 stats_overview 的「历史累计」口径不同：这里只统计窗口内的数据，
        用来观察**近期消耗速度**（例如对照 OpenCode Go 的 5 小时 / 每周 /
        每月额度）。

        **数据源按窗口长度选**：
        - 窗口 ≤ 日志保留期 → 查明细表 `request_logs`（精度高，有精确延迟）
        - 窗口 > 日志保留期 → 查日聚合表 `usage_daily_rollups`（不受保留期影响）

        否则保留 7 天时 30 天窗口只能看到 7 天的数据——那正是已知问题 #5。
        `retention_days` 由调用方传入（`LogDB` 不读 config，保持可独立测试）；
        0 表示永久保留，此时一律查明细。
        """
        now = time.time()
        out: list[dict] = []
        with self._lock:
            c = self._conn.cursor()
            for key, label, secs in USAGE_WINDOWS:
                since = now - secs
                # 窗口超出保留期 → 改查日聚合表
                use_rollup = bool(retention_days) and secs > retention_days * 86400
                if use_rollup:
                    agg = self._rollup_range_locked(since, top)
                    n = agg["n"]
                    ok_n = agg["ok_n"]
                    tin = agg["tin"]
                    tout = agg["tout"]
                    cin = agg["cin"]
                    cwt = agg["cwt"]
                    avg_ms = agg["avg_ms"]
                    first_ts = 0.0
                    last_ts = 0.0
                    by_model = agg["by_model"]
                    by_up = agg["by_upstream"]
                else:
                    row = c.execute(
                        "SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok_n, "
                        "COALESCE(SUM(input_tokens),0) tin, "
                        "COALESCE(SUM(output_tokens),0) tout, "
                        "COALESCE(SUM(cache_read_tokens),0) cin, "
                        "COALESCE(SUM(cache_write_tokens),0) cwt, "
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
                        "COALESCE(SUM(output_tokens),0) tout, "
                        "COALESCE(SUM(cache_read_tokens),0) cin, "
                        "COALESCE(SUM(cache_write_tokens),0) cwt "
                        "FROM request_logs WHERE ts >= ? GROUP BY upstream_name "
                        "ORDER BY (COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0) + "
                        "COALESCE(SUM(cache_read_tokens),0)) DESC",
                        (since,),
                    ).fetchall()
                    n = int(row["n"] or 0)
                    ok_n = int(row["ok_n"] or 0)
                    tin = int(row["tin"] or 0)
                    tout = int(row["tout"] or 0)
                    cin = int(row["cin"] or 0)
                    cwt = int(row["cwt"] or 0)
                    avg_ms = float(row["avg_ms"] or 0)
                    first_ts = float(row["first_ts"] or 0)
                    last_ts = float(row["last_ts"] or 0)
                    by_model = [dict(r) for r in by_model]
                    by_up = [dict(r) for r in by_up]

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
                    "cache_read_tokens": cin,
                    "cache_write_tokens": cwt,
                    "total_tokens": tin + tout,
                    "avg_latency_ms": round(float(avg_ms), 1),
                    "first_ts": first_ts,
                    "last_ts": last_ts,
                    "by_model": by_model,
                    "by_upstream": by_up,
                    # 数据来源标记：rollup=日聚合表，detail=明细表
                    "source": "rollup" if use_rollup else "detail",
                })
        return out

    def timeline(self, hours: int = 24) -> list[dict]:
        """按小时聚合请求量与成功率。"""
        since = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                "SELECT CAST((ts - ?) / 3600 AS INTEGER) AS bucket, COUNT(*) n, SUM(ok) ok_n, "
                "COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout, "
                "COALESCE(SUM(cache_read_tokens),0) cin, COALESCE(SUM(cache_write_tokens),0) cwt "
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

    # ---------------- 日聚合 ----------------
    def rollup_days(self, days: int = 2) -> int:
        """把最近 N 天的明细重算进日聚合表。

        **先删该日再重算**，不是 UPSERT 累加——累加在重跑时会双计，
        重算是幂等的。必须赶在 `cleanup()` 之前调用：明细一旦被删，
        那部分数据就永远进不了聚合表。

        返回写入的行数。
        """
        now = time.time()
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        yesterday = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
        written = 0
        with self._lock:
            for day in (today, yesterday):
                self._conn.execute("DELETE FROM usage_daily_rollups WHERE date = ?", (day,))
                cur = self._conn.execute(
                    """
                    INSERT INTO usage_daily_rollups
                        (date, upstream_id, upstream_name, actual_model, request_model,
                         request_count, success_count, input_tokens, output_tokens,
                         cache_read_tokens, cache_write_tokens, avg_latency_ms)
                    SELECT date(ts, 'unixepoch', 'localtime'),
                           upstream_id, upstream_name,
                           CASE WHEN actual_model <> '' THEN actual_model ELSE request_model END,
                           request_model,
                           COUNT(*), SUM(ok),
                           COALESCE(SUM(input_tokens), 0),
                           COALESCE(SUM(output_tokens), 0),
                           COALESCE(SUM(cache_read_tokens), 0),
                           COALESCE(SUM(cache_write_tokens), 0),
                           COALESCE(AVG(CASE WHEN ok = 1 THEN latency_ms ELSE 0 END), 0)
                    FROM request_logs
                    WHERE date(ts, 'unixepoch', 'localtime') = ?
                    GROUP BY 1, 2, 3, 4, 5
                    """,
                    (day,),
                )
                written += cur.rowcount or 0
            self._conn.commit()
        return written

    def rollup_range(self, since: float, top: int = 20) -> dict[str, Any]:
        """从日聚合表统计「since 之后」的用量，供超长窗口使用。

        返回结构与明细查询对齐：总量 + by_model + by_upstream。
        """
        with self._lock:
            return self._rollup_range_locked(since, top)

    def _rollup_range_locked(self, since: float, top: int = 20) -> dict[str, Any]:
        """`rollup_range` 的无锁版本——调用方已持有 `self._lock` 时用。

        `self._lock` 是非重入的 threading.Lock，锁内再取锁会死锁。
        """
        since_day = time.strftime("%Y-%m-%d", time.localtime(since))
        if True:
            c = self._conn.cursor()
            row = c.execute(
                "SELECT COALESCE(SUM(request_count),0) n, "
                "COALESCE(SUM(success_count),0) ok_n, "
                "COALESCE(SUM(input_tokens),0) tin, "
                "COALESCE(SUM(output_tokens),0) tout, "
                "COALESCE(SUM(cache_read_tokens),0) cin, "
                "COALESCE(SUM(cache_write_tokens),0) cwt, "
                "COALESCE(AVG(avg_latency_ms),0) avg_ms "
                "FROM usage_daily_rollups WHERE date >= ?",
                (since_day,),
            ).fetchone()
            by_model = c.execute(
                "SELECT actual_model AS model, SUM(request_count) n, "
                "COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout "
                "FROM usage_daily_rollups WHERE date >= ? GROUP BY model "
                "ORDER BY (COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0)) DESC "
                "LIMIT ?",
                (since_day, top),
            ).fetchall()
            by_up = c.execute(
                "SELECT upstream_name, SUM(request_count) n, "
                "COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout, "
                "COALESCE(SUM(cache_read_tokens),0) cin, "
                "COALESCE(SUM(cache_write_tokens),0) cwt "
                "FROM usage_daily_rollups WHERE date >= ? GROUP BY upstream_name "
                "ORDER BY (COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0) + "
                "COALESCE(SUM(cache_read_tokens),0)) DESC",
                (since_day,),
            ).fetchall()
        return {
            "n": int(row["n"] or 0),
            "ok_n": int(row["ok_n"] or 0),
            "tin": int(row["tin"] or 0),
            "tout": int(row["tout"] or 0),
            "cin": int(row["cin"] or 0),
            "cwt": int(row["cwt"] or 0),
            "avg_ms": float(row["avg_ms"] or 0),
            "by_model": [dict(r) for r in by_model],
            "by_upstream": [dict(r) for r in by_up],
        }


db = LogDB()
