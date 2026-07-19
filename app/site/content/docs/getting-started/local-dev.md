---
title: "本地启动"
weight: 2
description: "启动当前短剧制作工作台。"
---

## 当前工作台

日常制作统一使用仓库根目录的 `web/`，访问地址为 `http://127.0.0.1:5273`。`app/front/` 的 `7788` 页面是旧 Studio 管理界面，不能作为当前产品页面的比对基准。

项目数据、生成图片和密钥保存在本机，不在 Git 中；不同电脑即使代码提交一致，也需要单独导入数据才能显示相同项目。

## 启动后端

```bash
cd app/backend
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 启动工作台

```bash
cd web
pnpm install
pnpm dev
```

## 默认端口

- 工作台：`http://localhost:5273`
- 后端：`http://localhost:8000`
- Swagger：`http://localhost:8000/docs`

## 旧 Studio 管理界面

仅在维护旧管理页面时使用：

```bash
cd app/front
pnpm run openapi:update
```

## 官网与文档站本地预览

```bash
cd app/site
hugo mod tidy
hugo server --buildDrafts --disableFastRender
```

## 推荐的联调顺序

1. 启动后端，确认 `/docs` 和 `/health` 正常。
2. 启动工作台，确认 `5273` 页面能访问并能请求后端。
3. 仅在维护 `app/front/` 旧管理界面并修改接口定义时，执行 `openapi:update`。
4. 如果同时在维护官网，再单独启动 `site/` 预览。
