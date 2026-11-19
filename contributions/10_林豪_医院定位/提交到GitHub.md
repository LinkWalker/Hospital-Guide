# 提交到 GitHub

> 把 `<仓库地址>` 换成老师/团队给的 GitHub 作业仓库地址。

## 1. 克隆仓库

```bash
git clone <仓库地址>
cd <仓库目录>
```

## 2. 新建自己的分支

```bash
git checkout -b contrib-10-linhao
```

## 3. 复制本包

把本文件夹整个复制到仓库的：

```text
contributions/10_林豪_医院定位/
```

Windows 可以直接把文件夹拖进去。

## 4. 提交

```bash
git add contributions/10_林豪_医院定位
git commit -m "docs(alpha): add 林豪 hospital location contribution"
git push -u origin contrib-10-linhao
```

## 5. 发起 PR

在 GitHub 上把 `contrib-10-linhao` 合并到 `main`，PR 标题：

```text
docs(alpha): 林豪 contribution
```

## 注意

- 不要提交 `.env`、`data/`、`.venv/`、`__pycache__/`；
- 不要直接 push 到 `main`；
- 提交前先看一眼 `README.md` 的“提交前确认”。
