export interface FailureInfo {
  title: string;
  explanation: string;
  recovery: string;
  technicalDetail: string;
  recoveryMode?: "REVISION_LIMIT" | "ENVIRONMENT" | "PROJECT_TEST_FAILURE" | "WORKSPACE";
  checks?: string[];
  retryLabel?: string;
}

export function explainFailure(
  message: string | null,
  latestTestReport?: Record<string, unknown> | null,
): FailureInfo {
  const technicalDetail = message?.trim() || "系统没有返回更详细的错误信息。";
  const normalized = technicalDetail.toLowerCase();

  if (
    normalized.includes("workspace_write_permission")
    || normalized.includes("项目目录没有写入权限")
    || normalized.includes("项目文件没有写入权限")
    || (
      normalized.includes("代码变更应用失败")
      && (normalized.includes("permissionerror") || normalized.includes("permission denied"))
    )
  ) {
    return {
      title: "项目目录当前不可写",
      explanation: "开发智能体已经生成代码修改方案，但 DevTeam Agent 的后台进程没有权限在目标项目目录中创建或修改文件。系统已在写入前停止或回滚本轮变更，因此不会留下半成品文件。",
      recovery: "点击“检查目录并继续”会先执行真实写入检测；若仍不可写，可直接更换为可访问的项目目录。验证通过后系统会恢复到最近安全检查点，不会重新生成需求和设计。",
      checks: [
        "确认项目目录不是只读目录，并且当前 Windows 用户拥有修改权限",
        "如果目录位于其他磁盘，确认安全软件或受控文件夹访问没有拦截 Python",
        "关闭可能独占目标文件的编辑器、预览程序或同步工具后重试",
      ],
      retryLabel: "检查目录并继续",
      recoveryMode: "WORKSPACE",
      technicalDetail,
    };
  }

  if (
    normalized.includes("model_connection_error")
    || normalized.includes("apiconnectionerror")
    || normalized.includes("connection error")
  ) {
    return {
      title: "无法连接模型服务",
      explanation: "DevTeam Agent 没有与模型服务建立网络连接。任务已停在安全检查点，已有产物不会丢失；这不是生成代码本身出错。",
      recovery: "请先恢复网络或代理连接，再点击“重新连接并继续”。系统会从当前阶段重新执行，无需重新创建项目。",
      checks: [
        "确认浏览器能够正常访问互联网",
        "如果使用 Clash、VPN 等代理，确认系统代理或 TUN 模式已真正启用",
        "确认防火墙没有拦截 DevTeam Agent 的 Python 进程",
      ],
      retryLabel: "重新连接并继续",
      technicalDetail,
    };
  }
  if (
    normalized.includes("model_authentication_error")
    || normalized.includes("authenticationerror")
    || normalized.includes("permissiondeniederror")
  ) {
    return {
      title: "模型服务身份验证失败",
      explanation: "模型服务已经收到请求，但拒绝了当前凭证。常见原因是 API Key 无效、已失效或没有模型访问权限。",
      recovery: "请更新正确的 API Key 并重启 DevTeam Agent，然后从当前检查点继续。",
      checks: [
        "确认 API Key 没有多余空格或引号",
        "确认 API Key 所属账户仍然有效",
        "确认账户拥有当前模型的访问权限",
      ],
      technicalDetail,
    };
  }
  if (
    normalized.includes("model_rate_limit_error")
    || normalized.includes("ratelimiterror")
  ) {
    return {
      title: "模型服务额度或频率受限",
      explanation: "网络连接正常，但模型服务因余额、并发或请求频率限制拒绝了本次调用。",
      recovery: "请检查账户余额与并发限制，稍候再从当前检查点继续。",
      retryLabel: "稍后重试当前阶段",
      technicalDetail,
    };
  }
  if (
    normalized.includes("model_timeout_error")
    || normalized.includes("apitimeouterror")
  ) {
    return {
      title: "模型生成超过阶段时限",
      explanation: "模型服务可以连接，但本次代码上下文或生成内容较大，未能在限定时间内完整返回。已有产物和检查点不会丢失。",
      recovery: "可以重新执行当前阶段；系统会压缩历史反馈和代码上下文，并使用一次完整生成，避免超时后从头重复。",
      retryLabel: "重新连接并继续",
      technicalDetail,
    };
  }
  if (normalized.includes("structuredmodelresponseerror")) {
    return {
      title: "模型返回格式未通过校验",
      explanation: "模型服务已经返回内容，但返回的 JSON 字段不完整或格式不符合当前智能体产物规范。已有测试报告、代码和检查点不会丢失。",
      recovery: "恢复后系统会携带具体校验错误自动要求模型纠正一次，并使用压缩、去控制码的测试错误上下文重新生成修复计划。",
      retryLabel: "纠正格式并继续",
      technicalDetail,
    };
  }

  if (
    (normalized.includes("npm_build") || normalized.includes("npm_test"))
    && (
      normalized.includes("webpack")
      || normalized.includes("jest")
      || normalized.includes("\"node\"")
      || normalized.includes("'node'")
    )
    && (
      technicalDetail.includes("�")
      || normalized.includes("not recognized")
      || normalized.includes("找不到")
      || normalized.includes("不是内部或外部命令")
    )
  ) {
    return {
      title: normalized.includes("node")
        ? "测试终端没有找到 Node.js"
        : "项目 npm 依赖尚未安装",
      explanation: normalized.includes("node")
        ? "项目依赖已经安装，但受限测试终端没有正确继承 Node.js 所在目录。这是 DevTeam Agent 的环境配置问题，不是生成项目的代码错误。"
        : "package.json 已经存在，但当前项目还没有安装 webpack、jest 等本地依赖。这属于运行环境准备问题，不是代码功能测试失败，也不应消耗自动返工次数。",
      recovery: normalized.includes("node")
        ? "系统会重新构造测试终端的 PATH，并从当前测试检查点继续，不需要重新安装项目或修改业务代码。"
        : "点击“检查并自动补齐环境”，系统会展示 npm install 安装方案；只有你确认后才会下载依赖。安装完成后会从测试检查点继续。",
      retryLabel: normalized.includes("node") ? "修复测试环境并继续" : "安装依赖后继续",
      recoveryMode: "ENVIRONMENT",
      technicalDetail,
    };
  }

  if (normalized.includes("test failure limit")) {
    return {
      title: "自动测试连续失败，已暂停返工",
      explanation: "工程检查仍未通过，工作流已安全暂停。旧版计数可能把环境检查报告也计入了失败上限，但不代表开发智能体已经真正完成两轮代码返工。",
      recovery: "恢复后系统会根据最新测试报告，按真实的“测试失败→代码修复”次数重新判断，并优先补齐 package.json、HTML 入口和构建脚本；已有产物不会丢失。",
      retryLabel: "修复工程结构并重新验证",
      recoveryMode: "REVISION_LIMIT",
      technicalDetail,
    };
  }
  if (normalized.includes("review revision limit")) {
    return {
      title: "代码审查连续未通过，已暂停自动返工",
      explanation: "系统已经完成多轮“审查—修改—复审”，但最新代码仍有未解决问题。为防止重复修改和消耗模型调用，任务已停在最近的安全检查点。",
      recovery: "系统会重新读取审查问题指向的真实文件，强制覆盖所有 BLOCKER 和 MAJOR 问题，并禁止对同一文件连续执行会导致哈希失效的多次替换。最多可由你确认执行三轮受控修复。",
      technicalDetail,
      recoveryMode: "REVISION_LIMIT",
    };
  }
  if (normalized.includes("visual quality revision limit")) {
    return {
      title: "视觉质量连续未达标，已暂停自动返工",
      explanation: "页面经过多轮视觉优化后仍未达到质量门槛，系统已停止自动循环并保留当前代码与视觉报告。",
      recovery: "可以追加一轮受控智能修复，系统会汇总历次视觉反馈并优先处理反复出现的低分项。最多可由你确认追加三轮。",
      technicalDetail,
      recoveryMode: "REVISION_LIMIT",
    };
  }
  if (normalized.includes("test plan does not cover acceptance criteria")) {
    return {
      title: "测试计划遗漏了部分验收标准",
      explanation: "测试智能体只关联了部分 AC 验收标准，系统为避免生成不完整的测试报告而暂停了当前阶段；已有代码不会丢失。",
      recovery: "系统会补齐遗漏验收标准与完整工程检查命令的追踪关系，然后重新执行测试验证。",
      technicalDetail,
    };
  }
  if (normalized.includes("test plan references unknown acceptance criteria")) {
    return {
      title: "测试计划包含无效验收编号",
      explanation: "测试智能体引用了当前需求文档中不存在的 AC 编号，因此测试计划未被执行。",
      recovery: "系统会移除无效编号、补齐真实验收标准后，重新执行测试验证。",
      technicalDetail,
    };
  }
  if (normalized.includes("developer plan references unknown requirements")) {
    return {
      title: "开发计划中的追踪编号不一致",
      explanation: "开发智能体把验收标准编号（AC）误写进了需求编号字段，现有需求、设计和代码文件不会因此丢失。",
      recovery: "系统会将 AC 编号转换为其关联的 FR/NFR 需求编号，再从安全检查点重新执行当前阶段。",
      technicalDetail,
    };
  }
  if (
    normalized.includes("review references unknown requirements")
    || normalized.includes("acceptance criteria reference unknown requirements")
  ) {
    return {
      title: "审查报告中的追踪编号不一致",
      explanation: "智能体把验收标准编号和需求编号混用了，代码文件本身不一定存在问题。",
      recovery: "系统会从安全检查点恢复，自动校正编号后重新执行代码审查。",
      technicalDetail,
    };
  }
  if (normalized.includes("review references unchanged files")) {
    return {
      title: "审查报告中的文件路径不完整",
      explanation: "智能体返回了空路径或没有对应到真实变更文件，代码文件本身不一定存在问题。",
      recovery: "系统会根据真实代码变更校正文件路径，然后重新执行代码审查。",
      technicalDetail,
    };
  }
  if (
    normalized.includes("old_text must occur exactly once")
    || normalized.includes("file changed after it was read")
    || normalized.includes("代码修改计划与当前文件内容不一致")
  ) {
    return {
      title: "代码修改计划与当前文件版本不一致",
      explanation: "文件可能已在前一次执行中被修改，当前替换计划使用了过期内容；已有修改不会丢失。",
      recovery: "系统会重新读取文件；已应用的修改会幂等确认，失效的修改计划会基于最新内容重新生成。",
      technicalDetail,
    };
  }
  if (
    normalized.includes("fileexistserror")
    || normalized.includes("already exists")
  ) {
    return {
      title: "开发计划重复创建了已有文件",
      explanation: "前一次执行已经成功写入了部分项目文件，本轮计划又把其中一个文件当作新文件创建，因此安全策略阻止了覆盖。此前写入的 package.json 等文件不会丢失。",
      recovery: "恢复后系统会先读取每个目标文件：内容相同则直接确认完成，内容不同则转换为带版本校验的安全更新，然后继续执行剩余文件，不再因单个已存在文件中断整批变更。",
      retryLabel: "校准文件并继续",
      technicalDetail,
    };
  }
  if (
    normalized.includes("no longer matches codechangeartifact")
    || normalized.includes("code_change_file_drift")
  ) {
    return {
      title: "代码文件与旧的变更记录不一致",
      explanation: "代码文件在变更产物生成后又发生了更新，旧记录中的文件摘要已经失效。文件本身不会丢失，也不代表代码一定有错误。",
      recovery: "点击“重新校准并继续”后，系统会重新读取仓库当前文件，把现有修改纳入新的代码审查基线，再继续执行当前阶段。",
      retryLabel: "重新校准并继续",
      technicalDetail,
    };
  }
  if (normalized.includes("environment") || normalized.includes("executable")) {
    const results = Array.isArray(latestTestReport?.results)
      ? latestTestReport.results as Array<Record<string, unknown>>
      : [];
    const projectFailures = results.filter((item) => item.status === "FAILED");
    if (projectFailures.length > 0) {
      const details = projectFailures
        .slice(0, 3)
        .map((item) => String(item.stderr_excerpt || item.stdout_excerpt || "工程检查未通过"));
      return {
        title: "项目结构或代码未通过测试",
        explanation: "测试同时发现了项目自身问题，因此不能只归因于本机环境。系统将优先让开发智能体补齐缺失文件或修复代码。",
        recovery: `本轮发现：${details.join("；")}。恢复后会把这些真实测试结果交给开发智能体返工。`,
        retryLabel: "修复项目并重新验证",
        recoveryMode: "PROJECT_TEST_FAILURE",
        technicalDetail,
      };
    }
    return {
      title: "运行环境暂时无法完成验证",
      explanation: "当前电脑缺少所需命令、依赖或执行环境，因此系统没有把项目错误地标记为完成。",
      recovery: "点击“检查并自动补齐环境”，系统会列出需要安装的内容；只有你确认后才会下载安装。",
      retryLabel: "直接重新执行",
      recoveryMode: "ENVIRONMENT",
      technicalDetail,
    };
  }
  if (normalized.includes("timeout") || normalized.includes("timed out")) {
    return {
      title: "执行时间超过限制",
      explanation: "智能体或工程命令在规定时间内没有返回结果，任务已安全停止。",
      recovery: "恢复后系统会重新执行；如果连续超时，请查看时间线中的具体命令。",
      technicalDetail,
    };
  }
  if (normalized.includes("model") || normalized.includes("api")) {
    return {
      title: "模型服务调用失败",
      explanation: "模型服务可能暂时不可用、请求超时或返回格式不符合要求。",
      recovery: "确认模型服务可用后，恢复并重新执行当前阶段即可。",
      technicalDetail,
    };
  }
  if (normalized.includes("revision limit")) {
    return {
      title: "自动修订次数已达到上限",
      explanation: "系统多次修改后仍未通过质量门禁，为避免无限循环已暂停任务。",
      recovery: "请查看最近的审查或测试报告，再补充明确反馈后发起新一轮修改。",
      technicalDetail,
    };
  }
  return {
    title: "当前阶段执行失败",
    explanation: "任务已经停在最近的安全检查点，已有需求、设计和代码产物不会丢失。",
    recovery: "可以恢复并重新执行当前阶段；技术详情可用于进一步排查。",
    technicalDetail,
  };
}
