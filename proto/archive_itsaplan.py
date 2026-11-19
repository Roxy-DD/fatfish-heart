"""itsaplan 全量存档 —— 7 个项目的工作项/任务/状态/关联信息

用途：虚拟机 + WSL 环境完全重置前，把平台数据扒全落盘，供迁移用。

抓取内容（每个项目）：
  1. 项目配置：列（状态）、issue 类型、标签、自定义字段定义
  2. 倡议 initiatives
  3. 周期 cycles
  4. 全部 issue（含已归档），每个 issue 的完整详情：
     - 标题 / 描述 / 状态 / 优先级 / 截止日 / 估算 / 耗时
     - assignee / delegate
     - 父任务 / 子任务 / 标签 / 自定义字段值 / checklist
  5. 每个 issue 的链接关系（links）

输出：
  archive/itsaplan-archive/
    <KEY>_<name>.json     —— 完整原始数据（迁移用）
    <KEY>_<name>.md       —— 人类可读汇总（查阅用）

运行：
  python archive_itsaplan.py
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from itsaplan_call import mcp  # noqa: E402

OUT = os.path.join(HERE, "archive", "itsaplan-archive")

# 7 个项目 key（按 list_projects 顺序）
PROJECTS = [
    "DAILY", "DRAW", "FISH", "FISHNOTE", "GAME", "RUST", "WHISTLE",
]


def safe(fn, *args, **kwargs):
    """容错调用：带重试（401/限流时退避重试）"""
    import urllib.error
    retries = kwargs.pop("_retries", 3)
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt < retries - 1:
                # 401 可能是限流，退避重试
                wait = (attempt + 1) * 5
                print(f"    ⚠ 401 限流，{wait}s 后重试 ({attempt+1}/{retries-1})")
                time.sleep(wait)
                continue
            print(f"    ⚠ {fn.__name__} 失败: HTTP {e.code}")
            return None
        except Exception as e:
            print(f"    ⚠ {fn.__name__} 失败: {type(e).__name__}: {e}")
            return None
    return None


def slug(name):
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name).strip("-")
    return s or name


def fetch_project(project_key):
    print(f"\n{'='*66}\n抓取 {project_key}\n{'='*66}")

    # 项目配置
    print("  [1/5] 项目配置")
    proj = safe(mcp, "get_project", {"projectKey": project_key})
    if proj is None:
        print("    ❌ 拿不到项目配置，跳过")
        return None
    name = proj.get("name", project_key)
    print(f"    name={name}, columns={len(proj.get('columns', []))}, "
          f"types={len(proj.get('issueTypes', []))}, "
          f"labels={len(proj.get('labels', []))}")

    # 倡议（返回 {items:[...]} 或直接 list）
    print("  [2/5] 倡议")
    initiatives_raw = safe(mcp, "list_initiatives", {"projectKey": project_key}) or []
    if isinstance(initiatives_raw, dict):
        initiatives = initiatives_raw.get("items", [])
    else:
        initiatives = initiatives_raw
    print(f"    {len(initiatives)} 个")

    # 周期
    print("  [3/5] 周期")
    cycles_raw = safe(mcp, "list_cycles", {"projectKey": project_key}) or []
    if isinstance(cycles_raw, dict):
        cycles = cycles_raw.get("items", [])
    else:
        cycles = cycles_raw
    print(f"    {len(cycles)} 个")

    # issue 列表（含归档）
    print("  [4/5] issue 列表")
    issues = safe(mcp, "list_issues",
                  {"projectKey": project_key, "limit": 500,
                   "includeArchived": "true"}) or []
    print(f"    {len(issues)} 个 issue")

    # 逐个拿完整详情
    print("  [5/5] issue 完整详情")
    full_issues = []
    for i, it in enumerate(issues):
        iid = it.get("id")
        detail = safe(mcp, "get_issue", {"issueId": iid})
        time.sleep(0.3)
        if detail:
            full_issues.append(detail)
        else:
            full_issues.append(it)  # 拿不到详情就留列表项
        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(issues)} ...")

    return {
        "project": proj,
        "initiatives": initiatives,
        "cycles": cycles,
        "issues": full_issues,
        "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "archiver": "大肥鱼 (big_fat_fish)",
    }


def state_name(proj, column_id):
    for c in proj.get("columns", []):
        if c.get("id") == column_id:
            return c.get("name", f"列{column_id}")
    return f"列{column_id}"


def assignee_name(proj, uid):
    if not uid:
        return "-"
    for a in proj.get("assignees", []):
        if a.get("userId") == uid:
            return a.get("name") or a.get("handle") or uid
    return uid


def type_name(proj, tid):
    for t in proj.get("issueTypes", []):
        if t.get("id") == tid:
            return t.get("name", f"类型{tid}")
    return f"类型{tid}"


def label_names(proj, ids):
    if not ids:
        return []
    m = {l.get("id"): l.get("name", "") for l in proj.get("labels", [])}
    return [m.get(i, str(i)) for i in ids]


def write_markdown(data, path):
    proj = data["project"]
    # ★ 修正：get_project 返回 {project:{...}, columns,...}，
    #   项目名/key 在 proj["project"] 里，不是 proj 顶层。
    inner = proj.get("project", proj)
    name = inner.get("name", "?")
    key = inner.get("key", "?")
    L = []
    L.append(f"# {name}（{key}）")
    L.append("")
    L.append(f"存档时间：{data['archived_at']}")
    L.append(f"存档人：{data['archiver']}")
    L.append("")
    L.append(proj.get("description") or "")
    L.append("")

    # 倡议
    L.append("## 倡议（Initiatives）")
    if data["initiatives"]:
        for ini in data["initiatives"]:
            L.append(f"- **{ini.get('title','?')}** "
                     f"[{ini.get('status','?')}]"
                     f"{('：' + ini['description']) if ini.get('description') else ''}")
    else:
        L.append("- （无）")
    L.append("")

    # 周期
    L.append("## 周期（Cycles）")
    if data["cycles"]:
        for cy in data["cycles"]:
            L.append(f"- **{cy.get('name','?')}** "
                     f"[{cy.get('status','?')}] "
                     f"{cy.get('startDate','')} ~ {cy.get('endDate','')}"
                     f"{('：' + cy['goal']) if cy.get('goal') else ''}")
    else:
        L.append("- （无）")
    L.append("")

    # 任务
    L.append("## 工作项 / 任务")
    L.append("")
    issues = data["issues"]
    if not issues:
        L.append("（无）")
    for it in issues:
        ident = it.get("identifier", str(it.get("sequenceNumber", "?")))
        title = it.get("title", "?")
        st = state_name(proj, it.get("columnId"))
        prio = it.get("priority") or "-"
        assignee = assignee_name(proj, it.get("assigneeUserId"))
        delegate = assignee_name(proj, it.get("delegateUserId"))
        tname = type_name(proj, it.get("typeId"))
        labels = label_names(proj, it.get("labelIds"))
        due = it.get("dueDate") or "-"
        archived = " [已归档]" if it.get("archived") else ""

        L.append(f"### {ident} · {title}{archived}")
        L.append("")
        L.append(f"- 状态：**{st}**")
        L.append(f"- 类型：{tname}")
        L.append(f"- 优先级：{prio}")
        L.append(f"- 负责人：{assignee}")
        L.append(f"- 委托 AI：{delegate}")
        L.append(f"- 截止日：{due}")
        if it.get("estimatePoints") is not None:
            L.append(f"- 估算点数：{it['estimatePoints']}")
        if it.get("estimateMinutes"):
            L.append(f"- 估算分钟：{it['estimateMinutes']}")
        if it.get("loggedMinutes"):
            L.append(f"- 已记耗时：{it['loggedMinutes']} 分钟")
        if labels:
            L.append(f"- 标签：{', '.join(labels)}")
        if it.get("startDate"):
            L.append(f"- 开始日：{it['startDate']}")
        if it.get("completedAt"):
            L.append(f"- 完成时间：{it['completedAt']}")

        # 父任务
        parent = it.get("parent")
        if parent:
            L.append(f"- 父任务：{parent.get('identifier','?')} {parent.get('title','')}")

        # 子任务
        subs = it.get("subtasks") or []
        if subs:
            L.append(f"- 子任务（{len(subs)}）：")
            for s in subs:
                L.append(f"    - {s.get('identifier','?')} {s.get('title','')}")

        # 描述
        desc = it.get("description")
        if desc:
            L.append("")
            L.append("**任务内容/描述：**")
            L.append("")
            L.append(desc)
            L.append("")

        # checklist
        checks = it.get("checklists") or []
        for ck in checks:
            L.append(f"**清单：{ck.get('title','')}**")
            items = ck.get("items") or []
            for ci in items:
                mark = "x" if ci.get("done") else " "
                L.append(f"- [{mark}] {ci.get('content','')}")
            L.append("")

        # 链接关系
        links = it.get("links") or []
        if links:
            L.append("**关联关系：**")
            for lk in links:
                L.append(f"- {lk.get('kind','?')} → {lk.get('targetIdentifier','?')}")
            L.append("")

        # 自定义字段值
        fields = it.get("fieldValues") or it.get("fields") or []
        if fields:
            L.append("**自定义字段：**")
            if isinstance(fields, dict):
                for k, v in fields.items():
                    L.append(f"- {k}: {v}")
            else:
                for f in fields:
                    L.append(f"- {f}")
            L.append("")

        L.append("---")
        L.append("")

    return "\n".join(L)


def main():
    os.makedirs(OUT, exist_ok=True)
    print(f"存档目录：{OUT}")

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只抓取某个项目 key")
    args = ap.parse_args()
    keys = [args.only] if args.only else PROJECTS

    summary = []
    for key in keys:
        data = fetch_project(key)
        if data is None:
            continue

        name = data["project"].get("name", key)
        fname = f"{key}_{slug(name)}"

        # JSON
        jp = os.path.join(OUT, fname + ".json")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        # Markdown
        mp = os.path.join(OUT, fname + ".md")
        with open(mp, "w", encoding="utf-8") as f:
            f.write(write_markdown(data, mp))

        n_issues = len(data["issues"])
        n_archived = sum(1 for i in data["issues"] if i.get("archived"))
        print(f"  ✅ {fname}  （{n_issues} issue，{n_archived} 归档）")
        summary.append({
            "key": key, "name": name,
            "n_initiatives": len(data["initiatives"]),
            "n_cycles": len(data["cycles"]),
            "n_issues": n_issues,
            "n_archived": n_archived,
        })

    # 总览
    sp = os.path.join(OUT, "_SUMMARY.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(f"\n{'='*66}")
    print("存档完成")
    print(f"{'='*66}")
    for s in summary:
        print(f"  {s['key']:<10} {s['name']:<12} "
              f"倡议{s['n_initiatives']} 周期{s['n_cycles']} "
              f"任务{s['n_issues']}（归档{s['n_archived']}）")
    print(f"\n  全部文件在：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
