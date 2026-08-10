"""실행 증거용 지문 — 이 리포트가 어떤 테스트 정의로 나왔는지.

리포트에 "통과"라고만 적혀 있으면, 누가 대조할 열을 하나 빼거나 단언을
느슨하게 바꿔도 지난주와 똑같아 보인다. 테스트가 조용히 약해지는 것을
결과만 보고는 알 수 없다.

두 가지를 남긴다.

    config_sha256 : 설정 파일 바이트 그대로. 무엇이든 바뀌면 달라진다.
    checks_sha256 : 실제로 검증한 내용만 추린 지문. 주석·제목처럼 판정과
                    무관한 수정에는 바뀌지 않고, 대조할 열이나 단언이
                    바뀌면 달라진다.

둘을 나눈 이유는 후자가 "검사가 달라졌는가"에만 답하기 때문이다. 파일 해시만
있으면 주석 한 줄 고쳐도 경보가 울려, 곧 아무도 보지 않게 된다.

대상 앱의 커밋 해시는 넣지 않는다. 배포된 사이트나 남의 오픈소스를 검사할
때는 그런 것이 아예 없어서, 필수로 두면 절반의 상황에서 빈칸이 된다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_sha256(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _step_digest(step) -> dict:
    """스텝 하나의 판정 관련 정보. 비밀값은 마스킹된 값을 쓴다.

    `${환경변수}`는 설정을 읽는 시점에 실제 값으로 바뀌어 있으므로, 원본
    value를 그대로 해싱하면 비밀번호가 지문 계산에 들어간다. 해시는 되돌릴
    수 없지만, 같은 비밀번호를 쓰는 두 설정이 같은 지문을 갖는 것 자체가
    정보가 되므로 마스킹된 값을 쓴다.
    """
    return {
        "action": step.action,
        "selector": step.selector,
        "value": step.log_value,
        "store_as": step.store_as,
        "pattern": step.pattern,
        "frame": step.frame,
        "json_path": step.json_path,
        "expect_status": step.expect_status,
    }


def _query_digest(query) -> dict | None:
    if query is None:
        return None
    api = query.api
    return {
        "db": query.db,
        "sql": query.sql,
        "params": dict(sorted(query.params.items())),
        "order_matters": query.order_matters,
        "api": None if api is None else {
            "url": api.url,
            "rows_path": api.rows_path,
            "columns": list(api.columns),
            # 헤더 '값'은 토큰이므로 넣지 않는다. 어떤 헤더를 썼는지만 남긴다.
            "header_names": sorted(api.headers),
        },
    }


def _expect_digest(spec) -> list | None:
    """쓰기 검증의 사후조건 전부.

    이걸 빼면 조건을 하나 지워도 지문이 그대로다. 지문을 만든 이유가 바로
    '검사가 조용히 약해지는 것'을 잡는 것이므로, 무엇을 몇 개 확인하는지는
    반드시 들어가야 한다. 값(params)은 비밀일 수 있어 이름만 남긴다.
    """
    from .config import post_conditions, WriteCheckSpec

    if not isinstance(spec, WriteCheckSpec):
        return None
    return [{
        "label": cond.label,
        "sql": cond.sql,
        "expected_delta": cond.expected_delta,
        "param_names": sorted(cond.params or {}),
    } for cond in post_conditions(spec)]


def _table_digest(table) -> dict | None:
    if table is None:
        return None
    return {
        "selector": table.selector,
        "columns": list(table.columns) if table.columns else None,
        "count_selector": table.count_selector,
        "value_map": dict(sorted(table.value_map.items())),
    }


def checks_sha256(cfg) -> str:
    """검증 내용만 추린 지문. 같은 검사면 같은 값이 나온다."""
    groups = []
    for name in ("data_checks", "spec_checks", "write_checks",
                 "visual_checks", "responsive_checks", "perf_checks"):
        for spec in getattr(cfg, name, []) or []:
            groups.append({
                "group": name,
                "name": spec.name,
                "page": getattr(spec, "page", ""),
                "steps": [_step_digest(s) for s in getattr(spec, "steps", [])],
                "ui_table": _table_digest(getattr(spec, "ui_table", None)),
                "query": _query_digest(getattr(spec, "query", None)),
                "expect_delta": getattr(spec, "expect_delta", None),
                "expect": _expect_digest(spec),
                "threshold": getattr(spec, "threshold", None),
                "viewports": list(getattr(spec, "viewports", []) or []),
                "budget_ms": getattr(spec, "budget_ms", None),
                "metric": getattr(spec, "metric", None),
            })
    # 인증 단계가 바뀌면 어떤 권한으로 본 화면인지가 달라진다.
    auth = getattr(cfg, "auth", None)
    payload = {
        "checks": groups,
        "auth": None if auth is None else {
            "per_context": auth.per_context,
            "steps": [_step_digest(s) for s in auth.steps],
        },
        "locale": cfg.target.locale,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
