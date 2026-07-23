"""데모 주문 관리 대시보드 (검증 대상 앱).

DEMO_BUG=1 로 실행하면 에이전트 탐지력 시연용 버그 3개가 주입된다:
  BUG-1  상태 'shipped' 필터가 'delivered'까지 포함해 조회 (데이터 버그)
  BUG-2  '요약 보기' 버튼이 미정의 함수를 호출 (JS 콘솔 에러)
  BUG-3  날짜 범위 종료일이 미포함(<) 처리되어 경계일 주문 누락 (데이터 버그)
"""
import csv
import io
import os
import sqlite3

from flask import Flask, Response, render_template, request

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "demo.db")
BUG = os.environ.get("DEMO_BUG") == "1"

app = Flask(__name__, template_folder=os.path.join(BASE, "templates"))

STATUSES = ["pending", "shipped", "delivered", "cancelled"]
CATEGORIES = ["전자기기", "의류", "식품"]


def build_query(args) -> tuple[str, list]:
    where, params = [], []
    status = args.get("status") or ""
    if status:
        if BUG and status == "shipped":
            where.append("status IN ('shipped', 'delivered')")  # BUG-1
        else:
            where.append("status = ?")
            params.append(status)
    category = args.get("category") or ""
    if category:
        where.append("category = ?")
        params.append(category)
    q = (args.get("q") or "").strip()
    if q:
        where.append("customer LIKE ?")
        params.append(f"%{q}%")
    date_from = args.get("date_from") or ""
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from)
    date_to = args.get("date_to") or ""
    if date_to:
        op = "<" if BUG else "<="  # BUG-3
        where.append(f"created_at {op} ?")
        params.append(date_to)
    sql = "SELECT id, customer, status, category, amount, created_at FROM orders"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    return sql, params


def fetch(args) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    try:
        sql, params = build_query(args)
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@app.route("/")
def index():
    rows = fetch(request.args)
    return render_template(
        "index.html", rows=rows, count=len(rows),
        statuses=STATUSES, categories=CATEGORIES, args=request.args, bug=BUG,
    )


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/export.csv")
def export_csv():
    rows = fetch(request.args)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "customer", "status", "category", "amount", "created_at"])
    writer.writerows(rows)
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders.csv"},
    )


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        raise SystemExit("demo.db가 없습니다. 먼저 `python3 demo_app/seed.py`를 실행하세요.")
    print(f"[demo-app] http://127.0.0.1:5057 · DEMO_BUG={'1 (버그 주입 모드)' if BUG else '0 (정상 모드)'}")
    app.run(host="127.0.0.1", port=5057, threaded=True)
