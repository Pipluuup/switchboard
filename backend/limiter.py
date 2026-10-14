"""客户端 Key 限流：滑动窗口 RPM + 每日 token 配额。"""
from __future__ import annotations

import threading
import time
from collections import deque, defaultdict


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._rpm: dict[str, deque[float]] = defaultdict(deque)
        self._daily: dict[tuple[str, str], int] = {}

    def _day_key(self) -> str:
        return time.strftime("%Y-%m-%d")

    def check(self, key_id: str, rpm: int, daily_tokens: int, used_tokens: int) -> tuple[bool, str]:
        now = time.time()
        with self._lock:
            if rpm and rpm > 0:
                q = self._rpm[key_id]
                while q and now - q[0] > 60:
                    q.popleft()
                if len(q) >= rpm:
                    retry = int(60 - (now - q[0])) + 1
                    return False, f"超出速率限制 {rpm}/分钟，请 {retry} 秒后重试"
                q.append(now)

            if daily_tokens and daily_tokens > 0:
                if used_tokens >= daily_tokens:
                    return False, f"已超出每日 token 配额 {daily_tokens}"
            return True, ""

    def used_tokens_today(self, key_name: str) -> int:
        """从数据库查今日已用 token（由调用方注入 db 以避免循环导入）。"""
        from .database import db  # noqa: PLC0415

        return int(db.today_tokens(key_name))

    def reset(self, key_id: str = "") -> None:
        with self._lock:
            if key_id:
                self._rpm.pop(key_id, None)
            else:
                self._rpm.clear()


limiter = RateLimiter()
