# 文章批量生成：同事分发版

本项目提供Windows窗口和终端入口，用于采集公司官网首屏图片及About Us内容，并通过FTP上传图片、生成公网URL映射。

这是源码分发版，不包含Python运行环境。首次部署需要Python和Google Chrome，目前不是免安装环境的EXE便携包。本目录独立包含运行组件，不需要原电脑的 `outputs` 或上一级项目目录。

## FTP配置必须向同事领取

**仓库仅提供空白 `ftp_config.example.json`，不提交 `ftp_config.json`。需要使用FTP时，请向负责本项目的同事索取真实 `ftp_config.json`，通过内部渠道获取后放在项目根目录；如启动器已创建空模板，请替换它。真实配置不得提交 GitHub。**

- 真实FTP配置只留本机，不得上传GitHub或放进对外分发包。
- `ftp_config.example.json` 展示配置结构，可以提交Git；`ftp_config.json` 已加入 `.gitignore`。
- 地址、账号密码、FTP目录和公网域名以同事提供的完整配置为准，不要凭示例猜测。
- 空配置不影响网站调研；FTP连接和上传必须先配置。替换后重新启动UI。
- 首次Git clone不会带下被忽略的本地配置；启动器会从示例创建空模板，仍需向同事领取完整配置。

## 首次部署

1. 安装Python，建议与原项目一致使用3.11，包含pip、venv及Tcl/Tk，勾选加入PATH。
2. 安装Google Chrome。
3. 在项目目录双击 `安装依赖.bat`。它优先使用Python 3.11，创建独立 `.venv` 并安装 `requirements.txt`；依赖下载需要联网。它不会自动安装Python或Chrome，也不会覆盖已有FTP配置。
4. 双击 `启动调研和上传工具.bat`，启动器优先使用本目录 `.venv`。
5. 要使用FTP时，另向同事领取完整配置。

也可以在项目目录用PowerShell手动部署：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath "ftp_config.json")) {
    Copy-Item -LiteralPath "ftp_config.example.json" -Destination "ftp_config.json"
}
.\.venv\Scripts\python.exe workflow_ui.py
```

### 交给Agent部署

让Agent先阅读本README，检查Python和Chrome，在当前仓库目录执行 `安装依赖.bat`，再启动窗口。缺少运行环境时，按使用者允许的方式安装。首次部署不要自动执行真实搜索、采集或FTP上传。

Agent不得猜测或自行获取FTP凭据。配置缺失时提醒使用者找同事索取完整JSON；网站调研可以先使用。不要在日志、会话或仓库中输出真实FTP密码和浏览器Cookie。

## 网站调研

在UI的“网站调研”页填写目标公司官网，再填写关键词。需要指定公司官网时，在关键词下面逐行填写网址；支持多组关键词，也可以加载 `keywords.txt`。

```text
industrial pump manufacturer
https://example.com/
https://example.org/

chemical pump manufacturer
https://example.net/
```

这是格式示例，运行前须替换为真实内容。分发版的 `keywords.txt` 仅保留注释示例，没有原项目任务清单。

- “仅采集指定网址”：跳过Google，只采集关键词下面的网址，每个关键词至少带一个网址。
- “Google 搜索关键词 + 指定网址”：照常搜索关键词，可只填关键词；填写的网址合并进候选池。
- 默认采集全部候选，最多并发10个标签；网页load后等待7秒截图，并尝试处理常见Cookie确认。
- 指定网址自动模式直接启动本机Chrome；窗口默认最小化，可从任务栏展开。
- Google搜索和人工截图模式可能需要首次检查或下载ChromeDriver。Google验证码仍需要人工处理；程序要求输入时，可在UI日志区发送回车或对应字符。
- 不要同时运行多个共用同一Google Profile的搜索任务。

终端示例（CMD中整行运行，替换目标公司官网）：

```cmd
.venv\Scripts\python.exe research.py --keywords-file "keywords.txt" --target-url "https://your-company.example/" --keyword-url-mode urls-only --companies 0
```

同时搜索Google时，将 `urls-only` 改为 `google-plus-urls`。

### 输出目录

- `runs/关键词 时间戳/`：自动创建，每个关键词一份独立结果。
- 每份结果的 `companies/`：公司介绍、结构化JSON、汇总Markdown和可用截图；无企业图片方案不保存公司截图。
- `runs/_ui_jobs/`：窗口提交的关键词输入副本。
- `chrome_profile/`：本机Google登录状态和Cookie，运行时生成，不随项目分发。
- 不需要预建 `output` 或 `outputs`；旧版截图脚本独立运行时使用的 `output` 也会自动创建。

## FTP图片上传

先向同事领取完整 `ftp_config.json` 并替换空模板，然后：

1. 在“图片上传”页选择 `server1` 或 `server2`。
2. 点击“连接FTP并选择目录”，进入客户网站的 `uploads` 文件夹，选择当前目录。也可手动填写准确FTP路径。
3. 选择本地图片目录，填写月份，例如 `202610`。
4. 填写该客户网站的正式公网域名。切换客户时，应同时确认远程目录和域名。
5. 检查“含月份的目标目录”，确认后上传。

根目录应选到 `uploads`，程序只自动追加月份，不会替你补上 `uploads`。月份文件夹不存在时，上传阶段会尝试创建。不同服务器的FTP路径格式可能不同，请以FTP浏览器显示的路径为准，不要将Windows物理路径直接当成FTP路径。

目录浏览会显示连接、登录、读取目录的阶段和等待时间；单次网络等待10秒，单次目录操作总等待上限45秒。“仅模拟检查”不连接FTP，只预览文件及映射。

图片默认追加时间戳文件名，并额外上传同内容的 `.webp` 后缀副本；副本没有重新转码。上传成功后，在所选本地图片目录生成：

- `upload_manifest.json`：上传明细。
- `image_url_mapping.csv`：本地图片路径与公网图片URL两列，不单列webp副本。

终端示例：

```cmd
.venv\Scripts\python.exe deploy_ftp.py --profile server1 --source "C:\your-images" --remote-upload-root "/your-site/uploads" --month 202610 --public-domain "https://your-company.example"
```

路径、域名只是占位示例，必须换成同事提供的真实配置或FTP目录浏览器选出的路径。

## 文章写作要求

- `article_prompt_template.md`：单篇文章的结构、HTML和写作标准。
- `batch_article_generation_prompt.md`：批量生成、Banner、图片整理、映射与质检交付要求。

这两份是交给AI的指令文档，采集脚本和UI不会自动执行写作。采集完成后，将素材路径、两份要求、目标公司和输出目录交给AI。

目前要求优先10家公司、其次8家。目标公司为必选、计入总数，不进入候选核实或淘汰流程；批量文章的目标公司在第1—4名轮换。详细规则以这两份文档为准。

## 本分发副本保留和排除的内容

保留：调研、截图、UI、FTP上传程序，所需JS组件、依赖清单、两份写作要求、安装启动脚本和空白FTP模板。

没有复制：`runs/`、`chrome_profile/`、`work_tmp/`、`__pycache__/`、空的旧 `prompts/` 目录、历史文章生成/核验脚本，以及带固定旧电脑路径的文章图片替换脚本。原项目没有修改。

`.gitignore` 会排除真实FTP配置、浏览器数据、采集结果、虚拟环境、缓存、日志和ZIP。Git不会保存空目录，程序会在运行时创建需要的目录。不要用强制添加方式把已填写的真实配置或个人会话数据提交到仓库。
