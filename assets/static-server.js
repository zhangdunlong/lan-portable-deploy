#!/usr/bin/env node
/**
 * static-server.js —— 纯静态项目的最小可用服务（零第三方依赖）
 *
 * 用途：项目本身只有 HTML/CSS/JS 时，用它把目录变成「局域网可访问的站点」，
 *      并补齐便携部署必需的两个能力：
 *        ① /api/version  —— 升级后校验版本用（安装引擎依赖它判定升级是否成功）
 *        ② 静态资源缓存策略 —— HTML 不缓存；带 ?v= 的资源长缓存
 *
 * 用法：
 *   node static-server.js [--dir .] [--port 8787] [--host 0.0.0.0]
 *
 * 约定：
 *   · 版本号优先读同目录 version.txt 的第 1 行；没有则用下面的 FALLBACK_VERSION
 *   · 默认绑定 0.0.0.0（局域网可访问）；只想本机访问用 --host 127.0.0.1
 */
const http = require('http');
const fs = require('fs');
const path = require('path');

const FALLBACK_VERSION = '1.0.0';

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

// 站点根目录：显式 --dir 优先；否则若同级有 public/ 就用 public/（与安装引擎的 {{STATIC_DIR}} 约定一致）
const DEFAULT_DIR = fs.existsSync(path.join(__dirname, 'public')) ? path.join(__dirname, 'public') : __dirname;
const ROOT = path.resolve(arg('dir', DEFAULT_DIR));
const PORT = parseInt(arg('port', process.env.PORT || '8787'), 10);
const HOST = arg('host', '0.0.0.0');

function readVersion() {
  try {
    const v = fs.readFileSync(path.join(ROOT, 'version.txt'), 'utf8').split(/\r?\n/)[0].trim();
    if (v) return v;
  } catch (e) { /* 没有 version.txt 就走兜底 */ }
  return FALLBACK_VERSION;
}

const MIME = {
  '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.webp': 'image/webp', '.ico': 'image/x-icon',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
  '.pdf': 'application/pdf', '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
};

function send(res, code, body, headers) {
  res.writeHead(code, Object.assign({ 'Content-Type': 'text/plain; charset=utf-8' }, headers || {}));
  res.end(body);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const pathname = decodeURIComponent(url.pathname);

  // ① 版本接口：安装引擎升级后靠它判断是否生效
  if (pathname === '/api/version') {
    return send(res, 200, JSON.stringify({ version: readVersion() }),
      { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  }

  // ② 静态文件
  let rel = pathname === '/' ? '/index.html' : pathname;
  const filePath = path.join(ROOT, path.normalize(rel).replace(/^([/\\])+/, ''));
  if (!filePath.startsWith(ROOT)) return send(res, 403, 'Forbidden');   // 防目录穿越

  fs.readFile(filePath, (err, data) => {
    if (err) {
      // 找不到时回落到 index.html（单页应用刷新子路由不至于 404）
      if (rel !== '/index.html') {
        return fs.readFile(path.join(ROOT, 'index.html'), (e2, d2) => {
          if (e2) return send(res, 404, 'Not Found');
          send(res, 200, d2, { 'Content-Type': MIME['.html'], 'Cache-Control': 'no-store' });
        });
      }
      return send(res, 404, 'Not Found');
    }
    const ext = path.extname(filePath).toLowerCase();
    const isHtml = ext === '.html' || ext === '.htm';
    const headers = { 'Content-Type': MIME[ext] || 'application/octet-stream' };
    // HTML 永远不缓存；带版本戳的资源可长缓存（升级后 URL 变了自然失效）
    if (isHtml) {
      headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0';
    } else if (url.searchParams.has('v')) {
      headers['Cache-Control'] = 'public, max-age=31536000, immutable';
    } else {
      headers['Cache-Control'] = 'no-store';
    }
    res.writeHead(200, headers);
    res.end(data);
  });
});

server.on('error', (e) => {
  if (e.code === 'EADDRINUSE') {
    console.error(`[失败] 端口 ${PORT} 已被占用。先双击「停止.bat」，或换端口：--port 8788`);
  } else {
    console.error('[失败]', e.message);
  }
  process.exit(1);
});

server.listen(PORT, HOST, () => {
  const nets = require('os').networkInterfaces();
  console.log(`静态站点已启动：${ROOT}`);
  console.log(`  本机访问：http://127.0.0.1:${PORT}/`);
  Object.values(nets).flat().forEach((n) => {
    if (n && n.family === 'IPv4' && !n.internal) console.log(`  局域网访问：http://${n.address}:${PORT}/`);
  });
});
