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

# 거래량 급증을 EMA/RSI 조건과 무관하게 그 자체로도 트리거로 볼지 여부.
# ("바닥이든 고점이든 거래량이 급증한 종목을 주목해야 한다" — 실적 개선/턴어라운드
# 뉴스가 나올 때 세력이 매집하며 거래량이 먼저 터지는 경우가 많다는 통념을 반영.)
# True로 켜면 EMA/RSI 신호가 없어도 거래량만 튀면 클로드를 호출하므로, 트리거 빈도와
# API 비용이 늘어납니다. 기본값은 False — 켜고 싶으면 이 값만 True로 바꾸세요.
ENABLE_STANDALONE_VOLUME_TRIGGER = False

# ── 리스크 관리 (코드 레벨 강제 — 클로드 판단과 무관하게 적용) ─────────
HARD_STOP_LOSS_PCT = 0.03       # 포지션당 최대 손실 3% (자본 기준)
ATR_STOP_MULTIPLIER = 1.75      # ATR 기반 손절폭 = ATR * 1.75 (고정 %와 비교해 더 타이트한 쪽 사용)
CIRCUIT_BREAKER_DRAWDOWN = 0.15  # 계좌 누적 손실 15% 도달 시 자동 매매 중단
TAKER_FEE_PCT = 0.0005          # 업비트 통상 수수료 가정치 (0.05%) — 실제 체결과의 괴리를 줄이기 위한 시뮬레이션용
SLIPPAGE_PCT = 0.0005           # 슬리피지 가정치 (0.05%)

# ── 익절(테이크프로핏) / 손익비(RR) 설정 ────────────────────────────
# "승률보다 손익비(risk-reward ratio)가 더 중요하다"는 통념을 실제로 검증해보기
# 위한 옵션입니다. 켜면 진입 시점의 리스크(진입가-손절가)를 1로 놓고,
# 그 TAKE_PROFIT_RR_MULTIPLE배만큼 이익이 나면 클로드 판단과 무관하게
# 코드 레벨에서 자동 익절합니다 (하드스탑과 동일하게 항상 우선 적용).
# 예: TAKE_PROFIT_RR_MULTIPLE=2.0 → 손익비 1:2 (리스크 1을 걸고 목표 2를 노림).
#
# 주의: 손익비를 키운다고 그 자체로 수익이 느는 게 아니라, 목표가에 도달하기 전에
# 반대로 튕겨나갈 확률(=승률)이 함께 낮아지는 트레이드오프가 있습니다. 그래서
# "켜두면 무조건 좋다"가 아니라, backtest.py로 이 값을 껐을 때/켰을 때, 또는
# 배수를 바꿔가며(예: 1.5 / 2.0 / 3.0) 실제 결과를 직접 비교해보는 용도입니다.
ENABLE_TAKE_PROFIT = True
TAKE_PROFIT_RR_MULTIPLE = 2.0   # 손익비 1 : 이 값 (예: 2.0 → 1:2, 3.0 → 1:3)

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
