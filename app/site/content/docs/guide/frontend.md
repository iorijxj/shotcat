---
title: "前端说明"
weight: 2
description: "理解前端页面组织、请求层和状态管理。"
---

当前日常制作前端位于仓库根目录 `web/`，使用 React、TypeScript 与 Vite，默认访问 `http://localhost:5273`。它是产品功能的唯一比对基准。

`app/front/` 是保留的旧 Studio 管理前端，使用 Ant Design 与 OpenAPI 生成客户端，默认端口 `7788`；它与当前工作台页面不同，仅用于维护和历史兼容。

旧 Studio 前端的核心结构如下：

- `src/App.tsx`：路由入口
- `src/layouts/`：全局布局
- `src/pages/aiStudio/`：项目、章节、分镜、资产等页面
- `src/services/`：OpenAPI 生成客户端与请求封装
- `src/store/`：全局状态

## 页面组织思路

前端页面不是按“技术组件”分，而是按业务域分：

- `project/`
- `chapter/`
- `shots/`
- `assets/`
- `prompts/`
- `files/`
- `models/`
- `agents/`

## 请求层

前端主要通过 `front/src/services/generated/` 中的 OpenAPI 生成代码与后端通信。  
这意味着后端接口变更后，前端应同步执行一次 `pnpm run openapi:update`。
