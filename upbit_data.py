"""
업비트 공개(Public) API에서 캔들 데이터를 가져오는 모듈.

업비트의 시세 조회 API는 인증(API 키) 없이 접근 가능합니다.
실제 계좌 개설 없이도 실시간/과거 캔들 데이터를 그대로 받아올 수 있습니다.
(주문/자산조회 등 Private API만 인증이 필요합니다 — 이 프로젝트는 사용하지 않습니다.)

주의: 이 샌드박스 환경에서는 외부 네트워크 정책상 api.upbit.com에 직접 접근이
막혀 있어 실제 호출 테스트는 여기서 할 수 없습니다. 로인님의 로컬 환경이나
GitHub Actions 러너에서는 정상 동작해야 합니다 (업비트 공개 API는 별도 화이트리스트
없이 누구나 호출 가능).
"""
from __future__ import annotations

import time
from typing import List, Optional

import pandas as pd
import requests

UPBIT_BASE_URL = "https://api.upbit.com/v1"


def fetch_candles(market: str, unit_minutes: int, count: int) -> pd.DataFrame:
    """
    업비트 분봉 캔들을 가져와 시간 오름차순 DataFrame으로 반환합니다.

    Args:
        market: 예) "KRW-BTC"
        unit_minutes: 캔들 단위(분). 업비트는 1/3/5/15/10/30/60/240 지원.
        count: 가져올 캔들 개수 (최대 200/요청)

    Returns:
        columns: [timestamp, open, high, low, close, volume]
        (오름차순: 과거 → 최신)
    """
    if count > 200:
        raise ValueError("업비트 API는 1회 요청당 최대 200개 캔들만 지원합니다. "
                          "더 긴 이력이 필요하면 to 파라미터로 페이지네이션하세요.")

    url = f"{UPBIT_BASE_URL}/candles/minutes/{unit_minutes}"
    params = {"market": market, "count": count}

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    raw = resp.json()

    if not raw:
        raise RuntimeError(f"{market}: 업비트에서 빈 응답을 받았습니다.")

    df = pd.DataFrame(raw)
    df = df.rename(columns={
        "candle_date_time_kst": "timestamp",
        "opening_price": "open",
        "high_price": "high",
        "low_price": "low",
        "trade_price": "close",
        "candle_acc_trade_volume": "volume",
    })
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    # 업비트는 최신순으로 내려주므로 오름차순으로 뒤집음
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def fetch_candles_history(market: str, unit_minutes: int, lookback_days: int,
                            request_interval_sec: float = 0.15) -> pd.DataFrame:
    """지정한 기간(lookback_days)만큼 과거 캔들을 여러 번 나눠 요청해 이어붙입니다.

    업비트 공개 API는 1회 요청당 최대 200개 캔들만 주므로, `to` 파라미터로
    "이 시각 이전 캔들을 달라"고 반복 요청하며 과거로 페이지네이션합니다.
    백테스트(backtest.py)에서 수개월치 데이터를 모을 때 사용합니다.
    """
    candles_needed = int((lookback_days * 24 * 60) / unit_minutes)
    chunks: List[pd.DataFrame] = []
    to_param: Optional[str] = None

    while sum(len(c) for c in chunks) < candles_needed:
        remaining = candles_needed - sum(len(c) for c in chunks)
        count = min(200, remaining)

        url = f"{UPBIT_BASE_URL}/candles/minutes/{unit_minutes}"
        params = {"market": market, "count": count}
        if to_param:
            params["to"] = to_param

        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        if not raw:
            break  # 더 이상 과거 데이터가 없음

        chunk = pd.DataFrame(raw).rename(columns={
            "candle_date_time_kst": "timestamp",
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
        })[["timestamp", "open", "high", "low", "close", "volume"]]
        chunks.append(chunk)

        # 다음 요청은 이번 청크에서 가장 오래된 캔들 "이전"부터
        oldest = raw[-1]["candle_date_time_utc"]
        to_param = oldest
        time.sleep(request_interval_sec)

    if not chunks:
        raise RuntimeError(f"{market}: 과거 캔들을 하나도 받지 못했습니다.")

    df = pd.concat(chunks, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def fetch_all(coin_universe: List[str], unit_minutes: int, count: int,
               request_interval_sec: float = 0.15) -> dict[str, pd.DataFrame]:
    """코인 유니버스 전체에 대해 캔들을 가져옵니다.

    업비트 공개 API는 초당 요청 수 제한(레이트리밋)이 있으므로
    호출 사이에 짧은 대기(request_interval_sec)를 둡니다.
    """
    result = {}
    for market in coin_universe:
        result[market] = fetch_candles(market, unit_minutes, count)
        time.sleep(request_interval_sec)
    return result
