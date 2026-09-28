"""
로깅 모듈: 클로드의 판단(근거 포함)과 실제 체결 기록을 CSV로 누적 저장합니다.

- decisions.csv: 트리거가 발생해 클로드에게 판단을 요청할 때마다 한 줄씩 기록.
  reasoning을 그대로 저장하므로, warminsight 콘텐츠("실제 판단 근거") 소재로 바로 활용 가능.
- trades.csv: 실제로 체결(가상)된 매매만 기록. 손익 계산의 원본 데이터.
"""
from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

import config

# trades.csv에는 두 가지 형태의 행이 섞여서 쌓입니다 — 매수(buy: stop_loss_price,
# take_profit_price 포함)와 매도(sell: realized_pnl_krw, r_multiple 등 포함).
# 두 형태의 키가 서로 다르기 때문에, 매번 그 행의 키만으로 헤더를 새로 잡으면
# (예전 방식) 첫 행 이후로는 헤더와 실제 컬럼이 어긋나는 CSV가 만들어집니다.
# 그래서 trades.csv만은 모든 가능한 컬럼을 포함한 고정 헤더를 쓰고, 없는 값은
# 빈 칸으로 채웁니다.
TRADE_CSV_FIELDNAMES = [
    "timestamp", "market", "side", "price", "quantity", "amount_krw", "fee_krw",
    "stop_loss_price", "take_profit_price",
    "realized_pnl_krw", "realized_pnl_pct", "risk_amount_krw", "r_multiple",
    "reason",
]


def _append_csv(path: str, row: Dict, fieldnames: Optional[List[str]] = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    file_exists = os.path.exists(path)
    fieldnames = fieldnames or list(row.keys())
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fieldnames})


def log_decision(market: str, trigger_type: str, decision, timestamp,
                   path: Optional[str] = None) -> None:
    """클로드 판단 1건을 decisions.csv(또는 지정한 path)에 기록합니다.

    path를 넘기면 backtest.py처럼 라이브 로그와 분리된 별도 파일에 기록할 수 있습니다.
    """
    row = {
        "timestamp": str(timestamp),
        "market": market,
        "trigger_type": trigger_type,
        "action": decision.action,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        "stop_loss_pct": decision.stop_loss_pct,
    }
    _append_csv(path or config.DECISIONS_LOG_FILE, row)


def log_trades(trades_log: List[Dict], path: Optional[str] = None) -> None:
    """이번 실행에서 발생한 체결(들)을 trades.csv(또는 지정한 path)에 기록합니다.
    buy/sell 행의 컬럼이 서로 달라서 TRADE_CSV_FIELDNAMES 고정 헤더를 씁니다."""
    for trade in trades_log:
        _append_csv(path or config.TRADES_LOG_FILE, trade, fieldnames=TRADE_CSV_FIELDNAMES)


def log_equity_snapshot(timestamp, equity: float, cash: float, num_positions: int,
                          drawdown: float, halted: bool, path: Optional[str] = None) -> None:
    """실행할 때마다 전체 자산 스냅샷을 남겨서 시간에 따른 수익률 곡선을 그릴 수 있게 합니다."""
    row = {
        "timestamp": str(timestamp),
        "equity_krw": round(equity, 2),
        "cash_krw": round(cash, 2),
        "num_positions": num_positions,
        "drawdown_pct": round(drawdown * 100, 3),
        "halted": halted,
    }
    _append_csv(path or f"{config.LOG_DIR}/equity_curve.csv", row)
