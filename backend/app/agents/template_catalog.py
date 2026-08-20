from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ProductTemplate:
    id: str
    name: str
    suitable_for: tuple[str, ...]
    layout: str
    components: tuple[str, ...]
    visual_rules: tuple[str, ...]

    def as_prompt_data(self) -> dict:
        return asdict(self)


TEMPLATES = (
    ProductTemplate(
        id="learning-focus",
        name="沉浸式学习产品",
        suitable_for=("教育", "训练", "语言学习", "知识工具"),
        layout="清晰学习路径、单一主任务、练习区与即时反馈并重",
        components=("学习进度", "练习卡片", "结果反馈", "连续学习记录", "空状态"),
        visual_rules=("低干扰背景", "高可读排版", "强调正向反馈", "移动端优先"),
    ),
    ProductTemplate(
        id="saas-workbench",
        name="专业 SaaS 工作台",
        suitable_for=("管理后台", "AI 工具", "协作平台", "企业软件"),
        layout="稳定侧栏、任务主区、上下文详情区和渐进式信息披露",
        components=("数据概览", "筛选器", "工作表格", "详情抽屉", "审计时间线"),
        visual_rules=("克制配色", "高密度但不拥挤", "状态语义统一", "键盘可操作"),
    ),
    ProductTemplate(
        id="editorial-showcase",
        name="编辑型内容展示",
        suitable_for=("作品集", "品牌官网", "内容社区", "产品落地页"),
        layout="强首屏叙事、编辑式网格、内容节奏和明确转化路径",
        components=("主视觉", "内容章节", "案例卡片", "社会证明", "行动区"),
        visual_rules=("大字号层级", "留白充足", "素材比例统一", "动效克制"),
    ),
    ProductTemplate(
        id="data-command",
        name="数据决策中心",
        suitable_for=("数据看板", "监控系统", "分析平台", "运营工具"),
        layout="核心指标优先、趋势与异常并列、详情按需展开",
        components=("指标卡", "趋势图", "异常列表", "筛选工具栏", "详情面板"),
        visual_rules=("数据墨水比优先", "颜色具有语义", "对比维度一致", "支持空数据"),
    ),
)


def template_catalog_payload() -> list[dict]:
    return [item.as_prompt_data() for item in TEMPLATES]


def get_template(template_id: str) -> ProductTemplate:
    return next(
        (item for item in TEMPLATES if item.id == template_id),
        TEMPLATES[0],
    )
