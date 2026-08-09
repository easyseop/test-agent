"""2단계 인증 코드(TOTP) 생성 — RFC 6238.

로그인에 6자리 코드를 요구하는 앱이 늘었다. 그 코드는 앱과 인증기가 **같은
비밀키와 현재 시각**으로 각자 계산하는 값이라, 비밀키만 있으면 브라우저 없이도
같은 값을 만들 수 있다.

표준 라이브러리만 쓴다(hmac·hashlib·base64). 의존성을 늘리지 않기 위해서이기도
하지만, 인증 코드를 만드는 코드가 외부 패키지 업데이트로 조용히 달라지면
곤란하기 때문이다.

**비밀키는 환경변수로만 받는다.** 이 값은 비밀번호와 같은 무게이며 YAML·리포트·
로그 어디에도 평문으로 남지 않는다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

# 표준(RFC 6238)의 기본값. 대부분의 앱이 이 조합을 쓴다.
DEFAULT_PERIOD = 30
DEFAULT_DIGITS = 6
_ALGORITHMS = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}


class TotpError(ValueError):
    """비밀키 형식이 잘못됐거나 지원하지 않는 설정."""


def _decode_secret(secret: str) -> bytes:
    """인증기 앱이 보여주는 base32 문자열을 바이트로 바꾼다.

    사람이 옮겨 적기 좋게 공백과 소문자를 허용하고, 길이가 8의 배수가 아니면
    '=' 채움을 보태 준다 — 실제 앱들이 채움 없이 표시하는 경우가 흔하다.
    """
    cleaned = "".join(secret.split()).upper()
    if not cleaned:
        raise TotpError("TOTP 비밀키가 비어 있습니다")
    cleaned += "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned, casefold=True)
    except (ValueError, TypeError) as err:
        raise TotpError("TOTP 비밀키가 base32 형식이 아닙니다") from err


def generate(secret: str, *, at: float | None = None, period: int = DEFAULT_PERIOD,
             digits: int = DEFAULT_DIGITS, algorithm: str = "sha1") -> str:
    """지금 시각의 인증 코드를 만든다.

    at을 주면 그 시각 기준으로 계산한다 — 테스트가 시계에 기대지 않게 하려는
    용도이며, 실행 중에는 쓰지 않는다.
    """
    if period <= 0:
        raise TotpError("TOTP period는 1초 이상이어야 합니다")
    if not 6 <= digits <= 10:
        raise TotpError("TOTP digits는 6에서 10 사이여야 합니다")
    digest = _ALGORITHMS.get(algorithm.lower())
    if digest is None:
        raise TotpError(f"지원하지 않는 TOTP 알고리즘입니다: {algorithm}")

    key = _decode_secret(secret)
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    # RFC 4226 동적 절단 — 마지막 니블이 가리키는 위치에서 4바이트를 취한다.
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)
