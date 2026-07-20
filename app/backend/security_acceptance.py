"""安全整改端到端验收脚本（供本机测试用，非应用代码）。

一条命令跑完手册 B 类的全部端到端检查，逐项打印 PASS/FAIL/SKIP 并给汇总。
用法（在 app/backend 目录）：
    uv run python security_acceptance.py

前置：已按测试手册第 1 步起栈（run.bat：backend 在 8000、mysql/redis/rustfs 已起），
并已建好 alice / bob 两个账号（密码 Test1234!），alice 建过至少一个项目。
"""

from __future__ import annotations

import asyncio
import sys

try:  # 让 Windows 控制台正常显示中文（cmd 默认 GBK 时可能仍需 chcp 65001）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
ALICE = ("alice", "Test1234!")
BOB = ("bob", "Test1234!")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def _login(username: str, password: str) -> str:
    r = httpx.post(BASE + "/auth/login", json={"username": username, "password": password}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def check_isolation(ta: str, tb: str) -> tuple[str, str]:
    """3.1/3.2/4.x：跨用户项目隔离。"""
    ha = {"Authorization": f"Bearer {ta}"}
    hb = {"Authorization": f"Bearer {tb}"}
    a_ids = [i["id"] for i in httpx.get(BASE + "/studio/projects", headers=ha, timeout=10).json()["data"]["items"]]
    b_ids = [i["id"] for i in httpx.get(BASE + "/studio/projects", headers=hb, timeout=10).json()["data"]["items"]]
    overlap = set(a_ids) & set(b_ids)
    cross = httpx.get(BASE + f"/studio/projects/{a_ids[0]}", headers=hb, timeout=10).status_code if a_ids else None
    no_token = httpx.get(BASE + "/studio/projects", timeout=10).status_code
    ok = (not overlap) and no_token == 401 and (cross in (404, None))
    note = "" if a_ids else "（alice 无项目，越权项未覆盖，建议先用 alice 建一个项目）"
    detail = f"alice={len(a_ids)}个 bob={len(b_ids)}个 交集={len(overlap)} bob越权访问={cross}(期望404) 无token={no_token}(期望401){note}"
    return (PASS if ok else FAIL), detail


def check_crypto() -> tuple[str, str]:
    """2.3：api_key 加密存储（直连库看密文）。"""
    async def _run():
        from sqlalchemy import text
        from app.core.db import async_session_maker
        async with async_session_maker() as db:
            return (await db.execute(text("SELECT id, api_key FROM providers WHERE api_key <> ''"))).all()

    try:
        rows = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        return SKIP, f"无法连接数据库（容器未起？）：{type(exc).__name__}"
    if not rows:
        return SKIP, "库里没有已配 key 的 provider（可在页面配一个再测）"
    bad = [rid for rid, ak in rows if not str(ak).startswith("gAAAAA")]
    ok = not bad
    return (PASS if ok else FAIL), f"{len(rows)}个provider，非Fernet密文的={bad}"


def check_upload(tb: str) -> tuple[str, str]:
    """3.2：上传大小限制（3MB 图片超 2MB 上限 -> 413）。"""
    big = b"\xff" * (3 * 1024 * 1024)
    r = httpx.post(
        BASE + "/studio/files/upload",
        headers={"Authorization": f"Bearer {tb}"},
        files={"file": ("big.png", big, "image/png")},
        timeout=30,
    )
    return (PASS if r.status_code == 413 else FAIL), f"上传3MB图片 HTTP {r.status_code}（期望413）"


def check_rate_limit(tb: str) -> tuple[str, str]:
    """3.4：生成类接口限流（每用户 10 次/分钟，第 11 次起 429）。"""
    h = {"Authorization": f"Bearer {tb}"}
    codes = []
    for _ in range(12):
        r = httpx.post(
            BASE + "/script-processing/divide-async",
            headers=h,
            json={"script_text": "x", "chapter_id": "nope", "write_to_db": False},
            timeout=10,
        )
        codes.append(r.status_code)
    return (PASS if 429 in codes else FAIL), f"12次请求状态码={codes}（期望出现429）"


def check_login_lockout() -> tuple[str, str]:
    """3.1：登录防暴力破解（连续失败达阈值 -> 429）。用假用户名，不锁真实账号。"""
    probe = "__lockout_probe__"
    codes = [
        httpx.post(BASE + "/auth/login", json={"username": probe, "password": "wrong"}, timeout=10).status_code
        for _ in range(6)
    ]
    return (PASS if 429 in codes else FAIL), f"6次错误登录={codes}（期望出现429）"


def main() -> None:
    print("==== shotcat 安全整改端到端验收 ====")
    print(f"目标 backend: {BASE}\n")
    try:
        ta = _login(*ALICE)
        tb = _login(*BOB)
    except httpx.ConnectError:
        print("[前置失败] 连不上 backend(http://127.0.0.1:8000)。请先按手册第 1 步起栈(run.bat)。")
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(f"[前置失败] alice/bob 登录失败：{exc}")
        print("请确认已建账号(手册 1.5)、密码为 Test1234!，且 alice 未被登录锁定。")
        sys.exit(2)

    checks = [
        ("跨用户项目隔离", lambda: check_isolation(ta, tb)),
        ("API Key 加密存储", check_crypto),
        ("上传大小限制(3MB->413)", lambda: check_upload(tb)),
        ("生成接口限流(第11次429)", lambda: check_rate_limit(tb)),
        ("登录防暴力破解(锁定429)", check_login_lockout),
    ]

    n_pass = n_fail = n_skip = 0
    for i, (name, fn) in enumerate(checks, 1):
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001
            status, detail = FAIL, f"执行异常：{type(exc).__name__}: {exc}"
        if status == PASS:
            n_pass += 1
        elif status == FAIL:
            n_fail += 1
        else:
            n_skip += 1
        print(f"[{i}/{len(checks)}] {name} .......... [{status}]")
        print(f"        {detail}")

    print(f"\n结果: {n_pass} 通过, {n_fail} 失败, {n_skip} 跳过")
    if n_fail == 0:
        print("✅ 应用层端到端验收通过（跳过项请按提示补测）")
    else:
        print("❌ 有失败项，请对照手册排查")
    print("提示：本脚本会占用 bob 当分钟的生成配额、并累积本机 IP 的登录失败计数；")
    print("      反复运行若触发 IP 级登录锁定，重启 backend 即可清空内存计数。")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
