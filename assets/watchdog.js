#!/usr/bin/env node
/**
 * watchdog.js —— 零依赖进程守护（崩溃自动重启 + 日志落盘 + 干净停机）
 *
 * 为什么需要：普通 Windows 电脑上没有 systemd / 服务管理器，node 进程一旦因为
 * 未捕获异常、内存不足、端口冲突退出，站点就彻底失联，现场只能等人来双击。
 * 这个小守护进程把「挂了自动拉起来」这件事补上。
 *
 * 用法（由 启动.bat 调用）：
 *   node watchdog.js <入口文件>             # 默认日志 server.log
 *   node watchdog.js <入口文件> --log x.log
 *
 * 约定：
 *   · 安装目录出现 _stop.flag 时，守护会停掉子进程并自行退出（停止.bat 用它做干净停机）
 *   · 10 分钟内连续崩溃超过 MAX_RESTARTS 次，写 _崩溃过多.txt 后放弃，避免无限重启刷屏
 *   · 日志超过 5 MB 自动轮转为 server.log.1（只保留一份旧的）
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const DIR = __dirname;
const ENTRY = process.argv[2] || 'server.js';
const logArg = process.argv.indexOf('--log');
const LOG = path.join(DIR, logArg > 0 ? (process.argv[logArg + 1] || 'server.log') : 'server.log');
const STOP_FLAG = path.join(DIR, '_stop.flag');
const GIVEUP = path.join(DIR, '_崩溃过多.txt');

const MAX_RESTARTS = 20;          // 10 分钟窗口内最多重启次数
const WINDOW_MS = 10 * 60 * 1000;
const BACKOFF_MS = 2000;
const LOG_MAX = 5 * 1024 * 1024;

let child = null;
let stopping = false;
let restarts = [];

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try {
    if (fs.existsSync(LOG) && fs.statSync(LOG).size > LOG_MAX) {
      fs.renameSync(LOG, LOG + '.1');
    }
  } catch (e) { /* 轮转失败不影响主流程 */ }
  try { fs.appendFileSync(LOG, line); } catch (e) { /* 磁盘满/权限问题时静默 */ }
  process.stdout.write(line);
}

function readStopFlag() {
  try { return fs.existsSync(STOP_FLAG); } catch (e) { return false; }
}

function startChild() {
  const args = [path.join(DIR, ENTRY)];
  // 端口等配置由环境变量继承（启动.bat 里已 set）
  child = spawn(process.execPath, args, {
    cwd: DIR,
    env: process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  child.stdout.on('data', (d) => fs.appendFileSync(LOG, d));
  child.stderr.on('data', (d) => fs.appendFileSync(LOG, d));

  child.on('exit', (code, signal) => {
    child = null;
    if (stopping || readStopFlag()) {
      log(`子进程已退出（code=${code} signal=${signal}），检测到停止信号，守护退出`);
      process.exit(0);
    }
    log(`[警告] 服务进程退出（code=${code} signal=${signal}），准备重启`);

    const now = Date.now();
    restarts = restarts.filter((t) => now - t < WINDOW_MS);
    restarts.push(now);
    if (restarts.length > MAX_RESTARTS) {
      const msg = `10 分钟内已重启 ${restarts.length} 次，判定为反复崩溃，停止守护。\n` +
        `请查看 ${path.basename(LOG)} 里的报错，修好后双击「启动.bat」重来。\n` +
        `时间：${new Date().toLocaleString()}\n`;
      try { fs.writeFileSync(GIVEUP, msg, 'utf8'); } catch (e) { }
      log('[失败] ' + msg.replace(/\n/g, ' '));
      process.exit(1);
    }
    setTimeout(startChild, BACKOFF_MS);
  });
}

// 停止信号：停止.bat 写 _stop.flag 后本进程会主动收尾
setInterval(() => {
  if (readStopFlag() && !stopping) {
    stopping = true;
    log('收到 _stop.flag，正在停止服务 ...');
    if (child) {
      try { child.kill(); } catch (e) { }
      setTimeout(() => { try { child && child.kill('SIGKILL'); } catch (e) { } process.exit(0); }, 3000);
    } else {
      process.exit(0);
    }
  }
}, 500);

['SIGINT', 'SIGTERM', 'SIGBREAK'].forEach((sig) => {
  process.on(sig, () => {
    stopping = true;
    log(`收到 ${sig}，正在停止服务 ...`);
    if (child) { try { child.kill(); } catch (e) { } }
    setTimeout(() => process.exit(0), 1500);
  });
});

fs.writeFileSync(path.join(DIR, '_watchdog.pid'), String(process.pid), 'utf8');
log(`守护启动：PID=${process.pid} 入口=${ENTRY} 日志=${path.basename(LOG)}`);
startChild();
