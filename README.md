# Claude 数据助手（Claude Agent SDK + 自建 LiteLLM）

前端聊天 + **Claude Agent SDK**（`ClaudeSDKClient`），默认走内网 LiteLLM 的 Anthropic 兼容接口：

```
浏览器 → FastAPI → Claude Agent SDK → http://143.21.64.150:4000/v1/messages → dsv4
```

## Linux x86_64 离线安装

把整个项目拷到 Linux（**不要带** Windows 的 `.venv`），然后：

```bash
bash scripts/install-linux.sh
source .venv/bin/activate
python -m backend.main
```

详细说明见 `离线部署.txt`。打开 http://服务器IP:8000

## Windows 本机启动

```powershell
cd E:\李焱\claude-data-chat
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m backend.main
```

打开 http://127.0.0.1:8000

## 能力

- 读/写/搜索工作目录、Bash/Python 分析、SQLite 只读查询
- 压缩包内容查看（`list_archive` / `find_files`）
- 回答中匹配到的部门受案号可点击，查看 Excel 字段与 PDF 文书
