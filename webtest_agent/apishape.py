"""REST 응답에서 `query.api` 설정 초안을 뽑는다.

정답원을 API로 둘 때 사람이 손으로 해야 했던 일이 이것이다.

    curl … | jq          응답을 눈으로 훑고
    "배열이 data 밑이네"   rows_path를 찾고
    "owners[0].displayName이네"  점 표기 경로를 만든다

설정에서 가장 오래 걸리는 부분이고, 화면을 보고 아는 것이 아니라 응답을
읽어야 아는 것이라 `discover`로도 대신할 수 없었다.

여기서는 응답을 걸어다니며 **행 배열 후보**와 **각 항목의 잎 필드 경로**를
찾는다. 판단은 하지 않는다 — 어느 배열이 맞는지, 어느 필드를 대조할지는
사람이 정한다. 이 명령은 선택지를 보여줄 뿐이다.
"""
from __future__ import annotations

# 행 배열로 볼 수 있는 최소 길이. 1개짜리 배열은 대개 목록이 아니라
# 단일 객체를 감싼 것이라 후보로 올리면 잡음이 된다.
MIN_ROWS = 1
MAX_DEPTH = 4
MAX_FIELDS = 40
_SAMPLE_LEN = 40


def _leaf_paths(obj, prefix: str = "", depth: int = 0) -> list[tuple[str, str, str]]:
    """항목 하나에서 (경로, 타입, 예시값)을 뽑는다.

    dict는 점으로, list는 인덱스로 내려간다 — `query.api.columns`가 쓰는
    표기와 같아야 그대로 붙여넣을 수 있다.
    """
    if depth > MAX_DEPTH:
        return []
    out: list[tuple[str, str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.extend(_leaf_paths(value, f"{prefix}{key}." if not prefix
                                   else f"{prefix}{key}.", depth + 1)
                       if isinstance(value, (dict, list))
                       else [(f"{prefix}{key}", _type_of(value), _sample(value))])
    elif isinstance(obj, list):
        # 목록은 첫 항목만 본다. 나머지도 같은 모양이라고 보고, 다르면
        # 사람이 응답을 직접 봐야 한다 — 여기서 추측하지 않는다.
        if obj:
            out.extend(_leaf_paths(obj[0], f"{prefix}0.", depth + 1)
                       if isinstance(obj[0], (dict, list))
                       else [(f"{prefix}0", _type_of(obj[0]), _sample(obj[0]))])
    return out[:MAX_FIELDS]


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "참/거짓"
    if isinstance(value, (int, float)):
        return "숫자"
    if value is None:
        return "없음"
    return "문자열"


def _sample(value) -> str:
    text = "" if value is None else str(value)
    return text[:_SAMPLE_LEN] + ("…" if len(text) > _SAMPLE_LEN else "")


def find_row_arrays(data, prefix: str = "", depth: int = 0) -> list[dict]:
    """행 배열이 될 수 있는 위치를 모두 찾는다.

    응답이 `{"data": [...]}`인지 `{"result": {"items": [...]}}`인지는 앱마다
    다르므로 하나를 고르지 않고 후보를 전부 보여준다.
    """
    found: list[dict] = []
    if isinstance(data, list) and len(data) >= MIN_ROWS:
        found.append({
            "rows_path": prefix.rstrip("."),
            "count": len(data),
            "fields": _leaf_paths(data[0]) if isinstance(data[0], (dict, list))
            else [("", _type_of(data[0]), _sample(data[0]))],
        })
    elif isinstance(data, dict) and depth <= MAX_DEPTH:
        for key, value in data.items():
            found.extend(find_row_arrays(value, f"{prefix}{key}.", depth + 1))
    return found


def render(data, url: str) -> str:
    """사람이 읽고 그대로 붙여넣을 수 있는 형태로."""
    candidates = find_row_arrays(data)
    lines = [f"응답 구조 — {url}", ""]
    if not candidates:
        lines += [
            "행 배열을 찾지 못했습니다.",
            "",
            "목록을 주는 경로인지 확인하세요. 단건 조회(/name/<이름>)는 표와",
            "행 단위로 대조할 수 없습니다.",
        ]
        return "\n".join(lines)

    # 큰 배열이 대개 목록이다. 다만 고르지는 않고 순서만 그렇게 둔다.
    candidates.sort(key=lambda c: -c["count"])
    for i, cand in enumerate(candidates, start=1):
        path = cand["rows_path"] or "(응답 자체가 배열)"
        lines.append(f"[{i}] rows_path: {path}   — {cand['count']}개 항목")
        for name, kind, sample in cand["fields"]:
            label = name or "(값 자체)"
            lines.append(f"      {label:<44} {kind:<6} {sample}")
        lines.append("")

    best = candidates[0]
    picked = [name for name, _, _ in best["fields"] if name][:4]
    lines += [
        "─" * 60,
        "붙여넣을 설정 초안 (후보 [1] 기준):",
        "",
        "  query:",
        "    api:",
        f"      url: '{url}'",
        f"      rows_path: {best['rows_path'] or ''}",
        f"      columns: [{', '.join(picked)}]",
        "",
        "columns는 화면 표의 열과 **같은 순서로** 맞춰야 합니다.",
        "위 목록은 응답에 있는 필드일 뿐, 무엇을 대조할지는 정해 주세요.",
    ]
    return "\n".join(lines)
