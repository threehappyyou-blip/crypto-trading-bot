"""
클로드 판단 모듈.

트리거가 발생했을 때만 호출됩니다 (비용 통제). 지표 값과 최근 가격 흐름을
프롬프트에 담아 전달하고, 고정된 JSON 스키마로만 응답받습니다.

출력 스키마:
{
  "action": "buy" | "sell" | "hold",
  "confidence": 0.0 ~ 1.0,
  "reasoning": "판단 근거 (한국어, 2~4문장)",
  "stop_loss_pct": 0.0 ~ 1.0   # 클로드가 제안하는 손절 비율(참고용).
                                 # 실제 하드스탑은 config.py 값이 항상 우선 적용됨.
}

주의: 이 모듈은 Anthropic API(api.anthropic.com) 호출이 필요합니다.
이 샌드박스에서는 실제 API 키가 없어 호출 테스트를 하지 못했습니다 —
로인님 환경에서 ANTHROPIC_API_KEY 환경변수를 설정한 뒤 사용하세요.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import requests

import config
from trigger import TriggerEvent

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

VALID_ACTIONS = {"buy", "sell", "hold"}

SYSTEM_PROMPT = """당신은 암호화폐 스윙 트레이딩 판단을 돕는 어시스턴트입니다.
아래 규칙을 반드시 지키세요.

1. 확실하지 않으면 무조건 "hold"를 선택하세요. 애매한 상황에서 무리하게 buy/sell을 고르지 마세요.
2. 제공된 지표 값과 가격 흐름만 근거로 판단하세요. 모르는 정보를 지어내지 마세요.
3. reasoning에는 실제로 그렇게 판단한 구체적 이유를 2~4문장으로 쓰세요.
   (이 reasoning은 나중에 매매 기록을 되짚어보는 용도로 그대로 사용됩니다.)
4. 반드시 아래 JSON 스키마로만 응답하세요. 다른 텍스트나 설명을 앞뒤에 붙이지 마세요.

{
  "action": "buy" | "sell" | "hold",
  "confidence": 0.0에서 1.0 사이 숫자,
  "reasoning": "판단 근거",
  "stop_loss_pct": 0.0에서 1.0 사이 숫자 (권장 손절 비율)
}
"""

USER_PROMPT_TEMPLATE = """다음은 {market}의 최신 시장 데이터입니다.

- 트리거 종류: {trigger_type}
- 현재가: {close:,.0f} KRW
- EMA{ema_fast_period}: {ema_fast:,.0f}
- EMA{ema_slow_period}: {ema_slow:,.0f}
- RSI({rsi_period}): {rsi:.1f}
- ATR({atr_period}): {atr:,.0f}
- 현재 거래량: {volume:,.2f} (최근 {vol_period}구간 평균 대비 {volume_ratio:.1f}배)

이 데이터를 바탕으로 지금 매수/매도/보유 중 무엇이 적절한지 판단하고,
정해진 JSON 스키마로만 답하세요.
"""


@dataclass
class ClaudeDecision:
    action: str
    confidence: float
    reasoning: str
    stop_loss_pct: float
    raw_response: str

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "stop_loss_pct": self.stop_loss_pct,
        }


def _build_user_prompt(event: TriggerEvent) -> str:
    volume_ratio = (event.volume / event.volume_avg) if event.volume_avg and event.volume_avg > 0 else float("nan")
    return USER_PROMPT_TEMPLATE.format(
        market=event.market,
        trigger_type=event.trigger_type,
        close=event.close,
        ema_fast_period=config.EMA_FAST,
        ema_fast=event.ema_fast,
        ema_slow_period=config.EMA_SLOW,
        ema_slow=event.ema_slow,
        rsi_period=config.RSI_PERIOD,
        rsi=event.rsi,
        atr_period=config.ATR_PERIOD,
        atr=event.atr,
        volume=event.volume,
        vol_period=config.VOLUME_AVG_PERIOD,
        volume_ratio=volume_ratio,
    )


def _extract_json(text: str) -> dict:
    """모델 응답에서 JSON 객체만 안전하게 추출합니다."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"응답에서 JSON을 찾을 수 없습니다: {text!r}")
    return json.loads(text[start:end + 1])


def _validate_decision(parsed: dict) -> ClaudeDecision:
    action = parsed.get("action")
    if action not in VALID_ACTIONS:
        raise ValueError(f"유효하지 않은 action 값: {action!r}")

    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        raise ValueError(f"유효하지 않은 confidence 값: {parsed.get('confidence')!r}")
    confidence = min(max(confidence, 0.0), 1.0)  # 0~1로 clamp

    reasoning = str(parsed.get("reasoning", "")).strip()
    if not reasoning:
        raise ValueError("reasoning이 비어 있습니다.")

    try:
        stop_loss_pct = float(parsed.get("stop_loss_pct"))
    except (TypeError, ValueError):
        stop_loss_pct = config.HARD_STOP_LOSS_PCT  # 값이 이상하면 기본 하드스탑으로 대체
    stop_loss_pct = min(max(stop_loss_pct, 0.0), 1.0)

    return ClaudeDecision(
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        stop_loss_pct=stop_loss_pct,
        raw_response=json.dumps(parsed, ensure_ascii=False),
    )


def judge(event: TriggerEvent, api_key: Optional[str] = None) -> ClaudeDecision:
    """트리거 이벤트를 클로드에게 보내 매매 판단을 받습니다.

    실패(네트워크 오류, JSON 파싱 실패, 스키마 검증 실패) 시에는
    예외를 던지는 대신 안전하게 "hold"로 폴백합니다 — 판단 실패가
    곧 매매 실행으로 이어지면 안 되기 때문입니다.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다. "
            "Anthropic Console에서 API 키를 발급받아 설정하세요."
        )

    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": config.CLAUDE_MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": _build_user_prompt(event)},
        ],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    try:
        resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        text = "".join(block.get("text", "") for block in body.get("content", []))
        parsed = _extract_json(text)
        return _validate_decision(parsed)
    except Exception as exc:  # noqa: BLE001 — 의도적으로 넓게 캐치 후 안전 폴백
        return ClaudeDecision(
            action="hold",
            confidence=0.0,
            reasoning=f"[자동 폴백] 클로드 판단 실패로 보유 처리: {exc}",
            stop_loss_pct=config.HARD_STOP_LOSS_PCT,
            raw_response="",
        )
