"""
메인 오케스트레이션 스크립트.

1회 실행 시 하는 일:
  1. 저장된 가상 포트폴리오 상태를 로드 (없으면 초기 자본으로 새로 생성)
  2. 코인 유니버스 전체의 최신 캔들을 업비트 공개 API에서 가져옴
  3. 지표 계산
  4. 보유 포지션에 대해 하드스탑/서킷브레이커 먼저 체크 (클로드 판단과 무관하게 항상 우선)
  5. 트리거가 발생한 종목만 클로드에게 판단 요청 → 판단 로그 기록 → 포트폴리오에 반영
  6. 최종 자산 스냅샷 기록, 포트폴리오 상태 저장

이 스크립트는 "계속 켜져 있는 서버"가 아니라 "주기적으로 한 번 실행되고 끝나는"
방식을 전제로 설계했습니다 (warminsight 파이프라인과 동일하게 GitHub Actions cron으로
1시간마다 실행하는 걸 권장합니다 — 회사 다니시면서 상시 모니터링이 불필요합니다).

실행 예:
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import config
import content_digest
import indicators
import logger
import upbit_data
from claude_judge import judge
from portfolio_sim import Portfolio
from trigger import detect_trigger


def run_once() -> None:
    portfolio = Portfolio.load_or_create()
    trades_log: list = []

    print(f"[{datetime.now(timezone.utc).isoformat()}] 실행 시작 — "
          f"코인 유니버스: {config.COIN_UNIVERSE}, halted={portfolio.halted}")

    # 1. 데이터 수집 + 지표 계산
    candle_data = upbit_data.fetch_all(
        config.COIN_UNIVERSE, config.CANDLE_UNIT_MINUTES, config.CANDLE_FETCH_COUNT
    )
    indicator_data = {m: indicators.compute_all(df) for m, df in candle_data.items()}
    current_prices = {m: float(df.iloc[-1]["close"]) for m, df in indicator_data.items()}
    latest_timestamp = max(df.iloc[-1]["timestamp"] for df in indicator_data.values())

    # 2. 안전장치 먼저 적용 (클로드 판단보다 항상 우선)
    portfolio.check_stop_losses(current_prices, latest_timestamp, trades_log)
    portfolio.check_take_profits(current_prices, latest_timestamp, trades_log)
    just_halted = portfolio.check_circuit_breaker(current_prices, latest_timestamp, trades_log)
    if just_halted:
        print("⚠️  서킷브레이커 발동: 누적 손실 한도 초과로 모든 포지션 청산, 신규 매매 중단.")

    # 3. 트리거 감지 → 클로드 판단 → 포트폴리오 반영 (halted면 스킵)
    if not portfolio.halted:
        for market, df in indicator_data.items():
            event = detect_trigger(market, df)
            if event is None:
                continue

            print(f"  트리거 감지: {market} / {event.trigger_type}")
            decision = judge(event)
            logger.log_decision(market, event.trigger_type, decision, event.timestamp)
            print(f"    → 클로드 판단: {decision.action} (confidence={decision.confidence})")

            portfolio.apply_decision(
                market=market,
                action=decision.action,
                price=event.close,
                atr=event.atr,
                timestamp=event.timestamp,
                trades_log=trades_log,
            )
    else:
        print("  halted 상태 — 신규 판단/매매를 건너뜁니다. (수동 검토 후 재개 필요)")

    # 4. 체결 기록 저장
    if trades_log:
        logger.log_trades(trades_log)
        for t in trades_log:
            print(f"    체결: {t['side']} {t['market']} @ {t['price']:.0f} ({t['reason']})")

    # 5. 자산 스냅샷 기록 + 포트폴리오 저장
    equity = portfolio.equity(current_prices)
    drawdown = portfolio.current_drawdown(current_prices)
    logger.log_equity_snapshot(
        latest_timestamp, equity, portfolio.cash, len(portfolio.positions), drawdown,
        portfolio.halted,
    )
    portfolio.save()

    # 6. 청산 매매가 새로운 마일스톤(기본 5건)을 넘었으면 콘텐츠 초안 자동 생성
    #    (실패해도 여기서 예외를 던지지 않으므로 위의 매매 실행에는 영향 없음)
    content_digest.maybe_generate_digest()

    print(f"[{datetime.now(timezone.utc).isoformat()}] 실행 종료 — "
          f"총자산: {equity:,.0f} KRW (초기자본 대비 {equity / portfolio.initial_capital - 1:+.2%}), "
          f"drawdown: {drawdown:.2%}")


if __name__ == "__main__":
    run_once()
