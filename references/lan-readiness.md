# 改造清单：让项目能在普通 Windows 电脑上稳定跑起来

七条要求。每条给出「为什么」「怎么改」「怎么验收」。改动越小越好——目标是让项目在**没有你的电脑**上跑起来，不是重写项目。

---

## 1. 监听地址改成 `0.0.0.0`（局域网可达）

**为什么**：绑在 `127.0.0.1` / `localhost` 上时，只有本机能连；别的电脑连过来会被拒绝。这是"本机能开、别人打不开"最常见的原因，占一半以上。

**怎么改**

```js
// Node 原生 http —— 原来
server.listen(8787);
server.listen(8787, '127.0.0.1');        // ❌ 只能本机
// 改后
server.listen(8787, '0.0.0.0');          // ✅ 所有网卡
```

```js
// Express
app.listen(8787, '0.0.0.0');
```

```python
# Flask
app.run(host='0.0.0.0', port=80)          # 默认就是 127.0.0.1，必须显式写
# FastAPI / uvicorn
uvicorn.run(app, host='0.0.0.0', port=80)
```

**验收**：在另一台电脑上 `curl http://<本机IP>:<port>/api/version` 能返回；若失败先查防火墙（第 6 条），不要先怀疑代码。

---

## 2. 端口从环境变量读，默认 80

**为什么**：80 是"别的电脑只输 IP"的前提；但 80 在 Windows 上经常被占用，必须留一条退路，否则现场会卡死。

**怎么改**

```js
const PORT = parseInt(process.env.PORT || '80', 10);
server.listen(PORT, '0.0.0.0');
```

```python
import os
PORT = int(os.environ.get('PORT', '80'))
```

启动脚本会 `set PORT=...` 后再拉起进程，所以代码里**不要再写死端口**。

**验收**：`PORT=8080 node server.js` 能起在 8080；不带 PORT 时起在 80。

---

## 3. 提供 `/api/version`

**为什么**：部署/升级后唯一能自动验证"新版本真生效了"的手段。没有它，升级只能靠人眼看页面，出问题也说不清。

**怎么改**

```js
const APP_VERSION = 'v1.2.0';           // 版本号唯一来源，别处不要再写
const APP_VERSION_DATE = '2026-11-02';

if (req.url === '/api/version') {
  res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  return res.end(JSON.stringify({ version: APP_VERSION, date: APP_VERSION_DATE }));
}
```

**验收**：`curl http://127.0.0.1/api/version` 返回版本号，且版本号与源码里一致。

---

## 4. 数据目录与代码目录分离

**为什么**：升级是"覆盖代码"，不是"覆盖一切"。业务数据（数据库、上传文件、配置）必须放在独立目录，升级时不碰它。

**怎么改**

```
<安装目录>/
├── server.js        ← 每次升级会覆盖
├── public/          ← 每次升级会覆盖
└── data/            ← 永不覆盖（数据库、上传、配置）
    └── app.db
```

代码里把数据路径统一指向 `data/`：

```js
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
```

**验收**：把 `server.js` 换掉、重启后数据还在。

---

## 5. 静态资源缓存策略

**为什么**：默认不缓存 → 每次翻页重下；默认长缓存 → 升级后用户看到旧页面，你会收到"改了没生效"的反馈。

**怎么改**：HTML 不缓存；带版本戳的资源长缓存。

```js
const isHtml = /\.html?$/i.test(filePath);
if (isHtml) {
  headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0';
} else if (url.searchParams.has('v')) {
  headers['Cache-Control'] = 'public, max-age=31536000, immutable';
} else {
  headers['Cache-Control'] = 'no-store';
}
```

页面里的引用带上版本：`<script src="/assets/app.js?v=1.2.0">`。版本一变，URL 就变，浏览器自然拉新。

**验收**：改一个静态文件、升版本号、刷新页面，看到的是新内容。

---

## 6. 零外部依赖（或把依赖塞进包）

**为什么**：目标机不能联网、也不该为了跑一个页面去装运行时和依赖。

**三条路线，按优先级**

1. **只用语言内置模块**：Node 原生 `http` / `fs` / `crypto`；Python 标准库 `http.server` / `sqlite3`。最稳，零安装。
2. **依赖已装好随包带走**：`node_modules` 一起打进 zip（注意：只带生产依赖 `npm ci --omit=dev`）。
3. **自带便携运行时**：把 `runtime/node.exe` 放进安装目录，目标机完全不需要装 Node。

**验收**：在一台没装过 Node/Python 的电脑上双击「启动.bat」能起来。

---

## 7. 无交互启动 + 可隐藏运行

**为什么**：现场是双击，不是开终端。任何需要输入、需要确认、需要保持窗口打开的行为都会变成"没人会弄"。

**怎么改**

- 启动参数全部走环境变量或配置文件，不要交互式提问
- 用 VBS 拉起以隐藏窗口：`wscript _run_hidden.vbs "node.exe" "server.js"`
- 输出重定向进 `server.log`，不要依赖控制台
- 出错要写日志文件，不能只在控制台打印

**验收**：关掉所有窗口，服务仍在跑；`server.log` 里有启动记录。

---

## 附：稳定性三件套（对应 `watchdog.js`）

| 能力 | 做法 | 为什么必须有 |
|---|---|---|
| 崩溃自动重启 | 守护进程监听子进程 exit，2 秒后重启 | 普通 Windows 没有 systemd，崩了就是彻底失联 |
| 日志落盘 + 轮转 | stdout/stderr 追加到 `server.log`，超 5MB 轮转 | 现场没人看控制台，日志是唯一线索 |
| 干净停机 | 写 `_stop.flag` → 守护停子进程并退出 | 否则"停止"后守护会立刻把服务拉回来，看起来像停不掉 |
| 反复崩溃止损 | 10 分钟内崩 20 次 → 放弃并写 `_崩溃过多.txt` | 避免无限重启刷日志、把磁盘写满 |
