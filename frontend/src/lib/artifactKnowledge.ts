import type { Artifact } from "../api/types";

export type KnowledgeKind =
  | "需求"
  | "验收标准"
  | "用户故事"
  | "接口"
  | "技术术语"
  | "代码标识符"
  | "文件"
  | "接口地址";

export interface KnowledgeDetail {
  label: string;
  value: string;
}

export interface KnowledgeEntry {
  id: string;
  kind: KnowledgeKind;
  title: string;
  summary: string;
  details: KnowledgeDetail[];
  learningHint: string;
  source?: string;
}

export interface ArtifactKnowledge {
  entries: Record<string, KnowledgeEntry>;
}

const REFERENCE_PATTERN = /\b(?:FR|NFR|AC|US)-\d{3}\b/g;

const glossary: Record<string, Omit<KnowledgeEntry, "id">> = {
  Props: {
    kind: "技术术语",
    title: "Props（组件属性）",
    summary: "父组件传给子组件的数据或配置。子组件通过 Props 决定显示什么以及如何响应。",
    details: [
      { label: "通俗理解", value: "像调用函数时传入的参数，也像给一个可复用零件设置不同选项。" },
      { label: "常见用途", value: "传递文本、状态、事件处理函数和样式配置。" },
    ],
    learningHint: "阅读组件时，可以先查看它接收哪些 Props，再判断它依赖哪些外部数据。",
  },
  API: {
    kind: "技术术语",
    title: "API（应用程序接口）",
    summary: "不同软件模块之间约定好的通信方式，用来发送请求和返回数据。",
    details: [
      { label: "通俗理解", value: "类似餐厅菜单：调用方按菜单点单，服务方按约定返回结果。" },
      { label: "项目中的作用", value: "通常负责连接前端页面、后端业务逻辑和数据库。" },
    ],
    learningHint: "理解 API 时重点看四件事：地址、请求方法、输入参数和返回结果。",
  },
  REST: {
    kind: "技术术语",
    title: "REST 接口风格",
    summary: "一种围绕资源设计接口地址，并使用 HTTP 方法表达操作意图的常见方式。",
    details: [
      { label: "示例", value: "GET /tasks 表示查询任务，POST /tasks 表示创建任务。" },
      { label: "优点", value: "命名统一、容易理解，也便于前后端协作。" },
    ],
    learningHint: "看到 REST 接口时，可以把 URL 理解为对象，把 GET、POST、PUT、DELETE 理解为动作。",
  },
  RAG: {
    kind: "技术术语",
    title: "RAG（检索增强生成）",
    summary: "先从项目知识库中检索相关内容，再让大模型基于这些内容回答或生成代码。",
    details: [
      { label: "解决的问题", value: "减少模型不了解现有代码、凭空猜测或遗漏项目约束的情况。" },
      { label: "典型流程", value: "代码解析 → 切片 → 建立索引 → 检索 → 注入智能体上下文。" },
    ],
    learningHint: "RAG 不会训练新模型，它是在每次执行前为模型准备更可靠的参考资料。",
  },
  BM25: {
    kind: "技术术语",
    title: "BM25 关键词检索",
    summary: "根据关键词在文档中的出现情况计算相关度，适合查找函数名、类名和错误信息。",
    details: [
      { label: "优势", value: "对精确代码符号和专业名词非常敏感。" },
      { label: "项目中的作用", value: "通常与向量检索组合，形成混合检索。" },
    ],
    learningHint: "向量检索擅长找语义相似内容，BM25 擅长找字面精确匹配内容。",
  },
  WebSocket: {
    kind: "技术术语",
    title: "WebSocket",
    summary: "浏览器与服务端保持长连接并双向实时传递消息的通信协议。",
    details: [{ label: "适用场景", value: "实时进度、聊天消息、协作状态和服务器主动通知。" }],
    learningHint: "普通 HTTP 通常由浏览器主动请求；WebSocket 建立连接后，服务端也可以主动推送。",
  },
  React: {
    kind: "技术术语",
    title: "React",
    summary: "用于构建网页界面的组件化 JavaScript 库。",
    details: [{ label: "核心思想", value: "把页面拆成可复用组件，并让界面随状态变化自动更新。" }],
    learningHint: "学习 React 可以从组件、Props、State 和事件处理四个概念开始。",
  },
  FastAPI: {
    kind: "技术术语",
    title: "FastAPI",
    summary: "基于 Python 的现代 Web API 框架，强调类型提示、自动校验和接口文档。",
    details: [{ label: "项目中的作用", value: "接收前端请求，执行业务逻辑，并返回结构化数据。" }],
    learningHint: "可从路由、请求模型、依赖注入和响应模型四部分理解 FastAPI 项目。",
  },
  SQLite: {
    kind: "技术术语",
    title: "SQLite",
    summary: "将数据库保存在单个本地文件中的轻量级关系型数据库。",
    details: [{ label: "适用场景", value: "本地开发、原型项目和单机部署。" }],
    learningHint: "它不需要单独启动数据库服务器，但多人并发和大型生产场景通常会选择 PostgreSQL。",
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value: unknown, fallback = "未提供"): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join("、") || fallback;
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function latestPrd(artifacts: Artifact[]): Artifact | undefined {
  return [...artifacts].reverse().find((item) => item.type === "PRD");
}

export function buildArtifactKnowledge(artifacts: Artifact[]): ArtifactKnowledge {
  const entries: Record<string, KnowledgeEntry> = {};
  const prd = latestPrd(artifacts);
  const content = prd?.content ?? {};

  const requirements = Array.isArray(content.requirements) ? content.requirements.filter(isRecord) : [];
  for (const requirement of requirements) {
    const id = stringValue(requirement.id);
    if (!/^(?:FR|NFR)-\d{3}$/.test(id)) continue;
    entries[id] = {
      id,
      kind: "需求",
      title: `${id} · ${id.startsWith("NFR") ? "非功能需求" : "功能需求"}`,
      summary: stringValue(requirement.description),
      details: [
        { label: "优先级", value: stringValue(requirement.priority) },
        {
          label: "需求类型",
          value: id.startsWith("NFR")
            ? "描述性能、安全性、可用性等质量要求"
            : "描述用户能够使用的具体功能",
        },
      ],
      learningHint: "架构、代码和测试中的相同编号都指向这条需求，用它可以追踪需求最终是如何实现和验证的。",
      source: `需求文档 · 版本 ${prd?.version ?? 1}`,
    };
  }

  const criteria = Array.isArray(content.acceptance_criteria)
    ? content.acceptance_criteria.filter(isRecord)
    : [];
  for (const criterion of criteria) {
    const id = stringValue(criterion.id);
    if (!/^AC-\d{3}$/.test(id)) continue;
    const linked = Array.isArray(criterion.requirement_ids)
      ? criterion.requirement_ids.map(String)
      : [];
    entries[id] = {
      id,
      kind: "验收标准",
      title: `${id} · 验收标准`,
      summary: `${stringValue(criterion.condition)}，应当：${stringValue(criterion.expected_result)}`,
      details: [
        { label: "触发条件", value: stringValue(criterion.condition) },
        { label: "预期结果", value: stringValue(criterion.expected_result) },
        { label: "关联需求", value: linked.join("、") || "未关联" },
      ],
      learningHint: "验收标准是判断功能是否真正完成的可验证条件，测试用例应当覆盖它。",
      source: `需求文档 · 版本 ${prd?.version ?? 1}`,
    };
  }

  const stories = Array.isArray(content.user_stories) ? content.user_stories.filter(isRecord) : [];
  for (const story of stories) {
    const id = stringValue(story.id);
    if (!/^US-\d{3}$/.test(id)) continue;
    entries[id] = {
      id,
      kind: "用户故事",
      title: `${id} · 用户故事`,
      summary: `作为${stringValue(story.role)}，希望${stringValue(story.goal)}，从而${stringValue(story.benefit)}。`,
      details: [
        { label: "用户角色", value: stringValue(story.role) },
        { label: "用户目标", value: stringValue(story.goal) },
        { label: "用户收益", value: stringValue(story.benefit) },
      ],
      learningHint: "用户故事从使用者视角解释为什么要做这个功能，它不是具体的技术实现方案。",
      source: `需求文档 · 版本 ${prd?.version ?? 1}`,
    };
  }

  return { entries };
}

export function referenceIds(value: string): string[] {
  return [...new Set(value.match(REFERENCE_PATTERN) ?? [])];
}

export function explainReference(id: string, knowledge: ArtifactKnowledge): KnowledgeEntry | null {
  if (knowledge.entries[id]) return knowledge.entries[id];
  if (!/^(?:FR|NFR|AC|US)-\d{3}$/.test(id)) return null;
  return {
    id,
    kind: id.startsWith("AC") ? "验收标准" : id.startsWith("US") ? "用户故事" : "需求",
    title: `${id} · 未找到对应详情`,
    summary: "最新版本的需求文档中没有找到这个编号，它可能来自旧版本，或产物之间的引用尚未同步。",
    details: [
      { label: "当前状态", value: "引用存在，但对应定义缺失" },
      { label: "建议检查", value: "查看最新需求文档的需求列表和验收标准，确认编号是否已经修改或删除" },
    ],
    learningHint: "这属于需求追踪异常。不要根据编号猜测含义，应以最新需求文档中的正式定义为准。",
  };
}

function identifierExplanation(value: string, fieldKey?: string): KnowledgeEntry | null {
  const propsMatch = value.match(/^Props?\s*:\s*([A-Za-z_$][\w$]*)$/i);
  if (propsMatch) {
    const name = propsMatch[1];
    return {
      id: `interface:${value}`,
      kind: "接口",
      title: `组件属性：${name}`,
      summary: `这是父组件传给子组件的一个属性。${name} 通常用于${/^active/i.test(name) ? "表示当前处于激活或选中状态的对象" : "向组件传入显示内容、状态或行为配置"}。`,
      details: [
        { label: "接口形式", value: "React 组件 Props" },
        { label: "属性名称", value: name },
        { label: "数据方向", value: "父组件 → 子组件" },
        { label: "实际类型", value: "当前产物没有声明类型，需要在组件类型定义或调用位置确认" },
      ],
      learningHint: `在代码中搜索“${name}”，查看它在哪里被传入、在哪里被读取，就能确认它的真实类型和作用。`,
    };
  }

  const httpMatch = value.match(/\b(GET|POST|PUT|PATCH|DELETE)\s+(\/\S+)/i);
  if (httpMatch) {
    return {
      id: `http:${value}`,
      kind: "接口地址",
      title: `${httpMatch[1].toUpperCase()} ${httpMatch[2]}`,
      summary: `这是一个 HTTP 接口：${httpMatch[1].toUpperCase()} 表示操作方式，${httpMatch[2]} 是访问路径。`,
      details: [
        { label: "请求方法", value: httpMatch[1].toUpperCase() },
        { label: "接口路径", value: httpMatch[2] },
        { label: "具体输入输出", value: "当前简略产物未完整声明，需要查看接口模型或后端路由代码" },
      ],
      learningHint: "继续查看请求参数、响应结构、错误状态和权限要求，才能完整理解一个接口。",
    };
  }

  if (/^(?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z0-9]+$/.test(value) || /^[\w.-]+\.(?:tsx?|jsx?|py|html|css|json|md)$/.test(value)) {
    return {
      id: `file:${value}`,
      kind: "文件",
      title: `项目文件：${value}`,
      summary: "这是项目中的文件路径。文件名和扩展名通常能帮助判断它负责的技术层次。",
      details: [
        { label: "文件路径", value },
        { label: "建议操作", value: "结合“项目结构”和代码搜索查看它由谁调用、又依赖哪些文件" },
      ],
      learningHint: "理解项目时不要逐行通读所有文件，先从入口文件和核心模块之间的调用关系开始。",
    };
  }

  if (
    fieldKey === "interfaces"
    || /^[a-z_$][A-Za-z0-9_$]*$/.test(value)
    || /^[A-Z][A-Za-z0-9]+(?:Service|Controller|Repository|Component|Provider|Manager|Agent)$/.test(value)
  ) {
    return {
      id: `identifier:${value}`,
      kind: fieldKey === "interfaces" ? "接口" : "代码标识符",
      title: value,
      summary: fieldKey === "interfaces"
        ? "这是组件向其他模块提供或依赖的交互边界。接口用于降低模块之间的直接耦合。"
        : "这是代码中的命名标识符，可能代表变量、函数、类、组件或服务。",
      details: [
        { label: "当前可确认的信息", value: `产物中仅提供了名称“${value}”，没有完整声明参数、返回值和调用位置。` },
        { label: "如何确认", value: `在项目代码中搜索“${value}”，优先查看定义位置和所有调用位置。` },
      ],
      learningHint: "系统会明确区分“产物中已确认的信息”和“根据命名推测的信息”，避免把推测当成事实。",
    };
  }
  return null;
}

export function explainTechnicalValue(
  value: string,
  fieldKey: string | undefined,
  knowledge: ArtifactKnowledge,
): KnowledgeEntry | null {
  const exactReference = knowledge.entries[value];
  if (exactReference) return exactReference;
  const identifier = identifierExplanation(value.trim(), fieldKey);
  if (identifier) return identifier;
  const exactGlossary = glossary[value.trim()];
  if (exactGlossary) return { id: `term:${value.trim()}`, ...exactGlossary };
  const foundTerm = Object.keys(glossary).find((term) => new RegExp(`\\b${term}\\b`, "i").test(value));
  return foundTerm ? { id: `term:${foundTerm}`, ...glossary[foundTerm] } : null;
}
