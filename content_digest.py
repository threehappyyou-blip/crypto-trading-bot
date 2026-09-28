"""
매매가 일정 건수(기본 5건)씩 쌓일 때마다, 그 구간의 성과와 클로드의 실제 판단 근거를
모아서 warminsight/thegeolog에 바로 쓸 수 있는 한국어 초안을 자동으로 생성합니다.

main.py 실행이 끝날 때 호출됩니다. 트레이딩 로직과는 완전히 분리되어 있어서,
여기서 실패해도(예: API 오류, 네트워크 문제) 봇의 매매 자체에는 전혀 영향을 주지
않습니다 — claude_judge.py의 "판단 실패 시 hold로 안전하게 폴백"과 같은 원칙입니다.

동작 방식:
  1. trades.csv에서 청산(side=sell)된 매매만 셉니다.
  2. logs/digest_state.json에 "지금까지 몇 건째에서 마지막으로 초안을 만들었는지" 저장합니다.
  3. 청산 건수가 새로운 5의 배수를 넘었으면(should_generate), 그 구간의 매매+판단근거를
     모아 클로드에게 블로그용 짧은 초안을 요청하고 logs/content_drafts/digest_NNN.md로
     저장합니다.
  4. GitHub Actions 워크플로우가 logs/ 전체를 이미 커밋하고 있으므로, 이 초안 파일도
     자동으로 레포에 올라갑니다 — 로인님은 CSV를 직접 열어볼 필요 없이 이 폴더만
     가끔 확인하시면 됩니다.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Optional

import requests

import config

DIGEST_MILESTONE = 5  # 몇 건(청산 매매 기준)마다 초안을 만들지
DIGEST_STATE_FILE = f"{config.LOG_DIR}/digest_state.json"
DIGEST_DIR = f"{config.LOG_DIR}/content_drafts"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

DIGEST_SYSTEM_PROMPT = """당신은 개인 트레이딩 실험 블로그(warminsight)의 필진을 돕는 어시스턴트입니다.
아래 규칙을 지켜 한국어로 짧은 초안을 작성하세요.

1. 이건 투자 조언이 아니라 개인 실험 기록입니다. "반드시 오릅니다" 같은 확정적/과장된
   표현을 쓰지 마세요.
2. 제공된 매매 데이터와 판단 근거만 사용하세요. 없는 사실을 지어내지 마세요.
3. 승률, 손익, R-멀티플 같은 숫자는 있는 그대로 인용하세요. 좋게 포장하지 말고, 손실이면
   손실이라고 그대로 쓰세요.
4. 판단 근거(reasoning) 중 흥미롭거나 의외였던 사례가 있으면 짧게 인용하세요.
5. 3~6문장, 블로그에 바로 붙여넣을 수 있는 자연스러운 문단으로 작성하세요. 제목·불릿은 쓰지 마세요.
"""


def _read_csv(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_state() -> Dict:
    if os.path.exists(DIGEST_STATE_FILE):
        with open(DIGEST_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_digest_count": 0}


def _save_state(state: Dict) -> None:
    os.makedirs(os.path.dirname(DIGEST_STATE_FILE), exist_ok=True)
    with open(DIGEST_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_generate(closed_count: int, last_digest_count: int,
                      milestone: int = DIGEST_MILESTONE) -> bool:
    """청산된 매매 건수가 milestone(기본 5)의 새로운 배수를 이번에 넘었는지 판단합니다.
    순수 함수라 네트워크 없이 테스트 가능합니다.

    예: milestone=5 -> 5, 10, 15... 건째마다 True. 한 번 실행에서 여러 건이 한꺼번에
    청산돼 배수를 여러 개 건너뛰어도(예: 3건 -> 12건) 그 실행에서는 1번만 생성합니다."""
    if milestone <= 0 or closed_count < milestone:
        return False
    return closed_count // milestone > last_digest_count // milestone


def _build_digest_data(trades: List[Dict], decisions: List[Dict], since_index: int) -> Dict:
    """마지막 초안 이후 새로 청산된 매매들을 모아 요약 통계 + 개별 매매 정보를 만듭니다."""
    closed = [t for t in trades if t.get("side") == "sell"]
    window = closed[since_index:]

    wins = 0
    total_pnl = 0.0
    r_values: List[float] = []
    rows: List[Dict] = []

    for t in window:
        try:
            pnl = float(t.get("realized_pnl_krw") or 0)
        except ValueError:
            pnl = 0.0
        total_pnl += pnl
        if pnl > 0:
            wins += 1

        r_raw = t.get("r_multiple")
        if r_raw not in (None, ""):
            try:
                r_values.append(float(r_raw))
            except ValueError:
                pass

        reasoning = ""
        for d in decisions:
            if d.get("timestamp") == t.get("timestamp") and d.get("market") == t.get("market"):
                reasoning = d.get("reasoning", "")
                break

        rows.append({
            "market": t.get("market"),
            "timestamp": t.get("timestamp"),
            "pnl_krw": pnl,
            "pnl_pct": t.get("realized_pnl_pct"),
            "reason": t.get("reason"),
            "r_multiple": r_raw,
            "reasoning": reasoning,
        })

    win_rate = (wins / len(window) * 100) if window else 0.0
    avg_r = (sum(r_values) / len(r_values)) if r_values else None

    return {
        "count": len(window),
        "win_rate": win_rate,
        "total_pnl_krw": total_pnl,
        "avg_r_multiple": avg_r,
        "trades": rows,
    }


def _build_user_prompt(data: Dict) -> str:
    lines = [
        f"최근 매매 {data['count']}건 요약입니다:",
        f"- 승률: {data['win_rate']:.1f}%",
        f"- 총 손익: {data['total_pnl_krw']:,.0f}원",
    ]
    if data["avg_r_multiple"] is not None:
        lines.append(f"- 평균 R-멀티플: {data['avg_r_multiple']:.2f}")
    lines.append("")
    lines.append("개별 매매:")
    for t in data["trades"]:
        lines.append(
            f"- {t['market']} / {t['timestamp']} / 손익 {t['pnl_krw']:,.0f}원 "
            f"/ 청산사유 {t['reason']} / 판단근거: {t['reasoning']}"
        )
    lines.append("")
    lines.append("이 내용을 바탕으로 블로그 초안 문단을 작성하세요.")
    return "\n".join(lines)


def _call_claude_for_digest(data: Dict, api_key: str) -> str:
    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": 600,
        "system": DIGEST_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_user_prompt(data)}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    return "".join(block.get("text", "") for block in body.get("content", []))


def maybe_generate_digest(api_key: Optional[str] = None) -> Optional[str]:
    """main.py 실행 끝에서 호출합니다. 새 마일스톤을 넘었으면 초안 파일을 만들고
    그 경로를 반환하고, 아니면(또는 실패하면) None을 반환합니다.

    의도적으로 예외를 밖으로 던지지 않습니다 — 다이제스트 생성 실패가 봇의 매매
    실행 자체를 막으면 안 되기 때문입니다."""
    try:
        trades = _read_csv(config.TRADES_LOG_FILE)
        decisions = _read_csv(config.DECISIONS_LOG_FILE)
        closed_count = sum(1 for t in trades if t.get("side") == "sell")

        state = _load_state()
        last = state.get("last_digest_count", 0)

        if not should_generate(closed_count, last):
            return None

        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("  [content_digest] ANTHROPIC_API_KEY 없음 — 다이제스트 건너뜀")
            return None

        data = _build_digest_data(trades, decisions, since_index=last)
        draft_text = _call_claude_for_digest(data, api_key)

        os.makedirs(DIGEST_DIR, exist_ok=True)
        out_path = f"{DIGEST_DIR}/digest_{closed_count:03d}.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"<!-- 누적 청산 매매 {closed_count}건 시점 자동 생성 초안 (검토 후 사용하세요) -->\n\n")
            f.write(draft_text.strip() + "\n")

        state["last_digest_count"] = closed_count
        _save_state(state)
        print(f"  [content_digest] 콘텐츠 초안 생성: {out_path}")
        return out_path
    except Exception as exc:  # noqa: BLE001 — 의도적으로 넓게 캐치, 다이제스트 실패가 봇 실행을 막으면 안 됨
        print(f"  [content_digest] 다이제스트 생성 실패(무시하고 계속): {exc}")
        return None
