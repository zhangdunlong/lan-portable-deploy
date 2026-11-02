# Windows 编码三坑：GBK / BOM / CRLF

三个坑都不会报错，只会"看起来没生效"，排查起来极耗时间。都是实测结论，不是推测。

---

## 坑 1：`.bat` 必须是 GBK + CRLF

**现象**：双击后窗口里全是乱码，中文提示看不懂；更糟的是某些行被解析错，脚本行为异常。

**根因**：Windows `cmd.exe` 按系统 ANSI 代码页（简体中文系统是 936 = GBK）解码 `.bat` 文件，不看 BOM、不认 UTF-8。

**规则**

- 写 `.bat` 用 `encoding='gbk'`，换行用 `\r\n`
- 行内显式 `chcp 936 >nul`（防止机器代码页被改成 65001 后乱码）
- 文件里**不要出现 GBK 表示不了的字符**：emoji、部分生僻字、特殊数学符号。写到这些字符时编码会失败——这不是坏事，是提前暴露问题
- `echo` 里的 `|` `>` `&` 要转义成 `^|` `^>` `^&`
- 括号块里的 `)` 要转义成 `^)`

**验证**

```python
b = open('启动.bat','rb').read()
assert b'\r\n' in b          # CRLF
b.decode('gbk')              # 不抛异常 = GBK 合法
```

---

## 坑 2：`.ps1` 必须 UTF-8 带 BOM，且只能有一个 BOM

**现象**

- 缺 BOM：PowerShell 5.1 在 GBK 系统上把中文读成乱码，严重时整段解析失败——**脚本跑起来什么都不输出、瞬间退出**
- 双 BOM：正文第一个字符变成 `U+FEFF`，同样可能导致解析失败

**根因**：Windows PowerShell 5.1 只认 BOM 来判断 UTF-8；没有 BOM 时按 ANSI(GBK) 读。而 `pwsh` 7+ 默认 UTF-8，行为不同——所以"我这儿能跑"不等于现场能跑。

**规则**

- 写 `.ps1` 用 `encoding='utf-8-sig'`（Python 的 `utf-8-sig` 写出来就是带 BOM 的）
- 写入前把正文里已有的 `\ufeff` 全部 `lstrip('\ufeff')`，保证有且只有一个
- 换行用 `\r\n`

**验证**

```python
b = open('setup.ps1','rb').read()
assert b.startswith(b'\xef\xbb\xbf'), '缺 BOM'
assert not b.startswith(b'\xef\xbb\xbf\xef\xbb\xbf'), '双 BOM'
```

---

## 坑 3：单文件自解压 `.bat` 的 payload 必须先 `exit /b 0`

**现象**：双击后屏幕刷出几万行"不是内部或外部命令"，看着像安装失败，其实是 cmd 在逐行执行 base64。

**根因**：把 zip 用 base64 内嵌到 `.bat` 尾部后，cmd 会继续往下读并把它当命令执行。

**规则**

```
...正常的 bat 逻辑...
exit /b 0                      ← 必须有，挡在 payload 之前

__PAYLOAD__                    ← 标记行
<base64 数据，每行 76 字符>
```

- 提取时用**最后一个**标记位置（PowerShell 代码里也会出现 `__PAYLOAD__` 字面量）
- 提取后正则清洗 `[^A-Za-z0-9+/=]`，因为换行和 CR 会被读进来
- 完整流程验证：从 `.bat` 里还原出的字节必须与原 zip **逐字节相等**

---

## 附：其他容易踩的编码相关点

| 场景 | 规则 |
|---|---|
| `.txt` 说明文件给 Windows 记事本看 | UTF-8 + BOM（否则老记事本按 GBK 读，中文乱码） |
| `.vbs` 脚本 | 保持纯 ASCII（VBScript 按 ANSI 读，中文注释有风险；注释写英文最稳） |
| JSON 配置 | UTF-8 无 BOM（带 BOM 会让 `JSON.parse` 报错） |
| 控制台输出重定向进日志 | 显式设 `[Console]::OutputEncoding = UTF8`，否则 PowerShell 5.1 按 ANSI 落盘 |
| 中文路径 | 避免出现在命令行解析链里：`.bat` 先把自己复制到 `%TEMP%` 的英文名再处理 |

---

## 一句话记忆

**bat 要 ANSI（GBK）、ps1 要 BOM、payload 要 exit 挡、说明要 BOM 给记事本。**
四者写错任何一个，现场表现都是"看着像失败"或"看着没生效"，而不是明确的报错。
