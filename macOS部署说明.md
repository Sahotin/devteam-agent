# DevTeam Agent macOS M4 部署说明

本部署包适用于 Apple Silicon（M1/M2/M3/M4）Mac。包中已包含构建完成的前端页面，但不包含 Windows 虚拟环境、Node.js 依赖、历史数据库、用户生成项目或任何 API Key。

## 一、准备运行环境

建议使用 macOS 13 或更高版本，并准备：

- Python 3.11 或 3.12
- Git
- Node.js（推荐安装；运行 Agent 生成的 npm 项目测试时需要）

如果电脑已经安装 Homebrew，可在“终端”中执行：

```bash
brew install python@3.12 git node
```

如果尚未安装 Git，也可以先执行：

```bash
xcode-select --install
```

## 二、首次安装

1. 解压部署包，建议移动到“文稿”目录。
2. 打开“终端”。
3. 输入 `cd `（注意 cd 后有一个空格），然后把解压后的 `DevTeamAgent` 文件夹拖入终端窗口，按回车。
4. 执行：

```bash
bash scripts/install_macos.sh
```

脚本会为当前 Mac 创建全新的 `.venv`，安装 ARM64 兼容依赖，并创建空数据库与项目工作目录。

## 三、配置 DeepSeek

部署包不会携带原电脑上的 API Key。首次安装后，用文本编辑器打开项目根目录的 `.env`，设置：

```env
DEVTEAM_LLM_PROVIDER=deepseek
DEVTEAM_LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=在这里填写新的DeepSeek密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

请勿把填写了密钥的 `.env` 发送给其他人。

如果暂时只想查看界面，可保留默认的 `DEVTEAM_LLM_PROVIDER=demo`。

## 四、启动系统

安装完成后，可双击根目录中的：

```text
启动 DevTeam Agent.command
```

也可以在终端中运行：

```bash
bash scripts/start_macos.sh
```

服务启动成功后会自动打开：`http://127.0.0.1:8000`。

如果 macOS 第一次阻止 `.command` 文件运行，请右键该文件，选择“打开”；或者继续使用终端启动命令。

## 五、创建项目时的路径

Windows 路径（例如 `C:\Users\...`）不能在 macOS 上使用。建议先创建统一的项目目录：

```bash
mkdir -p "$HOME/Documents/DevTeamProjects"
```

在 DevTeam Agent 中填写类似下面的绝对路径：

```text
/Users/你的用户名/Documents/DevTeamProjects/snake-game
```

可在终端中执行 `echo $HOME` 查看 `/Users/你的用户名` 部分。

## 六、停止与重新启动

- 停止：在运行服务的终端窗口中按 `Control+C`。
- 重新启动：再次双击 `.command` 文件。
- 数据库位置：`data/devteam_agent.db`。
- Agent 创建的代码位于你创建项目时填写的仓库绝对路径中。

## 七、Docker 方式（可选）

如果已安装支持 Apple Silicon 的 Docker Desktop，也可以在项目根目录执行：

```bash
docker compose up --build
```

Docker 方式会使用 PostgreSQL；个人本机使用更推荐前面的原生 Python + SQLite 方式。

