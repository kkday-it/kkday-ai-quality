"""LLM 批次任務的自適應併發原語（AIMD governor）。

從 `app.judge.prejudge_batch` 提取為共用層：初判歸因與售後根因 Prompt 跑批是兩條獨立的批次線，
卻面對同一個現實——**沒有任何供應商公布「併發數」這個維度**。OpenAI / Gemini / 火山方舟都只公布
RPM / TPM / RPD，且綁「帳號 tier」而非模型，所以「這顆 model 該開幾條併發」不存在可查的權威數值，
只能執行期自己探：樂觀起於一個保守的靜態上限，撞到 429 才降、降完再慢慢爬回去。

純 `threading` 實作、零外部依賴，兩條線都是 ThreadPoolExecutor，可直接共用。
"""

from __future__ import annotations

import threading
import time


class ConcurrencyGovernor:
    """AIMD 自適應併發：樂觀起於 ceiling，遇 429 失敗乘性收縮、清空後加性回升，收斂到 API 可持續的最大併發。

    ceiling＝該 model 靜態上限（呼叫端以 config 查表 ∩ 製程級硬天花板算出），永不超過；只在其下自適應。
    信號＝item 因 429 **失敗**（SDK 內建退避 + 有界重試全耗盡仍 429＝真過載）；SDK 能吸收的暫時 429
    （item 仍成功）不觸發——恰好在「429 開始造成失敗」時降速，而不是被瞬時抖動嚇到。

    thread-safe（worker 併發呼叫 `on_429`；`current` 由提交執行緒單獨呼叫）。
    """

    def __init__(
        self,
        ceiling: int,
        *,
        floor: int = 2,
        backoff: float = 0.5,
        probe_interval_s: float = 3.0,
        cooldown_s: float = 5.0,
    ) -> None:
        self._ceiling = max(1, ceiling)
        self._floor = max(1, min(floor, self._ceiling))
        self._backoff = backoff
        self._probe_interval = probe_interval_s
        self._cooldown = cooldown_s
        self._limit = self._ceiling  # 樂觀起步（config 值已是保守估計）
        self._last_429 = 0.0
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def current(self) -> int:
        """當前允許併發（供提交迴圈背壓）；順帶時間驅動加性回升——僅提交執行緒呼叫（單執行緒讀）。"""
        with self._lock:
            now = time.monotonic()
            if self._limit < self._ceiling and (now - self._last_429) >= self._probe_interval:
                self._limit = min(self._ceiling, self._limit + 1)
                self._last_429 = now  # 重置探測時鐘：每 interval 回升一階（漸進不暴衝）
            return self._limit

    def on_429(self) -> None:
        """worker 遇 429 失敗時呼叫：乘性收縮（cooldown 內只反應一次，避免一波 429 過度收縮）。"""
        with self._lock:
            now = time.monotonic()
            self._last_429 = now
            if now < self._cooldown_until:
                return
            self._limit = max(self._floor, int(self._limit * self._backoff))
            self._cooldown_until = now + self._cooldown


def is_rate_limit(exc: BaseException) -> bool:
    """例外是否為 OpenAI 429 RateLimitError（自適應併發的收縮信號）；SDK 未安裝時回 False。"""
    try:
        from openai import RateLimitError
    except Exception:  # noqa: BLE001
        return False
    return isinstance(exc, RateLimitError)
