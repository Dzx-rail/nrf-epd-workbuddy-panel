# 发布清单

仓库结构已就绪，缺少的只是账号与凭证。

## 1. 初始化并本地提交

```bash
cd dist/epaper-panel
git init -b main
git add .
git commit -m "feat: e-paper dashboard for EPD-nRF5 tags

- BLE transport: 1bpp dual-plane packing, MTU-aware chunking, calendar mode switch
- Layout: weekly calendar with lunar/weather icons/holiday marking + credits & task panel
- Docs: protocol notes, BLE troubleshooting, quantified readability findings"
```

## 2. 创建远端仓库

GitHub CLI 未安装，二选一：

**A. 装 gh 后一条命令**
```bash
winget install GitHub.cli
gh auth login
gh repo create epaper-panel --public --source=. --push
```

**B. 网页建仓后推送**
```bash
git remote add origin https://github.com/<你的账号>/epaper-panel.git
git push -u origin main
```

## 3. 发布前复查

- [ ] 确认 `config.json`、`state.json` 未被提交（`.gitignore` 已覆盖，含设备地址与余额）
- [ ] README 里的示意图与实际版面一致
- [ ] 若有他人的标签地址出现在文档中，一并移除
- [ ] 选择 License（当前为 MIT；若希望下游改动回馈，改用 GPL-3.0）

## 4. 发布为 WorkBuddy 技能

技能包已生成：`dist/epaper-dashboard.zip`（10 个文件，无运行期产物）。
把它放到 `~/.workbuddy/skills/` 下解压即可被 WorkBuddy 识别；
若要上架市场，需按平台要求补充图标与截图。
