# AI Hub #86 감성대화 데이터 다운로드 명세

## 원천·접근

- 공식 페이지: [감성 대화 말뭉치 #86](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=86)
- 이용 승인 확인일: 2026-08-27
- 다운로드 방식: AI Hub 공식 Shell API v0.6과 동일한 endpoint·header 계약
- 공식 Shell SHA-256: `3475a89b89ca10cdebfd7ef0542ec54650759bd5c15491e4dc0da6c15d93390e`
- 로컬 경로: `data/raw/aihub_empathy/dataset-86-filekeys-66046-66049/`
- 접근 보호: raw 최상위 디렉터리 `0700`, 파일·manifest Git 제외

API 키는 저장소 밖 `~/.config/saju_diary_assistant/aihub.env`에서만 읽었고 명령행·로그·보고서에 기록하지 않았다.

## 고정 다운로드 파일

| file key | 공식 구분 | archive bytes | archive SHA-256 |
|---:|---|---:|---|
| `66046` | 학습 원천데이터 | 10,755,584 | `bf814d56cc6ea7fe92afd1eeba395271b36067257cb9ba37341539bdb92bf906` |
| `66047` | 학습 라벨링데이터 | 8,307,200 | `61c0aa71182a2c60b8876d5f99da187ca2522ee3ad96264534bd45590b512dda` |
| `66048` | 검증 라벨링데이터 | 995,840 | `ca8b4f899ad24fc556d779078190ecf2d7f44b3e668853777efbb8fa84a0fa6b` |
| `66049` | 검증 원천데이터 | 1,292,288 | `a339140ff6a5b95f540f2752ecf6a52a9142306fdc3f7a34e05652610aad7c92` |

원본 tar 합계는 21,350,912 bytes다. 안전 검사를 통과한 `.zip.part0`을 병합하되 원본 tar와 part를 모두 보존했다. tar 4개, part 4개, 병합 zip 4개의 총 12파일 bytes·SHA-256은 `data/reports/saju_1b_baseline/source/v1.0.0/build-b3890c552e38/source_inventory.json`과 raw `SOURCE_MANIFEST.json`에 고정했다.

## 구조 inventory

- 라벨 JSON: 58,268 conversation record
- JSON 파싱 실패: 0건
- 2개 이상 완전한 사람 발화·시스템 응답 pair를 가진 record: 58,268건
- 2-pair record: 10,891건, 3-pair record: 47,377건
- 구조적으로 적격한 고유 `talk.id.talk-id`: 51,886개
- upstream train 고유 group: 51,625개
- upstream validation 고유 group: 6,640개
- train/validation group ID 교집합: 6,379개
- train/validation exact record 교집합: 0개

멀티턴 최소 Gate 1,200 group은 통과했다. 다만 upstream split 사이에 동일 group ID가 있으므로 Phase 2에서는 원천 split을 학습 split으로 신뢰하지 않고 출처 메타로만 보존한다. 전체 데이터를 `talk.id.talk-id`로 다시 묶은 뒤 holdout과 train을 group-first로 재분리한다.

## 이용 제한

AI Hub 원문과 변환 원문은 공개 저장소, 공개 dataset 또는 제3자 공유 경로에 게시하지 않는다. 공개 가능한 것은 수집 코드, 파일 해시, 집계 통계와 이용조건 기록뿐이다. 이 명세는 기술적 사용 기록이며 법률 자문을 대신하지 않는다.
