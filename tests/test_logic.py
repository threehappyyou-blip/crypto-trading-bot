"""
합성(가짜) 가격 데이터로 지표/트리거/포트폴리오 로직을 검증하는 스크립트.

이 샌드박스에서는 api.upbit.com에 대한 외부 네트워크 접근이 막혀 있어
실제 업비트 데이터로는 테스트할 수 없었습니다. 대신 실제 캔들 데이터와
동일한 구조(open/high/low/close/volume)의 합성 데이터를 직접 만들어
계산 로직 자체가 올바른지 검증합니다.

실행: python tests/test_logic.py
(정상이면 마지막에 "모든 테스트 통과" 출력)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
import indicators
from trigger import detect_trigger
from portfolio_sim import Portfolio
from claude_judge import _extract_json, _validate_decision


def make_synthetic_candles(n=120, seed=42) -> pd.DataFrame:
    """상승 추세 → 과열(RSI 상승) → 하락 전환이 섞인 합성 캔들 생성.
    거래량은 마지막 20개 구간에서 급증하도록 만들어 volume_spike 필터도 함께 검증."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2026-01-01", periods=n, freq="h")

    # 앞 60개: 완만한 상승, 뒤 60개: 상승 가속 후 급락 (EMA 골든/데드크로스 유발 목적)
    trend = np.concatenate([
        np.linspace(50_000_000, 55_000_000, 60),
        np.linspace(55_000_000, 70_000_000, 40),
        np.linspace(70_000_000, 60_000_000, 20),
    ])
    noise = rng.normal(0, 150_000, n)
    close = trend + noise

    open_ = close + rng.normal(0, 50_000, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 80_000, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 80_000, n))

    volume = rng.uniform(80, 120, n)
    volume[-20:] *= 2.0  # 마지막 구간 거래량 급증

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def test_indicators():
    df = make_synthetic_candles()
    out = indicators.compute_all(df)

    expected_cols = {f"ema_{config.EMA_FAST}", f"ema_{config.EMA_SLOW}", "rsi", "atr",
                      "volume_avg", "volume_spike"}
    assert expected_cols.issubset(out.columns), "지표 컬럼이 누락되었습니다"

    # RSI는 항상 0~100 범위여야 함
    valid_rsi = out["rsi"].dropna()
    assert valid_rsi.between(0, 100).all(), "RSI 값이 0~100 범위를 벗어났습니다"

    # ATR은 음수가 될 수 없음
    valid_atr = out["atr"].dropna()
    assert (valid_atr >= 0).all(), "ATR이 음수입니다"

    print("✅ test_indicators 통과")


def test_trigger_detection():
    df = make_synthetic_candles()
    out = indicators.compute_all(df)

    # 데이터가 충분히 쌓이기 전(EMA_SLOW+2 미만)에는 None을 반환해야 함
    short_df = out.iloc[:config.EMA_SLOW].reset_index(drop=True)
    assert detect_trigger("KRW-TEST", short_df) is None, \
        "데이터 부족 상황에서 트리거를 감지하면 안 됩니다"

    # 전체 구간을 순회하며 최소 1건 이상 트리거가 감지되는지 확인
    # (합성 데이터는 골든크로스/데드크로스가 일어나도록 설계함)
    triggers = []
    for i in range(config.EMA_SLOW + 2, len(out) + 1):
        window = out.iloc[:i].reset_index(drop=True)
        event = detect_trigger("KRW-TEST", window)
        if event is not None:
            triggers.append(event)

    assert len(triggers) > 0, "합성 데이터에서 트리거가 하나도 감지되지 않았습니다"
    for t in triggers:
        assert t.trigger_type in {"ema_golden_cross", "ema_dead_cross", "rsi_rebound", "rsi_reversal"}

    print(f"✅ test_trigger_detection 통과 (감지된 트리거 {len(triggers)}건: "
          f"{[t.trigger_type for t in triggers]})")


def test_portfolio_buy_and_stop_loss():
    portfolio = Portfolio(cash=config.INITIAL_CAPITAL_KRW, initial_capital=config.INITIAL_CAPITAL_KRW)
    trades_log = []

    entry_price = 50_000_000.0
    atr = 1_000_000.0
    portfolio.open_position("KRW-BTC", entry_price, atr, "2026-01-01T00:00:00", trades_log)

    assert "KRW-BTC" in portfolio.positions, "포지션이 열리지 않았습니다"
    position = portfolio.positions["KRW-BTC"]
    expected_allocation = config.INITIAL_CAPITAL_KRW * config.MAX_POSITION_WEIGHT
    assert abs(portfolio.cash - (config.INITIAL_CAPITAL_KRW - expected_allocation)) < 1, \
        "매수 후 현금 차감이 예상과 다릅니다"

    # 손절가 아래로 가격이 떨어지면 check_stop_losses가 자동 청산해야 함
    crashed_price = position.stop_loss_price - 1_000_000
    portfolio.check_stop_losses({"KRW-BTC": crashed_price}, "2026-01-01T05:00:00", trades_log)

    assert "KRW-BTC" not in portfolio.positions, "손절가 하회 시 포지션이 청산되어야 합니다"
    assert any(t["reason"] == "hard_stop_loss" for t in trades_log), \
        "하드스탑 청산 기록이 남아야 합니다"

    print("✅ test_portfolio_buy_and_stop_loss 통과")


def test_r_multiple_on_close():
    portfolio = Portfolio(cash=config.INITIAL_CAPITAL_KRW, initial_capital=config.INITIAL_CAPITAL_KRW)
    trades_log = []

    entry_price = 50_000_000.0
    atr = 1_000_000.0
    portfolio.open_position("KRW-BTC", entry_price, atr, "t0", trades_log)
    position = portfolio.positions["KRW-BTC"]
    risk_per_unit = position.entry_price - position.stop_loss_price
    assert risk_per_unit > 0, "손절가가 진입가보다 낮아야 리스크가 양수입니다"

    # 리스크의 정확히 2배만큼 이익을 보고 청산 -> R-멀티플이 약 +2.0R 이어야 함
    target_price = position.entry_price + (risk_per_unit * 2)
    portfolio.close_position("KRW-BTC", target_price, "t1", reason="test", trades_log=trades_log)

    sell_trade = [t for t in trades_log if t["side"] == "sell"][0]
    assert sell_trade["r_multiple"] is not None
    # 수수료/슬리피지 때문에 정확히 2.0은 아니지만 근사해야 함
    assert 1.5 < sell_trade["r_multiple"] < 2.2, f"예상 범위를 벗어난 R-멀티플: {sell_trade['r_multiple']}"

    print(f"✅ test_r_multiple_on_close 통과 (R-멀티플={sell_trade['r_multiple']})")


def test_circuit_breaker():
    portfolio = Portfolio(cash=config.INITIAL_CAPITAL_KRW, initial_capital=config.INITIAL_CAPITAL_KRW)
    trades_log = []

    portfolio.open_position("KRW-BTC", 50_000_000.0, 1_000_000.0, "t0", trades_log)
    portfolio.open_position("KRW-ETH", 3_000_000.0, 100_000.0, "t0", trades_log)

    # 전체 자산이 서킷브레이커 임계치 아래로 폭락했다고 가정
    crashed_prices = {"KRW-BTC": 30_000_000.0, "KRW-ETH": 1_500_000.0}
    just_halted = portfolio.check_circuit_breaker(crashed_prices, "t1", trades_log)

    assert just_halted is True, "서킷브레이커가 발동해야 합니다"
    assert portfolio.halted is True
    assert len(portfolio.positions) == 0, "서킷브레이커 발동 시 모든 포지션이 청산되어야 합니다"

    # halted 상태에서는 신규 매수가 막혀야 함
    result = portfolio.open_position("KRW-XRP", 1_000.0, 10.0, "t2", trades_log)
    assert result is None, "halted 상태에서는 신규 포지션을 열 수 없습니다"

    print("✅ test_circuit_breaker 통과")


def test_claude_json_parsing():
    # 정상 케이스
    good = _extract_json('앞에 텍스트가 붙어도 {"action": "buy", "confidence": 0.7, '
                          '"reasoning": "테스트", "stop_loss_pct": 0.03} 뒤에도 텍스트')
    decision = _validate_decision(good)
    assert decision.action == "buy"
    assert 0.0 <= decision.confidence <= 1.0

    # 비정상 action → 예외 발생해야 함 (main에서는 이 예외를 잡아 hold로 폴백)
    try:
        _validate_decision({"action": "buy_a_lot", "confidence": 0.5, "reasoning": "x",
                             "stop_loss_pct": 0.03})
        raise AssertionError("잘못된 action 값을 걸러내지 못했습니다")
    except ValueError:
        pass

    # confidence 범위를 벗어나면 0~1로 clamp 되어야 함
    clamped = _validate_decision({"action": "hold", "confidence": 5.0, "reasoning": "x",
                                    "stop_loss_pct": -1})
    assert clamped.confidence == 1.0
    assert clamped.stop_loss_pct == 0.0

    print("✅ test_claude_json_parsing 통과")


if __name__ == "__main__":
    test_indicators()
    test_trigger_detection()
    test_portfolio_buy_and_stop_loss()
    test_r_multiple_on_close()
    test_circuit_breaker()
    test_claude_json_parsing()
    print("\n모든 테스트 통과 🎉")
