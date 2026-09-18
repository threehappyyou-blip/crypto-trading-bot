"""
백테스트 스크립트.

라이브 페이퍼 트레이딩(main.py)은 스케줄마다 캔들 1개만 처리하고 끝나기 때문에,
검증 기간(60~90일)을 실시간으로 기다려야 결과가 쌓입니다. 이 스크립트는 과거
데이터를 한 번에 쭉 리플레이해서 같은 전략 로직을 몇 분 안에 미리 점검할 수 있게
합니다 — 틱톡에서 보신 TradingView "Bar Replay" 기능을, main.py와 동일한
지표/트리거/판단/포트폴리오 코드로 그대로 자동화한 것입니다.

주의해서 봐야 할 점:
- 트리거가 뜰 때마다 실제 Anthropic API를 호출합니다 (과거 데이터라고 가짜 응답을
  쓰지 않습니다 — 그래야 forward 결과와 같은 방식으로 비교할 수 있습니다). 조회
  기간(--days)을 너무 길게 잡으면 API 호출 비용이 늘어나니 기본값(90일) 정도로
  시작하는 걸 권합니다.
- 라이브 포트폴리오(logs/portfolio_state.json)와는 완전히 분리된 별도 상태/로그
  (logs/backtest_*.csv, logs/backtest_portfolio_state.json)를 씁니다. main.py
  실행이나 검증 기간 집계에는 전혀 영향을 주지 않습니다.
- 완벽한 미래참조 방지(look-ahead bias 제거)를 보장하지는 않습니다 — 빠르게 전략
  감각을 잡는 용도로 쓰고, 최종 신뢰는 반드시 forward 페이퍼 트레이딩(main.py를
  실시간으로 돌린 결과)으로 확인하세요. 백테스트 성과가 좋다고 warminsight에
  "검증 완료"로 공개하지 마세요 — 어디까지나 사전 점검용입니다.

실행 예:
    ANTHROPIC_API_KEY=sk-ant-... python backtest.py --days 90
    ANTHROPIC_API_KEY=sk-ant-... python backtest.py --days 30 --markets KRW-BTC
"""
from __future__ import annotations

import argparse
import os

import config
import indicators
import logger
import upbit_data
from claude_judge import judge
from portfolio_sim import Portfolio
from trigger import detect_trigger

BACKTEST_PORTFOLIO_FILE = f"{config.LOG_DIR}/backtest_portfolio_state.json"
BACKTEST_DECISIONS_FILE = f"{config.LOG_DIR}/backtest_decisions.csv"
BACKTEST_TRADES_FILE = f"{config.LOG_DIR}/backtest_trades.csv"
BACKTEST_EQUITY_FILE = f"{config.LOG_DIR}/backtest_equity_curve.csv"


def reset_backtest_logs() -> None:
    """이전 백테스트 결과를 지우고 새로 시작합니다 (라이브 로그는 건드리지 않음)."""
    for path in [BACKTEST_PORTFOLIO_FILE, BACKTEST_DECISIONS_FILE,
                 BACKTEST_TRADES_FILE, BACKTEST_EQUITY_FILE]:
        if os.path.exists(path):
            os.remove(path)


def run_backtest(markets: list[str], lookback_days: int) -> None:
    portfolio = Portfolio(cash=config.INITIAL_CAPITAL_KRW, initial_capital=config.INITIAL_CAPITAL_KRW)

    print(f"백테스트 시작 — 종목: {markets}, 기간: 최근 {lookback_days}일, "
          f"캔들 단위: {config.CANDLE_UNIT_MINUTES}분")

    # 1. 종목별 과거 캔들 + 지표를 미리 전부 계산
    history = {}
    for market in markets:
        df = upbit_data.fetch_candles_history(market, config.CANDLE_UNIT_MINUTES, lookback_days)
        history[market] = indicators.compute_all(df)
        print(f"  {market}: 캔들 {len(df)}개 로드 완료 "
              f"({df.iloc[0]['timestamp']} ~ {df.iloc[-1]['timestamp']})")

    min_len = min(len(df) for df in history.values())
    start_idx = config.EMA_SLOW + 2

    decision_count = 0
    # 2. 시간순으로 한 캔들씩 리플레이 (모든 종목을 같은 시점까지만 보게 동기화)
    for i in range(start_idx, min_len):
        trades_log: list = []
        current_prices = {m: float(df.iloc[i - 1]["close"]) for m, df in history.items()}
        timestamp = history[markets[0]].iloc[i - 1]["timestamp"]

        # 안전장치는 매 캔들마다 항상 우선 체크 (트리거 여부와 무관)
        portfolio.check_stop_losses(current_prices, timestamp, trades_log)
        if not portfolio.halted:
            portfolio.check_circuit_breaker(current_prices, timestamp, trades_log)

        if not portfolio.halted:
            for market, df in history.items():
                window = df.iloc[:i].reset_index(drop=True)
                event = detect_trigger(market, window)
                if event is None:
                    continue

                decision = judge(event)
                decision_count += 1
                logger.log_decision(market, event.trigger_type, decision, event.timestamp,
                                      path=BACKTEST_DECISIONS_FILE)
                portfolio.apply_decision(
                    market=market, action=decision.action, price=event.close,
                    atr=event.atr, timestamp=event.timestamp, trades_log=trades_log,
                )

        if trades_log:
            logger.log_trades(trades_log, path=BACKTEST_TRADES_FILE)

        equity = portfolio.equity(current_prices)
        drawdown = portfolio.current_drawdown(current_prices)
        logger.log_equity_snapshot(timestamp, equity, portfolio.cash, len(portfolio.positions),
                                     drawdown, portfolio.halted, path=BACKTEST_EQUITY_FILE)

    portfolio.save(path=BACKTEST_PORTFOLIO_FILE)

    final_prices = {m: float(df.iloc[min_len - 1]["close"]) for m, df in history.items()}
    final_equity = portfolio.equity(final_prices)
    print(f"\n백테스트 종료 — 클로드 판단 호출: {decision_count}회")
    print(f"최종 자산: {final_equity:,.0f} KRW "
          f"(초기자본 대비 {final_equity / portfolio.initial_capital - 1:+.2%})")
    print(f"결과 파일: {BACKTEST_TRADES_FILE}, {BACKTEST_DECISIONS_FILE}, {BACKTEST_EQUITY_FILE}")
    print("이 결과로 generate_report.py를 돌리면 종목별 히트맵을 볼 수 있습니다:")
    print(f"  python generate_report.py --trades {BACKTEST_TRADES_FILE} "
          f"--out logs/backtest_heatmap.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="과거 데이터로 전략을 빠르게 리플레이 검증합니다.")
    parser.add_argument("--days", type=int, default=90, help="조회할 과거 기간(일). 기본 90일")
    parser.add_argument("--markets", nargs="+", default=config.COIN_UNIVERSE,
                         help="백테스트할 마켓 코드 목록. 기본은 config.COIN_UNIVERSE 전체")
    parser.add_argument("--reset", action="store_true", help="이전 백테스트 로그를 지우고 새로 시작")
    args = parser.parse_args()

    if args.reset:
        reset_backtest_logs()

    run_backtest(args.markets, args.days)
