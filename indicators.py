"""
지표 계산 모듈: EMA, RSI, ATR, 거래량 급증 여부.
입력은 upbit_data.fetch_candles()가 반환하는 DataFrame(오름차순) 형식을 기대합니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def add_ema(df: pd.DataFrame, fast: int = config.EMA_FAST, slow: int = config.EMA_SLOW) -> pd.DataFrame:
    df = df.copy()
    df[f"ema_{fast}"] = df["close"].ewm(span=fast, adjust=False).mean()
    df[f"ema_{slow}"] = df["close"].ewm(span=slow, adjust=False).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = config.RSI_PERIOD) -> pd.DataFrame:
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # 손실이 0인 구간(평균 손실 0)은 RSI 100으로 처리
    rsi = rsi.fillna(100)
    df["rsi"] = rsi
    return df


def add_atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.DataFrame:
    df = df.copy()
    high_low = df["high"] - df["low"]
    high_close_prev = (df["high"] - df["close"].shift(1)).abs()
    low_close_prev = (df["low"] - df["close"].shift(1)).abs()

    true_range = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    df["atr"] = true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return df


def add_volume_signal(df: pd.DataFrame, avg_period: int = config.VOLUME_AVG_PERIOD,
                        spike_multiplier: float = config.VOLUME_SPIKE_MULTIPLIER) -> pd.DataFrame:
    df = df.copy()
    df["volume_avg"] = df["volume"].rolling(window=avg_period, min_periods=avg_period).mean()
    df["volume_spike"] = df["volume"] > (df["volume_avg"] * spike_multiplier)
    return df


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """지표를 모두 계산해서 붙인 DataFrame을 반환합니다."""
    df = add_ema(df)
    df = add_rsi(df)
    df = add_atr(df)
    df = add_volume_signal(df)
    return df
