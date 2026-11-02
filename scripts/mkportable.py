#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mkportable.py —— 把任意 Web / 服务器项目打包成「Windows 便携 + 局域网可访问」部署包
=================================================================================

产出（--out 指定的目录下）：

    <out>/
    ├── <slug>/                          ← 可直接双击 启动.bat 运行的安装目录
    │   ├── <entry>                      ← 应用入口（server.js / app.py）
    │   ├── <static>/                    ← 静态资源
    │   ├── 启动.bat / 停止.bat / _run_hidden.vbs
    │   ├── version.txt
    │   ├── runtime/                     ← 便携运行时（默认必带）
    │   ├── data/                        ← 空数据目录（永不进包）
    │   └── tools/deploy/_setup.ps1      ← 升级引擎（供以后接收远程升级）
    └── 部署包/
        ├── <name>_升级包_v<ver>_<日期>.zip
        └── <name>_一键升级_v<ver>_<日期>.bat   ← 单文件自解压，双击即装

三个子命令：
    inspect <项目>                体检：识别入口/静态目录/数据目录/是否已具备局域网部署条件
    build   <项目> --out <目录>   生成安装目录 + zip + 单文件自解压 + 自检
                                  默认自带运行时（U 盘拷到别的电脑双击就能跑）；
                                  目标机已有运行时可用 --no-runtime 减小体积
    verify  <out 目录>            只做自检（不重新构建）

设计红线（照抄实战项目的教训，别改）：
    · 数据目录永不进包 —— 升级覆盖代码时不能碰业务数据
    · .bat 必须 GBK + CRLF（cmd 按 ANSI 解码，UTF-8 会乱码）
    · .ps1 必须 UTF-8 with BOM 且只有一个 BOM（PowerShell 5.1 靠 BOM 认 UTF-8）
    · 单文件自解压 .bat：base64 段前面必须有 exit /b 0，否则 cmd 会把数据当命令刷屏
    · 版本号只有一个来源（入口文件的 APP_VERSION），打包脚本只读不写
"""
import argparse
import base64
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL_ROOT, 'assets')

PAYLOAD_MARK = '__PAYLOAD__'
SELF_TMP_NAME = 'portable_upgrade_self.bat'      # ASCII 名，避免中文路径参与解析

# 打包范围里要排除的杂项（备份/临时/缓存）
JUNK_RE = re.compile(
    r'(^__pycache__$|^\.git$|^node_modules$|^\.venv$|^venv$|'
    r'\.bak($|[._-])|\.tmp$|\.orig$|\.rej$|~$|^\.#|\.swp$|\.log$|'
    r'^_部署包_|^_build_|^_升级备份_)', re.IGNORECASE)

# ---------------------------------------------------------------- 编码读写

def read_text(path, enc='utf-8'):
    with open(path, 'r', encoding=enc, newline='') as f:
        return f.read()


def write_text(path, text, enc='utf-8', bom=False, newline='\r\n'):
    """统一剥掉正文里已有的 BOM，保证「有且只有一个」。"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    text = text.lstrip('\ufeff')
    data = text.replace('\r\n', '\n').replace('\n', newline)
    try:
        raw = data.encode(enc)
    except UnicodeEncodeError as e:
        raise SystemExit('[错误] %s 无法用 %s 编码：%s\n  .bat 只能是 GBK 能表示的字符，'
                         '请把这类字符（emoji / 生僻字）换成 ASCII 或常用汉字。'
                         % (os.path.basename(path), enc.upper(), e))
    if bom and enc.lower().startswith('utf-8'):
        raw = b'\xef\xbb\xbf' + raw
    with open(path, 'wb') as f:
        f.write(raw)


def render(template_name, mapping):
    """渲染模板；渲染完必须没有残留占位符，否则直接失败。"""
    src = read_text(os.path.join(ASSETS, template_name), 'utf-8')
    for k, v in mapping.items():
        src = src.replace('{{%s}}' % k, str(v))
    left = sorted(set(re.findall(r'\{\{[A-Z_]+\}\}', src)))
    if left:
        raise SystemExit('[错误] 模板 %s 渲染后仍有未填充的占位符：%s' % (template_name, ', '.join(left)))
    return src


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return '%.1f %s' % (n, u)
        n /= 1024.0
    return '%.1f TB' % n


# ---------------------------------------------------------------- 项目体检

def guess_type(project):
    has = lambda *p: os.path.exists(os.path.join(project, *p))
    if has('package.json') or any(has(f) for f in ('server.js', 'app.js', 'index.js', 'main.js')):
        return 'node'
    if any(has(f) for f in ('app.py', 'main.py', 'manage.py', 'requirements.txt')):
        return 'python'
    if has('index.html'):
        return 'static'
    return 'unknown'


def pick_entry(project, ptype):
    cands = {
        'node': ['server.js', 'app.js', 'index.js', 'main.js'],
        'python': ['app.py', 'main.py', 'manage.py', 'run.py'],
    }.get(ptype, [])
    for c in cands:
        if os.path.exists(os.path.join(project, c)):
            return c
    return ''


def pick_static(project):
    for d in ('public', 'static', 'dist', 'www', 'web', 'site'):
        if os.path.isdir(os.path.join(project, d)):
            if any(f.endswith('.html') for f in os.listdir(os.path.join(project, d))):
                return d
    for d in ('public', 'static', 'dist', 'www'):
        if os.path.isdir(os.path.join(project, d)):
            return d
    return ''


def pick_data(project):
    for d in ('data', 'db', 'storage', 'var'):
        p = os.path.join(project, d)
        if os.path.isdir(p):
            # 数据标记：目录里最大的那个文件当「有数据」的证据
            files = [(os.path.getsize(os.path.join(p, f)), f) for f in os.listdir(p)
                     if os.path.isfile(os.path.join(p, f))]
            mark = max(files)[1] if files else ''
            return d, (os.path.join(d, mark) if mark else '')
    return 'data', ''


def pick_runtime(project, ptype):
    if ptype == 'python':
        for r in ('runtime\\python.exe', 'runtime/python.exe'):
            if os.path.exists(os.path.join(project, r.replace('\\', os.sep))):
                return 'runtime\\python.exe', 'python'
        return 'runtime\\python.exe', 'python'
    for r in ('runtime\\node.exe', 'runtime/node.exe'):
        if os.path.exists(os.path.join(project, r.replace('\\', os.sep))):
            return 'runtime\\node.exe', 'node'
    return 'runtime\\node.exe', 'node'


def detect_version(project, entry):
    """版本号单一来源：入口文件 APP_VERSION > package.json > version.txt > 兜底。"""
    ep = os.path.join(project, entry) if entry else ''
    if ep and os.path.isfile(ep):
        try:
            src = read_text(ep, 'utf-8')
            m = re.search(r"""APP_VERSION\s*=\s*['"]([^'"]+)['"]""", src)
            if m:
                return m.group(1), os.path.basename(ep) + ' 的 APP_VERSION'
        except Exception:
            pass
    pj = os.path.join(project, 'package.json')
    if os.path.isfile(pj):
        try:
            v = json.loads(read_text(pj, 'utf-8')).get('version')
            if v:
                return v, 'package.json 的 version'
        except Exception:
            pass
    vt = os.path.join(project, 'version.txt')
    if os.path.isfile(vt):
        try:
            v = read_text(vt, 'utf-8').split('\n')[0].strip()
            if v:
                return v, 'version.txt 第一行'
        except Exception:
            pass
    return '1.0.0', '兜底默认值（未找到版本来源，建议在入口文件里加 APP_VERSION）'


def grep_any(project, files, pattern):
    """在若干文件里找正则，返回 (命中文件, 匹配)。"""
    for f in files:
        p = os.path.join(project, f)
        if not os.path.isfile(p):
            continue
        try:
            src = read_text(p, 'utf-8', ) if f.endswith(('.js', '.py', '.json', '.html', '.mjs', '.cjs')) else ''
        except Exception:
            continue
        m = re.search(pattern, src)
        if m:
            return f, m
    return None, None


def inspect_project(project, data_dir=None, static_dir=None):
    """体检结果：给 build 用，也给 agent 判断「这个项目能不能直接上局域网」。"""
    project = os.path.abspath(project)
    ptype = guess_type(project)
    entry = pick_entry(project, ptype)
    static = static_dir or pick_static(project)
    # 纯静态站点常见形态：index.html 直接在项目根 → 约定 static='.'，build 时会收进 public/
    if not static and ptype == 'static' and os.path.isfile(os.path.join(project, 'index.html')):
        static = '.'
    data, data_mark = pick_data(project)
    if data_dir:
        data = data_dir
        data_mark = os.path.join(data_dir, os.path.basename(data_mark)) if data_mark else ''
    runtime_rel, runtime_name = pick_runtime(project, ptype)
    version, version_src = detect_version(project, entry)

    # 局域网就绪度检查
    checks = []
    files = [f for f in (entry, 'server.js', 'app.js', 'index.js') if f]
    hf, hm = grep_any(project, files, r"""listen\s*\([^)]*['"](\d+\.\d+\.\d+\.\d+)['"]""")
    if hm:
        host = hm.group(1)
        checks.append(('绑定地址', host,
                       'ok' if host in ('0.0.0.0', '::') else
                       'bad：绑在 %s 上局域网访问不到，改成 0.0.0.0 或删掉 host 参数' % host))
    else:
        # 再查一层：HOST 常量 / uvicorn host= / app.run(host=) 里写成回环地址
        _, hloop = grep_any(project, files,
                            r"""(?:host|HOST|Host)\s*[:=]\s*['"](?:127\.0\.0\.1|localhost)['"]""")
        if hloop:
            checks.append(('绑定地址', '回环地址（127.0.0.1 / localhost）',
                           'bad：绑在回环地址上，局域网其他电脑访问不到，改成 0.0.0.0'))
        else:
            checks.append(('绑定地址', '未显式指定（多数框架默认监听所有网卡）',
                           'warn：本机能开、别人打不开时，先确认这里和防火墙'))

    _, pm = grep_any(project, files, r"""['"]/api/version['"]""")
    checks.append(('/api/version', '有' if pm else '没有',
                   'ok' if pm else 'bad：升级引擎靠它校验版本，必须补'))

    checks.append(('入口文件', entry or '未识别', 'ok' if entry else 'bad：无法确定启动入口'))
    checks.append(('静态目录', static or '未识别',
                   'ok' if static else 'warn：纯 API 项目可忽略，否则要指定 --static'))
    checks.append(('数据目录', data + ('（' + data_mark + '）' if data_mark else '（空/新建）'),
                   'ok' if os.path.isdir(os.path.join(project, data)) else 'warn：首启会自动新建'))
    # 打包范围内的杂项
    junk = []
    for item in [entry, static]:
        if not item:
            continue
        p = os.path.join(project, item)
        if os.path.isfile(p):
            if JUNK_RE.search(os.path.basename(item)):
                junk.append(item)
        elif os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                for n in list(dirnames) + list(filenames):
                    if JUNK_RE.search(n):
                        junk.append(os.path.relpath(os.path.join(dirpath, n), project).replace(os.sep, '/'))
    if junk:
        checks.append(('杂项文件', '%d 个（会自动排除）' % len(junk), 'warn：' + '、'.join(junk[:5])))

    return {
        'project': project,
        'type': ptype,
        'entry': entry,
        'static_dir': static,
        'data_dir': data,
        'data_mark': data_mark,
        'runtime_rel': runtime_rel,
        'runtime_name': runtime_name,
        'version': version,
        'version_source': version_src,
        'checks': checks,
        'junk': junk,
    }


# ---------------------------------------------------------------- 构建

def slugify(name):
    s = re.sub(r'[^\w\u4e00-\u9fa5-]+', '-', name).strip('-')
    return s or 'app'


def reg_slug(name):
    """注册表键名只留 ASCII 字母数字。"""
    s = re.sub(r'[^A-Za-z0-9]+', '', name)
    return 'Portable' + (s or 'App')


def copy_tree_filtered(src, dst):
    def ignore(d, names):
        return [n for n in names if JUNK_RE.search(n)]
    shutil.copytree(src, dst, ignore=ignore)


def build(args):
    info = inspect_project(args.project, args.data, args.static)
    project = info['project']
    type_ = args.type or info['type']
    entry = args.entry or info['entry']
    static = args.static or info['static_dir']
    data = info['data_dir']
    runtime_rel = args.runtime or info['runtime_rel']
    runtime_name = os.path.basename(runtime_rel).replace('.exe', '')
    version = args.version or info['version']
    version_date = datetime.now().strftime('%Y-%m-%d')
    stamp = datetime.now().strftime('%Y%m%d')
    app_name = args.name or os.path.basename(project.rstrip('\\/'))

    # 纯静态项目：自动补一个零依赖静态服务器当入口
    use_static_server = False
    static_src_root = False          # True = 站点就在项目根，收进 public/
    if type_ == 'static' and not entry:
        entry = 'server.js'
        use_static_server = True
    if static == '.':
        static_src_root = True
        static = 'public'            # 统一收进 public/，安装引擎与静态服务器都按这个名字走
    if not entry:
        raise SystemExit('[错误] 识别不到启动入口，请用 --entry 指定（Node 一般是 server.js，Python 是 app.py）')
    if not os.path.exists(os.path.join(project, entry)) and not use_static_server:
        raise SystemExit('[错误] 入口文件不存在：%s' % os.path.join(project, entry))

    data_mark = info['data_mark'] or os.path.join(data, 'app.db')
    mapping = {
        'APP_NAME': app_name,
        'DEFAULT_PORT': args.port,
        'ENTRY_FILE': entry,
        'STATIC_DIR': static or 'public',
        'DATA_DIR': data,
        'DATA_MARK': data_mark.replace('/', '\\'),
        'RUNTIME_REL': runtime_rel.replace('/', '\\'),
        'RUNTIME_DIR': os.path.dirname(runtime_rel).replace('/', '\\') or 'runtime',
        'RUNTIME_EXE_NAME': runtime_name,
        'REG_SLUG': reg_slug(app_name),
        # 与 zip / 一键升级 .bat 的文件名保持一致（去掉前导 v，文件名里固定带 v）
        'VERSION_TAG': re.sub(r'^[vV]', '', version),
    }

    out = os.path.abspath(args.out)
    staging = os.path.join(out, '_build_%s' % stamp)
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)

    print('=' * 62)
    print('  %s · 便携部署打包' % app_name)
    print('=' * 62)
    print('  项目    ：%s' % project)
    print('  类型    ：%s' % type_)
    print('  入口    ：%s' % entry)
    print('  静态目录：%s' % (static or '（无）'))
    print('  数据目录：%s（不进包）' % data)
    print('  端口    ：%s' % args.port)
    print('  版本    ：%s（来源：%s）' % (version, args.version and '--version' or info['version_source']))
    print('-' * 62)

    copied = []

    # ① 应用本体
    if use_static_server:
        shutil.copy2(os.path.join(ASSETS, 'static-server.js'), os.path.join(staging, 'server.js'))
        copied.append('server.js（由技能提供的零依赖静态服务器）')
    else:
        shutil.copy2(os.path.join(project, entry), os.path.join(staging, entry))
        copied.append(entry)

    if static_src_root:
        # 纯静态站：把项目根下的文件收进 staging/public（跳过数据目录、杂项与外壳文件）
        pub = os.path.join(staging, 'public')
        os.makedirs(pub, exist_ok=True)
        skip = {data, 'version.txt', '安装说明.txt', '_run_hidden.vbs'}
        for name in sorted(os.listdir(project)):
            if JUNK_RE.search(name) or name in skip:
                continue
            src = os.path.join(project, name)
            dst = os.path.join(pub, name)
            if os.path.isdir(src):
                copy_tree_filtered(src, dst)
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
        n = sum(len(f) for _, _, f in os.walk(pub))
        copied.append('public/（项目根收进 %d 文件）' % n)
    elif static and os.path.isdir(os.path.join(project, static)):
        copy_tree_filtered(os.path.join(project, static), os.path.join(staging, static))
        n = sum(len(f) for _, _, f in os.walk(os.path.join(staging, static)))
        copied.append('%s/（%d 文件）' % (static, n))

    # ② 可选：升级后钩子（幂等迁移脚本）
    hook = os.path.join(project, 'tools', 'deploy', 'post-upgrade.js')
    if os.path.isfile(hook):
        dst = os.path.join(staging, 'tools', 'deploy', 'post-upgrade.js')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(hook, dst)
        copied.append('tools/deploy/post-upgrade.js（升级后钩子）')

    # ③ 便携运行时（默认必带 —— 目标是「U 盘拷到别的电脑双击就能跑」，目标机可能什么都没有）
    bundled = False
    if args.no_runtime:
        print('  [注意] --no-runtime：不带运行时，目标机必须自备 %s' % runtime_name)
    else:
        exe_src = ''
        if args.runtime_exe and os.path.isfile(args.runtime_exe):
            exe_src = args.runtime_exe
        elif os.path.isfile(os.path.join(project, runtime_rel.replace('\\', os.sep))):
            exe_src = os.path.join(project, runtime_rel.replace('\\', os.sep))
        else:
            # 兜底查找顺序：PATH → 环境变量 → 常见安装位置
            cands = [shutil.which(runtime_name)]
            if runtime_name == 'node':
                if os.environ.get('NODE_EXE'):
                    cands.append(os.environ['NODE_EXE'])
                for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                             os.environ.get('LOCALAPPDATA')):
                    if base:
                        cands.append(os.path.join(base, 'nodejs', 'node.exe'))
                        cands.append(os.path.join(base, 'Programs', 'nodejs', 'node.exe'))
            for c in cands:
                if c and os.path.isfile(c):
                    exe_src = c
                    break
        if exe_src:
            os.makedirs(os.path.join(staging, os.path.dirname(runtime_rel).replace('/', os.sep) or 'runtime'),
                        exist_ok=True)
            dst_exe = os.path.join(staging, runtime_rel.replace('\\', os.sep))
            shutil.copy2(exe_src, dst_exe)
            bundled = True
            copied.append('%s（%.1f MB，目标机免安装 %s）'
                          % (runtime_rel, os.path.getsize(dst_exe) / 1048576.0, runtime_name))
        else:
            raise SystemExit('[错误] 找不到 %s 可执行文件，无法做成「拷过去就能跑」的包。\n'
                             '  二选一：① 用 --runtime-exe <路径> 指定一个 %s.exe\n'
                             '            ② 明确接受目标机自备运行时时加 --no-runtime' % (runtime_name, runtime_name))

    # ④ 运行时外壳（编码是硬约束）
    write_text(os.path.join(staging, '启动.bat'), render('start.bat.tmpl', mapping), 'gbk')
    write_text(os.path.join(staging, '停止.bat'), render('stop.bat.tmpl', mapping), 'gbk')
    write_text(os.path.join(staging, '安装.bat'), render('install.bat.tmpl', mapping), 'gbk')
    write_text(os.path.join(staging, '防火墙放行.bat'), render('firewall.bat.tmpl', mapping), 'gbk')
    write_text(os.path.join(staging, '开机自启.bat'), render('autostart.bat.tmpl', mapping), 'gbk')
    shutil.copy2(os.path.join(ASSETS, 'run_hidden.vbs'), os.path.join(staging, '_run_hidden.vbs'))
    copied.append('启动.bat / 停止.bat / 安装.bat / 防火墙放行.bat / 开机自启.bat / _run_hidden.vbs（GBK+CRLF）')

    # 进程守护：只有 Node 系项目能用（它自己也是 node 脚本）
    if runtime_name == 'node':
        shutil.copy2(os.path.join(ASSETS, 'watchdog.js'), os.path.join(staging, 'watchdog.js'))
        copied.append('watchdog.js（崩溃自动重启 + 日志落盘）')

    # ⑤ 安装引擎（包根一份 + tools/deploy 一份给目标机以后收远程升级）
    ps1 = render('setup.ps1.tmpl', mapping)
    write_text(os.path.join(staging, 'setup.ps1'), ps1, 'utf-8', bom=True)
    write_text(os.path.join(staging, 'tools', 'deploy', '_setup.ps1'), ps1, 'utf-8', bom=True)
    copied.append('setup.ps1 + tools/deploy/_setup.ps1（安装/升级引擎，UTF-8 BOM）')

    # ⑥ 版本与说明
    write_text(os.path.join(staging, 'version.txt'), '%s\n%s\n' % (version, version_date), 'utf-8')
    runtime_tip = ('本包已自带 %s，目标机不需要安装任何东西，U 盘拷过去双击「启动.bat」即可。' % runtime_rel
                   if bundled else
                   '本包【不含】运行时：目标机必须自备 %s，否则双击「启动.bat」会提示缺少运行时。'
                   % runtime_name)
    write_text(os.path.join(staging, '安装说明.txt'),
               render('readme.txt.tmpl', dict(mapping, **{
                   'VERSION': version, 'VDATE': version_date, 'STAMP': stamp,
                   'NOW': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                   'PORT': args.port,
                   'PORT_HINT': '' if str(args.port) == '80' else ':' + str(args.port),
                   'RUNTIME_TIP': runtime_tip,
               })), 'utf-8', bom=True)
    copied.append('version.txt + 安装说明.txt')

    # ⑦ 打包（zip + 单文件自解压）
    dist = os.path.join(out, '部署包')
    os.makedirs(dist, exist_ok=True)
    safe = slugify(app_name)
    # 文件名里的版本不带前导 v（version.txt 里保留 APP_VERSION 原样，引擎要靠它做等值比较）
    vtag = mapping['VERSION_TAG']
    zip_path = os.path.join(dist, '%s_升级包_v%s_%s.zip' % (safe, vtag, stamp))
    bat_path = os.path.join(dist, '%s_一键升级_v%s_%s.bat' % (safe, vtag, stamp))
    if args.no_zip:
        print('  [跳过] --no-zip：只生成安装目录')
    else:
        make_zip(staging, zip_path)
        make_selfextract(zip_path, bat_path, app_name, version, stamp)

    # ⑧ 安装目录（可直接双击运行）
    install = os.path.join(out, slugify(app_name))
    if os.path.isdir(install):
        shutil.rmtree(install)
    shutil.copytree(staging, install)
    os.makedirs(os.path.join(install, data), exist_ok=True)

    for c in copied:
        print('       + %s' % c)

    print('-' * 62)
    print('  安装目录：%s' % install)
    if not args.no_zip:
        print('  部署包  ：%s  (%s)' % (zip_path, human(os.path.getsize(zip_path))))
        print('  一键升级：%s  (%s)' % (bat_path, human(os.path.getsize(bat_path))))
        print('-' * 62)
        print('  自检 ...')
        problems = verify_paths(zip_path, bat_path, version, entry, static)
        if problems:
            print('       [失败] 发现问题：')
            for p in problems:
                print('         · %s' % p)
            return 1
        print('       zip 结构 OK ／ payload 还原一致 ／ 编码与 BOM OK ／ 无数据目录泄漏 ／ 无占位符残留')

    shutil.rmtree(staging, ignore_errors=True)
    print('=' * 62)
    print('  下一步：')
    print('    1) 本机试跑：双击 %s\\启动.bat' % install)
    print('    2) 让别的电脑能访问：右键「防火墙放行.bat」→ 以管理员身份运行')
    print('       然后用 http://本机IP%s/ 访问（端口 %s%s）'
          % ('' if args.port == '80' else ':' + args.port, args.port,
             '，默认无需写端口号' if args.port == '80' else ''))
    print('    3) 开机自启（可选）：双击「开机自启.bat」，再点一次即取消')
    if not args.no_zip:
        print('    4) 部署到公司电脑：把「一键升级」.bat 拷过去双击，或用 zip + 安装.bat')
    print('=' * 62)
    return 0


def make_zip(staging, zip_path):
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, dirs, files in os.walk(staging):
            for f in sorted(files):
                p = os.path.join(base, f)
                z.write(p, os.path.relpath(p, staging))
    return zip_path


SELFEXTRACT_HEAD = r'''@echo off
chcp 936 >nul
setlocal EnableExtensions
title {APP_NAME} - 一键升级 {VERSION}

echo.
echo   ============================================================
echo              {APP_NAME}   一键升级 / 安装
echo                      {VERSION}   {STAMP}
echo   ============================================================
echo.
echo   正在解压升级文件，请稍候...
echo.

REM 可选：把安装目录直接拖到本文件图标上运行 = 手动指定安装目录
set "EQ_TARGET=%~1"

REM 先把自己复制到临时目录的英文名，避免中文路径参与后续解析
set "SELF=%TEMP%\{SELF_TMP}"
copy /y "%~f0" "%SELF%" >nul 2>nul
if not exist "%SELF%" (
  echo   [失败] 无法把升级包复制到临时目录，请检查杀毒软件是否拦截。
  echo.
  pause
  exit /b 1
)

REM 从自身尾部取出 base64 -> 还原 zip -> 解压 -> 交给 setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$f=Join-Path $env:TEMP '{SELF_TMP}';$t=[IO.File]::ReadAllText($f,[Text.Encoding]::GetEncoding(936));$m='{MARK}';$i=$t.LastIndexOf($m);if($i -lt 0){Write-Host '  [失败] 升级包数据不完整，请重新获取。' -ForegroundColor Red;exit 1};$b=($t.Substring($i+$m.Length) -replace '[^A-Za-z0-9+/=]','');$z=Join-Path $env:TEMP 'portable_upgrade_pkg.zip';[IO.File]::WriteAllBytes($z,[Convert]::FromBase64String($b));$d=Join-Path $env:TEMP 'portable_upgrade_pkg';if(Test-Path $d){Remove-Item $d -Recurse -Force};Expand-Archive -LiteralPath $z -DestinationPath $d -Force;& (Join-Path $d 'setup.ps1')"
set "RC=%errorlevel%"

del /q "%SELF%" >nul 2>nul

if not "%RC%"=="0" (
  echo.
  echo   [失败] 安装过程出错（代码 %RC%），请把上面的信息截图反馈。
  echo.
  pause
  exit /b %RC%
)

echo.
echo   临时文件已清理。
pause

REM ============================================================================
REM  下面紧跟着的是内嵌的升级数据（base64），不是命令。
REM  必须用 exit /b 0 挡住 —— 否则 cmd 会逐行把 payload 当命令执行，刷屏像失败。
REM ============================================================================
exit /b 0
'''


def make_selfextract(zip_path, out_bat, app_name, version, stamp):
    with open(zip_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    head = (SELFEXTRACT_HEAD
            .replace('{APP_NAME}', app_name)
            .replace('{VERSION}', version)
            .replace('{STAMP}', stamp)
            .replace('{SELF_TMP}', SELF_TMP_NAME)
            .replace('{MARK}', PAYLOAD_MARK))
    lines = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    body = head + '\n' + PAYLOAD_MARK + '\n' + '\n'.join(lines) + '\n'
    write_text(out_bat, body, 'gbk')
    return out_bat


# ---------------------------------------------------------------- 自检

def verify_paths(zip_path, bat_path, version, entry='server.js', static='public'):
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        need = [entry, 'setup.ps1', '安装.bat', 'version.txt', '启动.bat', '停止.bat',
                '防火墙放行.bat', '开机自启.bat', '_run_hidden.vbs', 'tools/deploy/_setup.ps1']
        for n in need:
            if n not in names:
                problems.append('zip 缺少 %s' % n)
        if static and not any(x.startswith(static + '/') for x in names):
            problems.append('zip 里没有 %s/' % static)
        # 数据目录绝对不能进包
        data_dirs = {'data/', 'db/', 'storage/', 'var/'}
        leaked = [n for n in names if n.split('/')[0] + '/' in data_dirs]
        if leaked:
            problems.append('zip 里混进了数据目录（绝对不能有）：%s' % '、'.join(leaked[:5]))

        # 编码与 BOM
        for ename in ('setup.ps1', 'tools/deploy/_setup.ps1'):
            if ename not in names:
                continue
            eb = z.read(ename)
            if eb.startswith(b'\xef\xbb\xbf\xef\xbb\xbf'):
                problems.append('包内 %s 是双 BOM，可能导致解析失败' % ename)
            elif not eb.startswith(b'\xef\xbb\xbf'):
                problems.append('包内 %s 缺少 UTF-8 BOM，GBK 系统上 PowerShell 5.1 会读成乱码' % ename)
            etxt = eb.decode('utf-8-sig', 'replace')
            if '[int]$Port' not in etxt:
                problems.append('包内 %s 不支持 -Port，远程升级会误杀别的进程' % ename)
            if '$Detached' not in etxt:
                problems.append('包内 %s 不会自派独立进程，远程升级会卡在「停止服务」那一步' % ename)

        for bname in ('启动.bat', '停止.bat', '安装.bat'):
            if bname not in names:
                continue
            bb = z.read(bname)
            if b'\r\n' not in bb:
                problems.append('%s 不是 CRLF 换行，cmd 可能解析异常' % bname)
            try:
                bb.decode('gbk')
            except UnicodeDecodeError:
                problems.append('%s 不是合法 GBK，Windows cmd 会乱码' % bname)

        # 占位符残留
        for n in names:
            if n.endswith(('.bat', '.ps1', '.txt', '.vbs')):
                try:
                    if '{{' in z.read(n).decode('utf-8-sig', 'replace'):
                        problems.append('%s 里有未替换的模板占位符' % n)
                except Exception:
                    pass

        # 杂项
        junk = [n for n in names if JUNK_RE.search(os.path.basename(n))]
        if junk:
            problems.append('zip 里混进了杂项文件：%s' % '、'.join(junk[:8]))

        # 版本一致
        v = z.read('version.txt').decode('utf-8').split('\n')[0].strip()
        if v != version:
            problems.append('zip 内 version.txt=%s，与期望的 %s 不一致' % (v, version))
        zsz = len(names)

    # payload 还原一致性
    raw = open(bat_path, 'rb').read().decode('gbk', errors='ignore')
    i = raw.rfind(PAYLOAD_MARK)
    if i < 0:
        problems.append('一键升级 .bat 里找不到 payload 标记')
    else:
        b64 = re.sub(r'[^A-Za-z0-9+/=]', '', raw[i + len(PAYLOAD_MARK):])
        try:
            recovered = base64.b64decode(b64)
            if recovered != open(zip_path, 'rb').read():
                problems.append('bat 里的 payload 还原后与 zip 不一致')
            else:
                with zipfile.ZipFile(__import__('io').BytesIO(recovered)) as z2:
                    if len(z2.namelist()) != zsz:
                        problems.append('还原出的 zip 文件数不符')
        except Exception as e:
            problems.append('payload 还原失败：%s' % e)

        head = raw[:i]
        for kw in ('chcp 936', 'GetEncoding(936)', 'Expand-Archive', 'setup.ps1',
                   'EQ_TARGET', 'exit /b 0'):
            if kw not in head:
                problems.append('bat 头部缺少关键片段：%s' % kw)
        if 'exit /b 0' not in head[-400:]:
            problems.append('payload 前没有 exit /b 0 —— cmd 会执行到 base64 刷屏')
    if '\r\n' not in raw:
        problems.append('一键升级 .bat 不是 CRLF 换行')
    return problems


def verify(args):
    out = os.path.abspath(args.out)
    dist = os.path.join(out, '部署包')
    if not os.path.isdir(dist):
        raise SystemExit('[错误] 找不到 %s，先跑 build' % dist)
    zips = sorted(f for f in os.listdir(dist) if f.endswith('.zip'))
    if not zips:
        raise SystemExit('[错误] 部署包里没有 zip')
    zpath = os.path.join(dist, zips[-1])
    stamp = re.search(r'_(\d{8})\.zip$', zips[-1])
    stamp = stamp.group(1) if stamp else ''
    bats = [f for f in os.listdir(dist) if f.endswith('.bat') and stamp and stamp in f]
    bpath = os.path.join(dist, bats[0]) if bats else ''
    version = ''
    with zipfile.ZipFile(zpath) as z:
        version = z.read('version.txt').decode('utf-8').split('\n')[0].strip()
        names = z.namelist()
        entry = next((n for n in names if n in ('server.js', 'app.js', 'index.js', 'main.js', 'app.py')), 'server.js')
        static = next((n.split('/')[0] for n in names
                       if '/' in n and n.split('/')[0] in ('public', 'static', 'dist', 'www')), '')
    print('自检对象：%s' % zips[-1])
    problems = verify_paths(zpath, bpath, version, entry, static) if bpath else ['找不到与 zip 配对的一键升级 .bat']
    if problems:
        print('[失败] 发现问题：')
        for p in problems:
            print('  · %s' % p)
        return 1
    print('[通过] 结构 / 编码 / BOM / payload 一致性 / 无数据泄漏 / 无占位符残留 全部 OK')
    return 0


# ---------------------------------------------------------------- CLI

def cmd_inspect(args):
    info = inspect_project(args.project, args.data, args.static)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print('=' * 62)
    print('  项目体检：%s' % info['project'])
    print('=' * 62)
    print('  类型      ：%s' % info['type'])
    print('  入口      ：%s' % (info['entry'] or '未识别'))
    print('  静态目录  ：%s' % (info['static_dir'] or '未识别'))
    print('  数据目录  ：%s' % info['data_dir'])
    print('  运行时    ：%s（%s）' % (info['runtime_rel'], info['runtime_name']))
    print('  版本      ：%s（%s）' % (info['version'], info['version_source']))
    print('-' * 62)
    print('  局域网就绪度：')
    bad = 0
    for name, val, verdict in info['checks']:
        is_bad = verdict.startswith('bad')
        is_warn = verdict.startswith('warn')
        mark = '!!  ' if is_bad else ('~   ' if is_warn else 'OK  ')
        if is_bad:
            bad += 1
        print('    %s %-12s %s' % (mark, name, val))
        if is_bad or is_warn:
            print('        → %s' % verdict)
    print('=' * 62)
    if bad:
        print('  结论：有 %d 项必须先改，否则上局域网会出问题（见上文 bad 项）。' % bad)
    else:
        print('  结论：可以直接打包。')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='把 Web / 服务器项目打包成 Windows 便携 + 局域网可访问的部署包')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p1 = sub.add_parser('inspect', help='体检项目，判断能否直接上局域网')
    p1.add_argument('project', nargs='?', default='.',
                    help='项目目录；省略 = 当前目录（技能默认就处理当前项目）')
    p1.add_argument('--data', help='手动指定数据目录名')
    p1.add_argument('--static', help='手动指定静态目录名')
    p1.add_argument('--json', action='store_true', help='输出 JSON（给程序用）')
    p1.set_defaults(func=cmd_inspect)

    p2 = sub.add_parser('build', help='构建安装目录 + 部署包')
    p2.add_argument('project', nargs='?', default='.',
                    help='项目目录；省略 = 当前目录（技能默认就处理当前项目）')
    p2.add_argument('--out', required=True,
                    help='输出目录（必须由用户指定：产物含运行时，几十 MB，放错地方既占空间又难找）')
    p2.add_argument('--name', help='显示名（默认取项目目录名）')
    p2.add_argument('--port', default='80', help='对外服务端口（默认 80，局域网其他设备不用带端口号）')
    p2.add_argument('--entry', help='入口文件名（默认自动识别）')
    p2.add_argument('--static', help='静态目录名（默认自动识别）')
    p2.add_argument('--data', help='数据目录名（默认自动识别）')
    p2.add_argument('--runtime', help='便携运行时相对路径（默认 runtime\\node.exe）')
    p2.add_argument('--type', choices=['node', 'static', 'python'], help='项目类型（默认自动识别）')
    p2.add_argument('--version', help='覆盖版本号（默认从入口文件 APP_VERSION 读）')
    p2.add_argument('--runtime-exe', help='指定要打进包的运行时可执行文件（默认自动找：项目 runtime/ → PATH 里的同名可执行文件）')
    p2.add_argument('--no-runtime', action='store_true',
                    help='不带运行时（默认会带，因为目标是 U 盘拷到别的电脑就能跑）')
    p2.add_argument('--full', action='store_true', help='（兼容保留）现在默认就带运行时，此参数无效果')
    p2.add_argument('--no-zip', action='store_true', help='只生成安装目录，不打 zip')
    p2.set_defaults(func=build)

    p3 = sub.add_parser('verify', help='只做自检')
    p3.add_argument('out')
    p3.set_defaults(func=verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
