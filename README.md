# lan-portable-deploy

把任意 Web / 服务器项目，改造成**普通 Windows 电脑上免安装、双击即跑、局域网可访问（默认 80 端口）**的部署形态。

一个 WorkBuddy / Claude 风格的 **Skill**，同时也可**独立当作命令行工具**使用（纯 Python 标准库，无第三方依赖）。

---

## 它解决什么问题

| 现场真实症状 | 本项目的处理 |
|---|---|
| 目标机没装 Node/Python，也上不了网 | **默认自带便携运行时**，U 盘拷过去双击即用 |
| 本机能开，别的电脑打不开 | 绑 `0.0.0.0` + 一键放行防火墙 + 默认 80 端口（免写端口号） |
| 访问地址要带 `:8787`，现场记不住 | 默认 80，别的电脑只输 `http://本机IP/` |
| 服务崩一次就彻底失联，等人来双击 | `watchdog.js` 守护：崩溃 2 秒后自动重启 + 日志轮转 + 反复崩溃止损 |
| 升级覆盖了文件，业务数据跟着没了 | 数据目录永不打进包、升级过程全程不碰 |
| 升级失败，机器停在"服务不可用" | 停服 → 备份 → 原子替换 → 校验版本 → **失败自动回滚** |
| 中文乱码、双击弹一堆"不是内部或外部命令" | 编码/换行/BOM 全部按 Windows 规则强制处理，构建时自检拦截 |

---

## 快速开始

```bash
# 1. 体检：判断项目能不能直接上局域网
python scripts/mkportable.py inspect /path/to/your-project

# 2. 构建（默认 80 端口，默认自带运行时 —— 目标机什么都不用装）
python scripts/mkportable.py build /path/to/your-project --out ./dist --name "我的系统"
#    找不到运行时时用 --runtime-exe <node.exe 路径> 指定；
#    目标机已有运行时、想减小体积才加 --no-runtime

# 3. 自检（build 已内置，也可单独跑）
python scripts/mkportable.py verify ./dist
```

产出：

```
dist/
├── 我的系统/                     ← 安装目录，双击 启动.bat 即运行
│   ├── server.js  public/  data/
│   ├── 启动.bat  停止.bat  安装.bat
│   ├── 防火墙放行.bat   开机自启.bat
│   ├── watchdog.js  runtime/  setup.ps1
│   └── version.txt  安装说明.txt
└── 部署包/
    ├── 我的系统_升级包_v1.0.0_20261102.zip
    └── 我的系统_一键升级_v1.0.0_20261102.bat   ← 单文件自解压，拷过去双击即装
```

---

## 作为 Skill 使用

把本仓库整个目录放到技能目录下：

```
~/.workbuddy/skills/lan-portable-deploy/     # Windows: C:\Users\<你>\.workbuddy\skills\
```

然后直接说"把这个项目部署到公司电脑上、要局域网能访问"，技能会被触发，按它的五步流程执行：
**体检 → 改造 → 构建 → 本机验证 → 上机部署**。

`SKILL.md` 是主入口，`references/` 下是五份专题文档：

| 文档 | 内容 |
|---|---|
| `lan-readiness.md` | 七条改造清单，含 Node 原生 / Express / Flask 的具体改法 |
| `port80-and-firewall.md` | 80 端口被 http.sys 占用的诊断、释放、防火墙放行与访问验证 |
| `windows-encoding.md` | GBK / BOM / CRLF 三坑的实测结论 |
| `troubleshooting.md` | 现场故障速查（症状 → 根因 → 处理） |
| `layout.md` | 产出结构逐文件职责，便于交接 |

---

## 默认 80 端口：收益与代价

**收益**：局域网其他设备只输 IP，不用记端口号，现场培训成本为零。

**代价**：Windows 上 80 常被 IIS / `http.sys` / 管理软件占着。所以启动器内置了检测：

1. 连一次 `127.0.0.1`，通 = 被占用
2. 被占用时列出占用进程（PID + 进程名），给出两条出路
3. 可按回车临时改用 8080
4. 实际端口写入 `_port.txt`，停止脚本与升级引擎都读它

第 4 条是关键：不记录实际端口，升级时会按 80 去停服和校验，结果是"提示升级成功、版本号却没变"。

彻底拿回 80（管理员）：`net stop http`

---

## 红线（写在代码里，构建时自检）

1. **数据目录永不进包** —— 升级覆盖代码时不能碰业务数据
2. **`.bat` 必须 GBK + CRLF** —— cmd 按 ANSI 解码，UTF-8 会乱码
3. **`.ps1` 必须 UTF-8 带 BOM 且只有一个** —— PowerShell 5.1 靠 BOM 认 UTF-8
4. **单文件自解压 `.bat` 的 payload 前必须有 `exit /b 0`** —— 否则 cmd 把 base64 当命令刷屏
5. **版本号只有一个来源** —— 入口文件的 `APP_VERSION`，打包脚本只读不写
6. **不用 `taskkill /f /im node.exe` 做日常停止** —— 会误杀同机其他 Node 程序，按端口定位

这六条都是真实踩过的坑，不是理论。`references/windows-encoding.md` 里有实测过程。

---

## 支持的项目类型

| 类型 | 支持程度 | 说明 |
|---|---|---|
| Node.js（原生 http / Express / Koa） | 完整 | 含守护进程、80 端口、便携运行时 |
| 纯静态站点 | 完整 | 自动附带零依赖静态服务器（含 `/api/version`、缓存策略、目录穿越防护） |
| Python（Flask / FastAPI / 标准库） | 基础 | 外壳与安装引擎可用；守护进程 `watchdog.js` 不适用于 Python 进程 |

---

## 已验证

在本机 Windows 上用真实环境做过端到端验证，不是"写完就发"：

- Node 项目与纯静态项目各构建一次，自检全通过（结构 / 编码 / BOM / payload 还原一致 / 无数据泄漏 / 无占位符残留）
- 安装目录里的服务实跑：`/api/version` 与首页均正常返回
- 守护进程：强杀服务进程后 **2.2 秒观测到下线、2.4 秒自动恢复**，PID 已更换，日志留痕
- 干净停机：写 `_stop.flag` 后守护退出、服务不再自启
- 安装引擎：PowerShell 5.1 语法解析通过，`-DryRun` 走完全部步骤
- 顺带修掉一个真实缺陷：环境里同时存在 `http_proxy` 与 `HTTP_PROXY` 时，`Start-Process` 会因大小写重名报错导致引擎完全无法自派

---

## 现成提示词

不想自己组织需求，直接复制对应文件里的整段提示词：

| 场景 | 文件 | 说明 |
|---|---|---|
| **任意智能体**（不装技能也能用） | [prompts/通用智能体提示词.md](prompts/通用智能体提示词.md) | **短版为主**：一句话让它来读本仓库的 `SKILL.md` 与 `references/`；附录保留离线内联长版 |
| **已装本技能**（WorkBuddy 等） | [prompts/WorkBuddy技能调用.md](prompts/WorkBuddy技能调用.md) | 短版，含标准/一句话/分场景变体 |

最短的一条，直接可用：

```
按 https://github.com/zhangdunlong/lan-portable-deploy 的规范，把 D:\你的项目 改造成
Windows 免安装 + 局域网 80 端口部署；先体检，改完自检给证据。
```

为什么只需要一句：真正容易出错的不是"做什么"，而是 `.bat` 的 GBK 编码、`.ps1` 的 BOM、
`data/` 不能进包、80 端口被占时怎么回退这些细节 —— 它们都在 `references/` 里写死了，
让智能体去读比塞进提示词更可靠，也不会污染上下文。

## 许可

MIT。见 [LICENSE](LICENSE)。
