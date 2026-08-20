# Terminal Docker Runner

当前阶段提供固定的 Python 测试 Runner 镜像定义。构建命令：

```text
docker build -t devteam-agent/python-runner:0.5.0 sandbox/python
```

然后配置：

```text
DEVTEAM_TERMINAL_EXECUTOR=docker
DEVTEAM_DOCKER_EXECUTABLE=docker
```

Docker 执行器会禁用网络、限制 CPU/内存/PID、移除 Linux capabilities、启用只读容器根文件系统，并以 UID/GID `10001:10001` 运行。项目 Workspace 是唯一可写的持久化挂载。

Python Runner 只预装 pytest。被测项目的额外依赖仍需通过后续的项目专用 Runner 镜像机制提供。Node 和 Maven Runner 当前要求依赖已经存在于项目 Workspace 或预构建镜像中。
