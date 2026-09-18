"""
가상 포트폴리오 시뮬레이션.

- 실제 계좌/거래소 연동 없이, 실시간(또는 최신 캔들) 가격을 기준으로
  "만약 진짜 주문을 넣었다면" 체결을 가정합니다.
- 수수료(TAKER_FEE_PCT)와 슬리피지(SLIPPAGE_PCT)를 가정해 실제 체결과의
  괴리를 최대한 줄입니다.
- 클로드의 판단과 무관하게 코드 레벨에서 하드스탑/서킷브레이커를 강제합니다.
- main.py가 스케줄(cron/GitHub Actions)로 반복 실행되는 걸 전제로,
  상태를 config.PORTFOLIO_LOG_FILE(JSON)에 저장/로드해 실행 간 연속성을 유지합니다.

현물(spot) 거래소인 업비트 특성상 공매도는 지원하지 않는다고 가정합니다.
따라서 "sell" 판단은 보유 중인 포지션을 청산하는 의미로만 사용됩니다
(보유 물량이 없는데 sell 신호가 오면 매매하지 않고 신호만 기록합니다).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Optional

import config


@dataclass
class Position:
    market: str
    quantity: float
    entry_price: float
    stop_loss_price: float
    entry_timestamp: str


@dataclass
class Portfolio:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    peak_equity: float = 0.0
    halted: bool = False
    initial_capital: float = config.INITIAL_CAPITAL_KRW

    def __post_init__(self):
        if self.peak_equity == 0.0:
            self.peak_equity = self.initial_capital

    # ── 상태 저장/로드 ──────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "positions": {m: asdict(p) for m, p in self.positions.items()},
            "peak_equity": self.peak_equity,
            "halted": self.halted,
            "initial_capital": self.initial_capital,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        positions = {m: Position(**p) for m, p in data.get("positions", {}).items()}
        return cls(
            cash=data["cash"],
            positions=positions,
            peak_equity=data.get("peak_equity", data["cash"]),
            halted=data.get("halted", False),
            initial_capital=data.get("initial_capital", config.INITIAL_CAPITAL_KRW),
        )

    @classmethod
    def load_or_create(cls, path: str = config.PORTFOLIO_LOG_FILE) -> "Portfolio":
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        return cls(cash=config.INITIAL_CAPITAL_KRW)

    def save(self, path: str = config.PORTFOLIO_LOG_FILE) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    # ── 평가 ────────────────────────────────────────────────────
    def equity(self, current_prices: Dict[str, float]) -> float:
        market_value = sum(
            pos.quantity * current_prices.get(m, pos.entry_price)
            for m, pos in self.positions.items()
        )
        return self.cash + market_value

    def current_drawdown(self, current_prices: Dict[str, float]) -> float:
        eq = self.equity(current_prices)
        self.peak_equity = max(self.peak_equity, eq)
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - eq) / self.peak_equity)

    # ── 매매 실행 ───────────────────────────────────────────────
    def _fill_price(self, price: float, side: str) -> float:
        """슬리피지 적용: 매수는 불리하게(더 비싸게), 매도는 불리하게(더 싸게) 체결된다고 가정."""
        if side == "buy":
            return price * (1 + config.SLIPPAGE_PCT)
        return price * (1 - config.SLIPPAGE_PCT)

    def open_position(self, market: str, price: float, atr: float, timestamp,
                        trades_log: list) -> Optional[Position]:
        if self.halted:
            return None
        if market in self.positions:
            return None  # 이미 보유 중이면 추가 매수하지 않음 (MVP 단순화)

        allocation = self.initial_capital * config.MAX_POSITION_WEIGHT
        allocation = min(allocation, self.cash)
        if allocation <= 0:
            return None

        fill_price = self._fill_price(price, "buy")
        fee = allocation * config.TAKER_FEE_PCT
        quantity = (allocation - fee) / fill_price
        if quantity <= 0:
            return None

        # 손절가: 고정 % 하드스탑과 ATR 기반 손절 중 더 타이트한(가까운) 쪽을 채택
        pct_stop = fill_price * (1 - config.HARD_STOP_LOSS_PCT)
        atr_stop = fill_price - (atr * config.ATR_STOP_MULTIPLIER)
        stop_loss_price = max(pct_stop, atr_stop)  # 더 높은(=덜 여유로운, 더 타이트한) 쪽

        self.cash -= allocation
        position = Position(
            market=market,
            quantity=quantity,
            entry_price=fill_price,
            stop_loss_price=stop_loss_price,
            entry_timestamp=str(timestamp),
        )
        self.positions[market] = position

        trades_log.append({
            "timestamp": str(timestamp),
            "market": market,
            "side": "buy",
            "price": fill_price,
            "quantity": quantity,
            "amount_krw": allocation,
            "fee_krw": fee,
            "stop_loss_price": stop_loss_price,
            "reason": "claude_signal",
        })
        return position

    def close_position(self, market: str, price: float, timestamp, reason: str,
                         trades_log: list) -> None:
        position = self.positions.get(market)
        if position is None:
            return

        fill_price = self._fill_price(price, "sell")
        proceeds = position.quantity * fill_price
        fee = proceeds * config.TAKER_FEE_PCT
        net_proceeds = proceeds - fee

        cost_basis = position.quantity * position.entry_price
        realized_pnl = net_proceeds - cost_basis
        realized_pnl_pct = realized_pnl / cost_basis if cost_basis else 0.0

        # R-멀티플: 이 트레이드에서 애초에 감수하기로 했던 리스크(진입가-손절가) 대비
        # 실제로 몇 배를 벌었는지/잃었는지. 자산마다 변동성이 달라 원화 손익이나 %보다
        # 전략의 일관성을 비교하기에 더 적합한 지표입니다 (틱톡에서 본 "+9.6R" 표기 방식).
        risk_per_unit = position.entry_price - position.stop_loss_price
        risk_amount = risk_per_unit * position.quantity
        r_multiple = (realized_pnl / risk_amount) if risk_amount > 0 else None

        self.cash += net_proceeds
        del self.positions[market]

        trades_log.append({
            "timestamp": str(timestamp),
            "market": market,
            "side": "sell",
            "price": fill_price,
            "quantity": position.quantity,
            "amount_krw": net_proceeds,
            "fee_krw": fee,
            "realized_pnl_krw": realized_pnl,
            "realized_pnl_pct": realized_pnl_pct,
            "risk_amount_krw": risk_amount,
            "r_multiple": round(r_multiple, 3) if r_multiple is not None else None,
            "reason": reason,
        })

    def check_stop_losses(self, current_prices: Dict[str, float], timestamp,
                            trades_log: list) -> None:
        """보유 포지션 중 손절가를 하회한 종목을 강제 청산합니다.
        클로드 판단과 무관하게 항상 우선 적용되는 안전장치입니다."""
        for market in list(self.positions.keys()):
            price = current_prices.get(market)
            if price is None:
                continue
            if price <= self.positions[market].stop_loss_price:
                self.close_position(market, price, timestamp, reason="hard_stop_loss",
                                      trades_log=trades_log)

    def check_circuit_breaker(self, current_prices: Dict[str, float], timestamp,
                                trades_log: list) -> bool:
        """계좌 전체 누적 손실이 임계치를 넘으면 모든 포지션을 즉시 청산하고
        이후 신규 매매를 영구 중단합니다. 반환값: 이번에 새로 발동했는지 여부."""
        if self.halted:
            return False

        drawdown = self.current_drawdown(current_prices)
        if drawdown >= config.CIRCUIT_BREAKER_DRAWDOWN:
            for market in list(self.positions.keys()):
                self.close_position(market, current_prices[market], timestamp,
                                      reason="circuit_breaker", trades_log=trades_log)
            self.halted = True
            return True
        return False

    def apply_decision(self, market: str, action: str, price: float, atr: float,
                         timestamp, trades_log: list) -> None:
        if self.halted:
            return
        if action == "buy":
            self.open_position(market, price, atr, timestamp, trades_log)
        elif action == "sell":
            self.close_position(market, price, timestamp, reason="claude_signal",
                                  trades_log=trades_log)
        # action == "hold" → 아무 것도 하지 않음
