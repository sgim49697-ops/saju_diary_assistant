<!-- THIRD_PARTY_NOTICES.md - 저장소에 포함한 외부 공개 fixture와 참고 구현의 고지를 기록한다. -->

# 제3자 자료 고지

이 문서는 현재 저장소에 복제한 공개 검증 fixture와 계산 정책 사례의 출처·라이선스를 기록한다. 아래 자료는 학습 데이터에 포함되지 않으며, runtime 정답 엔진을 자동 승인하지 않는다. 라이선스 해석은 법률 자문이 아닌 프로젝트 운영 기록이다.

## KASI 음양력 200행 fixture

- 공식 원천: 한국천문연구원 음양력 정보 OpenAPI, <https://www.data.go.kr/data/15012679/openapi.do>
- 수집기: `jinill1/korean-lunar-calendar@6f988e3f50a424d165b9834f9e28cd3ea962da63`
- fixture를 고정해 제공한 저장소: `JaeSang1998/oh-my-saju@a8c0cf64adfde8fb1ce253112529edd8e90cc94b`
- fixture SHA-256: `d651d5a77d7970cde4b36f414995b6ea833b4d50760f23fe0f462c96fdf8ca1a`
- 라이선스: MIT
- 저작권: Copyright (c) 2022 Jinil Lee
- 전체 라이선스: [`licenses/third_party/korean-lunar-calendar-MIT.txt`](licenses/third_party/korean-lunar-calendar-MIT.txt)

## manseryeok 정책 경계 사례

- 원천: `yhj1024/manseryeok@fba3253d7305b8b61189bd78318a7a27ed8c9b09`
- 사용 범위: 공개 테스트에 명시된 4주 표본, 자시·절입·진태양시·십신·공망 정책 사례를 출처와 함께 구조화
- 라이선스: MIT
- 저작권: Copyright (c) 2025 Yoohyojun
- 전체 라이선스: [`licenses/third_party/manseryeok-MIT.txt`](licenses/third_party/manseryeok-MIT.txt)

## Astronomy Engine runtime 후보 의존성

- 원천: `cosinekitty/astronomy@61dc07020aaa6885d2c7f688a4d82beaf6edb9ef`
- 고정 배포본: `astronomy-engine==2.1.19`, wheel SHA-256 `232ba7dd2bbf42225c48be6721b676e8c6c079dbd4588d2781dfa68adcb6f67f`
- 사용 범위: 24절기의 겉보기 태양 황경 도달 순간을 계산하는 후보 provider. KASI 1900~2049 경계 전수 Gate 전에는 runtime·학습 Gold로 승인하지 않음
- 라이선스: MIT
- 저작권: Copyright (c) 2019-2023 Don Cross `<cosinekitty@gmail.com>`
- 전체 라이선스: [`licenses/third_party/astronomy-engine-MIT.txt`](licenses/third_party/astronomy-engine-MIT.txt)

## Python tzdata runtime 의존성

- 원천: `python/tzdata@a44279419071b7aa41ebe7eca301ebb2e759571a`, tag `2026.3`
- 고정 배포본: `tzdata==2026.3`, wheel SHA-256 `dc096730c87af6cab1b171c9d532be840741ff5d459015e7f6947bd7d7e54931`
- 사용 범위: system tzdb가 없는 환경에서도 IANA tzdb `2026c`를 제공하는 fallback 배포본
- 라이선스: Apache-2.0. 배포 wheel 안의 `licenses/LICENSE`와 `licenses/licenses/LICENSE_APACHE`가 전문·저작권 고지를 포함한다.
- 저작권: Copyright (c) 2020 Paul Ganssle (Google); Copyright (c) 2026 Stan Ulbrych

## Skyfield/JPL 독립 절입 validator·후보 provider 의존성

- 고정 배포본: `skyfield==1.55` (`9f989648…b66ad4`), `jplephem==2.24` (`2de15608…629ac`), `sgp4==2.27` (`d2fc2f68…3b630`), `numpy==2.2.6` (`fc7b73d0…e8915`), `certifi==2026.7.22` (`62f22742…83775`)
- 사용 범위: JPL DE440s와 함께 1900~2049년 12절 순간 1,800건을 독립 계산하고 Astronomy Engine과 provider 적격성을 비교하는 검증 경로. conformance v5에서 선택되지 않았으며 `skyfield`, `jplephem`, `sgp4`는 production 계산 결과를 만들지 않는다.
- 라이선스: Skyfield·jplephem·sgp4는 MIT, NumPy는 BSD-3-Clause와 wheel에 동봉된 제3자 고지, certifi CA bundle은 MPL-2.0이다. 각 고정 wheel의 `.dist-info` license 전문을 재배포 시 함께 보존한다.
- 저작권: Skyfield Copyright © 2013–2018 Brandon Rhodes; jplephem Copyright 2012–2018 Brandon Rhodes; sgp4 Copyright © 2012–2016 Brandon Rhodes; NumPy Copyright (c) 2005–2024 NumPy Developers.

## JPL DE440s 독립 검증 ephemeris

- 공식 배포 URL: <https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de440s.bsp>
- 고정 identity: 32,726,016 bytes, SHA-256 `c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2`
- 사용 범위: 독립 validator와 후보 provider 비교의 태양 위치 계산 입력만 허용한다. 파일은 Git·모델·데이터 bundle에 포함하지 않으며, 재배포가 필요해지면 JPL/NASA의 당시 배포 조건을 별도로 확인한다.

`oh-my-saju`, `korean_saju`, `lunar-python`, `ssaju`의 코드는 복제하거나 의존성으로 추가하지 않았다. 이들의 고정 revision은 비교 근거로만 기록했으며, 이후 코드 또는 실질적 부분을 복제하면 해당 라이선스 전문과 저작권 고지를 추가로 보존해야 한다.
