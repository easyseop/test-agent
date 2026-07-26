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

_SQL_MASKED_PARTS = re.compile(
    r"""
    '(?:''|[^'])*'
    |"(?:\"\"|[^"])*"
    |`(?:``|[^`])*`
    |\[(?:\]\]|[^\]])*\]
    |--[^\r\n]*
    |/\*.*?\*/
    """,
    re.DOTALL | re.VERBOSE,
)
_SQL_TOKEN = re.compile(r"[A-Za-z_]+")
_READ_ONLY_SQL_STARTS = {"SELECT", "WITH"}
_FORBIDDEN_SQL_TOKENS = {
    "ALTER", "ANALYZE", "ATTACH", "BEGIN", "CALL", "COMMENT", "COMMIT",
    "COPY", "CREATE", "DELETE", "DETACH", "DO", "DROP", "EXEC", "EXECUTE",
    "GRANT", "IMPORT", "INSERT", "INSTALL", "LOAD", "LOCK", "MERGE",
    "PRAGMA", "REINDEX", "RELEASE", "RENAME", "REPLACE", "REVOKE",
    "ROLLBACK", "SAVEPOINT", "SET", "TRUNCATE", "UPDATE", "UPSERT", "VACUUM",
}


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


def validate_read_only_sql(sql: str) -> str:
    """정답원 SQL이 보수적인 단일 SELECT/WITH 조회인지 검증한다.

    문자열·인용 식별자·주석 안의 단어와 세미콜론은 판정에서 제외한다.
    SQL 파서가 아닌 안전 가드이므로 모호한 문장은 허용하지 않는다.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("조회 SQL이 비어 있습니다")

    code = _SQL_MASKED_PARTS.sub(" ", sql)
    statements = [part.strip() for part in code.split(";") if part.strip()]
    if len(statements) != 1:
        raise ValueError("조회 SQL은 단일 문장만 허용됩니다")

    tokens = [token.upper() for token in _SQL_TOKEN.findall(statements[0])]
    if not tokens or tokens[0] not in _READ_ONLY_SQL_STARTS:
        raise ValueError("조회 SQL은 SELECT 또는 WITH로 시작해야 합니다")

    forbidden = next((token for token in tokens if token in _FORBIDDEN_SQL_TOKENS), None)
    if forbidden:
        raise ValueError(f"조회 SQL에 쓰기·관리 키워드 {forbidden}를 사용할 수 없습니다")

    return sql
