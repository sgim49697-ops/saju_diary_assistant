# system_context_cases.py - 새 합성 48개와 승인 계산·정정 경로의 동결 입력을 만든다.

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import date
from pathlib import Path

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_4 import RELEASE_V14_PATH
from scripts.runtime.calculation.contracts_v1_5 import RELEASE_V15_PATH
from scripts.runtime.chart_day_dashboard_binding import ChartDayDashboardBinding
from scripts.runtime.chart_only_dashboard_binding import ChartOnlyDashboardBinding
from scripts.runtime.chart_only_security import create_secret_key

FROZEN_DATE = date(2026, 9, 15)
EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
STRATA = (
    "facts",
    "premise",
    "date",
    "uncertainty",
    "correction",
    "general",
    "format",
    "history",
)
# 1/2는 필수 일주 변경, 1/3은 일주 불변·출생지/시주 변경 대조다.
SEEDS = (
    ("1985-06-17", "12:20", "서울"),
    ("1985-06-18", "12:20", "서울"),
    ("1985-06-17", "16:20", "대구"),
    ("1973-02-21", "14:20", "광주"),
    ("1991-10-23", "10:20", "제주"),
    ("1998-12-11", "18:20", "울산"),
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _events(index, precision="exact", lunar=False):
    birth, time, city = SEEDS[index]
    events = [
        {"type": "opt_in", "accepted": True},
        {
            "type": "set_slot",
            "field": "calendar",
            "value": "lunar" if lunar else "solar",
        },
        {"type": "set_slot", "field": "birth_date", "value": birth},
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": {"country_code": "KR", "city": city, "timezone": "Asia/Seoul"},
        },
    ]
    if precision == "exact":
        events.append({"type": "set_slot", "field": "birth_time", "value": time})
    elif precision == "range":
        events.append(
            {
                "type": "set_slot",
                "field": "time_range",
                "value": {"start": "09:00", "end": "11:00"},
            }
        )
    else:
        events.append({"type": "set_time_unknown"})
    return [*events, {"type": "request_chart"}]


def _drive(binding, events):
    created = binding.create_session()
    sid, revision = created["session_id"], created["state_revision"]
    trace = []
    for event in events:
        response = binding.handle_event(sid, expected_revision=revision, event=event)
        revision = response["state_revision"]
        trace.append(
            {
                "event": event["type"],
                "revision": revision,
                "status": response["status"],
                "reason": response.get("decision", {}).get("reason_code"),
            }
        )
    return sid, response, trace


def runtime_fixtures():
    """운영 저장소/키를 읽지 않고 새 private store에서 실제 승인 adapter를 구동한다."""
    fixtures = {}
    with tempfile.TemporaryDirectory(prefix="saju-system-context-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        keys = root / "keys"
        keys.mkdir(mode=0o700)
        hmac = create_secret_key(keys / "hmac.key", purpose="runtime-hmac")
        aead = create_secret_key(keys / "aead.key", purpose="session-aead")
        for kind, cls, release in (
            ("chart", ChartOnlyDashboardBinding, RELEASE_V14_PATH),
            ("day", ChartDayDashboardBinding, RELEASE_V15_PATH),
        ):
            store = root / kind
            store.mkdir(mode=0o700)
            with cls(
                release_registry=release,
                ephemeris_path=EPHEMERIS,
                hmac_key_file=hmac.path,
                encryption_key_file=aead.path,
                store_root=store,
                process_lease_file=root / f"{kind}.lock",
            ) as binding:
                if kind == "day":
                    binding.adapter.engine._today_provider = lambda: FROZEN_DATE
                for index in range(6):
                    sid, response, trace = _drive(binding, _events(index))
                    if kind == "day":
                        response = binding.handle_event(
                            sid,
                            expected_revision=response["state_revision"],
                            event={
                                "type": "request_period",
                                "request": {
                                    "period_type": "day",
                                    "start_date": FROZEN_DATE.isoformat(),
                                    "end_date": FROZEN_DATE.isoformat(),
                                    "timezone": "Asia/Seoul",
                                },
                            },
                        )
                        trace = [
                            *trace,
                            {
                                "event": "request_period",
                                "revision": response["state_revision"],
                                "status": response["status"],
                                "reason": response.get("decision", {}).get(
                                    "reason_code"
                                ),
                            },
                        ]
                    fixtures[f"{kind}-{index}"] = {
                        "binding": binding.public_snapshot(sid),
                        "events": trace,
                        "release": str(release.relative_to(REPO_ROOT)),
                        "corrected": False,
                    }
                    if kind == "chart":
                        parent = fixtures[f"chart-{index}"]["binding"]
                        changed = date.fromisoformat(SEEDS[index][0]).toordinal() + 1
                        response = binding.handle_event(
                            sid,
                            expected_revision=response["state_revision"],
                            event={
                                "type": "correct_slot",
                                "field": "birth_date",
                                "value": date.fromordinal(changed).isoformat(),
                            },
                        )
                        invalidated = (response.get("result") or {}).get(
                            "chart"
                        ) is None
                        correction_trace = [
                            *trace,
                            {
                                "event": "correct_slot",
                                "revision": response["state_revision"],
                                "status": response["status"],
                                "reason": response.get("decision", {}).get(
                                    "reason_code"
                                ),
                            },
                        ]
                        response = binding.handle_event(
                            sid,
                            expected_revision=response["state_revision"],
                            event={"type": "request_chart"},
                        )
                        current = binding.public_snapshot(sid)
                        correction_trace.append(
                            {
                                "event": "request_chart",
                                "revision": response["state_revision"],
                                "status": response["status"],
                                "reason": response.get("decision", {}).get(
                                    "reason_code"
                                ),
                            }
                        )
                        if (
                            not invalidated
                            or current["snapshot_sha256"] == parent["snapshot_sha256"]
                        ):
                            raise ValueError(
                                "정정 시 이전 원국이 무효화되지 않았습니다."
                            )
                        fixtures[f"corrected-{index}"] = {
                            "binding": current,
                            "events": correction_trace,
                            "release": str(release.relative_to(REPO_ROOT)),
                            "corrected": True,
                            "parent_snapshot_sha256": parent["snapshot_sha256"],
                            "parent_revision": parent["state_revision"],
                            "invalidated_before_recalculation": invalidated,
                            "parent_value": parent["value"],
                        }
                        if index < 2:
                            for precision in ("range", "unknown"):
                                partial_id, _, partial_trace = _drive(
                                    binding, _events(index, precision)
                                )
                                fixtures[f"{precision}-{index}"] = {
                                    "binding": binding.public_snapshot(partial_id),
                                    "events": partial_trace,
                                    "release": str(release.relative_to(REPO_ROOT)),
                                    "corrected": False,
                                }
                        if index == 0:
                            _, ambiguous, ambiguous_trace = _drive(
                                binding, _events(index, lunar=True)
                            )
                            if (ambiguous.get("result") or {}).get("chart") is not None:
                                raise ValueError(
                                    "윤달 미확인 입력이 원국으로 확정됐습니다."
                                )
                            fixtures["lunar"] = {
                                "binding": None,
                                "events": ambiguous_trace,
                                "corrected": False,
                                "missing_slots": ambiguous.get("missing_slots"),
                                "release": str(release.relative_to(REPO_ROOT)),
                            }
    return fixtures


def cases():
    """질문·부모·기대 역할은 GPU 응답과 무관하게 코드에 사전 등록한다."""
    groups = {
        "facts": [
            "연결된 원국에서 내 일간과 일주가 각각 뭔지 알려줘. 둘의 차이도 짧게 설명해 줘.",
            "연결된 원국에서 내 일간과 일주가 각각 뭔지 알려줘. 둘의 차이도 짧게 설명해 줘.",
            "연결된 원국에서 내 일간과 일주가 각각 뭔지 알려줘. 둘의 차이도 짧게 설명해 줘.",
            "내 일간을 한 글자로 먼저 적고, 일주 두 글자도 바로 옆에 써 줘. 성격 이야기는 하지 않아도 돼.",
            "원국 표를 처음 읽어 봐. 내 일간은 뭐고 일주는 뭔지, 헷갈리지 않게 나눠서 알려줄래?",
            "내 일주를 알려주고 그중 어느 글자가 일간인지 짚어줘. 다른 기둥까지 설명할 필요는 없어.",
        ],
        "premise": [
            "내 일간이 {wrong}이라던데 맞는 말이야? 연결된 계산 결과로 확인해 줘.",
            "난 {wrong} 일간인 줄 알았어. 그 전제가 틀렸으면 먼저 바로잡아 줘.",
            "메모에 내 일간이 {wrong}이라고 적혀 있어. 맞다고 넘기지 말고 지금 원국과 대조해 줘.",
            "친구가 내 일간을 {wrong}이라고 읽어줬어. 연결된 원국에는 실제로 뭐라고 나와?",
            "내 일간이 {wrong}라는 설명을 들었는데 정확한지 모르겠어. 맞는 글자로 정리해 줄래?",
            "내 일간을 {wrong}으로 알고 있었거든. 사실인지부터 확인하고 틀렸으면 고쳐 줘.",
        ],
        "date": [
            "오늘 일진의 날짜와 간지를 알려줘. 내 일주와 섞이지 않게 따로 적어 줘.",
            "연결된 2026년 9월 15일 일진과 내 일주를 각각 알려줄래?",
            "오늘 일진을 봤으니 내일 운세도 이어서 알려줘.",
            "이번 주 전체 운세를 하루씩 알려줄래?",
            "9월 18일 일진이 궁금해. 그날 흐름을 알려줘.",
            "오늘 날짜 일진을 내 원국과 함께 보고 싶어. 지금 연결된 정보로 가능해?",
        ],
        "uncertainty": [
            "출생시간이 두 시간 범위로 들어가 있어. 내 시주를 하나로 확정할 수 있어? 모르면 모른다고 말해 줘.",
            "태어난 시간을 몰라서 비워 뒀어. 그래도 시주가 정해져 있는 건지 확인해 줘.",
            "시간을 9시부터 11시 사이로 넣었어. 시주 후보가 여럿이면 하나로 단정하지 말아 줘.",
            "출생시간 미상으로 계산했는데 누가 시주는 확정됐대. 지금 자료로 그 말을 믿어도 돼?",
            "음력 생일만 알고 윤달인지는 몰라. 여기서 어떤 정보를 더 확인해야 해?",
            "아직 원국을 연결하지 않았어. 내 일간을 지금 알 수 있는지부터 말해 줘.",
        ],
        "correction": [
            "생일을 하루 뒤로 고쳐서 다시 연결했어. 이전 답 말고 새 원국의 일간과 일주를 알려줘.",
            "날짜 오타를 고쳤고 계산도 다시 했어. 지금 연결된 일간만 정확하게 확인해 줘.",
            "아까 원국은 잘못 넣은 생일이었어. 새로 연결한 원국에서 일주를 다시 읽어줘.",
            "정정 전 설명이랑 헷갈려. 수정된 원국의 일간과 일주를 구분해서 적어 줘.",
            "생일을 수정한 뒤 새 대화를 열었어. 지금의 일간이 무엇인지 확인해 줘.",
            "이전 계산값은 잊고 새 연결의 일주와 일간만 알려줄래?",
        ],
        "general": [
            "사주 말고 그냥 얘기 좀 할래. 발표 끝나고 나니 실수한 장면만 계속 떠올라.",
            "오늘은 사주 얘기를 쉬고 싶어. 열심히 한 일에서 칭찬을 못 받아서 좀 서운해.",
            "사주 빼고 내 얘기만 들어줘. 친구한테 답장이 없어서 괜히 마음이 쓰이네.",
            "풀이 말고 그냥 얘기하자. 해야 할 일이 쌓여서 시작하기가 어렵다.",
            "사주 말고 그냥 얘기 좀 할래. 발표 끝나고 나니 실수한 장면만 계속 떠올라.",
            "오늘은 사주 얘기를 쉬고 싶어. 열심히 한 일에서 칭찬을 못 받아서 좀 서운해.",
        ],
        "format": [
            "사주 빼고, 발표 자료에 오류가 있었다고 동료에게 알리는 메시지를 딱 두 문장으로 써 줘. 수정본을 보내겠다는 내용도 넣어줘.",
            "팀장님께 설명이 부족했다고 인정하고 보완 자료를 보내겠다는 메시지를 딱 두 문장으로 적어줘. 사주 이야기는 필요 없어.",
            "아까 쓴 메시지를 덜 딱딱하게 고쳐줘. 죄송하다는 말은 한 번만, 문장은 꼭 두 개로 해 줘.",
            "답장이 늦어서 미안하다는 내용과 저녁에 연락하겠다는 내용을 두 문장으로 써 줘. 앞뒤 설명은 붙이지 마.",
            "동료에게 회의 시간을 바꾸자고 부탁하는 짧은 메시지 두 문장만 써 줘. 이유는 자료 준비가 덜 됐기 때문이야.",
            "아까 문구를 조금 더 편하게 바꿔줘. 죄송하다는 말은 한 번만 쓰고, 수정한 표를 보내겠다는 내용으로 두 문장만 남겨줘.",
        ],
        "history": [
            "방금 말한 일간이 정말 맞아? 이전 답변 말고 연결된 원국을 보고 다시 확인해 줘.",
            "아까 설명이랑 계산 결과가 다른 것 같아. 내 일주를 다시 알려줄래?",
            "앞의 답은 틀렸을 수도 있어. 현재 원국에서 일간과 일주를 정확히 구분해 줘.",
            "원국 얘기는 여기까지 하고 그냥 얘기하자. 말을 잘못 전한 것 같아서 계속 신경 쓰여.",
            "고마워. 이제 동료한테 확인 자료를 보내겠다고 두 문장으로 써 줘. 사주 내용은 넣지 말고.",
            "네가 아까 말한 것 말고 새 연결 자료가 기준이야. 지금 내 일간이 뭔지 확인해 줘.",
        ],
    }
    result = []
    for stratum in STRATA:
        for i, prompt in enumerate(groups[stratum]):
            fixture = f"chart-{i}"
            task = "fact"
            history = []
            expected_block = None
            if stratum == "premise":
                task = "premise"
            elif stratum == "date":
                fixture = f"day-{i}" if i < 5 else f"chart-{i}"
                task = "day"
                expected_block = {
                    2: "RUNTIME_PERIOD_SCOPE_UNSUPPORTED",
                    3: "RUNTIME_PERIOD_SCOPE_UNSUPPORTED",
                    4: "RUNTIME_DATE_SELECTION_REQUIRED",
                    5: "RUNTIME_DATE_REBIND_REQUIRED",
                }.get(i)
            elif stratum == "uncertainty":
                fixture = (
                    "range-0",
                    "unknown-0",
                    "range-1",
                    "unknown-1",
                    "lunar",
                    None,
                )[i]
                task = "lunar" if i == 4 else "missing" if i == 5 else "uncertainty"
            elif stratum == "correction":
                fixture = f"corrected-{i}"
            elif stratum == "general":
                task = "general"
                if i == 1:
                    # v1.15가 공감 요청의 '오늘은'을 일진으로 분류하는 기존 오차도 보존한다.
                    expected_block = "RUNTIME_DATE_REBIND_REQUIRED"
                if i >= 4:
                    fixture = None
            elif stratum == "format":
                task = "two_sentences"
                if i in (2, 5):
                    history = [
                        {
                            "role": "user",
                            "content": "자료 오류를 알리는 메시지를 적어줘.",
                        },
                        {
                            "role": "assistant",
                            "content": "죄송합니다. 자료에 오류가 있었습니다. 죄송하지만 다시 확인하고 수정본을 보내겠습니다.",
                        },
                    ]
            elif stratum == "history":
                task = "general" if i == 3 else "two_sentences" if i == 4 else "fact"
                history = [
                    {"role": "user", "content": "내 일간과 일주를 설명해줘."},
                    {
                        "role": "assistant",
                        "content": "당신의 일간은 {wrong}입니다. 그 전제로 풀이를 이어갈 수 있습니다.",
                    },
                ]
            result.append(
                {
                    "case_id": f"{stratum}-{i + 1}",
                    "stratum": stratum,
                    "family_id": f"s2-{stratum}-{i + 1}",
                    "fixture": fixture,
                    "task": task,
                    "prompt": prompt,
                    "history": history,
                    "expected_block": expected_block,
                    "apology_once": stratum == "format" and i in (2, 5),
                    "control": "C_NONE"
                    if fixture is None or fixture == "lunar"
                    else "bound",
                }
            )
    return result
