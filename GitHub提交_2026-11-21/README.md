# 门诊排队｜智能导诊与挂号导航助手（Alpha）

## 这是什么

面向“不知道该挂哪个科、到了医院找不到路”的诊前分诊导航工具。核心链路：

```text
分诊报告 → 医院推荐 → 挂号指引 → 院内导航
```

## 本地运行

```bat
启动后端.bat
```

或：

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`。不启动后端时，可直接打开 `门诊排队-Alpha演示.html`。

## 测试

```powershell
.\run_tests.ps1
```

当前结果：**65 passed**；API 联调 **28/28**。

## 目录

```text
app/                 FastAPI 后端
frontend/            前端页面
tests/               pytest 测试
docs/                设计与 Alpha 材料
contributions/       12 人贡献证据包
门诊排队-Alpha演示.html   单文件离线演示
```

## 边界

- 不提供在线问诊、不开处方，不替代医生诊断；
- 没有真实号源，只做官方渠道指引；
- 当前大模型为 mock，真实模型回归未完成；
- 院内导航为示意路线，不是真实室内定位；
- 10 人试用、公网部署、完整 12 人合流仍需后续完成。

## 说明

本仓库用于 Alpha 冲刺提交与答辩演示。未完成项在 `docs/alpha/04_Alpha冲刺总结.md` 中如实列出，不把计划写成已完成。
