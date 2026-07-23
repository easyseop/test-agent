"""데모 주문 관리 대시보드 (검증 대상 앱).

DEMO_BUG=1 로 실행하면 에이전트 탐지력 시연용 버그 4개가 주입된다:
  BUG-1  상태 'shipped' 필터가 'delivered'까지 포함해 조회 (데이터 버그)
  BUG-2  '요약 보기' 버튼이 미정의 함수를 호출 (JS 콘솔 에러)
  BUG-3  날짜 범위 종료일이 미포함(<) 처리되어 경계일 주문 누락 (데이터 버그)
  BUG-4  내부 확인용 디버그 배너가 화면에 노출 (시각 회귀로 검출)
"""
import csv
import io
import os
import sqlite3
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, redirect, render_template, request, session

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "demo.db")
BUG = os.environ.get("DEMO_BUG") == "1"
AUTH = os.environ.get("DEMO_AUTH") == "1"  # 로그인 모드 (계정: demo / demo1234)

app = Flask(__name__, template_folder=os.path.join(BASE, "templates"))
app.secret_key = "demo-app-secret"


@app.before_request
def _auth_guard():
    if not AUTH:
        return None
    if request.endpoint in ("login", "favicon", "static"):
        return None
    if not session.get("user"):
        return redirect("/login")
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("username") == "demo" and request.form.get("password") == "demo1234":
            session["user"] = "demo"
            return redirect("/")
        error = "아이디 또는 비밀번호가 올바르지 않습니다."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login" if AUTH else "/")

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
    total = len(rows)
    per_page = request.args.get("per_page", type=int) or 0
    page_no = max(request.args.get("page", type=int) or 1, 1)
    last_page = 1
    prev_url = next_url = ""
    if per_page > 0:
        last_page = max((total + per_page - 1) // per_page, 1)
        page_no = min(page_no, last_page)
        rows = rows[(page_no - 1) * per_page: page_no * per_page]

        def page_url(n: int) -> str:
            params = request.args.to_dict()
            params["page"] = str(n)
            return "/?" + urlencode(params)

        prev_url = page_url(page_no - 1) if page_no > 1 else ""
        next_url = page_url(page_no + 1) if page_no < last_page else ""
    return render_template(
        "index.html", rows=rows, count=total,
        statuses=STATUSES, categories=CATEGORIES, args=request.args, bug=BUG,
        auth_user=session.get("user") if AUTH else None,
        per_page=per_page, page_no=page_no, last_page=last_page,
        prev_url=prev_url, next_url=next_url,
    )


@app.route("/api/orders")
def api_orders():
    rows = fetch(request.args)
    return jsonify({
        "count": len(rows),
        "orders": [
            {"id": r[0], "customer": r[1], "status": r[2],
             "category": r[3], "amount": r[4], "created_at": r[5]}
            for r in rows
        ],
    })


@app.route("/new", methods=["GET", "POST"])
def new_order():
    error = ""
    if request.method == "POST":
        customer = (request.form.get("customer") or "").strip()
        status = request.form.get("status") or ""
        category = request.form.get("category") or ""
        amount = request.form.get("amount") or ""
        created_at = request.form.get("date") or ""
        if not customer or not amount or status not in STATUSES or category not in CATEGORIES:
            error = "모든 항목을 올바르게 입력해주세요."
        else:
            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute(
                    "INSERT INTO orders (customer, status, category, amount, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (customer, status, category, int(amount), created_at or "2026-07-01"),
                )
                conn.commit()
            finally:
                conn.close()
            return redirect("/")
    return render_template("new.html", statuses=STATUSES, categories=CATEGORIES, error=error)


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
    print(f"[demo-app] http://127.0.0.1:5057 · DEMO_BUG={'1 (버그 주입 모드)' if BUG else '0 (정상 모드)'}"
          f" · DEMO_AUTH={'1 (로그인 필요: demo/demo1234)' if AUTH else '0'}")
    app.run(host="127.0.0.1", port=5057, threaded=True)
