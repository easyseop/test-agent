"""데모 DB 시드 — 고정 시드로 결정적 데이터 생성 (재실행 시 초기화)."""
import os
import random
import sqlite3
from datetime import date, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "demo.db")

CUSTOMERS = ["김민준", "이서연", "박지훈", "최수아", "정도윤", "강하은",
             "조은우", "윤지민", "임서준", "한예은", "오시우", "서지우"]
CATEGORIES = ["전자기기", "의류", "식품"]
STATUS_POOL = ["pending"] * 25 + ["shipped"] * 30 + ["delivered"] * 30 + ["cancelled"] * 15


def main() -> None:
    random.seed(20260501)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer TEXT NOT NULL,
            status TEXT NOT NULL,
            category TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    start = date(2026, 5, 1)
    rows = []
    for i in range(120):
        d = start + timedelta(days=random.randint(0, 60))
        rows.append((
            1001 + i,
            random.choice(CUSTOMERS),
            random.choice(STATUS_POOL),
            random.choice(CATEGORIES),
            random.randint(50, 2500) * 100,
            d.isoformat(),
        ))
    # 날짜 경계 검증 보장용 고정 행 (BUG-3 탐지에 필요)
    rows.append((1201, "경계일테스트", "shipped", "식품", 99000, "2026-06-30"))
    rows.append((1202, "경계일테스트", "delivered", "의류", 88000, "2026-06-01"))

    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM orders GROUP BY status"))
    conn.close()
    print(f"demo.db 생성 완료: 총 {len(rows)}건 · 상태별 {counts}")


if __name__ == "__main__":
    main()
