import { describe, expect, it } from "vitest";

import { explainFailure } from "./failure";

describe("explainFailure", () => {
  it("把模型连接错误解释为网络或代理故障", () => {
    const result = explainFailure("APIConnectionError: Connection error.");

    expect(result.title).toBe("无法连接模型服务");
    expect(result.explanation).toContain("不是生成代码本身出错");
    expect(result.checks).toContain("如果使用 Clash、VPN 等代理，确认系统代理或 TUN 模式已真正启用");
    expect(result.retryLabel).toBe("重新连接并继续");
  });

  it("把项目目录权限错误与模型鉴权错误区分开", () => {
    const result = explainFailure(
      "WorkspaceWritePermissionError: WORKSPACE_WRITE_PERMISSION：项目目录没有写入权限：E:\\TestProject\\6",
    );

    expect(result.title).toBe("项目目录当前不可写");
    expect(result.explanation).toContain("没有权限");
    expect(result.retryLabel).toBe("检查目录并继续");
    expect(result.recoveryMode).toBe("WORKSPACE");
    expect(result.checks).toContain("如果目录位于其他磁盘，确认安全软件或受控文件夹访问没有拦截 Python");
  });

  it("兼容旧任务中被汇总信息隐藏的文件权限错误", () => {
    const result = explainFailure(
      "RuntimeError: 代码变更应用失败，已自动恢复到本轮修改前的文件状态；底层工具错误：PermissionError: [Errno 13] Permission denied: 'E:\\TestProject\\6\\index.html'",
    );

    expect(result.title).toBe("项目目录当前不可写");
    expect(result.technicalDetail).toContain("index.html");
  });

  it("把代码摘要漂移解释为可自动重新校准的问题", () => {
    const result = explainFailure(
      "RuntimeError: changed file src/pages/SpeakingPractice.js no longer matches CodeChangeArtifact",
    );

    expect(result.title).toBe("代码文件与旧的变更记录不一致");
    expect(result.explanation).toContain("不代表代码一定有错误");
    expect(result.retryLabel).toBe("重新校准并继续");
  });

  it("将代码审查返工上限解释为可追加的受控修复", () => {
    const result = explainFailure(
      "RuntimeError: review revision limit 3 exceeded; 已完成 3 轮代码审查返工",
    );

    expect(result.title).toBe("代码审查连续未通过，已暂停自动返工");
    expect(result.recoveryMode).toBe("REVISION_LIMIT");
    expect(result.recovery).toContain("最多可由你确认执行三轮");
  });

  it("解释测试返工达到上限并给出诊断方向", () => {
    const result = explainFailure(
      "RuntimeError: test failure limit 2 exceeded；最近一次测试失败：STATIC_PAGE_CHECK：页面资源不存在",
    );

    expect(result.title).toBe("自动测试连续失败，已暂停返工");
    expect(result.explanation).toContain("两轮");
    expect(result.recovery).toContain("最新测试报告");
    expect(result.recoveryMode).toBe("REVISION_LIMIT");
  });

  it("把未安装 webpack 和 jest 识别为项目依赖问题", () => {
    const result = explainFailure(
      "RuntimeError: test failure limit 2 exceeded；NPM_BUILD: 'webpack' ������；NPM_TEST: 'jest' ������",
    );

    expect(result.title).toBe("项目 npm 依赖尚未安装");
    expect(result.recoveryMode).toBe("ENVIRONMENT");
    expect(result.recovery).toContain("确认");
  });

  it("把 npm 找不到 node 识别为测试终端环境问题", () => {
    const result = explainFailure(
      "RuntimeError: test failure limit 2 exceeded；NPM_BUILD: '\"node\" 不是内部或外部命令，也不是可运行的程序'",
    );

    expect(result.title).toBe("测试终端没有找到 Node.js");
    expect(result.recoveryMode).toBe("ENVIRONMENT");
    expect(result.recovery).toContain("PATH");
  });

  it("解释模型结构化输出校验失败", () => {
    const result = explainFailure(
      "DeepSeekStructuredModelResponseError: DeepSeek response did not match the requested structured output",
    );

    expect(result.title).toBe("模型返回格式未通过校验");
    expect(result.retryLabel).toBe("纠正格式并继续");
  });

  it("解释测试计划未覆盖全部验收标准", () => {
    const result = explainFailure(
      "ValueError: test plan does not cover acceptance criteria: {'AC-002', 'AC-004'}",
    );

    expect(result.title).toBe("测试计划遗漏了部分验收标准");
    expect(result.explanation).toContain("不完整");
    expect(result.recovery).toContain("补齐");
  });

  it("把开发计划编号错误解释为用户可理解的中文", () => {
    const result = explainFailure(
      "ValueError: developer plan references unknown requirements: {'AC-002', 'AC-001'}",
    );

    expect(result.title).toBe("开发计划中的追踪编号不一致");
    expect(result.explanation).toContain("验收标准编号");
    expect(result.recovery).toContain("FR/NFR");
  });

  it("把审查编号错误解释为用户可理解的中文", () => {
    const result = explainFailure(
      "ValueError: review references unknown requirements: {'AC-001'}",
    );

    expect(result.title).toBe("审查报告中的追踪编号不一致");
    expect(result.explanation).toContain("验收标准编号");
    expect(result.technicalDetail).toContain("AC-001");
  });

  it("为未知错误保留技术详情", () => {
    const result = explainFailure("Unexpected failure");

    expect(result.title).toBe("当前阶段执行失败");
    expect(result.technicalDetail).toBe("Unexpected failure");
  });

  it("解释审查文件路径缺失错误", () => {
    const result = explainFailure(
      "ValueError: review references unchanged files: {''}",
    );

    expect(result.title).toBe("审查报告中的文件路径不完整");
    expect(result.recovery).toContain("校正文件路径");
  });

  it("解释文件替换计划失效错误", () => {
    const result = explainFailure(
      "ValueError: old_text must occur exactly once, but occurred 0 times",
    );

    expect(result.title).toBe("代码修改计划与当前文件版本不一致");
    expect(result.recovery).toContain("幂等确认");
  });
});
