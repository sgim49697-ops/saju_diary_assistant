<!-- README.md - 사주 일기 도우미 baseline의 정본 문서와 현재 Gate를 안내한다. -->

# 사주 일기 도우미

Kanana 2 1.3B 기반 한국어 사주·공감 대화 baseline 프로젝트다. `KI10-MIX-v2`와 독립 `KI20-MIX-v2/run-1f5d732cae67`의 1 epoch Full FT·최종 새 프로세스 재로딩을 완료했다. Phase 6 sealed blind 평가는 아직 시작하지 않았고, 품질 인증·production 승격은 금지 상태다.

계산기는 Skyfield 1.55·고정 JPL DE440s·내장 UT1을 결합한 v1.3 candidate runtime과 conformance v8까지 구현했다. candidate 결합은 통과했지만 strict runtime Gate·release·앱 연결은 차단돼 있다. `MIX20K-v3.0.1-repaired/build-94eb7b543490`도 기술 검수 후보일 뿐이며 v3.1 생성·추가 학습은 수행하지 않았다.

- [프로젝트 현황판](PROJECT_STATUS.html)
- [10K/20K 정본 계획](implementation/plans/saju_1b_10k_20k_baseline/README.md)
- [Phase 5 학습 계약](implementation/plans/saju_1b_10k_20k_baseline/phase-5-baseline-training.md)
- [Phase 6 평가 계약](implementation/plans/saju_1b_10k_20k_baseline/phase-6-evaluation-v2-decision.md)
- [MIX20K-v3 보정 계획](implementation/plans/mix20k_v3_repair_plan.md)
- [만세력 Runtime 정본](implementation/plans/saju_runtime_calculator_adoption.md)

모델·checkpoint·AI Hub 원문과 파생 private 평가 payload는 Git에 넣지 않는다. `PROJECT_STATUS.html`은 공개 가능한 집계·버전·해시·Gate만 담는 결정적 생성물이다.
