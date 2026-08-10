"""리포트 본문의 개인정보 가리기.

`mask_selectors`는 화면 캡처만 덮는다. 실제 데이터를 대조하면 불일치 표본·
추출값·단언 메시지에 이름·전화번호가 그대로 남아서, 캡처만 가리고 공유하면
가려졌다고 착각하게 된다.

여기서 고정하는 계약 — 위험한 순서대로:

    판정을 바꾸지 않는다     대조 전에 가리면 서로 다른 값이 같은 ***가 되어
                            불일치가 일치로 둔갑한다. 가장 위험한 실패다.
    건수를 흔들지 않는다     몇 건이 어긋났는지는 가려도 사실 그대로여야 한다.
    한 곳만 가리지 않는다    JSON은 가렸는데 HTML에 남으면 가렸다고 착각한 채
                            남은 쪽을 공유하게 된다.
    구조는 가리지 않는다     시나리오 이름은 이력·채점의 키고, 셀렉터는 값이
                            아니라 구조다. 가리면 무엇이 깨졌는지 알 수 없다.
"""
from __future__ import annotations

import json

import pytest

from webtest_agent.config import ConfigError, load_config
from webtest_agent.models import (FAIL, PASS, DataCheckResult, RunMeta,
                                  ScenarioResult, StepResult)
from webtest_agent.report import mask_report_text, summarize, write_reports

PHONE = r"010-\d{4}-\d{4}"
NAME = r"홍길동|김철수"


def _result():
    r = ScenarioResult(name="고객목록-대조", kind="data_check", page="/customers",
                       status=FAIL)
    r.reasons = ["UI↔DB 불일치: 화면 2건 vs DB 3건",
                 "기대 텍스트 '홍길동'이 없습니다 (실제: '김철수')"]
    r.steps = [StepResult(index=1, action="extract", selector="#name",
                          value="홍길동 010-1234-5678",
                          description="#name의 값을 읽어 'who'에 담는다")]
    r.data_check = DataCheckResult(
        matched=False, ui_count=2, db_count=3,
        columns=["이름", "전화"],
        missing_in_ui=[["홍길동", "010-1234-5678"]],
        unexpected_in_ui=[["김철수", "010-9999-8888"]],
        missing_total=1, unexpected_total=1)
    r.console_errors = ["Uncaught: 홍길동 조회 실패"]
    r.not_proven_reason = ""
    return r


def _meta():
    return RunMeta(title="t", base_url="http://127.0.0.1:1", app_version="v",
                   config_path="c.yaml", started_at="2026-08-10 00:00:00",
                   duration_ms=10, status="failed")


# ── 가장 위험한 것: 판정을 바꾸면 안 된다 ───────────────────────

def test_masking_does_not_touch_the_original_results():
    """원본이 바뀌면 이후 판정·집계가 가려진 값 위에서 일어난다."""
    original = _result()
    mask_report_text([original], [NAME, PHONE])
    assert original.data_check.missing_in_ui == [["홍길동", "010-1234-5678"]]
    assert "홍길동" in original.reasons[1]


def test_masking_does_not_change_counts():
    """몇 건이 어긋났는지는 가려도 사실 그대로여야 한다."""
    masked = mask_report_text([_result()], [NAME, PHONE])[0]
    assert masked.data_check.ui_count == 2
    assert masked.data_check.db_count == 3
    assert masked.data_check.missing_total == 1
    assert masked.data_check.unexpected_total == 1
    assert masked.data_check.matched is False


def test_masking_does_not_change_status():
    masked = mask_report_text([_result()], [NAME, PHONE, r".*"])[0]
    assert masked.status == FAIL


def test_a_catch_all_pattern_cannot_turn_a_failure_into_a_pass():
    """전부 가리는 패턴을 줘도 실패는 실패로 남아야 한다."""
    results = [_result()]
    summary_before = summarize(results)
    masked = mask_report_text(results, [r"[\s\S]+"])
    assert summarize(masked) == summary_before
    assert masked[0].status == FAIL


def test_masking_touches_nothing_the_summary_reads():
    """집계가 읽는 필드는 가리기 전후가 같아야 한다.

    이게 성립해야 '판정 → 집계 → 가리기' 순서를 지키는 것이 안전 장치로
    남는다. 어느 하나라도 가려지면 순서가 곧 정답 여부를 가르게 된다.
    """
    results = [_result(), ScenarioResult(name="정상", kind="spec_check", page="/")]
    masked = mask_report_text(results, [r"[\s\S]+"])
    for before, after in zip(results, masked):
        assert (after.status, after.not_proven, after.flaky) == \
               (before.status, before.not_proven, before.flaky)


def test_two_different_values_stay_two_rows():
    """서로 다른 값이 같은 ***가 되어도 행이 합쳐지면 안 된다."""
    masked = mask_report_text([_result()], [NAME, PHONE])[0]
    assert len(masked.data_check.missing_in_ui) == 1
    assert len(masked.data_check.unexpected_in_ui) == 1


# ── 실제로 가려지는가 ───────────────────────────────────────────

def test_mismatch_rows_are_masked():
    masked = mask_report_text([_result()], [NAME, PHONE])[0]
    flat = json.dumps(masked.to_dict(), ensure_ascii=False, default=str)
    for secret in ("홍길동", "김철수", "010-1234-5678", "010-9999-8888"):
        assert secret not in flat, f"{secret}가 리포트에 남았다"


def test_reasons_and_extracted_values_are_masked():
    masked = mask_report_text([_result()], [NAME, PHONE])[0]
    assert "홍길동" not in " ".join(masked.reasons)
    assert "홍길동" not in (masked.steps[0].value or "")
    assert "홍길동" not in " ".join(masked.console_errors)


def test_masking_is_off_by_default():
    """설정하지 않았으면 아무것도 바뀌지 않는다."""
    results = [_result()]
    assert mask_report_text(results, []) is results


# ── 구조는 가리지 않는다 ────────────────────────────────────────

def test_scenario_name_and_selector_survive():
    """이름은 이력·채점의 키, 셀렉터는 값이 아니라 구조다."""
    masked = mask_report_text([_result()], [r".*목록.*", r"#name"])[0]
    assert masked.name == "고객목록-대조"
    assert masked.steps[0].selector == "#name"


def test_column_headers_survive():
    """열 이름까지 가리면 어느 칸이 어긋났는지 알 수 없다."""
    masked = mask_report_text([_result()], [NAME, PHONE, "이름"])[0]
    assert masked.data_check.columns == ["이름", "전화"]


# ── 모든 산출물이 같아야 한다 ───────────────────────────────────

def test_every_output_file_is_masked(tmp_path):
    """한 파일만 가리면 가렸다고 착각한 채 남은 쪽을 공유하게 된다."""
    write_reports(tmp_path, _meta(), [_result()], [], [],
                  mask_patterns=[NAME, PHONE])
    for name in ("report.json", "report.md", "report.html", "report.xml",
                 "walkthrough.md"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        for secret in ("홍길동", "김철수", "010-1234-5678"):
            assert secret not in text, f"{name}에 {secret}가 남았다"


def test_summary_is_unaffected_by_masking(tmp_path):
    summary = write_reports(tmp_path, _meta(), [_result()], [], [],
                            mask_patterns=[NAME, PHONE, r".*"])
    assert summary["total"] == 1 and summary["fail"] == 1


# ── 설정 검증 ───────────────────────────────────────────────────

def test_bad_regex_is_rejected_at_load_time(tmp_path):
    """실행을 다 돌린 뒤 리포트를 쓰다가 터지면 증거가 안 남는다."""
    path = tmp_path / "c.yaml"
    path.write_text("""
target:
  base_url: http://127.0.0.1:5057
report:
  mask_patterns: ["([unclosed"]
""", encoding="utf-8")
    with pytest.raises(ConfigError, match="정규식"):
        load_config(path)


def test_patterns_load(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("""
target:
  base_url: http://127.0.0.1:5057
report:
  mask_patterns:
    - "010-\\\\d{4}-\\\\d{4}"
""", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.report.mask_patterns == [r"010-\d{4}-\d{4}"]
