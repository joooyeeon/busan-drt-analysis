#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""승차 기록에서 노선별 대표 운영시간을 추정합니다.

원본 operate.ipynb의 집계 방식에 실행 경로 인자와 저장 기능을 추가했습니다.
"""
import argparse
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw" / "202511"


def run(raw_dir=DEFAULT_RAW_DIR, output_dir=PROJECT_ROOT / "data"):


    # ============================================================
    # 1. TRN001 파일 위치
    # ============================================================

    ROOT = Path(raw_dir).expanduser().resolve()

    TARGET_ROUTES = {"10", "36", "68", "180", "520"}

    USECOLS = [
        "행데이터기준일자",
        "승하차상태(탑승회차-승차:0/하차:1)",
        "노선번호",
        "카드사용시간",
    ]


    # ============================================================
    # 2. 파일 찾기
    # ============================================================

    files = sorted(ROOT.glob("TRN001_202511*.csv"))

    print("찾은 파일:", len(files), "개")

    if not files:
        raise FileNotFoundError("TRN001_202511*.csv 파일을 찾지 못했습니다. --raw-dir를 확인하세요.")

    rows = []


    # ============================================================
    # 3. 30개 파일 읽기
    # ============================================================

    for i, file in enumerate(files, 1):

        print(f"[{i}/{len(files)}] {file.name}")

        reader = pd.read_csv(
            file,
            encoding="utf-8-sig",
            usecols=USECOLS,
            chunksize=500_000,
            dtype=str
        )

        for chunk in reader:

            # ----------------------------------------------------
            # 날짜
            # ----------------------------------------------------

            date_raw = (
                chunk["행데이터기준일자"]
                .astype(str)
                .str.strip()
                .str.replace(r"\.0$", "", regex=True)
            )

            chunk["date"] = pd.to_datetime(
                date_raw,
                format="%Y%m%d",
                errors="coerce"
            )

            chunk = chunk[
                chunk["date"].notna()
            ]


            # ----------------------------------------------------
            # 노선번호
            # ----------------------------------------------------

            chunk["route_id"] = (
                chunk["노선번호"]
                .astype(str)
                .str.strip()
                .str.replace(r"\.0$", "", regex=True)
            )

            chunk = chunk[
                chunk["route_id"].isin(TARGET_ROUTES)
            ]


            # ----------------------------------------------------
            # 승차만 사용
            # ----------------------------------------------------

            status = (
                chunk[
                    "승하차상태(탑승회차-승차:0/하차:1)"
                ]
                .astype(str)
                .str.strip()
            )

            chunk = chunk[
                status.str.endswith("-0")
                | status.eq("0")
            ]


            # ----------------------------------------------------
            # 카드사용시간
            #
            # 데이터가 HHMM 형식이라고 보고 처리
            #
            # 예)
            # 503  -> 05:03
            # 646  -> 06:46
            # 1805 -> 18:05
            # 2359 -> 23:59
            # ----------------------------------------------------

            time_text = (
                chunk["카드사용시간"]
                .astype(str)
                .str.strip()
                .str.replace(r"\.0$", "", regex=True)
                .str.replace(r"\D", "", regex=True)
            )


            # 마지막 네 자리 HHMM
            time_text = (
                time_text
                .str[-4:]
                .str.zfill(4)
            )


            chunk["hour"] = pd.to_numeric(
                time_text.str[:2],
                errors="coerce"
            )

            chunk["minute"] = pd.to_numeric(
                time_text.str[2:4],
                errors="coerce"
            )


            # 말이 안 되는 시각 제거
            chunk = chunk[
                chunk["hour"].between(0, 23)
                &
                chunk["minute"].between(0, 59)
            ]


            # 자정 기준 분
            chunk["clock_min"] = (
                chunk["hour"] * 60
                + chunk["minute"]
            )


            # ----------------------------------------------------
            # 자정 이후 운행 처리
            #
            # 00:00~03:59 운행은
            # 전날 밤 운행의 연장으로 간주
            #
            # 예:
            # 00:30 → 24:30 = 1470분
            # ----------------------------------------------------

            after_midnight = (
                chunk["hour"] < 4
            )

            chunk["service_date"] = chunk["date"]

            chunk.loc[
                after_midnight,
                "service_date"
            ] = (
                chunk.loc[
                    after_midnight,
                    "date"
                ]
                - pd.Timedelta(days=1)
            )

            chunk["service_min"] = chunk["clock_min"]

            chunk.loc[
                after_midnight,
                "service_min"
            ] += 1440


            rows.append(
                chunk[
                    [
                        "service_date",
                        "route_id",
                        "service_min"
                    ]
                ]
            )


    # ============================================================
    # 4. 모두 합치기
    # ============================================================

    if not rows:
        raise ValueError("운영시간을 추정할 승차 기록이 없습니다.")

    df = pd.concat(
        rows,
        ignore_index=True
    )


    # ============================================================
    # 5. 2025년 11월 평일 운행일만
    # ============================================================

    df = df[
        (df["service_date"].dt.year == 2025)
        &
        (df["service_date"].dt.month == 11)
    ]

    df = df[
        df["service_date"].dt.weekday < 5
    ]


    # ============================================================
    # 6. 날짜 + 노선별 첫 승차 / 마지막 승차
    # ============================================================

    daily_span = (
        df
        .groupby(
            ["service_date", "route_id"]
        )["service_min"]
        .agg(
            first_min="min",
            last_min="max"
        )
        .reset_index()
    )


    daily_span["operating_min"] = (
        daily_span["last_min"]
        - daily_span["first_min"]
    )


    # 지나치게 짧은 이상일 제거
    daily_span = daily_span[
        daily_span["operating_min"] >= 300
    ]


    if daily_span.empty:
        raise ValueError("2025년 11월 평일 중 300분 이상인 대상 노선 승차 구간이 없습니다.")


    # ============================================================
    # 7. 보기 쉽게 HH:MM 변환 함수
    # ============================================================

    def minute_to_clock(x):

        x = int(round(x))

        hour = x // 60
        minute = x % 60

        if hour >= 24:
            return f"익일 {hour - 24:02d}:{minute:02d}"

        return f"{hour:02d}:{minute:02d}"


    # ============================================================
    # 8. 노선별 대표값 = 중앙값
    # ============================================================

    route_span = (
        daily_span
        .groupby("route_id")
        .agg(
            first_min=("first_min", "median"),
            last_min=("last_min", "median"),
            operating_min=("operating_min", "median"),
            days=("service_date", "count")
        )
        .reset_index()
    )


    route_span["operating_min"] = (
        route_span["operating_min"]
        .round()
        .astype(int)
    )


    route_span["first_time"] = (
        route_span["first_min"]
        .apply(minute_to_clock)
    )

    route_span["last_time"] = (
        route_span["last_min"]
        .apply(minute_to_clock)
    )


    # ============================================================
    # 9. 출력
    # ============================================================

    print()
    print("=" * 70)
    print("노선별 대표 운영시간")
    print("=" * 70)

    print(
        route_span[
            [
                "route_id",
                "first_time",
                "last_time",
                "operating_min",
                "days"
            ]
        ].to_string(index=False)
    )

    # 집계 결과 저장. routes_daily.csv에 필요한 다른 모수는 직접 입력해야 합니다.
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    daily_span.to_csv(output_dir / "operating_time_daily.csv", index=False, encoding="utf-8-sig")
    route_span.to_csv(output_dir / "operating_time_summary.csv", index=False, encoding="utf-8-sig")
    print("저장 폴더:", output_dir)
    return route_span


def main():
    parser = argparse.ArgumentParser(description='승차 기록에서 노선별 대표 운영시간을 추정합니다.')
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="TRN001_202511*.csv 파일이 있는 폴더")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data", help="운영시간 CSV 저장 폴더")
    args = parser.parse_args()
    run(args.raw_dir, args.output_dir)


if __name__ == "__main__":
    main()
