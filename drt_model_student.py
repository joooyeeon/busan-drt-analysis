#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
부산 저수요 버스노선의 감차·DRT 전환 수리모델 — 학생용 실행 코드
====================================================================

이 파일은 해설 PDF(busan_DRT_model)의 식을 그대로 코드로 옮긴 것이다.
식 번호는 PDF와 같다. 위에서 아래로 읽으면 된다.

  1부  모수(파라미터)               PDF 10쪽의 가상 예시 값
  2부  하루 평균 대기시간과 선택확률   식 (5.4), (10.1)
  3부  평형 이용비율 alpha            식 (3.3)의 정상상태  alpha = P_D(W(alpha))
  4부  비용과 1차 판정               PDF 10쪽 표
  5부  느린 시간 s(일): 습관이 바뀐다  식 (3.3) 미분방정식
  6부  빠른 시간 t(분): 하루 운영     식 (4.2) 연립미분방정식
  7부  가상 데이터 만들기             Poisson 통행 발생
  8부  민감도(수요·빈차이동시간)
  9부  실행 (main)

중요: 모든 숫자는 학습용 가정값이다. 실제 부산 데이터가 아니다.
      이 모형은 "평균"만 계산한다. 개별 차량 경로(DARP)나 P95 대기시간은 계산하지 않는다.

실행:  python drt_model_student.py
       (outputs/ 폴더에 CSV와 JSON이 생긴다. 그림은 drt_figures.py가 그린다.)
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp      # 미분방정식 수치해
from scipy.optimize import brentq          # 방정식 f(x)=0 의 근


# =====================================================================
# 1부. 모수 — 시간 단위는 분, 인원은 명, 차량은 대. theta 만 일(day) 단위
# =====================================================================
MODEL = {
    "b": 2.0,          # 한 번 배정될 때 함께 타는 평균 인원 (정원이 아님)
    "kappa": 0.15,     # 배정계수 [1/(명·분)] : 승객 q명, 빈차 I대이면 분당 kappa*q*I 건 배정
    "tau_p": 3.0,      # 픽업 이동 평균시간 (분)
    "tau_b": 12.0,     # 승객 수송 평균시간 (분)
    "tau_e": 5.0,      # 빈차 재배치 평균시간 (분)
    "a0": 2.0,         # DRT의 기본 선호도 (요금·환승 부담을 뺀 값)
    "beta": 0.12,      # 대기 1분당 효용 감소
    "theta": 5.0,      # 이용비율이 새 서비스에 적응하는 데 걸리는 시간척도 (일)
}

POLICY = {
    "operating_min": 600.0,        # 하루 운영시간 (분)
    "baseline_daily_cost": 1_800_000.0,   # 기존 버스 2대 하루 운영비 (원)
    "drt_daily_cost": 400_000.0,   # DRT 1대 하루 운영비 (원)
    "fare": 1_500.0,               # 1통행당 배분 수입 (원)
    "eval_days": 365,              # 재정 비교 기간 (일)
    "max_wait": 10.0,              # 평균 대기시간 상한 (분)
    "min_retention": 0.75,         # 유지율 하한
}

LAMBDA = 0.2   # 기존 수요율 (명/분). 600분 × 0.2 = 하루 120명


def cycle_time(p):
    """차량이 한 승객을 처리하는 데 걸리는 전체 시간  tau_c = tau_p + tau_b + tau_e"""
    return p["tau_p"] + p["tau_b"] + p["tau_e"]


# =====================================================================
# 2부. 정상상태 평균 대기시간(식 5.4)과 DRT 선택확률(식 10.1)
# =====================================================================
def mean_wait(alpha, M, lam, p):
    """식 (5.4):  W = tau_p + 1 / (kappa * (b*M - alpha*lam*tau_c))

    b*M 은 차량이 처리할 수 있는 양, alpha*lam*tau_c 는 실제로 묶여 있는 양.
    둘의 차이(여유)가 0에 가까워지면 대기시간이 급격히 커진다.
    여유가 0 이하이면 승객이 무한히 쌓이므로 inf 를 돌려준다.
    """
    slack = p["b"] * M - alpha * lam * cycle_time(p)
    if slack <= 0:
        return math.inf
    return p["tau_p"] + 1.0 / (p["kappa"] * slack)


def choice_prob(wait, p):
    """식 (10.1): 이항 로짓  P_D = 1 / (1 + exp(-(a0 - beta*W)))
    대기시간이 길수록 DRT를 고르는 확률이 낮아진다."""
    utility = p["a0"] - p["beta"] * wait
    if utility < -700:             # exp 오버플로 방지
        return 0.0
    return 1.0 / (1.0 + math.exp(-utility))


# =====================================================================
# 3부. 평형 이용비율:  alpha = P_D( W(alpha, M) )  를 푼다
# =====================================================================
def equilibrium(M, lam, p):
    """f(alpha) = P_D(W(alpha)) - alpha 의 근을 찾는다.

    alpha가 커지면 W가 커지고 P_D는 작아지므로 f는 감소함수 → 근이 하나뿐이다.
    alpha 의 탐색범위: 0 부터 '용량 한계' b*M/(lam*tau_c) (또는 1) 직전까지.
    핵심: alpha 는 우리가 정하는 값이 아니라 차량수 M 에 따라 '결정되는' 값이다.
    """
    cap = p["b"] * M / (lam * cycle_time(p)) if lam > 0 else math.inf
    upper = min(1.0, cap * (1 - 1e-12))

    def f(alpha):
        return choice_prob(mean_wait(alpha, M, lam, p), p) - alpha

    alpha = brentq(f, 0.0, upper, xtol=1e-13, rtol=1e-13)
    a = alpha * lam                                  # DRT 호출률 (명/분)
    I_star = M - a * cycle_time(p) / p["b"]          # 식 (5.2) 가용 차량
    return {
        "M": M, "lambda_per_min": lam, "alpha": alpha,
        "mean_wait_min": mean_wait(alpha, M, lam, p),
        "q_star": a / (p["b"] * p["kappa"] * I_star),   # 식 (5.2) 배정 대기 인원
        "I_star": I_star,
        "P_star": a * p["tau_p"] / p["b"],              # 식 (5.1)
        "B_star": a * p["tau_b"] / p["b"],
        "E_star": a * p["tau_e"] / p["b"],
        "utilization": a * cycle_time(p) / (p["b"] * M),
    }


# =====================================================================
# 4부. 비용 계산과 1차 판정 (PDF 10쪽 표)
# =====================================================================
def candidate_table(lam, vehicle_list, p, pol):
    """차량수 M 마다 평형을 풀고, 하루 순재정비용과 통과 여부를 표로 만든다."""
    daily_trips = lam * pol["operating_min"]
    base_net = pol["baseline_daily_cost"] - pol["fare"] * daily_trips   # 현행 순비용
    rows = []
    for M in vehicle_list:
        r = equilibrium(M, lam, p)
        op_cost = M * pol["drt_daily_cost"]
        revenue = pol["fare"] * daily_trips * r["alpha"]  # 남은 승객만 수입이 된다
        net = op_cost - revenue
        saving = pol["eval_days"] * (base_net - net)      # 평가기간 총 절감액

        wait_ok = r["mean_wait_min"] <= pol["max_wait"]
        retention_ok = r["alpha"] >= pol["min_retention"]
        r.update({
            "daily_drt_cost": op_cost,
            "daily_revenue": revenue,
            "daily_net_cost": net,
            "baseline_daily_net_cost": base_net,
            "daily_saving": base_net - net,
            "period_saving": saving,
            "wait_ok": wait_ok,
            "retention_ok": retention_ok,
            "service_pass": wait_ok and retention_ok,
            "screening_pass": wait_ok and retention_ok and saving > 0,
        })
        rows.append(r)
    return pd.DataFrame(rows)


def choose(table):
    """서비스 기준과 절감을 모두 만족하는 안 중 순비용이 가장 작은 M."""
    ok = table[table["screening_pass"]]
    if len(ok) == 0:
        return {"decision": "retain_baseline", "M": None}
    best = ok.sort_values(["daily_net_cost", "M"]).iloc[0]
    return {"decision": "DRT_candidate", "M": int(best["M"]),
            "alpha": float(best["alpha"]),
            "mean_wait_min": float(best["mean_wait_min"]),
            "daily_saving": float(best["daily_saving"])}


# =====================================================================
# 5부. 느린 시간 s(일): 이용비율이 서서히 바뀐다 — 식 (3.3)
#      theta * d(alpha)/ds = P_D( W(alpha, M) ) - alpha
# =====================================================================
def simulate_adaptation(M, lam, p, days=30.0, alpha0=0.1):
    """하루 운영은 늘 평형에 있다고 보고(준정상), 습관 alpha 만 천천히 움직인다."""
    def rhs(s, y):
        alpha = y[0]
        return [(choice_prob(mean_wait(alpha, M, lam, p), p) - alpha) / p["theta"]]

    sol = solve_ivp(rhs, (0, days), [alpha0], t_eval=np.linspace(0, days, 301),
                    max_step=0.1, rtol=1e-8, atol=1e-10)
    return pd.DataFrame({"s_day": sol.t, "M": M, "alpha": sol.y[0]})


# =====================================================================
# 6부. 빠른 시간 t(분): 하루 동안 승객과 차량을 함께 추적 — 식 (4.2)
#      상태 y = [q, I, P, B, E, done, wait_area]
#        q    : 아직 배정 못 받은 승객 (명)
#        I    : 대기 중인 빈차,  P: 픽업 이동 중,  B: 승객 수송 중,  E: 빈차 재배치 중 (대)
#        done : 수송을 마친 누적 승객 (명)           ← 유지율 계산용
#        wait_area : 누적 대기 인·분 = ∫(q + b*P)dt  ← 평균 대기시간 계산용
# =====================================================================
STATES = ["q", "I", "P", "B", "E", "done", "wait_area"]


def daily_rhs(t, y, lam, alpha, p):
    q, I, P, B, E, done, wait_area = y
    j = p["kappa"] * q * I                  # 식 (4.1) 분당 배정 건수
    return [
        alpha * lam - p["b"] * j,           # dq/dt : 들어온 승객 - 배정된 승객
        E / p["tau_e"] - j,                 # dI/dt : 재배치 끝난 차 - 배정 나간 차
        j - P / p["tau_p"],                 # dP/dt
        P / p["tau_p"] - B / p["tau_b"],    # dB/dt
        B / p["tau_b"] - E / p["tau_e"],    # dE/dt
        p["b"] * B / p["tau_b"],            # d(done)/dt : 수송 완료 승객
        q + p["b"] * P,                     # d(wait_area)/dt : 지금 기다리는 인원
    ]


def simulate_day(profile, M, alpha, p):
    """profile: start_min, end_min, rate_per_min 열을 가진 표 (15분 단위 수요).
    구간마다 수요율만 바꾸고 상태는 그대로 이어서 적분한다.
    운영 종료 후에는 수요를 0으로 두고 남은 승객이 모두 내릴 때까지 계속 돌린다.
    alpha 는 하루 동안 고정 (습관은 하루 안에 바뀌지 않는다고 본다)."""
    y = np.zeros(7)
    y[1] = M                                 # 처음엔 모든 차가 빈차 대기
    pieces = []

    def run(t0, t1, lam):
        nonlocal y
        n = int(math.ceil(t1 - t0)) + 1      # 1분 간격으로 저장
        sol = solve_ivp(daily_rhs, (t0, t1), y, args=(lam, alpha, p),
                        t_eval=np.linspace(t0, t1, n), max_step=1.0,
                        rtol=1e-8, atol=1e-10)
        df = pd.DataFrame(sol.y.T, columns=STATES)
        df.insert(0, "t_min", sol.t)
        if pieces:
            df = df.iloc[1:]                 # 구간 경계 시각은 한 번만 저장
        pieces.append(df)
        y = sol.y[:, -1].copy()

    # (1) 운영시간: 구간별 수요율로 적분
    for _, seg in profile.iterrows():
        run(seg["start_min"], seg["end_min"], seg["rate_per_min"])
    close_time = float(profile["end_min"].iloc[-1])
    done_at_close = y[5]

    # (2) 운영 종료 후: 수요 0, 남은 승객이 다 내리고 차가 다 돌아올 때까지 60분씩 연장
    t_end = close_time
    for _ in range(10):
        remaining = y[0] + p["b"] * (y[2] + y[3])   # q + b*(P+B)
        if remaining <= 1e-8 and abs(M - y[1]) <= 1e-7:
            break
        run(t_end, t_end + 60.0, 0.0)
        t_end += 60.0

    traj = pd.concat(pieces, ignore_index=True)
    traj["M"], traj["alpha"] = M, alpha

    # (3) 검산: 차량 총수 I+P+B+E = M 이 항상 유지되는가
    fleet_error = float((traj[["I", "P", "B", "E"]].sum(axis=1) - M).abs().max())
    assert fleet_error < 1e-6, "차량 보존 오류 — 적분 정확도를 점검하세요"

    total_demand = float(((profile["end_min"] - profile["start_min"])
                          * profile["rate_per_min"]).sum())
    requested = alpha * total_demand         # DRT를 부른 승객 수
    metrics = {
        "total_demand": total_demand,
        "drt_requested": requested,
        "completed_at_close": float(done_at_close),
        "eta_at_close": float(done_at_close / total_demand),   # 운영 종료 시점 유지율
        "eta_after_drain": float(y[5] / total_demand),
        "mean_wait_min": float(y[6] / requested),             # 누적 대기 ÷ 요청 수
        "max_queue": float(traj["q"].max()),
        "end_time_min": t_end,
        "fleet_error": fleet_error,
    }
    return traj, metrics


def demand_profile(lam=LAMBDA, peaked=True, minutes=600, step=15):
    """15분 단위 수요 프로파일. peaked=True 면 아침·저녁 두 첨두, False 면 일정.
    두 경우의 하루 총 수요는 정확히 같다."""
    start = np.arange(0, minutes, step, dtype=float)
    mid = start + step / 2
    shape = np.ones_like(mid)
    if peaked:
        shape = (0.35 + 1.8 * np.exp(-0.5 * ((mid - 130) / 35) ** 2)
                 + 1.4 * np.exp(-0.5 * ((mid - 450) / 50) ** 2))
        shape = shape / shape.mean()          # 평균을 1로 맞춰 총량을 같게
    return pd.DataFrame({"start_min": start, "end_min": start + step,
                         "rate_per_min": lam * shape})


# =====================================================================
# 7부. 가상 통행 데이터 — 실제 데이터를 넣기 전 모형 테스트용
# =====================================================================
def make_synthetic_data(seed=20260921, days=28):
    """날마다 수요가 ±10% 정도 흔들리고, 15분 구간의 통행수는 Poisson 으로 발생한다."""
    rng = np.random.default_rng(seed)
    base = demand_profile()
    frames = []
    for day in range(1, days + 1):
        factor = rng.lognormal(mean=-0.5 * 0.1 ** 2, sigma=0.1)   # 평균 1
        f = base.copy()
        f["true_rate_per_min"] = f["rate_per_min"] * factor
        dt = f["end_min"] - f["start_min"]
        f["trip_count"] = rng.poisson(f["true_rate_per_min"] * dt)
        f["observed_rate_per_min"] = f["trip_count"] / dt
        f["day"] = day
        frames.append(f.drop(columns="rate_per_min"))
    return pd.concat(frames, ignore_index=True)


# =====================================================================
# 8부. 민감도: 수요율과 빈차 이동시간이 바뀌면 필요한 최소 차량수는?
# =====================================================================
def sensitivity_grid(p, pol):
    rows = []
    for tau_e in (3.0, 5.0, 10.0, 15.0):
        p2 = dict(p, tau_e=tau_e)            # tau_e 만 바꾼 복사본
        for lam in np.linspace(0.05, 0.8, 31):
            tab = candidate_table(lam, range(1, 13), p2, pol)
            ok = tab[tab["service_pass"]]
            rows.append({"lambda_per_min": lam, "tau_e_min": tau_e,
                         "min_M_service": int(ok["M"].min()) if len(ok) else np.nan,
                         "best_M": choose(tab)["M"]})
    return pd.DataFrame(rows)


# =====================================================================
# 9부. 실행
# =====================================================================
def run_all(out="outputs", seed=20260921):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    p, pol = MODEL, POLICY
    save = lambda df, name: df.to_csv(out / name, index=False, encoding="utf-8-sig")

    # (a) PDF 10쪽 표 재현: 일정 수요 0.2명/분, 차량 1~6대
    ref = candidate_table(LAMBDA, range(1, 7), p, pol)
    save(ref, "reference_candidates.csv")

    # (b) 느린 시간: 이용비율 적응 30일
    adap = pd.concat([simulate_adaptation(M, LAMBDA, p) for M in (1, 2, 3, 4)],
                     ignore_index=True)
    save(adap, "adaptation_timeseries.csv")

    # (c) 빠른 시간: 같은 총수요의 일정 수요 vs 첨두 수요 하루 운영
    peak, flat = demand_profile(peaked=True), demand_profile(peaked=False)
    save(peak, "synthetic_peak_profile.csv")
    stress = []
    for M in (2, 3, 4):
        alpha = equilibrium(M, LAMBDA, p)["alpha"]     # 평형 alpha 를 고정
        for name, prof in (("constant", flat), ("peaked", peak)):
            traj, met = simulate_day(prof, M, alpha, p)
            stress.append({"profile": name, "M": M, "alpha_fixed": alpha, **met})
            save(traj, f"operation_{name}_M{M}.csv")
    save(pd.DataFrame(stress), "stress_metrics.csv")

    # (d) 가상 28일 데이터: 1~21일로 수요율 추정, 22~28일은 검증
    data = make_synthetic_data(seed)
    save(data, "synthetic_demand_28days.csv")
    train = data[data["day"] <= 21]
    lam_hat = train["trip_count"].sum() / (train["end_min"] - train["start_min"]).sum()
    save(candidate_table(lam_hat, range(1, 7), p, pol), "training_candidates.csv")
    holdout = []
    for day, g in data[data["day"] > 21].groupby("day"):
        prof = g[["start_min", "end_min", "observed_rate_per_min"]].rename(
            columns={"observed_rate_per_min": "rate_per_min"})
        for M in (2, 3, 4):
            alpha = equilibrium(M, lam_hat, p)["alpha"]
            _, met = simulate_day(prof, M, alpha, p)
            holdout.append({"day": int(day), "M": M, "alpha_fixed": alpha, **met})
    save(pd.DataFrame(holdout), "holdout_day_metrics.csv")

    # (e) 민감도 표
    save(sensitivity_grid(p, pol), "sensitivity_grid.csv")

    # (f) alpha 를 바깥에서 고정했을 때 식 (5.5)의 최소 차량수 (비교용)
    tau_c = cycle_time(p)
    scen = [{"alpha_fixed": a,
             "min_M_by_eq_5_5": math.ceil((a * LAMBDA * tau_c
                                          + 1 / (p["kappa"] * (pol["max_wait"] - p["tau_p"])))
                                         / p["b"])}
            for a in (0.6, 0.75, 0.9)]
    save(pd.DataFrame(scen), "exogenous_alpha_scenarios.csv")

    summary = {"is_synthetic": True, "seed": seed, "model": p, "policy": pol,
               "lambda_hat_from_training": float(lam_hat),
               "reference_choice": choose(ref),
               "note": "평균모형의 1차 선별. DARP·P95·근무·접근성·회사손익은 별도 검증 필요."}
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    return summary, ref


if __name__ == "__main__":
    summary, ref = run_all()
    cols = ["M", "alpha", "mean_wait_min", "daily_net_cost", "service_pass", "screening_pass"]
    print(ref[cols].round(3).to_string(index=False))
    print("\n1차 판정:", summary["reference_choice"])
