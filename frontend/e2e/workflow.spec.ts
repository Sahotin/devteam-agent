import { expect, test } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("用户可从需求提交推进到完整交付", async ({ page, request }) => {
  test.setTimeout(45_000);
  const workspace = mkdtempSync(join(tmpdir(), "devteam-e2e-"));
  const projectResponse = await request.post(
    "/api/v1/projects",
    {
      data: {
        name: "浏览器端到端验证",
        root_path: workspace,
        summary: "验证前后端、SSE 与工作流操作的核心链路",
      },
    },
  );
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();

  const requirement = "创建一个带健康检查接口的最小 Python 服务";
  const taskResponse = await request.post(
    "/api/v1/tasks",
    {
      data: {
        project_id: project.id,
        requirement,
        execution_scope: "AUTO",
        preference: "BALANCED",
      },
    },
  );
  expect(taskResponse.ok()).toBeTruthy();

  await page.goto("/");
  await expect(page.getByRole("heading", { name: requirement })).toBeVisible();
  console.log("E2E：任务页面已加载");

  await page.getByRole("button", { name: "启动智能体团队" }).click();
  await expect(page.getByRole("button", { name: "批准需求文档" })).toBeVisible();
  console.log("E2E：需求文档已生成");

  await page.getByRole("button", { name: "批准需求文档" }).click();
  await expect(
    page.getByRole("button", { name: "由智能体选择并继续" }),
  ).toBeVisible();
  console.log("E2E：架构方案已生成");

  await page.getByRole("button", { name: "由智能体选择并继续" }).click();
  await expect(page.getByRole("button", { name: "开始代码审查" })).toBeVisible();
  console.log("E2E：代码实现已完成");

  await page.getByRole("button", { name: "开始代码审查" }).click();
  await expect(page.getByRole("button", { name: "执行测试验证" })).toBeVisible();
  console.log("E2E：代码审查已完成");

  await page.getByRole("button", { name: "执行测试验证" }).click();
  await expect(
    page.getByText("工作流已完成，可以查看和启动项目"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "查看启动与项目说明" })).toBeVisible();
  console.log("E2E：完整交付已完成");
  // 显式关闭 SSE 所在页面，让本地 Windows 与 CI 均能立即回收测试服务。
  await page.close();
});
