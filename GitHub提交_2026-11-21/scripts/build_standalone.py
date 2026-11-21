"""把前端打包成“双击即可运行”的单文件 HTML。

用法（在 medical-triage-backend 目录）：
    python scripts/build_standalone.py

产物：
    ../门诊排队-Alpha演示.html       （推荐：单独发给老师/同学）
    ../原型演示_墨刀复刻版.html       （兼容旧文件名）

说明：
- CSS、JS、logo 全部内联，不依赖本地服务器；
- 打开后优先尝试 http://127.0.0.1:8000，连不上自动进入本地 mock；
- 历史记录保存在浏览器 localStorage；
- 接真实大模型仍需启动后端，单文件只做离线演示。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # medical-triage-backend
OUT_DIR = ROOT.parent                                # 可运行程序1.0
INDEX = ROOT / "frontend" / "index.html"
APP_JS = ROOT / "frontend" / "app.js"

MARKER = '<script src="./app.js"></script>'


def build() -> Path:
    html = INDEX.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    if MARKER not in html:
        raise SystemExit("index.html 中找不到 app.js 引用，请检查前端结构")
    combined = html.replace(MARKER, "<script>\n" + js + "\n</script>")
    # 单文件版加一行注释，方便确认版本
    combined = combined.replace(
        "<head>",
        "<head>\n<!-- 门诊排队 Alpha 单文件演示版：由 scripts/build_standalone.py 生成 -->",
        1,
    )
    out = OUT_DIR / "门诊排队-Alpha演示.html"
    out.write_text(combined, encoding="utf-8")
    return out


if __name__ == "__main__":
    out = build()
    # 兼容旧文件名，方便之前收藏/转发的链接继续可用
    legacy = OUT_DIR / "原型演示_墨刀复刻版.html"
    legacy.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"已生成: {out}")
    print(f"已同步: {legacy}")
