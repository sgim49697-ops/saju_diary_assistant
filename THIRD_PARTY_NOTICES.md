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

`oh-my-saju`, `korean_saju`, `lunar-python`, `ssaju`의 코드는 복제하거나 의존성으로 추가하지 않았다. 이들의 고정 revision은 비교 근거로만 기록했으며, 이후 코드 또는 실질적 부분을 복제하면 해당 라이선스 전문과 저작권 고지를 추가로 보존해야 한다.
