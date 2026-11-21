# -*- coding: utf-8 -*-
"""命令行演示：三条典型链路（完整多轮 / 急症拦截 / 改口修正）。"""
import json
import sys
import uuid
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def chat(sid, text):
    req = urllib.request.Request(
        BASE + "/api/v1/chat",
        data=json.dumps({"session_id": sid, "message": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def run(title, turns):
    sid = uuid.uuid4().hex[:8]
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)
    for text in turns:
        resp = chat(sid, text)
        print(f"\n[用户] {text}")
        if resp["type"] == "urgent":
            print(f"[系统·急症] ({resp['data']['level']}) {resp['message'][:60]}...")
            break
        elif resp["type"] == "question":
            print(f"[系统·追问] {resp['message']}")
        else:
            d = resp["data"]
            print(f"[系统·结果] 推荐: {d['recommended_department']} | 备选: {d['alternative_departments']}")
            print(f"  依据: {d['basis'][:1]}")
            print(f"  话术: {d['dialogue_script'][:60]}...")
            break
    return resp


if __name__ == "__main__":
    run("链路1 完整多轮：肚子疼两天+反酸", [
        "我肚子疼了两天，偶尔反酸，该挂什么科？",
        "是成年人",
    ])
    run("链路2 急症拦截", ["我刚刚剧烈胸痛，还出冷汗"])
    run("链路3 改口修正：先胸口后肚子", [
        "我胸口闷",
        "说错了，不是胸口闷，而是肚子疼，持续三天，我是成年人",
    ])
    run("链路4 追问上限兜底", ["不知道", "不清楚", "就难受"])
