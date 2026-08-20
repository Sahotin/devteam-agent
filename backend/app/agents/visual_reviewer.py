from __future__ import annotations

import base64
from dataclasses import dataclass, field
import html
import json
from pathlib import Path
import os
import re
import shutil
import socket
import subprocess
import sys
import time

from backend.app.domain.artifacts import (
    CodeChangeArtifact,
    UIUXArtifact,
    VisualReportArtifact,
    VisualScore,
)
from backend.app.execution.progress import ProgressReporter, report_progress


VISUAL_PASS_SCORE = 75
FRONTEND_SUFFIXES = {".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}


@dataclass(slots=True)
class InteractionProbeResult:
    attempted: bool = False
    target: str = ""
    found: bool = False
    changed: bool = False
    errors: list[str] = field(default_factory=list)
    limitation: str = ""

    @property
    def passed(self) -> bool:
        return self.attempted and self.found and self.changed and not self.errors


class VisualReviewerAgent:
    """基于真实源码和可用截图执行确定性的产品体验质量门禁。"""

    name = "visual-reviewer-agent"

    async def run(
        self,
        *,
        task_id: str,
        workspace_root: str,
        ui_design: UIUXArtifact,
        code_change: CodeChangeArtifact,
        interaction_report: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> VisualReportArtifact:
        root = Path(workspace_root).resolve()
        report_progress(progress, 86, "执行视觉质量检查", "正在检查设计落实与响应式规则")
        sources: list[str] = []
        frontend_paths: list[str] = []
        # 视觉门禁评估的是当前完整产品，而不是最后一次增量修复的文件集合。
        # 只扫描 code_change 会把“本轮没改 CSS/页面”误判成“仓库没有 CSS/页面”。
        excluded_parts = {
            "node_modules",
            "dist",
            "coverage",
            ".git",
            ".devteam",
        }
        candidates = (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in FRONTEND_SUFFIXES
            and not any(part.casefold() in excluded_parts for part in path.parts)
        )
        total_chars = 0
        for path in candidates:
            try:
                path.relative_to(root)
            except ValueError:
                continue
            relative_path = path.relative_to(root).as_posix()
            frontend_paths.append(relative_path)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:80_000]
            except OSError:
                continue
            sources.append(content)
            total_chars += len(content)
            if total_chars >= 1_000_000:
                break

        if not sources and not any(
            (root / candidate).is_file()
            for candidate in ("index.html", "public/index.html")
        ):
            return VisualReportArtifact(
                verdict="NOT_RUNNABLE",
                overall_score=0,
                scores=VisualScore(
                    hierarchy=0,
                    typography=0,
                    spacing=0,
                    consistency=0,
                    responsiveness=0,
                    interaction_feedback=0,
                    content_quality=0,
                    accessibility=0,
                ),
                summary="当前变更未识别到可自动检查的前端界面，未执行视觉评分。",
                limitations=["该任务可能不是界面项目，或界面文件不在当前代码变更中。"],
            )

        combined = "\n".join(sources)
        lowered = combined.lower()
        checks = {
            "使用统一设计令牌": "--" in combined or "theme" in lowered,
            "包含响应式规则": "@media" in lowered or "clamp(" in lowered,
            "包含完整页面状态": sum(
                marker in lowered
                for marker in ("loading", "error", "empty", "success", "加载", "错误", "空状态")
            ) >= 3,
            "包含交互反馈": sum(
                marker in lowered
                for marker in (":hover", ":focus", ":disabled", "aria-live", "role=\"alert")
            ) >= 2,
            "内容不是占位文本": not any(
                marker in lowered for marker in ("lorem ipsum", "todo:", "placeholder content")
            ),
            "具有语义化页面结构": any(
                marker in lowered for marker in ("<main", "<header", "<nav", "<section")
            ),
            "具有无障碍基础": any(
                marker in lowered for marker in ("aria-", "alt=", "label", ":focus-visible")
            ),
        }
        screenshots, screenshot_limitations = self._capture_screenshots(root, task_id)
        checks["已生成真实浏览器截图"] = bool(screenshots)
        interaction_probe = (
            self.probe_reported_interaction(root, task_id, interaction_report)
            if interaction_report
            else InteractionProbeResult()
        )
        if interaction_probe.attempted:
            checks[
                f"已验证交互：{interaction_probe.target}"
            ] = interaction_probe.passed
        if interaction_probe.limitation:
            screenshot_limitations.append(interaction_probe.limitation)

        scores = VisualScore(
            hierarchy=100 if checks["具有语义化页面结构"] else 55,
            typography=self._typography_score(combined),
            spacing=90 if checks["使用统一设计令牌"] else 55,
            consistency=95 if checks["使用统一设计令牌"] else 60,
            responsiveness=100 if checks["包含响应式规则"] else 45,
            interaction_feedback=100 if checks["包含交互反馈"] else 45,
            content_quality=95 if checks["内容不是占位文本"] else 35,
            accessibility=90 if checks["具有无障碍基础"] else 45,
        )
        score_values = list(scores.model_dump().values())
        overall = round(sum(score_values) / len(score_values))
        findings = [name for name, passed in checks.items() if not passed]
        recommendations = [
            self._recommendation(item, ui_design)
            for item in findings
            if item != "已生成真实浏览器截图"
        ]
        render_failed = any(
            marker in limitation
            for limitation in screenshot_limitations
            for marker in (
                "没有渲染出有效页面内容",
                "生产构建目录中没有找到",
                "静态页面入口不存在",
            )
        )
        if render_failed:
            findings.append("生产页面无法完成真实渲染")
            recommendations.append(
                "修复生产构建入口或浏览器运行时错误，确保构建产物能够加载并渲染出有效页面。"
            )
        interaction_failed = interaction_probe.attempted and not interaction_probe.passed
        if interaction_failed:
            target = interaction_probe.target or "用户报告的操作"
            if not interaction_probe.found:
                findings.append(f"真实浏览器中没有找到交互目标“{target}”")
                recommendations.append(
                    f"检查“{target}”对应元素是否实际渲染，并确保按钮文案、角色或可访问名称与需求一致。"
                )
            elif interaction_probe.errors:
                errors = "；".join(interaction_probe.errors[:3])
                findings.append(f"执行“{target}”时发生浏览器运行错误：{errors}")
                recommendations.append(
                    f"先修复“{target}”触发的运行时错误，再用真实浏览器点击并确认页面状态或画布内容发生变化。"
                )
            elif not interaction_probe.changed:
                findings.append(f"点击“{target}”后页面状态和画布内容均未变化")
                recommendations.append(
                    f"补齐“{target}”的事件绑定与状态更新，并增加覆盖该用户操作的回归测试。"
                )
        verdict = (
            "PASSED"
            if overall >= VISUAL_PASS_SCORE
            and len(recommendations) <= 2
            and not render_failed
            and not interaction_failed
            else "CHANGES_REQUESTED"
        )
        return VisualReportArtifact(
            verdict=verdict,
            overall_score=overall,
            scores=scores,
            summary=(
                f"视觉质量评分 {overall}/100，"
                + ("达到交付门槛。" if verdict == "PASSED" else "需要继续完善后再交付。")
            ),
            screenshots=screenshots,
            findings=findings,
            recommendations=recommendations,
            automated_checks=checks,
            limitations=screenshot_limitations,
        )

    @classmethod
    def probe_reported_interaction(
        cls,
        root: Path,
        task_id: str,
        report: str,
    ) -> InteractionProbeResult:
        """在隔离的真实浏览器页面中重放用户明确报告的按钮交互。"""
        target = cls._interaction_target(report)
        if not target:
            return InteractionProbeResult(
                limitation="未能从用户反馈中识别可自动点击的按钮名称，交互验证未执行。"
            )
        browser = cls._find_browser()
        if not browser:
            return InteractionProbeResult(
                attempted=True,
                target=target,
                limitation="当前电脑没有找到 Chrome 或 Edge，无法执行真实浏览器交互验证。",
            )
        serve_root, entry, limitation = cls._resolve_serve_root(root)
        if limitation:
            return InteractionProbeResult(
                attempted=True,
                target=target,
                limitation=limitation,
            )

        output_dir = serve_root / ".devteam" / "visual" / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        probe_file = output_dir / "interaction-probe.html"
        try:
            source = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return InteractionProbeResult(
                attempted=True,
                target=target,
                limitation=f"无法读取页面入口，交互验证未执行：{error}",
            )

        base_path = "/"
        try:
            relative_parent = entry.parent.relative_to(serve_root).as_posix().strip("/")
            if relative_parent:
                base_path = f"/{relative_parent}/"
        except ValueError:
            pass
        head_probe = f"""
<base href={json.dumps(base_path)}>
<script>
window.__devteamProbeErrors = [];
window.addEventListener('error', function (event) {{
  if (event.target && event.target !== window && event.target.tagName !== 'SCRIPT') return;
  var source = event.filename || (event.target && (event.target.src || event.target.href)) || '';
  window.__devteamProbeErrors.push([event.message || '资源加载失败', source, event.lineno || ''].filter(Boolean).join(' @ '));
}}, true);
window.addEventListener('unhandledrejection', function (event) {{
  window.__devteamProbeErrors.push('未处理的异步错误：' + String(event.reason || '未知原因'));
}});
</script>
"""
        body_probe = f"""
<script>
(async function () {{
  var targetText = {json.dumps(target, ensure_ascii=False)};
  var normalize = function (value) {{ return String(value || '').replace(/\\s+/g, '').toLowerCase(); }};
  var canvasHash = function () {{
    var result = [];
    document.querySelectorAll('canvas').forEach(function (canvas) {{
      try {{
        var data = canvas.toDataURL();
        var hash = 0;
        for (var index = 0; index < data.length; index += 97) hash = ((hash << 5) - hash + data.charCodeAt(index)) | 0;
        result.push([canvas.width, canvas.height, data.length, hash]);
      }} catch (error) {{ result.push(['不可读取', String(error)]); }}
    }});
    return result;
  }};
  var snapshot = function () {{
    var activeViews = Array.from(document.querySelectorAll('section, dialog, [role="dialog"], .screen, .page, .view'))
      .filter(function (element) {{
        var style = getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
      }})
      .map(function (element) {{ return [element.id, element.className]; }});
    var feedback = Array.from(document.querySelectorAll('[role="status"], [role="alert"], [aria-live]'))
      .filter(function (element) {{ return getComputedStyle(element).display !== 'none'; }})
      .map(function (element) {{ return (element.innerText || element.textContent || '').trim(); }});
    return {{ url: location.href, activeViews: activeViews, canvas: canvasHash(), feedback: feedback }};
  }};
  // 等待样式、字体和应用初始化稳定，避免把页面加载过程误判成交互成功。
  await new Promise(function (resolve) {{ setTimeout(resolve, 750); }});
  var expected = normalize(targetText);
  var candidates = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]'));
  var target = candidates.find(function (element) {{
    var label = normalize(element.innerText || element.value || element.getAttribute('aria-label'));
    return label === expected || label.includes(expected) || expected.includes(label);
  }});
  var before = snapshot();
  if (target) target.click();
  await new Promise(function (resolve) {{ setTimeout(resolve, 250); }});
  var afterImmediate = snapshot();
  await new Promise(function (resolve) {{ setTimeout(resolve, 650); }});
  var after = snapshot();
  var viewChanged = JSON.stringify(before.activeViews) !== JSON.stringify(after.activeViews);
  var canvasChanged = JSON.stringify(before.canvas) !== JSON.stringify(afterImmediate.canvas)
    || JSON.stringify(afterImmediate.canvas) !== JSON.stringify(after.canvas);
  var feedbackChanged = JSON.stringify(before.feedback) !== JSON.stringify(after.feedback);
  var urlChanged = before.url !== after.url;
  var result = {{
    target: targetText,
    found: Boolean(target),
    changed: viewChanged || canvasChanged || feedbackChanged || urlChanged,
    errors: window.__devteamProbeErrors || [],
    before: before,
    afterImmediate: afterImmediate,
    after: after
  }};
  var encoded = btoa(unescape(encodeURIComponent(JSON.stringify(result))));
  document.documentElement.setAttribute('data-devteam-interaction-probe', encoded);
}})();
</script>
"""
        prepared = source
        prepared = (
            prepared.replace("</head>", head_probe + "</head>", 1)
            if "</head>" in prepared
            else head_probe + prepared
        )
        prepared = (
            prepared.replace("</body>", body_probe + "</body>", 1)
            if "</body>" in prepared
            else prepared + body_probe
        )
        probe_file.write_text(prepared, encoding="utf-8")

        port = cls._free_port()
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=serve_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        try:
            time.sleep(0.4)
            relative_probe = probe_file.relative_to(serve_root).as_posix()
            result = subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--virtual-time-budget=2500",
                    "--dump-dom",
                    f"http://127.0.0.1:{port}/{relative_probe}",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=35,
                shell=False,
            )
            match = re.search(
                r'data-devteam-interaction-probe="([^"]+)"', result.stdout
            )
            if result.returncode != 0 or not match:
                return InteractionProbeResult(
                    attempted=True,
                    target=target,
                    limitation="真实浏览器已启动，但没有返回交互探针结果。",
                )
            decoded = base64.b64decode(html.unescape(match.group(1))).decode("utf-8")
            payload = json.loads(decoded)
            return InteractionProbeResult(
                attempted=True,
                target=target,
                found=bool(payload.get("found")),
                changed=bool(payload.get("changed")),
                errors=[str(item) for item in payload.get("errors", []) if item],
            )
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            return InteractionProbeResult(
                attempted=True,
                target=target,
                limitation=f"真实浏览器交互验证失败：{error}",
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    @staticmethod
    def _interaction_target(report: str) -> str:
        quoted = re.findall(r"[“\"「『']([^”\"」』']{1,40})[”\"」』']", report)
        for item in quoted:
            value = item.strip()
            if value and any(word in report for word in ("点击", "按下", "按钮", "选择")):
                return value
        match = re.search(
            r"(?:点击|按下|选择)\s*([^，。；：,.!?！？]{1,24}?)(?:按钮|后|没有|无响应|没反应)",
            report,
        )
        return match.group(1).strip(" ‘'\"“”") if match else ""

    @staticmethod
    def _resolve_serve_root(root: Path) -> tuple[Path, Path, str]:
        if (root / "package.json").is_file():
            for candidate in ("dist", "build"):
                entry = root / candidate / "index.html"
                if entry.is_file():
                    return entry.parent, entry, ""
            return root, root / "index.html", (
                "检测到前端工程，但没有找到 dist/index.html 或 build/index.html，无法执行交互验证。"
            )
        entry = root / "index.html"
        if not entry.is_file():
            return root, entry, "项目没有可直接预览的 index.html，无法执行交互验证。"
        return root, entry, ""

    @staticmethod
    def _typography_score(content: str) -> int:
        sizes = [int(value) for value in re.findall(r"font-size\s*:\s*(\d+)px", content)]
        if not sizes:
            return 55
        if max(sizes) >= 32 and any(size >= 15 for size in sizes):
            return 95
        return 65

    @staticmethod
    def _recommendation(finding: str, ui_design: UIUXArtifact) -> str:
        mapping = {
            "使用统一设计令牌": "建立 CSS Design Token，并让颜色、间距、圆角和阴影统一引用。",
            "包含响应式规则": "补充桌面、平板和手机断点，确保无横向溢出且主操作始终可见。",
            "包含完整页面状态": "补齐加载、空白、错误和成功反馈，不得只实现正常状态。",
            "包含交互反馈": "为按钮、表单和异步操作补充悬停、焦点、禁用和提交反馈。",
            "内容不是占位文本": "使用与业务场景一致的真实示例内容替换占位文本。",
            "具有语义化页面结构": "使用 main、nav、header、section 等语义结构建立清晰信息层级。",
            "具有无障碍基础": "补充可读标签、替代文本、键盘焦点和非颜色状态提示。",
        }
        return mapping.get(
            finding,
            f"按照设计质量标准修复：{ui_design.quality_criteria[0]}",
        )

    @classmethod
    def _capture_screenshots(
        cls,
        root: Path,
        task_id: str,
    ) -> tuple[list[str], list[str]]:
        browser = cls._find_browser()
        if not browser:
            return [], ["当前电脑未找到可用于自动截图的 Chrome 或 Edge。"]
        serve_root = root
        if (root / "package.json").is_file():
            for candidate in ("dist", "build"):
                built_root = root / candidate
                if (built_root / "index.html").is_file():
                    serve_root = built_root
                    break
            else:
                return [], [
                    "检测到前端工程，但没有找到 dist/index.html 或 build/index.html；"
                    "需要先完成生产构建才能执行真实页面截图。"
                ]
        elif not (root / "index.html").is_file():
            return [], ["项目没有可直接预览的 index.html，未生成浏览器截图。"]

        output_dir = root / ".devteam" / "visual" / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        port = cls._free_port()
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(port),
                "--bind",
                "127.0.0.1",
            ],
            cwd=serve_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        screenshots: list[str] = []
        try:
            time.sleep(0.5)
            dom_probe = subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--virtual-time-budget=3000",
                    "--dump-dom",
                    f"http://127.0.0.1:{port}/",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                shell=False,
            )
            rendered_dom = dom_probe.stdout.casefold()
            if (
                dom_probe.returncode != 0
                or "<body" not in rendered_dom
                or len(rendered_dom) < 300
            ):
                return [], ["生产构建可以启动，但浏览器没有渲染出有效页面内容。"]
            for name, width, height in (
                ("desktop", 1440, 1000),
                ("mobile", 390, 844),
            ):
                target = output_dir / f"{name}.png"
                result = subprocess.run(
                    [
                        browser,
                        "--headless=new",
                        "--disable-gpu",
                        "--hide-scrollbars",
                        f"--window-size={width},{height}",
                        f"--screenshot={target}",
                        f"http://127.0.0.1:{port}/",
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=30,
                    shell=False,
                )
                if result.returncode == 0 and target.is_file():
                    screenshots.append(str(target.relative_to(root)).replace("\\", "/"))
        except (OSError, subprocess.SubprocessError):
            return screenshots, ["浏览器截图执行失败，已保留源码质量检查结果。"]
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        return screenshots, ([] if screenshots else ["浏览器没有生成有效截图。"])

    @staticmethod
    def _find_browser() -> str | None:
        resolved = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("msedge")
        if resolved:
            return resolved
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
        return next((str(path) for path in candidates if path.is_file()), None)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])
