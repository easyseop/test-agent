"""쓰기·외부 전송·세션 파괴 동작에 대한 최소 안전 경계."""
from __future__ import annotations

import re
from collections.abc import Iterable


# 사용자가 YAML의 avoid_patterns를 비워도 제거할 수 없는 자동 탐색 차단 목록이다.
# 명시적으로 작성한 write_checks만 CLI 승인 후 실행할 수 있으며, 자동 스윕은
# 아래 동작을 어떤 경우에도 클릭하지 않는다.
HARD_BLOCK_PATTERNS = [
    "삭제", "delete", "remove", "destroy",
    "탈퇴", "결제", "pay", "checkout", "purchase", "구매",
    "발송", "send", "email", "sms", "문자",
    "초대", "invite", "배포", "publish", "deploy",
    "저장", "save", "등록", "create", "submit", "수정", "update",
    "logout", "로그아웃",
]


def merge_avoid_patterns(configured: Iterable[str]) -> list[str]:
    """사용자 패턴에 제거 불가능한 최소 차단 목록을 합친다."""
    return list(dict.fromkeys([*HARD_BLOCK_PATTERNS, *(str(p) for p in configured)]))


def find_hard_block(*values: object) -> str | None:
    """텍스트·selector·href 중 위험 동작을 나타내는 첫 패턴을 반환한다."""
    haystack = " ".join(str(value or "") for value in values)
    return next(
        (pattern for pattern in HARD_BLOCK_PATTERNS
         if re.search(re.escape(pattern), haystack, re.IGNORECASE)),
        None,
    )
