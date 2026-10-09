# -*- coding: utf-8 -*-

from pathlib import Path
from io import BytesIO
import math

import numpy as np
import pandas as pd

from drt_model_student import candidate_table, cycle_time


# ============================================================
# 파일 위치
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA = BASE_DIR / "data"
OUT = BASE_DIR / "outputs_daily"
FIG = OUT / "figures"

OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

BOARDINGS_FILE = DATA / "boardings_daily.csv"
ROUTES_FILE = DATA / "routes_daily.csv"


# ============================================================
# 연구 대상 / 공통 순서
# ============================================================

TARGET_ROUTES = {"68", "10", "36", "180", "520"}
ROUTE_ORDER = ["520", "180", "36", "10", "68"]


# ============================================================
# 수리모형 기본값
# ============================================================

MODEL_COMMON = {
    "b": 2.0,
    "kappa": 0.15,
    "a0": 2.0,
    "beta": 0.12,
    "theta": 5.0,
}


# ============================================================
# 비용 및 서비스 기준
# ============================================================
# 현재 최신 코드의 값을 그대로 사용.
# 최종 연구값이 바뀌면 아래 두 값만 수정하면 됨.

BUS_DAILY_COST = 834_327.0
DRT_DAILY_COST = 551_372.0
FARE = 1_500.0

MAX_WAIT = 10.0
MIN_RETENTION = 0.75

# 본문 기준안
BASE_AVOIDABLE_SHARE = 0.70

# 민감도 / 본문 / upper bound
SCENARIO_SHARES = [0.50, 0.70, 1.00]


# ============================================================
# 데이터 읽기
# ============================================================

boardings = pd.read_csv(
    BOARDINGS_FILE,
    dtype={"route_id": str},
)

routes = pd.read_csv(
    ROUTES_FILE,
    dtype={"route_id": str},
)

boardings["date"] = pd.to_datetime(boardings["date"])
boardings["boardings"] = pd.to_numeric(
    boardings["boardings"],
    errors="coerce",
)

# 2025년 11월만
boardings = boardings[
    (boardings["date"].dt.year == 2025)
    & (boardings["date"].dt.month == 11)
].copy()

# 토요일 / 일요일 제외
boardings = boardings[
    boardings["date"].dt.weekday < 5
].copy()

# 다섯 노선만
boardings = boardings[
    boardings["route_id"].isin(TARGET_ROUTES)
].copy()

routes = routes[
    routes["route_id"].isin(TARGET_ROUTES)
].copy()


# ============================================================
# 노선별 모수
# ============================================================

def make_params(route):
    return {
        **MODEL_COMMON,
        "tau_p": float(route["tau_p_min"]),
        "tau_b": float(route["tau_b_min"]),
        "tau_e": float(route["tau_e_min"]),
    }


# ============================================================
# 날짜별 분석
# ============================================================

results = []

for _, route in routes.iterrows():
    route_id = str(route["route_id"])
    label = str(route["label"])
    n_buses = int(route["n_buses"])
    operating_min = float(route["operating_min"])
    p = make_params(route)

    df_route = boardings[
        boardings["route_id"] == route_id
    ].copy()

    if df_route.empty:
        print(f"{route_id}번 데이터 없음")
        continue

    # 필요한 DRT 차량수 탐색범위
    max_daily_riders = float(df_route["boardings"].max())
    max_lambda = max_daily_riders / operating_min
    rough_M = math.ceil(
        max_lambda * cycle_time(p) / p["b"]
    )
    M_MAX = max(40, rough_M + 30)

    print()
    print("=" * 60)
    print(f"{label} 분석 중")
    print(f"DRT 차량수 1~{M_MAX}대 탐색")

    for _, day in df_route.iterrows():
        riders = float(day["boardings"])
        lam = riders / operating_min

        # candidate_table의 daily_net_cost는 baseline 값과 무관하게
        # DRT 운영비 - DRT 운임수입으로 계산된다.
        # baseline은 함수 인터페이스와 screening 계산을 위해 70%를 넣는다.
        policy = {
            "operating_min": operating_min,
            "baseline_daily_cost": (
                n_buses * BUS_DAILY_COST * BASE_AVOIDABLE_SHARE
            ),
            "drt_daily_cost": DRT_DAILY_COST,
            "fare": FARE,
            "eval_days": 1,
            "max_wait": MAX_WAIT,
            "min_retention": MIN_RETENTION,
        }

        table = candidate_table(
            lam,
            range(1, M_MAX + 1),
            p,
            policy,
        ).copy()

        # ----------------------------------------------------
        # 서비스 기준 통과 차량
        # ----------------------------------------------------
        service = table[
            table["service_pass"]
        ].copy()

        current_bus_total_cost = (
            n_buses * BUS_DAILY_COST
        )
        # 기존 버스 수입의 추정치: 일별 승차인원 x 가정 운임
        # 실제 운임 정산액이 아니므로 추후 실수입 자료로 검증해야 한다.
        current_bus_fare_revenue = riders * FARE

        if not service.empty:
            # 서비스에 필요한 최소 차량수: 실현가능성 판정용
            first_service = (
                service
                .sort_values("M")
                .iloc[0]
            )

            min_M = int(first_service["M"])
            wait = float(first_service["mean_wait_min"])
            retention = float(first_service["alpha"])

            # α* 계산용:
            # 서비스 기준을 통과하는 모든 안 중 DRT 순비용이 가장 낮은 안.
            # 기존 버스 운임수입을 반영한 이론적 손익분기 비율
            # α* = (DRT 순비용 + 기존 버스 운임수입) / 현재 버스 총운행비
            # 여기서는 서비스 기준만 적용한다. 실제 전환 여부는
            # 아래 실현가능성(min_M <= n_buses)을 별도로 확인한다.
            best_cost = (
                service
                .sort_values(["daily_net_cost", "M"])
                .iloc[0]
            )

            alpha_star_M = int(best_cost["M"])
            drt_net_cost_for_alpha_star = float(
                best_cost["daily_net_cost"]
            )
            alpha_star_day = (
                drt_net_cost_for_alpha_star + current_bus_fare_revenue
            ) / current_bus_total_cost

            # 실현가능성 조건
            feasible_day = (
                min_M <= n_buses
            )

        else:
            min_M = np.nan
            wait = np.nan
            retention = np.nan
            alpha_star_M = np.nan
            drt_net_cost_for_alpha_star = np.nan
            alpha_star_day = np.nan
            feasible_day = False

        # ----------------------------------------------------
        # 실현가능성 조건을 만족하는 서비스 후보
        # 필요 DRT 차량수 <= 현재 투입 버스 대수
        # ----------------------------------------------------
        feasible_service = service[
            service["M"] <= n_buses
        ].copy()

        if not feasible_service.empty:
            # 시나리오와 무관하게 DRT 순비용이 가장 낮은 실현가능 안
            best_feasible = (
                feasible_service
                .sort_values(["daily_net_cost", "M"])
                .iloc[0]
            )
            best_feasible_M = int(best_feasible["M"])
            best_feasible_drt_net_cost = float(
                best_feasible["daily_net_cost"]
            )
        else:
            best_feasible_M = np.nan
            best_feasible_drt_net_cost = np.nan

        # ----------------------------------------------------
        # 50 / 70 / 100% 시나리오
        # 70% = 본문 기준안
        # 100% = upper bound
        # ----------------------------------------------------
        scenario_values = {}

        for share in SCENARIO_SHARES:
            key = int(round(share * 100))
            # 전환 시 회피 가능한 버스 운행비
            baseline_cost = current_bus_total_cost * share
            # 기존 버스 운임수입 손실까지 반영한 비교용 순비용
            baseline_net_cost = baseline_cost - current_bus_fare_revenue
            scenario_values[f"bus_avoidable_cost_{key}"] = baseline_cost
            scenario_values[f"bus_net_cost_{key}"] = baseline_net_cost

            if np.isfinite(best_feasible_drt_net_cost):
                saving = baseline_net_cost - best_feasible_drt_net_cost
                scenario_values[f"drt_net_cost_{key}"] = (
                    best_feasible_drt_net_cost
                )
                scenario_values[f"daily_saving_{key}"] = saving
                scenario_values[f"economic_M_{key}"] = (
                    best_feasible_M if saving > 0 else np.nan
                )
            else:
                scenario_values[f"drt_net_cost_{key}"] = np.nan
                scenario_values[f"daily_saving_{key}"] = np.nan
                scenario_values[f"economic_M_{key}"] = np.nan

        results.append({
            "date": day["date"],
            "route_id": route_id,
            "label": label,
            "boardings": riders,
            "lambda_per_min": lam,
            "current_bus_count": n_buses,
            "current_bus_total_cost": current_bus_total_cost,
            "current_bus_fare_revenue": current_bus_fare_revenue,
            "operating_min": operating_min,
            "min_M_service": min_M,
            "mean_wait_at_min_M": wait,
            "retention_at_min_M": retention,
            "alpha_star_M": alpha_star_M,
            "drt_net_cost_for_alpha_star": drt_net_cost_for_alpha_star,
            "alpha_star_day": alpha_star_day,
            "feasible_day": feasible_day,
            "best_feasible_M": best_feasible_M,
            "best_feasible_drt_net_cost": best_feasible_drt_net_cost,
            **scenario_values,
        })


# ============================================================
# 날짜별 결과
# ============================================================

daily = pd.DataFrame(results)

if daily.empty:
    raise RuntimeError("분석 가능한 날짜별 결과가 없습니다.")

daily = daily.sort_values(
    ["route_id", "date"]
).reset_index(drop=True)

daily.to_csv(
    OUT / "daily_results.csv",
    index=False,
    encoding="utf-8-sig",
)


# ============================================================
# 노선별 요약
# ============================================================

summary_rows = []

for route_id, g in daily.groupby("route_id"):
    required_M = g["min_M_service"].dropna()
    alpha_values = g["alpha_star_day"].dropna()

    row = {
        "route_id": route_id,
        "label": g["label"].iloc[0],
        "weekday_days": len(g),
        "current_bus_count": int(g["current_bus_count"].iloc[0]),
        "mean_boardings": g["boardings"].mean(),
        "median_boardings": g["boardings"].median(),
        "p95_boardings": g["boardings"].quantile(0.95),
        "min_boardings": g["boardings"].min(),
        "max_boardings": g["boardings"].max(),
        "mean_lambda": g["lambda_per_min"].mean(),
        "median_required_M": required_M.median(),
        "p95_required_M": required_M.quantile(0.95),
        "max_required_M": required_M.max(),
        "feasible_day_rate": g["feasible_day"].mean(),
        "alpha_star": alpha_values.median(),
        "median_alpha_star_M": g["alpha_star_M"].dropna().median(),
        "median_drt_net_cost_alpha": (
            g["drt_net_cost_for_alpha_star"].dropna().median()
        ),
        "median_current_bus_total_cost": (
            g["current_bus_total_cost"].median()
        ),
        "median_current_bus_fare_revenue": (
            g["current_bus_fare_revenue"].median()
        ),
    }

    for share in SCENARIO_SHARES:
        key = int(round(share * 100))
        row[f"economic_day_rate_{key}"] = (
            g[f"economic_M_{key}"].notna().mean()
        )
        row[f"mean_daily_saving_{key}"] = (
            g[f"daily_saving_{key}"].mean()
        )
        # 보고서 표에서는 실현가능일의 일별 절감액 중앙값 사용
        row[f"median_daily_saving_{key}"] = (
            g[f"daily_saving_{key}"].median()
        )

    summary_rows.append(row)

summary = pd.DataFrame(summary_rows)
summary.to_csv(
    OUT / "route_summary.csv",
    index=False,
    encoding="utf-8-sig",
)


# ============================================================
# 그림 생성
# ============================================================

def make_figures(daily, summary):
    import matplotlib
    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.lines import Line2D

    # --------------------------------------------------------
    # 공통 스타일 - 보고서용 큰 글씨/굵은 선
    # --------------------------------------------------------
    C = {
        "blue": "#2a78d6",
        "blue2": "#7eafe8",
        "navy": "#183f73",
        "orange": "#eb6834",
        "gray": "#8d8d8d",
        "gray2": "#c8c8c8",
        "ink": "#111111",
        "ink2": "#595959",
        "grid": "#dedede",
        "surface": "#fcfcfb",
    }

    FS_TITLE = 22
    FS_AXIS = 16
    FS_TICK = 14
    FS_ROUTE = 18
    FS_LEGEND = 14
    FS_VALUE = 15
    FS_BOX = 13

    LW_MAIN = 3.0
    LW_STEM = 3.2
    LW_REF = 2.3
    MS_DOT = 180

    # --------------------------------------------------------
    # F1 / F2 전용 폰트 크기
    # F4는 위의 FS_* 값을 그대로 사용하므로 크기가 바뀌지 않음
    # --------------------------------------------------------
    F12_TITLE = 22
    F12_AXIS = 16
    F12_TICK = 14
    F12_ROUTE = 18
    F12_LEGEND = 14
    F12_VALUE = 15
    F12_BOX = 16

    # F2에서만 추가로 키울 요소
    F2_TITLE = 28
    F2_BOX = 26

    for name in (
        "Malgun Gothic",
        "AppleGothic",
        "NanumGothic",
        "Noto Sans CJK KR",
        "Noto Sans CJK JP",
    ):
        if any(
            f.name == name
            for f in font_manager.fontManager.ttflist
        ):
            plt.rcParams["font.family"] = name
            break

    plt.rcParams.update({
        "axes.unicode_minus": False,
        "figure.facecolor": C["surface"],
        "axes.facecolor": C["surface"],
        "savefig.facecolor": C["surface"],
        "axes.edgecolor": "#bdbdbd",
        "axes.linewidth": 1.0,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": C["ink2"],
        "ytick.color": C["ink2"],
        "axes.labelcolor": C["ink"],
        "axes.grid": True,
        "grid.color": C["grid"],
        "grid.linewidth": 0.9,
        "axes.axisbelow": True,
    })

    def save_figure(fig, filename):
        """
        Windows에서 기존 PNG가 열려 있거나 특정 파일명 쓰기가 막혀도
        분석 전체가 중단되지 않도록 안전하게 저장한다.
        """
        save_path = FIG / filename

        buffer = BytesIO()
        fig.savefig(
            buffer,
            format="png",
            dpi=220,
            bbox_inches="tight",
        )
        data = buffer.getvalue()
        buffer.close()

        final_path = save_path

        try:
            # 기존 파일이 있으면 먼저 제거 시도
            # (Windows에서 이미지 뷰어 등이 파일을 잡고 있으면 실패할 수 있음)
            if save_path.exists():
                try:
                    save_path.unlink()
                except OSError:
                    pass

            with open(str(save_path), "wb") as f:
                f.write(data)

        except OSError as e:
            # 원래 파일명 저장이 막히면 새 파일명으로 자동 저장
            stem = save_path.stem
            suffix = save_path.suffix
            n = 1

            while True:
                fallback = FIG / f"{stem}_new{n}{suffix}"
                if not fallback.exists():
                    break
                n += 1

            with open(str(fallback), "wb") as f:
                f.write(data)

            final_path = fallback
            print(f"주의: {save_path.name} 저장 실패 ({e})")
            print("대체 파일명으로 저장:", final_path)

        plt.close(fig)
        print("그림 저장:", final_path)

    # 이번 보고서에서는 F1/F2/F4만 사용.
    # 예전 실행의 F0/F3/F5가 남아 혼동되지 않도록 기존 파일은 제거.
    for old_name in (
        "F0_headline_heatmap.png",
        "F3_boardings_strip_log.png",
        "F5_daily_boardings.png",
    ):
        old_path = FIG / old_name
        if old_path.exists():
            old_path.unlink()

    d = daily.copy()
    s = summary.copy()

    d["route_id"] = d["route_id"].astype(str)
    s["route_id"] = s["route_id"].astype(str)
    d["date"] = pd.to_datetime(d["date"])

    d = d[d["route_id"].isin(ROUTE_ORDER)].copy()
    s = s[s["route_id"].isin(ROUTE_ORDER)].copy()

    order_map = {
        rid: i
        for i, rid in enumerate(ROUTE_ORDER)
    }

    d["route_order"] = d["route_id"].map(order_map)
    s["route_order"] = s["route_id"].map(order_map)

    d = d.sort_values(
        ["route_order", "date"]
    ).reset_index(drop=True)
    s = s.sort_values(
        "route_order"
    ).reset_index(drop=True)

    # ========================================================
    # F1. 노선별 손익분기 회피원가 비율 α*
    # ========================================================
    alpha_plot = s[[
        "route_id",
        "alpha_star",
    ]].copy()

    x = np.arange(len(alpha_plot))
    y = alpha_plot["alpha_star"].astype(float).to_numpy()

    fig, ax = plt.subplots(figsize=(13.0, 7.0))
    fig.subplots_adjust(
        left=0.07,
        right=0.95,
        top=0.87,
        bottom=0.15,
    )

    ax.axhline(
        BASE_AVOIDABLE_SHARE,
        color=C["orange"],
        linewidth=LW_REF,
        linestyle=(0, (6, 4)),
        zorder=1,
    )

    # 점선과 정확히 같은 높이에 70% 기준 표시
    ax.text(
        0.015,
        BASE_AVOIDABLE_SHARE,
        "70% 기준",
        transform=ax.get_yaxis_transform(),
        ha="left",
        va="center",
        fontsize=F12_AXIS,
        fontweight="bold",
        color=C["orange"],
        bbox=dict(
            boxstyle="round,pad=0.15",
            facecolor="white",
            edgecolor="none",
            alpha=0.90,
        ),
        zorder=5,
    )

    for xi, yi in zip(x, y):
        if not np.isfinite(yi):
            continue

        ax.vlines(
            xi,
            0,
            yi,
            color=C["blue"],
            linewidth=LW_STEM,
            zorder=2,
        )

        ax.scatter(
            xi,
            yi,
            s=MS_DOT,
            color=C["blue"],
            edgecolor="white",
            linewidth=1.8,
            zorder=3,
        )

        ax.text(
            xi,
            yi + 0.045,
            f"{yi:.2f}",
            ha="center",
            va="bottom",
            fontsize=F12_VALUE,
            fontweight="bold",
            color=C["ink"],
        )

    ax.set_xticks(x)
    ax.set_xticklabels(
        alpha_plot["route_id"],
        fontsize=F12_ROUTE,
        fontweight="bold",
    )
    ax.tick_params(axis="y", labelsize=F12_TICK)

    # 제목과 중복되므로 왼쪽 세로축 제목은 제거
    ax.set_ylabel("")
    ax.set_xlabel("")

    ax.set_title(
        "노선별 손익분기 회피원가 비율 α*",
        loc="left",
        fontsize=F12_TITLE,
        fontweight="bold",
        pad=16,
    )

    ax.grid(axis="x", visible=False)

    valid_y = y[np.isfinite(y)]
    ymax = (
        max(1.10, float(np.nanmax(valid_y)) + 0.16)
        if len(valid_y)
        else 1.10
    )
    ax.set_ylim(0, ymax)
    ax.set_xlim(-0.20, len(x) - 0.80)

    save_figure(
        fig,
        "F1_alpha_star_lollipop.png",
    )

    # ========================================================
    # F2. 회피가능 버스 순비용과 DRT 순비용 비교
    # 한 날짜에서 차액을 구한 뒤 중앙값을 내는 절감액과,
    # 두 비용 각각의 중앙값 차이는 일반적으로 서로 다르다.
    # F2 검수용 CSV에 두 가지 수치를 구분해 저장한다.
    # ========================================================
    cost_rows = []

    for rid in ROUTE_ORDER:
        g = d[d["route_id"] == rid]
        if g.empty:
            continue

        current_bus_count = int(g["current_bus_count"].iloc[0])
        required_M = float(g["min_M_service"].median())
        feasible_days = int(g["feasible_day"].sum())
        feasible = feasible_days > 0
        gf = g[g["feasible_day"]].copy()

        row = {
            "route_id": rid,
            "bus_full": float(g["current_bus_total_cost"].median()),
            "bus_fare": float(g["current_bus_fare_revenue"].median()),
            "drt": (
                float(gf["best_feasible_drt_net_cost"].median())
                if feasible else np.nan
            ),
            "current_bus_count": current_bus_count,
            "required_M": required_M,
            "feasible_days": feasible_days,
            "total_days": len(g),
            "feasible": feasible,
        }

        for pct in (50, 70, 100):
            # 두 점은 실현가능일에 한정한 각 비용의 중앙값
            row[f"bus_net_{pct}"] = (
                float(gf[f"bus_net_cost_{pct}"].median())
                if feasible else np.nan
            )
            row[f"median_daily_saving_{pct}"] = (
                float(gf[f"daily_saving_{pct}"].median())
                if feasible else np.nan
            )
            row[f"economic_days_{pct}"] = int(g[f"economic_M_{pct}"].notna().sum())
        cost_rows.append(row)

    cost_plot = pd.DataFrame(cost_rows)

    # 표 검수용: 회피가능 운행비, 운임수입, 순비용, 일별 절감액 중앙값을 별도 기록
    check_table = cost_plot.copy()
    for pct in (50, 70, 100):
        check_table[f"bus_avoidable_cost_{pct}"] = check_table["bus_full"] * (pct / 100)
    check_table.to_csv(
        OUT / "F2_cost_check.csv",
        index=False,
        encoding="utf-8-sig",
    )

    fig, ax = plt.subplots(figsize=(15.5, 9.2))
    fig.subplots_adjust(
        left=0.08,
        right=0.98,
        top=0.88,
        bottom=0.33,
    )

    y0 = np.arange(len(cost_plot))
    offsets = {
        50: -0.22,
        70: 0.00,
        100: 0.22,
    }
    scenario_style = {
        50: (C["gray2"], "50% 민감도"),
        70: (C["blue"], "70% 본문 기준"),
        100: (C["navy"], "100% upper bound"),
    }

    # 실현가능한 노선만 덤벨 표시
    for pct in (50, 70, 100):
        share = pct / 100.0
        color, _ = scenario_style[pct]
        yy = y0 + offsets[pct]

        for _, row in cost_plot.iterrows():
            i = int(row.name)

            if (
                not row["feasible"]
                or not np.isfinite(row["drt"])
            ):
                continue

            # 비교 대상: 회피가능 버스 운행비 - 기존 버스 운임수입
            current_cost = row[f"bus_net_{pct}"] / 10000.0
            drt_cost = row["drt"] / 10000.0

            ax.plot(
                [current_cost, drt_cost],
                [yy[i], yy[i]],
                color=color,
                linewidth=LW_MAIN,
                zorder=2,
            )
            ax.scatter(
                current_cost,
                yy[i],
                s=140,
                color=color,
                zorder=3,
            )
            ax.scatter(
                drt_cost,
                yy[i],
                s=140,
                facecolor="white",
                edgecolor=color,
                linewidth=2.4,
                zorder=3,
            )

    # 실현가능성 미충족 안내
    # 각 노선 행의 중앙에 한 줄로 표시
    box_y_adjust = {
        "36": 0.00,
        "10": 0.00,
        "68": 0.00,
    }
    box_x_pos = {
        "36": 0.50,
        "10": 0.50,
        "68": 0.50,
    }

    for _, row in cost_plot.iterrows():
        if row["feasible"]:
            continue

        i = int(row.name)
        rid = str(row["route_id"])

        if np.isfinite(row["required_M"]):
            reason = (
                f"실현가능성 미충족 · 필요 {row['required_M']:.0f}대 > "
                f"현재 {int(row['current_bus_count'])}대"
            )
        else:
            reason = "실현가능성 미충족 · 서비스 기준 통과안 없음"

        ax.text(
            box_x_pos.get(rid, 0.50),
            y0[i] + box_y_adjust.get(rid, 0.0),
            reason,
            transform=ax.get_yaxis_transform(),
            ha="center",
            va="center",
            fontsize=F2_BOX,
            fontweight="bold",
            color=C["ink2"],
            bbox=dict(
                boxstyle="round,pad=0.42",
                facecolor="white",
                edgecolor="#cfcfcf",
                linewidth=1.4,
                alpha=0.98,
            ),
            zorder=6,
        )

    ax.set_yticks(y0)
    ax.set_yticklabels(
        cost_plot["route_id"],
        fontsize=F12_ROUTE,
        fontweight="bold",
    )

    # ROUTE_ORDER = 520 -> 180 -> 36 -> 10 -> 68 이므로
    # invert_yaxis()를 쓰지 않아 520이 맨 아래에 위치한다.
    ax.set_ylim(-0.55, len(cost_plot) - 0.45)

    ax.set_xlabel(
        "하루 비용 (만원)",
        fontsize=F12_AXIS,
        fontweight="bold",
        labelpad=10,
    )
    ax.set_title(
        "회피가능 버스 순비용과 DRT 순비용 비교",
        loc="left",
        fontsize=F2_TITLE,
        fontweight="bold",
        pad=16,
    )
    ax.tick_params(axis="x", labelsize=F12_TICK)
    ax.grid(axis="y", visible=False)

    legend_handles = [
        Line2D(
            [0], [0],
            color=scenario_style[pct][0],
            lw=LW_MAIN,
            marker="o",
            markersize=10,
            label=scenario_style[pct][1],
        )
        for pct in (50, 70, 100)
    ]

    legend1 = ax.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.11),
        ncol=3,
        frameon=False,
        fontsize=F12_LEGEND,
    )
    ax.add_artist(legend1)

    point_handles = [
        Line2D(
            [0], [0],
            marker="o",
            linestyle="None",
            markerfacecolor=C["ink2"],
            markeredgecolor=C["ink2"],
            markersize=11,
            label="회피가능 버스 순비용",
        ),
        Line2D(
            [0], [0],
            marker="o",
            linestyle="None",
            markerfacecolor="white",
            markeredgecolor=C["ink2"],
            markeredgewidth=2.0,
            markersize=11,
            label="DRT 순비용",
        ),
    ]

    ax.legend(
        handles=point_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.22),
        ncol=2,
        frameon=False,
        fontsize=F12_LEGEND,
    )

    save_figure(
        fig,
        "F2_cost_dumbbell.png",
    )

    # F4 전용 폰트 확대 (기존 대비 약 2배)
    F4_TITLE = 42
    F4_AXIS = 30
    F4_TICK = 26
    F4_ROUTE = 34
    F4_LEGEND = 26

    # ========================================================
    # F4. 날짜별 최소 필요 DRT 차량수
    #
    # 기존 그래프 스타일 / 폰트 크기 / y축 유지
    # 위치만 오륜기 모양
    # 위 : 180 / 36 / 10
    # 아래 : 520 / 68
    # ========================================================

    mosaic = [
        ["180", "180", "36", "36", "10", "10"],
        [".", "520", "520", "68", "68", "."],
    ]

    fig, ax_map = plt.subplot_mosaic(
        mosaic,
        figsize=(24, 14),
        empty_sentinel=".",
    )

    fig.subplots_adjust(
        left=0.09,
        right=0.99,
        top=0.86,
        bottom=0.16,
        wspace=0.85,
        hspace=0.48,
    )

    fig.suptitle(
        "날짜별 최소 필요 DRT 차량수",
        x=0.09,
        ha="left",
        fontsize=F4_TITLE,
        fontweight="bold",
    )

    plot_order = [
        "180",
        "36",
        "10",
        "520",
        "68",
    ]

    for rid in plot_order:
        ax = ax_map[rid]

        g = (
            d[d["route_id"] == rid]
            .sort_values("date")
            .reset_index(drop=True)
        )

        if g.empty:
            ax.set_visible(False)
            continue

        xx = np.arange(len(g))
        yy = (
            g["min_M_service"]
            .astype(float)
            .to_numpy()
        )
        current_bus_count = int(
            g["current_bus_count"].iloc[0]
        )

        ax.step(
            xx,
            yy,
            where="mid",
            color=C["blue"],
            linewidth=LW_MAIN,
            zorder=3,
        )

        ax.scatter(
            xx,
            yy,
            color=C["blue"],
            s=38,
            zorder=4,
        )

        ax.axhline(
            current_bus_count,
            color=C["orange"],
            linewidth=LW_REF,
            linestyle=(0, (5, 4)),
            zorder=2,
        )

        idx = np.unique(
            np.linspace(
                0,
                len(g) - 1,
                4,
                dtype=int,
            )
        )

        ax.set_xticks(idx)
        ax.set_xticklabels(
            [
                g["date"].iloc[i].strftime("%m-%d")
                for i in idx
            ],
            fontsize=F4_TICK,
        )

        # x축 날짜와 y축 0이 너무 가까워 보이지 않도록 간격만 조정
        ax.tick_params(
            axis="x",
            labelsize=F4_TICK,
            pad=12,
        )
        ax.tick_params(
            axis="y",
            labelsize=F4_TICK,
            pad=10,
        )

        ax.set_title(
            rid,
            fontsize=F4_ROUTE,
            fontweight="bold",
            pad=12,
        )

        # 각 패널의 기존 독립 y축 범위 유지
        if np.isfinite(yy).any():
            ymax = max(
                float(np.nanmax(yy)),
                float(current_bus_count),
            )
            ax.set_ylim(0, ymax * 1.12)
        else:
            ax.set_ylim(
                0,
                max(current_bus_count * 1.12, 1),
            )

        ax.grid(axis="x", visible=False)

        # 왼쪽 패널의 y축 제목은 그래프에서 조금 더 떨어뜨림
        if rid == "180":
            ax.set_ylabel(
                "최소 필요 DRT 차량수 (대)",
                fontsize=F4_AXIS,
                fontweight="bold",
                labelpad=24,
            )

    legend_handles = [
        Line2D(
            [0], [0],
            color=C["blue"],
            linewidth=LW_MAIN,
            label="최소 필요 DRT 차량수",
        ),
        Line2D(
            [0], [0],
            color=C["orange"],
            linewidth=LW_REF,
            linestyle=(0, (5, 4)),
            label="현재 투입 버스 대수",
        ),
    ]

    fig.legend(
        handles=legend_handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.035),
        ncol=2,
        frameon=False,
        fontsize=F4_LEGEND,
    )

    save_figure(
        fig,
        "F4_required_M_step.png",
    )


# ============================================================
# 그림 생성 실행
# ============================================================

make_figures(
    daily,
    summary,
)


# ============================================================
# 화면 출력
# ============================================================

print()
print("=" * 60)
print("분석 완료")
print("=" * 60)
print()
print(
    summary
    .round(2)
    .to_string(index=False)
)
print()
print("날짜별 결과:", OUT / "daily_results.csv")
print("노선별 요약:", OUT / "route_summary.csv")
print("그림 폴더:", FIG)
print()
print("생성 그림 3개")
print("1.", FIG / "F1_alpha_star_lollipop.png")
print("2.", FIG / "F2_cost_dumbbell.png")
print("3.", FIG / "F4_required_M_step.png")
