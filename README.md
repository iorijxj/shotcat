<p align="center">
  <img src="assets/logo.png" alt="shotcat logo" width="520">
</p>

# shotcat 🎬

> plotcat 写故事，**shotcat** 拍故事。—— plotcat 系列的短剧生产层（猫叔的短剧工作台）

**剧本到来之后**的短剧生产工具。编剧不在范围内（那是「原点编剧系统 / plotcat」的活，做完我们接）。
视觉方向：暗色专业创作台（近黑 + 琥珀金）。设计稿见 `design/`。

本工具负责的链路：
```
剧本(原点产出) → 造型/场景/道具/服装设置 → 文字分镜 → 图像分镜 → 图生视频（最远到此）
```

## 结构
```
shotcat/
├── app/          平台 底座（生产平台：分镜/资产/关键帧/图生视频，React+FastAPI）
├── bridge/       剧本接入桥（story-bible.json → 平台 项目+造型资产+文字分镜）
├── knowledge/    story-bible.schema.v1.json = 与原点的交接契约（其余知识库为写作侧参考，本工具不用）
├── _archive/     已作废的编剧模块（screenwriter，保留备查）
└── PLAN.md       落地规划与进度
```

## 与原点的关系
- **原点**产出剧本 + 结构化设定（角色/场景等）。
- 交接格式 = `knowledge/story-bible.schema.v1.json`（角色 char_001 / 场景 scene_001，与 平台 实体同构）。
- 原点完成后，其输出映射到本 schema，经 `bridge` 一键进入生产。当前可用手写/样例 story-bible 先跑通生产侧。

## 现状
生产链路前半段已通：`bridge` 能把剧本 → 项目+造型资产+文字分镜（Celery 异步切镜）。
下一步：接入**图像模型**（造型图 + 图像分镜）与**视频模型**（图生视频）。详见 `PLAN.md`。

## 快速开始

前置：Docker Desktop ｜ Node.js 18+ 与 pnpm ｜ Python 3.10+ ｜ 一个 GLM API Key（[智谱开放平台](https://open.bigmodel.cn)）

**① 启动平台底座**（后端 API + MySQL + Redis + Celery + 对象存储，全在 Docker 里）
```bash
cd app/deploy/compose
cp .env.example .env        # 本地体验用默认值即可，建议改掉两个 change-me 密码
docker compose --env-file .env -f docker-compose.yml up -d
```
平台前端 http://localhost:7788 ｜ 后端 API http://localhost:8000

**② 配置 GLM Key**（切镜 / 视觉词典 / 设定抽取靠它）

写入 `bridge/.glm_key`（一行 key 即可），或设环境变量 `GLM_API_KEY`。

**③ 启动剧本接入桥**（纯 Python 标准库，无需 pip install）
```bash
cd bridge
python pipeline_server.py   # http://127.0.0.1:5280
```

**④ 启动 shotcat 工作台**
```bash
cd web
pnpm install
pnpm dev                    # http://localhost:5273（dev 已代理 /api→8000、/pipeline→5280）
```

日常使用只开浏览器访问 **http://localhost:5273** 即可；平台自带前端（7788）仅在需要底座原生功能时打开。

## 作者

本系统（shotcat / 短剧生产工作台）由云一工作室主理人 **猫叔** 独立开发。
仓库地址：<https://github.com/mmlong818/shotcat>
姊妹项目：[plotcat / 原点编剧系统](https://github.com/mmlong818/plotcat)（写故事的那一半）

## 许可证

[PolyForm Noncommercial 1.0.0](LICENSE) —— 允许个人使用、学习、修改和非商业分发；**不允许任何商业用途**。

> Required Notice: Copyright © 2026 猫叔 (<https://github.com/mmlong818/shotcat>)
