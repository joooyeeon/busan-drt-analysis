#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2025년 11월 평일 5개 노선 분석용 입력 파일을 확인합니다."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_ROUTES = {"520", "180", "36", "10", "68"}
BOARDING_COLUMNS = {"date", "route_id", "boardings"}
ROUTE_COLUMNS = {
    "route_id", "label", "n_buses", "operating_min",
    "tau_p_min", "tau_b_min", "tau_e_min",
}


def read_table(path, required, errors):
    if not path.is_file():
        errors.append(f"필수 입력 파일이 없습니다: {path}")
        return None
    try:
        table = pd.read_csv(path, dtype={"route_id": str}, encoding="utf-8-sig")
    except (OSError, ValueError, UnicodeError) as exc:
        errors.append(f"{path.name} 읽기 실패: {exc}")
        return None
    missing = required - set(table.columns)
    if missing:
        errors.append(f"{path.name} 필수 열 누락: {', '.join(sorted(missing))}")
        return None
    return table


def validate(data_dir):
    errors = []
    warnings = []
    data_dir = Path(data_dir)
    boardings = read_table(data_dir / "boardings_daily.csv", BOARDING_COLUMNS, errors)
    routes = read_table(data_dir / "routes_daily.csv", ROUTE_COLUMNS, errors)

    if routes is not None:
        outside = ~routes["route_id"].isin(TARGET_ROUTES)
        if outside.any():
            warnings.append(f"routes_daily.csv의 대상 외 노선 {outside.sum()}행은 분석에서 제외됩니다.")
        selected = routes.loc[~outside].copy()
        missing_routes = TARGET_ROUTES - set(selected["route_id"])
        if missing_routes:
            errors.append("routes_daily.csv 대상 노선 누락: " + ", ".join(sorted(missing_routes)))
        if selected["route_id"].duplicated().any():
            errors.append("routes_daily.csv에 중복 노선이 있습니다. 노선마다 한 행만 사용하세요.")
        if selected["label"].fillna("").astype(str).str.strip().eq("").any():
            errors.append("routes_daily.csv의 label에 빈 노선명이 있습니다.")
        for column in ["n_buses", "operating_min", "tau_p_min", "tau_b_min", "tau_e_min"]:
            values = pd.to_numeric(selected[column], errors="coerce")
            if not (np.isfinite(values) & values.gt(0)).all():
                errors.append(f"routes_daily.csv의 {column}는 빈칸 없이 유한한 양수여야 합니다.")
            if column == "n_buses" and (values.dropna() % 1 != 0).any():
                errors.append("n_buses는 정수여야 합니다.")
            if column == "tau_p_min" and values.ge(10).any():
                errors.append("tau_p_min이 10분 이상이면 평균 대기시간 10분 기준을 통과할 수 없습니다.")

    if boardings is not None:
        dates = pd.to_datetime(boardings["date"], errors="coerce")
        if dates.isna().any():
            errors.append("boardings_daily.csv의 date에 읽을 수 없는 날짜가 있습니다.")
        in_scope = (
            dates.dt.year.eq(2025)
            & dates.dt.month.eq(11)
            & dates.dt.weekday.lt(5)
            & boardings["route_id"].isin(TARGET_ROUTES)
        )
        if (~in_scope & dates.notna()).any():
            warnings.append("대상 월·평일·5개 노선에 해당하지 않는 승차인원 행은 분석에서 제외됩니다.")
        selected = boardings.loc[in_scope].copy()
        selected["date"] = dates.loc[in_scope]
        if selected.empty:
            errors.append("boardings_daily.csv에 분석 대상 수요가 없습니다.")
        if not selected["date"].eq(selected["date"].dt.normalize()).all():
            errors.append("date는 시각을 제외한 날짜(YYYY-MM-DD)여야 합니다.")
        if selected.duplicated(["date", "route_id"]).any():
            errors.append("boardings_daily.csv에 날짜·노선 중복 행이 있습니다.")
        values = pd.to_numeric(selected["boardings"], errors="coerce")
        if not (np.isfinite(values) & values.ge(0)).all():
            errors.append("boardings는 빈칸 없이 유한한 0 이상의 숫자여야 합니다.")
        if (values.dropna() % 1 != 0).any():
            errors.append("boardings는 정수여야 합니다.")
        expected_dates = set(pd.bdate_range("2025-11-01", "2025-11-30"))
        for route_id in ["520", "180", "36", "10", "68"]:
            actual = set(selected.loc[selected["route_id"].eq(route_id), "date"])
            missing = expected_dates - actual
            if missing:
                errors.append(f"{route_id}번: 평일 20일 중 {len(missing)}일 누락. 원자료 집계를 확인하세요.")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args()
    errors, warnings = validate(args.data_dir)
    for message in warnings:
        print("안내:", message)
    for message in errors:
        print("확인 필요:", message)
    if errors:
        print(f"입력 확인 실패: {len(errors)}개 항목을 수정하세요.")
        return 1
    print("입력 확인 완료: 5개 노선 × 평일 20일, 필수 열·수치·중복 검사 통과.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
