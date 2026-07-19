# 旧 Studio 管理界面

此目录是保留的 Studio 管理前端，默认端口为 `7788`。它不等同于当前短剧制作工作台，因此页面结构与功能展示会不同。

日常制作、验收和问题复现请统一使用仓库根目录的 `web/`：

```powershell
cd web
pnpm install
pnpm dev
```

访问 <http://127.0.0.1:5273>。

项目数据、生成图片和 API 密钥不在 Git 中。不同机器要得到相同的作品库和图片，还需要单独迁移 `app/backend/jellyfish.db`、`app/backend/local-storage/` 以及必要的本地配置。
