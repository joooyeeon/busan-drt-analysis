# 부산 시내버스 DRT 전환 경제성 분석

**수요·비용·서비스 기준을 함께 고려하는 노선별 DRT 전환 검토**

부산 시내버스 5개 노선의 일별 승차인원을 이용해 수요응답형 교통(Demand Responsive Transport, DRT) 전환 가능성을 분석합니다. 서비스 기준을 만족하는 차량수를 찾고, 현재 버스 대수 이내에서 운영할 수 있는지와 회피가능 원가에 따른 재정효과를 확인합니다.

## 분석 범위

| 항목 | 내용 |
| --- | --- |
| 기간 | 2025년 11월 1~30일 중 평일 20일 |
| 대상 노선 | 520 · 180 · 36 · 10 · 68번 |
| 수요 단위 | 노선별·날짜별 승차인원 합계 |
| 기본 시나리오 | 회피가능 원가 비율 70% |
| 민감도 분석 | 50%, 100% — 100%는 상한 시나리오(upper bound) |
| 서비스 기준 | 평균 대기시간 ≤ 10분, 이용유지율 ≥ 75% |
| 차량수 조건 | 필요 DRT 차량수 ≤ 현재 투입 버스 대수 |
| 결과물 | 날짜별 결과·노선별 요약 CSV, 검수 CSV, 그림 3개 |

**이 저장소에는 코드와 입력 양식이 포함되어 있습니다.** 실제 TRN001 원자료, `boardings_daily.csv`, `routes_daily.csv`, 연구 결과 파일은 포함되어 있지 않습니다. 입력 양식의 빈칸은 실제 노선별 값으로 채워야 합니다.

## 프로젝트 구성

| 경로 | 역할 |
| --- | --- |
| [`drt_daily_analysis.py`](drt_daily_analysis.py) | 일별 분석, 비용 시나리오 비교, CSV·그림 생성 |
| [`drt_model_student.py`](drt_model_student.py) | 평형 이용비율·대기시간·차량수별 비용 계산 모형 |
| [`scripts/make_boardings_daily.py`](scripts/make_boardings_daily.py) | 원자료에서 일별 승차인원 집계 |
| [`scripts/estimate_operating_time.py`](scripts/estimate_operating_time.py) | 승차 시각으로 대표 운영시간 추정 |
| [`scripts/validate_inputs.py`](scripts/validate_inputs.py) | 분석 전 입력 열·값·평일 20일 포함 여부 확인 |
| [`notebooks/`](notebooks/) | 전처리를 셀 단위로 실행하는 Jupyter 노트북 |
| [`data/README.md`](data/README.md) | 입력 데이터 형식·열 설명 |
| [`data/templates/`](data/templates/) | 일별 수요와 노선별 모수의 CSV 입력 양식 |
| [`data/raw/`](data/raw/) | 개인 실행 환경의 원자료 저장 위치 |
| [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) | 계산식·지표 해석·모형의 적용 범위 |
| [`docs/PACKAGING_NOTES.md`](docs/PACKAGING_NOTES.md) | 원본 대비 정리 내용·검증 범위 |

## 실행 방법

아래 명령은 **압축을 푼 프로젝트 폴더에서** 순서대로 실행합니다. 핵심 패키지는 검증 환경의 버전으로 `requirements.txt`에 고정했습니다. 확인한 Python 버전은 3.12입니다.

### 1. 환경 준비

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

가상환경 활성화 없이 가상환경의 Python을 직접 실행하는 방식입니다.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

이후 PowerShell 예시의 `.\.venv\Scripts\python.exe`를 macOS / Linux에서는 `python`으로 바꿔 실행합니다.

### 2. 원자료 준비

`data/raw/202511/` 폴더를 만들고 `TRN001_20251101.csv`부터 `TRN001_20251130.csv`까지 넣습니다. 기존 원자료 폴더를 그대로 사용하려면 다음 전처리 명령에 `--raw-dir "원자료_폴더_경로"`를 추가합니다.

### 3. 일별 승차인원 생성

```powershell
.\.venv\Scripts\python.exe scripts/make_boardings_daily.py
```

`data/boardings_daily.csv`가 생성됩니다. 이미 이 CSV를 갖고 있다면 이 단계는 건너뛸 수 있습니다.

승차 건수는 상태 코드의 앞 숫자가 아니라 **`탑승인원` 열을 합산**해 계산합니다. 예를 들어 `2-0`은 승차 기록을 뜻하며, 그 자체를 승객 2명으로 계산하지 않습니다.

### 4. 노선별 입력값 준비

`data/templates/routes_daily_template.csv`를 복사해 `data/routes_daily.csv`로 저장한 뒤, 각 노선의 다음 값을 채웁니다.

- `n_buses`: 현재 투입 버스 대수
- `operating_min`: 하루 운영시간(분)
- `tau_p_min`: 평균 픽업 이동시간(분)
- `tau_b_min`: 평균 승객 수송시간(분)
- `tau_e_min`: 평균 빈차 재배치시간(분)

운영시간 추정이 필요하면 다음 보조 코드를 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts/estimate_operating_time.py
```

`data/operating_time_summary.csv`의 `operating_min`을 참고할 수 있습니다. 이 값은 일별 첫·마지막 승차 기록 간격의 **중앙값**입니다. 시간표의 운행시간이나 차량 한 대의 근무시간과 같다는 뜻은 아닙니다. 이 코드는 버스 대수와 세 가지 이동시간을 자동으로 생성하지 않습니다.

### 5. 입력 확인 및 분석

```powershell
.\.venv\Scripts\python.exe scripts/validate_inputs.py
.\.venv\Scripts\python.exe drt_daily_analysis.py
```

검증에서 문제가 표시되면 해당 입력을 수정한 뒤 분석을 실행합니다. 검증기는 노선별 평일 20일, 날짜·노선 중복, 필수 열과 숫자 범위를 확인합니다.

### Jupyter로 전처리하기

Jupyter 사용 환경에서 `notebooks/make_boardings_daily.ipynb` 또는 `notebooks/operate.ipynb`를 열어 위에서부터 실행합니다. 두 노트북은 `scripts/`의 같은 전처리 함수를 호출합니다. Jupyter는 핵심 분석 실행의 필수 의존성이 아닙니다.

## 생성 결과

결과는 `outputs_daily/`에 저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `daily_results.csv` | 날짜별 수요, 필요 차량수, 실현가능성, 시나리오별 절감액 |
| `route_summary.csv` | 노선별 일평균 수요, 필요 차량수 요약, 경제적 평일 비율 |
| `F2_cost_check.csv` | 비용 비교 그림의 검수용 수치 |
| `figures/F1_alpha_star_lollipop.png` | 손익분기 회피원가 비율 α* |
| `figures/F2_cost_dumbbell.png` | 회피가능 현행 비용과 DRT 순비용 비교 |
| `figures/F4_required_M_step.png` | 날짜별 최소 필요 DRT 차량수와 현재 버스 대수 |

그림 번호는 첨부 원본을 유지했습니다. **현재 코드의 그림은 F1·F2·F4, 총 3개**입니다.

## 계산 기준

| 입력값 | 첨부 분석 코드의 설정 |
| --- | --- |
| 버스 1대 하루 비용 | 834,327원 |
| DRT 1대 하루 비용 | 551,372원 |
| 승차 1회당 운임 | 1,500원 |

현행 버스 총비용은 `현재 투입 버스 대수 × 834,327원`입니다. 노선 길이는 이 비용식에 직접 들어가지 않습니다. DRT 순비용은 `DRT 운행비 − 모형상 유지된 승객의 운임수입`입니다.

손익분기 회피원가 비율은 `α* = 서비스 기준을 만족하는 최소 DRT 순비용 ÷ 현행 버스 총비용`으로 계산합니다. **α*만으로 전환을 결정하지 않고 차량수 조건도 함께 확인**합니다. 세부 정의와 집계 방식은 [분석 방법](docs/METHODOLOGY.md)에 적었습니다.

이 분석의 절감액은 코드에서 정의한 재정 비교 지표입니다. 버스 회사의 실제 회계상 손익, 첨두시간대 대기열, 개별 차량 경로, 교통취약계층의 접근성 변화는 별도로 검토해야 합니다. 비용의 외부 출처 증빙과 노선별 실제 입력값은 원본 압축파일에 포함되어 있지 않습니다.

## GitHub에 올리기

압축을 푼 뒤 **이 README가 있는 폴더의 내부 파일과 폴더를 저장소 최상위에** 올립니다. ZIP 파일 자체를 올리면 README와 코드가 저장소 첫 화면에 펼쳐지지 않습니다.

`.gitignore`에는 원자료, 개인 입력 CSV, 자동 생성 결과, 가상환경, 노트북 체크포인트를 제외하도록 설정했습니다. 웹 화면에서 직접 업로드할 때는 제외 설정이 자동으로 적용되는 것으로 가정하지 말고 업로드할 항목을 선택하세요. 이 정리본에는 실제 원자료나 개인 입력 CSV가 들어 있지 않습니다.

원본 코드의 저작권·이용 조건이 확인되지 않아 별도 오픈소스 라이선스는 추가하지 않았습니다.
