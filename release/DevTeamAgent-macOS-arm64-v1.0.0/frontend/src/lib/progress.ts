const INDETERMINATE_STEPS = new Set([
  "分析需求",
  "设计系统",
  "生成开发计划",
  "模型审查中",
  "生成测试计划",
]);

/** 模型接口不提供总量，模型生成阶段只能展示不定进度。 */
export function isIndeterminateProgressStep(step: string): boolean {
  return INDETERMINATE_STEPS.has(step);
}
