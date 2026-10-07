#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TRN001 자료를 2025년 11월 평일의 노선별 일별 승차인원으로 집계합니다.

원본 make_boardings_daily.ipynb의 집계 방식에 실행 경로 인자와 저장 기능을 추가했습니다.
"""
import argparse
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw" / "202511"


def run(raw_dir=DEFAULT_RAW_DIR, output=PROJECT_ROOT / "data" / "boardings_daily.csv"):


    # ============================================================
    # 1. 설정
    # ============================================================

    # --raw-dir로 지정한 TRN001 CSV 폴더를 읽습니다.
    ROOT = Path(raw_dir).expanduser().resolve()

    # 우리가 분석할 버스 노선
    TARGET_ROUTES = {"10", "36", "68", "180", "520"}

    # 사용할 열
    USECOLS = [
        "행데이터기준일자",
        "승하차상태(탑승회차-승차:0/하차:1)",
        "노선번호",
        "운수업체명칭",
        "탑승인원",
    ]


    # ============================================================
    # 2. 파일 인코딩 확인
    # ============================================================

    def get_encoding(path):
        for enc in ["utf-8-sig", "cp949", "euc-kr"]:
            try:
                pd.read_csv(
                    path,
                    encoding=enc,
                    usecols=USECOLS,
                    nrows=5
                )
                return enc
            except UnicodeDecodeError:
                continue

        raise ValueError(f"인코딩을 확인하지 못했습니다: {path}")


    # ============================================================
    # 3. TRN001 파일 찾기
    # ============================================================

    files = sorted(ROOT.rglob("TRN001_202511*.csv"))

    print(f"찾은 파일 수: {len(files)}개")

    if len(files) == 0:
        raise FileNotFoundError(
            "TRN001 CSV를 찾지 못했습니다. ROOT 경로를 확인하세요."
        )


    # ============================================================
    # 4. 파일들을 조금씩 읽으면서 필요한 데이터만 추출
    # ============================================================

    results = []

    for i, file in enumerate(files, start=1):

        print(f"[{i}/{len(files)}] 처리 중: {file.name}")

        enc = get_encoding(file)

        # 파일이 매우 크므로 50만 행씩 나눠서 읽음
        reader = pd.read_csv(
            file,
            encoding=enc,
            usecols=USECOLS,
            chunksize=500_000,
            dtype=str,
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

            # 날짜 변환 실패 자료 제거
            chunk = chunk[chunk["date"].notna()]

            # 2025년 11월만
            chunk = chunk[
                (chunk["date"].dt.year == 2025)
                & (chunk["date"].dt.month == 11)
            ]

            # ----------------------------------------------------
            # 토요일/일요일 제거
            # 월=0 화=1 수=2 목=3 금=4 토=5 일=6
            # ----------------------------------------------------
            chunk = chunk[chunk["date"].dt.weekday < 5]

            # ----------------------------------------------------
            # 노선번호 정리
            # ----------------------------------------------------
            route = (
                chunk["노선번호"]
                .astype(str)
                .str.strip()
                .str.replace(r"\.0$", "", regex=True)
            )

            chunk["route_id"] = route

            # 5개 노선만
            chunk = chunk[
                chunk["route_id"].isin(TARGET_ROUTES)
            ]

            # ----------------------------------------------------
            # 지하철 제거
            # 목표 노선번호 필터만으로도 대부분 제거되지만 안전장치
            # ----------------------------------------------------
            subway = (
                chunk["운수업체명칭"]
                .astype(str)
                .str.contains("지하철|도시철도", na=False)
            )

            chunk = chunk[~subway]

            # ----------------------------------------------------
            # 승차 기록만
            #
            # 예:
            # 0-0 → 승차
            # 0-1 → 하차
            # 3-0 → 승차
            # ----------------------------------------------------
            status = (
                chunk["승하차상태(탑승회차-승차:0/하차:1)"]
                .astype(str)
                .str.strip()
            )

            is_boarding = (
                status.str.endswith("-0") |
                status.eq("0")
            )

            chunk = chunk[is_boarding]

            # ----------------------------------------------------
            # 탑승인원 숫자로 변환
            # ----------------------------------------------------
            chunk["boardings"] = pd.to_numeric(
                chunk["탑승인원"],
                errors="coerce"
            ).fillna(0)

            # ----------------------------------------------------
            # 날짜 + 노선별 합계
            # ----------------------------------------------------
            daily = (
                chunk
                .groupby(["date", "route_id"], as_index=False)
                ["boardings"]
                .sum()
            )

            results.append(daily)


    # ============================================================
    # 5. 모든 파일 결과 합치기
    # ============================================================

    if not results:
        raise ValueError("집계할 자료가 없습니다. CSV 내용을 확인하세요.")

    final = pd.concat(results, ignore_index=True)

    # 한 날짜가 여러 chunk에 나뉘었으므로 다시 한 번 합침
    final = (
        final
        .groupby(["date", "route_id"], as_index=False)
        ["boardings"]
        .sum()
    )

    # 날짜순 → 노선순 정렬
    final = final.sort_values(
        ["date", "route_id"]
    ).reset_index(drop=True)

    # 승차인원 정수화
    final["boardings"] = final["boardings"].round().astype(int)


    # ============================================================
    # 6. 저장
    # ============================================================

    if final.empty:
        raise ValueError("2025년 11월 평일의 대상 노선 승차 기록이 없습니다.")

    OUTPUT = Path(output).expanduser().resolve()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    final.to_csv(
        OUTPUT,
        index=False,
        encoding="utf-8-sig"
    )

    print()
    print("완료!")
    print("저장 파일:", OUTPUT.resolve())
    print()
    print(final.head(20))

    print()
    print("노선별 행 수")
    print(final.groupby("route_id").size())

    print()
    print("노선별 평일 일평균 승차인원")
    print(
        final.groupby("route_id")["boardings"]
        .mean()
        .round(1)
    )

    return final


def main():
    parser = argparse.ArgumentParser(description='TRN001 자료를 2025년 11월 평일의 노선별 일별 승차인원으로 집계합니다.')
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="TRN001_202511*.csv 파일이 있는 폴더")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "boardings_daily.csv", help="결과 CSV 저장 경로")
    args = parser.parse_args()
    run(args.raw_dir, args.output)


if __name__ == "__main__":
    main()
