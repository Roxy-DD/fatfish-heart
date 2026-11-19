"""用 GitHub API 从 main 新建分支 + 提交 README + 提 PR

git push 因 TLS 握手失败不可用，全程走 gh api。
"""
import base64
import json
import subprocess

FORK = "Roxy-DD/Awesome-Agent-Memory"
UPSTREAM = "TeleAI-UAGI/Awesome-Agent-Memory"
BRANCH = "add-fatfish-heart"
LOCAL = r"C:\Users\xuee\AppData\Local\Temp\awesome-agent-memory\README.md"


def gh_api(method, endpoint, body=None, allow_fail=False):
    args = ["gh", "api", endpoint, "-X", method]
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False)
        args += ["--input", "-"]
    p = subprocess.run(args, input=payload, capture_output=True, text=True,
                       encoding="utf-8")
    if p.returncode != 0:
        if allow_fail:
            return {"_error": p.stderr[:200]}
        raise RuntimeError(f"{method} {endpoint}: {p.stderr[:250]}")
    return json.loads(p.stdout) if p.stdout.strip() else {}


def main():
    print("[1] 拿 main 的 master ref SHA")
    ref = gh_api("GET", f"repos/{FORK}/git/ref/heads/main")
    main_sha = ref["object"]["sha"]
    print(f"    main = {main_sha[:12]}")

    print("[2] 创建分支（若已存在就跳过）")
    r = gh_api("POST", f"repos/{FORK}/git/refs", {
        "ref": f"refs/heads/{BRANCH}", "sha": main_sha,
    }, allow_fail=True)
    if "_error" in r:
        print("    分支可能已存在，继续")
    else:
        print("    ✅ 分支已建")

    print("[3] 在分支上更新 README（用本地已改好的内容）")
    content = open(LOCAL, "rb").read()
    b64 = base64.b64encode(content).decode("ascii")
    # 先拿分支上的 SHA（新建的分支 = main 的 SHA）
    info = gh_api("GET", f"repos/{FORK}/contents/README.md?ref={BRANCH}")
    res = gh_api("PUT", f"repos/{FORK}/contents/README.md", {
        "message": "Add Fat Fish Heart to Emerging projects",
        "content": b64,
        "sha": info["sha"],
        "branch": BRANCH,
    })
    print(f"    ✅ commit = {res['commit']['sha'][:12]}")

    print("[4] 提 PR 到上游")
    pr = gh_api("POST", f"repos/{UPSTREAM}/pulls", {
        "title": "Add Fat Fish Heart to Emerging projects",
        "head": f"Roxy-DD:{BRANCH}",
        "base": "main",
        "body": (
            "Adds Fat Fish Heart to Emerging projects (1 GitHub star, "
            "below the 100-star threshold).\n\n"
            "- Local SQLite episodic memory for AI agents\n"
            "- Lossless cross-process migration (R1: 8000/8000 = 100%)\n"
            "- Deterministic retrieval + rebuildable indexes (R3)\n"
            "- Offsite backup with verified restore (episodes=603)\n"
            "- AGPL-3.0, DSH plugin\n\n"
            "Factual description, marked self-reported, per CONTRIBUTING.md."
        ),
    }, allow_fail=True)
    if "_error" in pr:
        print("    PR 创建失败/已存在:", pr["_error"][:200])
    else:
        print(f"    ✅ PR #{pr['number']}: {pr['html_url']}")


if __name__ == "__main__":
    main()
