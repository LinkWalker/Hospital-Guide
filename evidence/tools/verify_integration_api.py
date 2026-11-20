import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
OUT = Path(r"C:\Users\LENOVO\Desktop\软件工程文件夹\alpha冲刺\联调测试结果.json")
RUN = str(int(time.time()))
results = []


def check(name, method, path, expected, **kwargs):
    try:
        r = httpx.request(method, BASE + path, timeout=30, **kwargs)
        ok = r.status_code in expected if isinstance(expected, (list, tuple, set)) else r.status_code == expected
        results.append({
            "name": name,
            "method": method,
            "path": path,
            "status": r.status_code,
            "expected": expected,
            "ok": ok,
            "detail": r.text[:300],
        })
        return r, ok
    except Exception as exc:
        results.append({
            "name": name,
            "method": method,
            "path": path,
            "status": None,
            "expected": expected,
            "ok": False,
            "detail": f"{type(exc).__name__}: {exc}",
        })
        return None, False


# 1. health + static pages
r, _ = check("健康检查", "GET", "/api/v1/health", 200)
health = r.json() if r is not None else {}
check("后端首页", "GET", "/", 200)
check("前端脚本", "GET", "/app.js", 200)

# 2. normal chat, answer follow-up until result
session = f"it-normal-{RUN}"
answers = [
    "我肚子疼了两天，偶尔反酸，我是成年人",
    "没有胸痛、发热，也没有其他不舒服",
    "没有",
    "没有",
    "没有",
    "没有",
]
normal_result = None
normal_messages = []
for msg in answers:
    r, _ = check("普通分诊对话", "POST", "/api/v1/chat", 200, json={"session_id": session, "message": msg})
    if r is None or r.status_code != 200:
        break
    payload = r.json()
    normal_messages.append({"request": msg, "type": payload.get("type"), "message": payload.get("message", "")[:100]})
    if payload.get("type") == "result":
        normal_result = payload.get("data")
        break
normal_ok = bool(normal_result and normal_result.get("recommended_department"))
results.append({
    "name": "普通分诊生成报告",
    "method": "POST",
    "path": "/api/v1/chat",
    "status": 200 if normal_ok else None,
    "expected": "type=result 且有推荐科室",
    "ok": normal_ok,
    "detail": json.dumps({
        "session": session,
        "department": (normal_result or {}).get("recommended_department"),
        "alternative": (normal_result or {}).get("alternative_departments"),
        "basis_count": len((normal_result or {}).get("basis") or []),
        "messages": normal_messages,
    }, ensure_ascii=False)[:1000],
})

# 3. emergency
emergency_session = f"it-emergency-{RUN}"
r, _ = check("急症拦截", "POST", "/api/v1/chat", 200, json={
    "session_id": emergency_session,
    "message": "胸痛，冒冷汗，喘不上气",
})
emergency_ok = False
if r is not None and r.status_code == 200:
    p = r.json()
    emergency_ok = p.get("type") == "urgent" and "120" in p.get("message", "")
    results.append({
        "name": "急症返回 urgent + 120",
        "method": "POST",
        "path": "/api/v1/chat",
        "status": 200,
        "expected": "type=urgent 且含 120",
        "ok": emergency_ok,
        "detail": json.dumps({"type": p.get("type"), "message": p.get("message"), "data": p.get("data")}, ensure_ascii=False)[:600],
    })

# 4. boundaries
check("空消息拒绝", "POST", "/api/v1/chat", 422, json={"session_id": f"it-empty-{RUN}", "message": ""})
check("纯空白拒绝", "POST", "/api/v1/chat", 422, json={"session_id": f"it-blank-{RUN}", "message": "   "})
check("超长消息拒绝", "POST", "/api/v1/chat", 422, json={"session_id": f"it-long-{RUN}", "message": "肚子疼" * 1400})

# 5. history
r, _ = check("历史列表", "GET", "/api/v1/history?limit=50", 200)
history_ok = False
if r is not None and r.status_code == 200:
    items = r.json().get("items", [])
    ids = [x.get("id") for x in items]
    history_ok = session in ids
    results.append({
        "name": "历史列表包含本次会话",
        "method": "GET",
        "path": "/api/v1/history",
        "status": 200,
        "expected": f"包含 {session}",
        "ok": history_ok,
        "detail": json.dumps({"total": r.json().get("total"), "contains": history_ok}, ensure_ascii=False),
    })
check("历史详情", "GET", f"/api/v1/history/{session}", 200)
check("历史恢复", "POST", f"/api/v1/history/{session}/restore", 200)
check("历史不存在 404", "GET", f"/api/v1/history/it-not-exist-{RUN}", 404)
check("删除测试历史-普通", "DELETE", f"/api/v1/history/{session}", 204)
check("删除测试历史-急症", "DELETE", f"/api/v1/history/{emergency_session}", 204)

# 6. hospitals
r, _ = check("附近医院", "GET", "/api/v1/hospitals/nearby?lat=26.074&lng=119.296&limit=4", 200)
if r is not None and r.status_code == 200:
    body = r.json()
    results.append({
        "name": "附近医院返回结果",
        "method": "GET",
        "path": "/api/v1/hospitals/nearby",
        "status": 200,
        "expected": "至少 1 家医院",
        "ok": len(body.get("items", [])) >= 1,
        "detail": json.dumps({"total": body.get("total"), "first": (body.get("items") or [{}])[0].get("name")}, ensure_ascii=False),
    })
check("纬度越界拒绝", "GET", "/api/v1/hospitals/nearby?lat=91&lng=119", 422)

# 7. saved location
check("清除测试位置", "DELETE", "/api/v1/preferences/location", 204)
check("保存测试位置", "PUT", "/api/v1/preferences/location", 200, json={
    "label": "联调测试位置",
    "address": "福州大学旗山校区",
    "latitude": 26.057,
    "longitude": 119.193,
})
r, _ = check("读取保存位置", "GET", "/api/v1/preferences/location", 200)
if r is not None and r.status_code == 200:
    body = r.json()
    results.append({
        "name": "保存位置内容正确",
        "method": "GET",
        "path": "/api/v1/preferences/location",
        "status": 200,
        "expected": "label=联调测试位置",
        "ok": body.get("label") == "联调测试位置",
        "detail": json.dumps(body, ensure_ascii=False)[:400],
    })
check("清理保存位置", "DELETE", "/api/v1/preferences/location", 204)

# 8. geocode (external dependency, optional)
r, _ = check("地址解析（高德）", "GET", "/api/v1/hospitals/geocode?address=旗山校区", [200, 503])
if r is not None and r.status_code == 200:
    body = r.json()
    results.append({
        "name": "地址解析结果",
        "method": "GET",
        "path": "/api/v1/hospitals/geocode",
        "status": 200,
        "expected": "有经纬度",
        "ok": body.get("latitude") is not None and body.get("longitude") is not None,
        "detail": json.dumps(body, ensure_ascii=False)[:400],
    })

# 9. CORS / PNA
try:
    r = httpx.options(BASE + "/api/v1/chat", timeout=10, headers={
        "Origin": "null",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
        "Access-Control-Request-Private-Network": "true",
    })
    ok = r.status_code == 200 and r.headers.get("access-control-allow-origin") in ("*", "null")
    results.append({
        "name": "CORS/PNA 预检",
        "method": "OPTIONS",
        "path": "/api/v1/chat",
        "status": r.status_code,
        "expected": "200 且允许 Origin null",
        "ok": ok,
        "detail": json.dumps(dict(r.headers), ensure_ascii=False)[:600],
    })
except Exception as exc:
    results.append({"name": "CORS/PNA 预检", "method": "OPTIONS", "path": "/api/v1/chat", "status": None, "expected": "200", "ok": False, "detail": str(exc)})

failed = [x for x in results if not x["ok"]]
report = {
    "base": BASE,
    "run": RUN,
    "health": health,
    "total": len(results),
    "passed": len(results) - len(failed),
    "failed": len(failed),
    "cases": results,
}
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"total": report["total"], "passed": report["passed"], "failed": report["failed"],
                  "failures": [{"name": x["name"], "detail": x["detail"][:200]} for x in failed]},
                 ensure_ascii=False, indent=2))
