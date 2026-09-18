"""
설정값 모음 — 여기 값들만 바꾸면 코인 유니버스/자본/리스크 파라미터를 조정할 수 있습니다.
"""

# ── 코인 유니버스 ──────────────────────────────────────────────
# 업비트 마켓 코드 기준 (KRW 마켓). 유동성 높은 상위 코인으로 시작.
# 필요하면 종목을 추가/삭제하세요.
COIN_UNIVERSE = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]

# ── 가상 자본 ──────────────────────────────────────────────────
INITIAL_CAPITAL_KRW = 10_000_000  # 가상 초기 자본 (1천만원)

# 코인 1개 종목당 최대 투입 비중 (분산 목적, 몰빵 방지)
MAX_POSITION_WEIGHT = 0.30  # 자본의 30%

# ── 캔들/타임프레임 ─────────────────────────────────────────────
CANDLE_UNIT_MINUTES = 60   # 1시간봉 (스윙 매매 기준)
CANDLE_FETCH_COUNT = 200   # 지표 계산에 필요한 캔들 개수 (EMA50 등 계산 위해 넉넉히)

# ── 지표 파라미터 ────────────────────────────────────────────────
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
ATR_PERIOD = 14
VOLUME_AVG_PERIOD = 20      # 거래량 평균 기준 기간
VOLUME_SPIKE_MULTIPLIER = 1.5  # 평균 대비 1.5배 이상이면 "급증"으로 판단

# ── 리스크 관리 (코드 레벨 강제 — 클로드 판단과 무관하게 적용) ─────────
HARD_STOP_LOSS_PCT = 0.03       # 포지션당 최대 손실 3% (자본 기준)
ATR_STOP_MULTIPLIER = 1.75      # ATR 기반 손절폭 = ATR * 1.75 (고정 %와 비교해 더 타이트한 쪽 사용)
CIRCUIT_BREAKER_DRAWDOWN = 0.15  # 계좌 누적 손실 15% 도달 시 자동 매매 중단
TAKER_FEE_PCT = 0.0005          # 업비트 통상 수수료 가정치 (0.05%) — 실제 체결과의 괴리를 줄이기 위한 시뮬레이션용
SLIPPAGE_PCT = 0.0005           # 슬리피지 가정치 (0.05%)

# ── Claude 판단 호출 관련 ───────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-5"  # 필요시 원하는 모델로 교체
CLAUDE_MAX_TOKENS = 500

# ── 검증 기간 (파일럿 종료 기준) ──────────────────────────────────
VALIDATION_MIN_DAYS = 60
VALIDATION_MIN_TRADES = 30

# ── 경로 ─────────────────────────────────────────────────────
LOG_DIR = "logs"
DECISIONS_LOG_FILE = f"{LOG_DIR}/decisions.csv"
PORTFOLIO_LOG_FILE = f"{LOG_DIR}/portfolio_state.json"
TRADES_LOG_FILE = f"{LOG_DIR}/trades.csv"
