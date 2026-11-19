"""OpenSSF Scorecard 本地扫描结果分析（FISH-28）

扫描时间：2026-11-19 08:21
命令：scorecard --local <repo> --format=json
得分：1.6 / 10

注意：--local 模式对 GitHub Actions workflow 识别有限制，
"no workflows found" 是误报（实际有 .github/workflows/ci.yml 和 release-market.yml）。
在线模式因无 token + GitHub 限流未能跑成。

═══════════════════════════════════════════════════════════
11 项检查分类
═══════════════════════════════════════════════════════════
"""
import json

# 本地扫描的真实结果
checks = [
    ("Binary-Artifacts", 0, "binaries present in source code",
     "误报", "6 个 PNG（架构图+截图）是合理资产，不是可执行二进制"),
    ("Dangerous-Workflow", -1, "no workflows found",
     "误报", "实际有 .github/workflows/ci.yml + release-market.yml，--local 识别不到"),
    ("Dependency-Update-Tool", 0, "no update tool detected",
     "真短板", "可加 Dependabot 配置（.github/dependabot.yml）"),
    ("Fuzzing", 0, "project is not fuzzed",
     "真短板", "Python 项目可做，但优先级低"),
    ("License", 9, "license file detected",
     "基本达标", "AGPL-3.0 LICENSE 存在"),
    ("Packaging", -1, "packaging workflow not detected",
     "不适用", "本项目是插件/库，不是独立发布包"),
    ("Pinned-Dependencies", 0, "dependency not pinned by hash",
     "部分真", "Python requirements 未 pin；git actions 部分未 pin"),
    ("SAST", 0, "no SAST tool detected",
     "真短板", "可加 CodeQL workflow"),
    ("Security-Policy", 10, "security policy file detected",
     "已达标", "本次新加的 SECURITY.md 生效"),
    ("Token-Permissions", -1, "No tokens found",
     "误报", "同 workflow 识别问题"),
    ("Vulnerabilities", 0, "49 existing vulnerabilities detected",
     "需核实", "可能来自 Python 依赖的历史 CVE，需看具体是哪些"),
]

print("=" * 78)
print("OpenSSF Scorecard 检查项分析（本地模式，得分 1.6/10）")
print("=" * 78)
print(f"\n{'检查项':<26}{'分':>4}  {'分类':<8} 说明")
print("-" * 78)
for name, score, reason, cls, note in checks:
    s = " N/A" if score == -1 else f"{score:>4}"
    print(f"{name:<26}{s}  {cls:<8} {note}")

real = [c for c in checks if c[3] == "真短板"]
false_pos = [c for c in checks if c[3] == "误报"]
print(f"\n真短板 {len(real)} 项：" + ", ".join(c[0] for c in real))
print(f"误报 {len(false_pos)} 项：" + ", ".join(c[0] for c in false_pos))
print("\n优先级建议：")
print("  1. Security-Policy ✅ 已完成（10分）")
print("  2. Dependency-Update-Tool → 加 dependabot.yml（简单）")
print("  3. SAST → 加 CodeQL workflow（简单，GitHub 原生）")
print("  4. Pinned-Dependencies → pin GitHub Actions 到 SHA（中等）")
print("  5. Vulnerabilities → 先核实 49 个 CVE 具体是什么")
print("  6. Fuzzing → 低优先级")
print("\n注意：1.6 分被误报严重压低，真实水平高于此。")
print("在线模式（有 token）才会准确识别 workflow 类检查。")
