"""
光里日语 作业系统 - 后端服务
提供用户注册/登录 + 做题记录同步
启动: python backend/main.py
访问: http://localhost:8000
"""

import os
import sys
import sqlite3
import hashlib
import json
import datetime
import secrets
import webbrowser
import threading
import time

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import uvicorn

# ─── 路径 ───
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Railway 上使用 Volume 持久化存储，本地使用 backend 目录
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "backend"))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "data.db")
# 密钥：Railway 上从环境变量读取，本地自动生成
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))

# ─── FastAPI ───
@asynccontextmanager
async def lifespan(app):
    init_db()
    yield

app = FastAPI(title="光里日语 作业系统", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 数据库 ───
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            time        TEXT NOT NULL,
            unit        TEXT NOT NULL,
            lesson      TEXT NOT NULL,
            section     TEXT NOT NULL,
            correct     INTEGER NOT NULL,
            total       INTEGER NOT NULL,
            score       INTEGER NOT NULL,
            results     TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_records_user ON records(user_id);
    """)
    conn.commit()
    conn.close()

# ─── JWT工具 ───
def create_token(user_id, username):
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + 86400 * 30  # 30天有效
    }
    return jwt_encode(payload)

def jwt_encode(payload):
    import jwt
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def jwt_decode(token):
    import jwt
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        return None

def get_current_user(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = jwt_decode(auth[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return payload

# ─── 数据模型 ───
class RegisterReq(BaseModel):
    username: str
    password: str

class LoginReq(BaseModel):
    username: str
    password: str

class RecordReq(BaseModel):
    time: str
    unit: str
    lesson: str
    section: str
    correct: int
    total: int
    score: int
    results: dict

# ─── API ───

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

@app.post("/api/register")
def register(req: RegisterReq):
    username = req.username.strip()
    password = req.password.strip()
    if len(username) < 4 or len(username) > 20:
        raise HTTPException(status_code=400, detail="账号长度4-20位")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="密码长度至少4位")
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="账号已存在")
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
                       (username, pwd_hash, now))
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    token = create_token(user_id, username)
    return {"token": token, "username": username, "user_id": user_id}

@app.post("/api/login")
def login(req: LoginReq):
    username = req.username.strip()
    password = req.password.strip()
    conn = get_db()
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    row = conn.execute("SELECT id, username FROM users WHERE username=? AND password=?",
                       (username, pwd_hash)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token = create_token(row["id"], row["username"])
    return {"token": token, "username": row["username"], "user_id": row["id"]}

@app.get("/api/records")
def get_records(user=Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, time, unit, lesson, section, correct, total, score, results FROM records WHERE user_id=? ORDER BY id DESC LIMIT 500",
        (user["user_id"],)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "time": r["time"],
            "unit": r["unit"],
            "lesson": r["lesson"],
            "section": r["section"],
            "correct": r["correct"],
            "total": r["total"],
            "score": r["score"],
            "results": json.loads(r["results"]) if r["results"] else {}
        })
    return result

@app.post("/api/records")
def save_record(req: RecordReq, user=Depends(get_current_user)):
    conn = get_db()
    conn.execute(
        "INSERT INTO records (user_id, time, unit, lesson, section, correct, total, score, results) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user["user_id"], req.time, req.unit, req.lesson, req.section, req.correct, req.total, req.score, json.dumps(req.results))
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/records")
def clear_records(user=Depends(get_current_user)):
    conn = get_db()
    conn.execute("DELETE FROM records WHERE user_id=?", (user["user_id"],))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# ─── 静态文件服务 ───
@app.get("/")
def serve_index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/{path:path}")
def serve_static(path: str):
    file_path = os.path.join(BASE_DIR, path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    # 如果是前端路由，返回index.html（SPA fallback）
    if not path.startswith("api/"):
        return FileResponse(os.path.join(BASE_DIR, "index.html"))
    raise HTTPException(status_code=404)

# ─── 启动 ───
def get_local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

if __name__ == "__main__":

    # Railway 部署时使用 $PORT 环境变量，本地运行默认 8000
    port = int(os.environ.get("PORT", 8000))
    is_railway = "PORT" in os.environ

    print("=" * 50)
    print("  光里日语 作业系统 已启动！")
    print("=" * 50)
    if is_railway:
        print(f"  已部署到 Railway 云端")
        print(f"  通过 Railway 分配的域名访问")
    else:
        local_ip = get_local_ip()
        print(f"  电脑访问: http://localhost:{port}")
        print(f"  手机访问: http://{local_ip}:{port}")
        print(f"  手机和电脑必须在同一WiFi网络下")
        # 自动打开浏览器
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    print(f"")
    print(f"  按 Ctrl+C 停止服务")
    print("=" * 50)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")