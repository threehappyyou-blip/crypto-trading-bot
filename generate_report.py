"""
자산별 히트맵 리포트 생성기.

trades.csv(또는 backtest_trades.csv)를 읽어 종목별로 승률·평균 R-멀티플·손익을
집계하고, 표(콘솔 출력)와 PNG 히트맵 이미지로 저장합니다. 틱톡에서 보신
"Assets — heatmap" 형태를 재현한 것인데, 두 가지를 다르게 했습니다.

  1. 색맹 안전 팔레트: 원본은 빨강/초록 대비였지만, 적록색맹 사용자에게는 구분이
     어려워서 파랑(이익)/주황(손실) 대비로 바꿨습니다.
  2. 색만으로 정보를 전달하지 않음: 모든 셀에 실제 수치(R, 승률, 표본 수)를
     텍스트로 함께 표시합니다 — 색은 "한눈에 훑어보는" 보조 수단일 뿐입니다.

이 리포트는 로인님이 실제로 기록한 진짜 매매 데이터를 그대로 집계한 것입니다.
틱톡 영상 속 히트맵은 마케팅용 데모일 가능성이 있어 실제 성과를 검증할 방법이
없으니, 그 영상의 숫자를 벤치마크로 삼지 마세요 — 여기서 나오는 숫자만 진짜입니다.

실행 예:
    python generate_report.py
    python generate_report.py --trades logs/backtest_trades.csv --out logs/backtest_heatmap.png
"""
from __future__ import annotations

import argparse
import os

import matplotlib
matplotlib.use("Agg")  # 화면 없는 환경(서버/CI)에서도 이미지 저장 가능하게
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import config

# 한글 폰트 자동 탐색: 설치된 폰트 중 한글을 지원하는 폰트가 있으면 그걸 쓰고,
# 없으면 기본 폰트로 대체합니다 (그 경우 한글 라벨이 네모(□)로 깨질 수 있습니다 —
# README의 "한글 폰트 안내" 참고).
_KOREAN_FONT_CANDIDATES = [
    "Noto Sans CJK KR", "NanumGothic", "Malgun Gothic", "AppleGothic", "Apple SD Gothic Neo",
]


def _configure_korean_font() -> None:
    available = {f.name for f in fm.fontManager.ttflist}

    for name in _KOREAN_FONT_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.family"] = name
            matplotlib.rcParams["axes.unicode_minus"] = False
            return

    # 정확히 일치하는 이름이 없으면, "CJK"가 들어간 폰트(예: Noto Sans CJK JP)를
    # 대신 씁니다. 한글(Hangul) 글리프는 CJK 계열 폰트라면 지역 변형과 무관하게
    # 대부분 포함되어 있습니다.
    for name in sorted(available):
        if "cjk" in name.lower() and "sans" in name.lower():
            matplotlib.rcParams["font.family"] = name
            matplotlib.rcParams["axes.unicode_minus"] = False
            return

    print("⚠️  한글을 지원하는 폰트를 찾지 못했습니다 — 히트맵의 한글 라벨이 깨질 수 있습니다.\n"
          "   (Linux: `sudo apt install fonts-noto-cjk` 후 다시 실행해보세요. "
          "Mac/Windows는 보통 기본 폰트에 한글이 포함되어 있어 이 메시지가 안 뜹니다.)")


_configure_korean_font()

# 색맹 안전 다이버징 팔레트 (손실=주황, 중립=회색, 이익=파랑)
COLOR_LOSS = "#B35806"
COLOR_NEUTRAL = "#F5F5F5"
COLOR_PROFIT = "#1B7837"
CMAP = matplotlib.colors.LinearSegmentedColormap.from_list(
    "pnl_diverging", [COLOR_LOSS, COLOR_NEUTRAL, COLOR_PROFIT]
)


def summarize(trades_path: str) -> pd.DataFrame:
    if not os.path.exists(trades_path):
        raise FileNotFoundError(
            f"{trades_path} 파일이 없습니다. main.py(또는 backtest.py)를 먼저 실행해서 "
            "매매 기록을 쌓은 뒤 다시 시도하세요."
        )

    df = pd.read_csv(trades_path)
    sells = df[df["side"] == "sell"].copy()
    if sells.empty:
        raise ValueError(f"{trades_path}에 청산(sell) 기록이 아직 없습니다. "
                          "매매가 최소 1건 이상 완료된 뒤에 리포트를 생성할 수 있습니다.")

    def agg(group: pd.DataFrame) -> pd.Series:
        wins = (group["realized_pnl_krw"] > 0).sum()
        total = len(group)
        return pd.Series({
            "trades": total,
            "win_rate": wins / total if total else 0.0,
            "total_pnl_krw": group["realized_pnl_krw"].sum(),
            "avg_r_multiple": group["r_multiple"].dropna().mean(),
        })

    summary = sells.groupby("market").apply(agg, include_groups=False).reset_index()
    return summary.sort_values("avg_r_multiple", ascending=False).reset_index(drop=True)


def print_table(summary: pd.DataFrame) -> None:
    print(f"\n{'종목':<10}{'매매건수':>8}{'승률':>8}{'평균 R':>10}{'누적손익(KRW)':>16}")
    print("-" * 54)
    for _, row in summary.iterrows():
        print(f"{row['market']:<10}{int(row['trades']):>8}{row['win_rate']*100:>7.1f}%"
              f"{row['avg_r_multiple']:>+9.2f}R{row['total_pnl_krw']:>16,.0f}")


def render_heatmap(summary: pd.DataFrame, out_path: str, n_cols: int = 4) -> None:
    n = len(summary)
    n_cols = min(n_cols, n) or 1
    n_rows = int(np.ceil(n / n_cols))

    max_abs_r = max(summary["avg_r_multiple"].abs().max(), 0.1)  # 0 나눗셈 방지
    norm = matplotlib.colors.Normalize(vmin=-max_abs_r, vmax=max_abs_r)

    fig, ax = plt.subplots(figsize=(3.0 * n_cols, 2.2 * n_rows))
    ax.set_xlim(0, n_cols)
    ax.set_ylim(0, n_rows)
    ax.invert_yaxis()
    ax.axis("off")
    ax.set_title("코인별 백테스트/페이퍼트레이딩 결과 (실제 기록 기반)", fontsize=13, pad=14)

    for idx, row in summary.iterrows():
        col = idx % n_cols
        grid_row = idx // n_cols
        color = CMAP(norm(row["avg_r_multiple"]))

        rect = plt.Rectangle((col + 0.04, grid_row + 0.04), 0.92, 0.92,
                               facecolor=color, edgecolor="white", linewidth=2)
        ax.add_patch(rect)

        # 명도에 따라 텍스트 색을 밝게/어둡게 자동 대비 (가독성 확보)
        r, g, b = color[:3]
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text_color = "#111111" if luminance > 0.55 else "#FFFFFF"

        cx, cy = col + 0.5, grid_row + 0.5
        ax.text(cx, cy - 0.22, row["market"], ha="center", va="center",
                 fontsize=12, fontweight="bold", color=text_color)
        ax.text(cx, cy + 0.05, f"{row['avg_r_multiple']:+.2f}R", ha="center", va="center",
                 fontsize=14, fontweight="bold", color=text_color)
        ax.text(cx, cy + 0.28, f"승률 {row['win_rate']*100:.0f}% · {int(row['trades'])}건",
                 ha="center", va="center", fontsize=9, color=text_color)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"\n히트맵 이미지 저장: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="매매 기록으로 코인별 히트맵 리포트를 만듭니다.")
    parser.add_argument("--trades", default=config.TRADES_LOG_FILE,
                         help="집계할 trades.csv 경로 (기본: 라이브 로그)")
    parser.add_argument("--out", default=f"{config.LOG_DIR}/heatmap.png",
                         help="저장할 PNG 경로")
    args = parser.parse_args()

    summary = summarize(args.trades)
    print_table(summary)
    render_heatmap(summary, args.out)
