# 80 端口与防火墙：默认端口的收益、代价与排查

## 为什么默认用 80

局域网里对其他设备来说，`http://192.168.1.20/` 比 `http://192.168.1.20:8787/` 好用得多——不用记端口、二维码/链接可以直接写、现场培训成本为零。
代价是 80 在 Windows 上**不是一个"空闲"端口**，它经常已经被系统组件占着。

---

## 一、80 被占用的三个典型来源

| 来源 | 特征 | 处理 |
|---|---|---|
| **http.sys 预留**（IIS / WinNAT / BranchCache / 某些管理软件） | `netstat` 里看到 `System` 进程（PID 4）在听 80 | 没法杀，只能让 http.sys 释放或换端口 |
| **IIS（W3SVC 服务）** | PID 指向 `System`，服务列表有 `W3SVC` | `net stop http`（同时停掉 W3SVC）或 `net stop w3svc` |
| **其他普通程序占了 80** | PID 指向具体的 exe 名 | 关掉该程序，或改它的端口 |

### 诊断命令（按顺序跑）

```bat
REM 1. 谁在听 80
netstat -ano | findstr :80
REM    最后一列是 PID，拿到 PID 后：
tasklist | findstr <PID>

REM 2. 是不是 http.sys 预留的
netsh http show servicestate
REM    输出里找 "Request queue" / "Registered URLs"，能看到哪个服务注册了 80

REM 3. 直接测能不能连（能连 = 已有人在服务）
powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',80); 'BUSY' } catch { 'FREE' }"
```

### 释放 80

```bat
REM 需要管理员权限
net stop http
REM 若提示依赖服务，按提示逐个停，或：
net stop w3svc
```

停完再跑一次 `netstat -ano | findstr :80` 确认没有 `LISTENING`。

> ⚠️ `net stop http` 会影响同机所有依赖 http.sys 的服务（IIS、WSUS、某些打印/扫描共享面板）。在别人的生产机上执行前先问清楚。

---

## 二、启动器的处理策略（已内置）

`启动.bat` 的流程：

1. 用 `TcpClient` 连一次 `127.0.0.1:80`，通 = 被占用
2. 被占用时：列出占用进程（PID + 进程名），打印两条出路（关占用程序 / 回车改用 8080）
3. 用户按回车 → `PORT=8080` 继续
4. 无论哪个端口，都写进 `_port.txt`
5. 用该端口拉起服务并等就绪，然后打开浏览器

**为什么必须写 `_port.txt`**：停止脚本和升级引擎都要知道"这台机器实际跑在哪个端口"。不记录的话，实际跑在 8080 而引擎按 80 去停服/校验，结果是"升级提示成功、版本号却没变"，甚至误杀别的进程。

---

## 三、防火墙：本机能开、别人打不开

这是第二大原因。Windows 默认阻止入站连接，`node.exe` 第一次监听时如果没点"允许"，规则就没了。

### 一键放行（推荐）

双击或右键**以管理员身份运行**安装目录里的 `防火墙放行.bat`，它会：

```bat
netsh advfirewall firewall add rule name="<应用名> (TCP 80)" dir=in action=allow protocol=TCP localport=80 profile=any
netsh advfirewall firewall add rule name="<应用名> (程序)" dir=in action=allow program="<安装目录>\runtime\node.exe" enable=yes profile=any
```

按端口和按程序**两条都加**：老系统上有时端口规则不生效，程序规则兜底。

### 手工命令

```bat
REM 查现有规则
netsh advfirewall firewall show rule name=all | findstr /i "<应用名>"

REM 删掉重加
netsh advfirewall firewall delete rule name="<应用名> (TCP 80)"
netsh advfirewall firewall add rule name="<应用名> (TCP 80)" dir=in action=allow protocol=TCP localport=80
```

### 判定是"防火墙"还是"绑定地址"

| 现象 | 结论 |
|---|---|
| 本机 `http://127.0.0.1/` 能开，别的电脑打不开，`ping` 得通 | 防火墙拦入站 |
| 本机 `http://127.0.0.1/` 能开，别的电脑 `ping` 都不通 | 网络层不通（不同网段/交换机/无线隔离） |
| 本机 `http://<本机IP>/` 打不开，`127.0.0.1` 能开 | 服务绑在 127.0.0.1 上（见改造清单第 1 条） |
| 全部打不开 | 服务没起，先看 `server.log` |

---

## 四、验证访问的三种手段

```bat
REM 本机
curl http://127.0.0.1/api/version

REM 本机按局域网 IP（能验出绑定地址问题）
ipconfig | findstr /i "IPv4"
curl http://<本机IP>/api/version

REM 另一台电脑（最终判据）
curl http://<本机IP>/api/version
```

三处都要过，才算"局域网可访问"。只测第一个是最常见的误判。

---

## 五、端口选择建议

| 端口 | 适合 | 备注 |
|---|---|---|
| **80** | 默认对外服务 | 局域网免写端口；可能被 http.sys 占 |
| 8080 / 8000 | 80 被占时的备选 | 局域网访问要带 `:8080` |
| 8787 等高位端口 | 开发调试 | 不会与系统组件冲突，适合本机开发 |

开发机上可以继续用高位端口，**部署包默认 80**——两者不冲突，因为端口是启动脚本传进去的。
