"""쓰기·외부 전송·세션 파괴 동작에 대한 최소 안전 경계."""
from __future__ import annotations

import re
from collections.abc import Iterable


# 차단 목록은 2단이다.
#
# ① DESTRUCTIVE_PATTERNS — 되돌릴 수 없거나 외부에 영향을 주는 동작.
#    자동 스윕은 물론이고 사람이 직접 적은 click 스텝에서도 실행하지 않는다.
#    (승인받은 write_checks만 예외)
# ② HARD_BLOCK_PATTERNS — ① 전부 + 자동 스윕에서만 막는 일반 쓰기·세션 동작.
#    사람이 명시적으로 적은 스텝은 로그인 폼 submit처럼 정상 흐름일 수 있어 막지 않는다.
#
# 한국어 우선 제품이므로 같은 동작의 한국어 표현을 함께 넣는다. 목록에 없는
# 어휘는 막히지 않으므로, 이 목록은 최후 안전망이지 업무 승인 체계가 아니다.
DESTRUCTIVE_PATTERNS = [
    "삭제", "제거", "폐기", "비우기", "delete", "remove", "destroy", "purge",
    "wipe", "truncate", "drop",
    "초기화", "reset",
    "탈퇴", "해지", "withdraw",
    "결제", "pay", "checkout", "purchase", "구매", "환불", "refund",
    "발송", "전송", "보내기", "send", "email", "sms", "문자",
    "초대", "invite", "배포", "publish", "deploy",
]

HARD_BLOCK_PATTERNS = [
    *DESTRUCTIVE_PATTERNS,
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
    # SELECT로 시작하면서 테이블·파일을 만드는 구문 (PostgreSQL·MySQL·SQL Server)
    "INTO", "OUTFILE", "DUMPFILE",
}

# 토큰 정규식이 '_'를 단어 문자로 포함하므로 LOAD_FILE 같은 이름은 LOAD와 따로
# 취급된다. 단일 키워드 차단으로는 막히지 않는 위험 함수를 이름 단위로 막는다.
# 함수 화이트리스트가 아니므로 완전하지 않다 — DB 계정 read-only가 본 방어선이다.
_FORBIDDEN_SQL_FUNCTIONS = {
    "LOAD_FILE", "SYS_EXEC", "SYS_EVAL", "XP_CMDSHELL",
    "LO_EXPORT", "LO_IMPORT",
    "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR", "PG_SLEEP",
    "DBLINK", "DBLINK_EXEC", "BENCHMARK", "SLEEP", "WAITFOR",
    "WRITEFILE", "READFILE",
}


def merge_avoid_patterns(configured: Iterable[str]) -> list[str]:
    """사용자 패턴에 제거 불가능한 최소 차단 목록을 합친다."""
    return list(dict.fromkeys([*HARD_BLOCK_PATTERNS, *(str(p) for p in configured)]))


def _find_pattern(patterns: list[str], values: tuple[object, ...]) -> str | None:
    haystack = " ".join(str(value or "") for value in values)
    return next(
        (pattern for pattern in patterns
         if re.search(re.escape(pattern), haystack, re.IGNORECASE)),
        None,
    )


def find_hard_block(*values: object) -> str | None:
    """자동 탐색(스윕) 차단 판정 — 텍스트·selector·href 중 첫 일치 패턴."""
    return _find_pattern(HARD_BLOCK_PATTERNS, values)


def find_destructive(*values: object) -> str | None:
    """사람이 명시한 스텝에도 적용하는 파괴적 동작 판정.

    되돌릴 수 없거나 외부에 영향을 주는 동작만 대상이며, 저장·등록·submit처럼
    정상 테스트 흐름에 필요한 동작은 포함하지 않는다.
    """
    return _find_pattern(DESTRUCTIVE_PATTERNS, values)


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

    function = next((token for token in tokens if token in _FORBIDDEN_SQL_FUNCTIONS), None)
    if function:
        raise ValueError(f"조회 SQL에 파일·명령·지연 함수 {function}를 사용할 수 없습니다")

    return sql
