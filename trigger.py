"""
트리거 감지 로직: 매 캔들마다 클로드를 호출하면 비용이 커지므로,
"이벤트가 발생했을 때만" 호출하도록 판단합니다.

트리거 조건 (하나라도 충족 + 거래량 필터 통과 시 신호로 채택):
  1. EMA 골든크로스: ema_fast가 직전 캔들에서는 ema_slow 아래였다가 이번 캔들에서 위로 교차
  2. EMA 데드크로스: 반대 방향 교차
  3. RSI가 과매도(30) 구간을 아래→위로 막 벗어남 (반등 신호 후보)
  4. RSI가 과매수(70) 구간을 위→아래로 막 벗어남 (하락 전환 후보)
  5. (선택, config.ENABLE_STANDALONE_VOLUME_TRIGGER=True일 때만) 위 1~4에 해당하지
     않아도 거래량 자체가 급증했으면 별도 트리거로 채택. "가격이 바닥이든 고점이든
     거래량이 급증한 종목을 주목해야 한다 — 실적 개선/턴어라운드 뉴스가 나올 때
     세력이 매집하며 거래량이 먼저 터지는 경우가 많다"는 통념을 반영한 것입니다.
     다만 이 봇은 뉴스/공시 데이터를 따로 받아오지 않으므로, 실제로 그런 뉴스가
     있는지는 클로드도 알 수 없습니다 — claude_judge.py의 프롬프트에서 이 한계를
     명시하고 있습니다.

거래량 필터: volume_spike가 True인 캔들에서만 신호를 "유효"로 인정합니다.
(거래량 없는 가짜 돌파를 걸러내기 위함 — 지표 스펙에서 합의한 내용)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config


@dataclass
class TriggerEvent:
    market: str
    trigger_type: str  # "ema_golden_cross" | "ema_dead_cross" | "rsi_rebound" | "rsi_reversal"
                        # | "volume_spike_standalone" (config.ENABLE_STANDALONE_VOLUME_TRIGGER=True일 때만)
    timestamp: pd.Timestamp
    close: float
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    volume: float
    volume_avg: float

    def as_dict(self) -> dict:
        return {
            "market": self.market,
            "trigger_type": self.trigger_type,
            "timestamp": str(self.timestamp),
            "close": self.close,
            "ema_fast": round(self.ema_fast, 2),
            "ema_slow": round(self.ema_slow, 2),
            "rsi": round(self.rsi, 2),
            "atr": round(self.atr, 2),
            "volume": self.volume,
            "volume_avg": round(self.volume_avg, 2) if pd.notna(self.volume_avg) else None,
        }


def detect_trigger(market: str, df: pd.DataFrame) -> Optional[TriggerEvent]:
    """가장 최근 완성된 캔들 기준으로 트리거를 감지합니다.

    df는 indicators.compute_all()을 거친 DataFrame이어야 하며,
    최소 EMA_SLOW + 2개 이상의 캔들이 있어야 유효한 판단이 가능합니다.
    """
    ema_fast_col = f"ema_{config.EMA_FAST}"
    ema_slow_col = f"ema_{config.EMA_SLOW}"

    required_cols = {ema_fast_col, ema_slow_col, "rsi", "atr", "volume_spike", "volume_avg"}
    if not required_cols.issubset(df.columns):
        raise ValueError("indicators.compute_all()을 먼저 실행해서 지표를 계산하세요.")

    if len(df) < config.EMA_SLOW + 2:
        return None  # 지표가 안정적으로 계산되기엔 데이터가 부족함

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    if not bool(curr["volume_spike"]):
        return None  # 거래량 필터 미통과 → 신호 채택 안 함

    trigger_type = None

    # EMA 크로스
    prev_diff = prev[ema_fast_col] - prev[ema_slow_col]
    curr_diff = curr[ema_fast_col] - curr[ema_slow_col]
    if prev_diff <= 0 and curr_diff > 0:
        trigger_type = "ema_golden_cross"
    elif prev_diff >= 0 and curr_diff < 0:
        trigger_type = "ema_dead_cross"

    # RSI 임계값 이탈 (EMA 크로스가 없을 때만 RSI 신호 체크 — 한 캔들에 신호 1개 원칙)
    if trigger_type is None:
        if prev["rsi"] < config.RSI_OVERSOLD <= curr["rsi"]:
            trigger_type = "rsi_rebound"
        elif prev["rsi"] > config.RSI_OVERBOUGHT >= curr["rsi"]:
            trigger_type = "rsi_reversal"

    # 선택적 트리거: EMA/RSI 신호가 없어도 거래량 급증 자체를 트리거로 채택
    if trigger_type is None and config.ENABLE_STANDALONE_VOLUME_TRIGGER:
        trigger_type = "volume_spike_standalone"

    if trigger_type is None:
        return None

    return TriggerEvent(
        market=market,
        trigger_type=trigger_type,
        timestamp=curr["timestamp"],
        close=float(curr["close"]),
        ema_fast=float(curr[ema_fast_col]),
        ema_slow=float(curr[ema_slow_col]),
        rsi=float(curr["rsi"]),
        atr=float(curr["atr"]),
        volume=float(curr["volume"]),
        volume_avg=float(curr["volume_avg"]) if pd.notna(curr["volume_avg"]) else float("nan"),
    )
