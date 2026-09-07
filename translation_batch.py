"""Translate one prepared batch; callers own grouping, scheduling and caches."""

import json
import re
import subprocess
import textwrap
import time
from collections.abc import Callable
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent


def write_schema(path: Path):
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "translation": {"type": "string"},
                    },
                    "required": ["id", "translation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

def make_prompt(batch):
    glossary = textwrap.dedent(
        """
        你在翻译一篇计算机领域的学术论文，主题可能涉及 AI 智能体、工具使用、语言模型、推理、系统架构或对齐训练。
        请将每个 text 字段翻译为严谨、流畅、自然的简体中文。

        强制要求：
        1. 忠实原意，不省略信息，不总结，不扩写。
        2. 保持学术写作风格，术语统一。
        3. 保留以下内容原样或仅在必要时做最小调整：
           - URL、邮箱、文件路径、命令行、代码标识、模型名、版本号
           - 作者姓名、机构名、系统名，如 Claude Code、OpenClaw、Anthropic、MCP
           - 引用格式与年份，如 (Chen et al., 2021)
        4. 对纯数字、页码或明显无需翻译的标识，原样返回。
        5. 以完整句子为最小翻译单元。遇到明显跨块或跨页的断句时，不要把残缺片段硬补成新意思，也不要重复相邻片段；后处理会用跨页上下文修复完整句。
        6. 输出必须是 JSON，对象格式固定为 {"items":[{"id":"...","translation":"..."}]}。
        7. 不要输出解释，不要使用 Markdown 代码块。

        术语约定：
        - agentic -> 代理式
        - agent system -> 智能体系统
        - coding agent -> 编码智能体
        - agentic loop -> 代理循环
        - permission system -> 权限系统
        - sandboxing -> 沙箱化
        - context window -> 上下文窗口
        - compaction -> 压缩
        - subagent -> 子代理
        - hook -> 钩子
        - plugin -> 插件
        - skill -> 技能
        - deny-first -> 默认拒绝
        - append-only -> 仅追加
        - trust spectrum -> 信任梯度
        - execution harness / harness -> 执行框架

        如果某个术语约定不适用于当前论文语境，请以原文上下文为准，选择更准确的译法。

        待翻译条目如下：
        """
    ).strip()
    payload = {"items": batch}
    return glossary + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)

def normalize_translation(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def validate_translation_payload(payload, expected_ids: set[str]) -> dict[str, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("expected an items array")
    result = {}
    for item in payload["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("expected a string item id")
        block_id = item["id"]
        if block_id in result:
            raise ValueError(f"duplicate id: {block_id}")
        text = item.get("translation")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"empty or invalid translation: {block_id}")
        result[block_id] = normalize_translation(text)
    if set(result) != expected_ids:
        raise ValueError(
            f"mismatched ids, missing={sorted(expected_ids - result.keys())[:5]}, "
            f"extra={sorted(result.keys() - expected_ids)[:5]}"
        )
    return result


def execute_translation_batch(
    items: list[dict],
    job_dir: Path,
    prefix: str,
    schema_path: Path,
    *,
    model: str,
    reasoning_effort: str = "low",
    retries: int = 3,
) -> dict[str, str]:
    expected_ids = {item["id"] for item in items}
    return execute_json_task(
        make_prompt(items), job_dir, prefix, schema_path,
        validate=lambda payload: validate_translation_payload(payload, expected_ids),
        model=model, reasoning_effort=reasoning_effort, retries=retries,
    )


def execute_json_task(
    prompt: str,
    job_dir: Path,
    prefix: str,
    schema_path: Path,
    *,
    validate: Callable[[object], dict],
    model: str,
    reasoning_effort: str = "low",
    retries: int = 3,
) -> dict:
    """Own command execution and fresh output; tasks own their payload contract."""
    (job_dir / f"{prefix}.prompt.txt").write_text(prompt, encoding="utf-8")
    out_path = job_dir / f"{prefix}.out.json"
    log_path = job_dir / f"{prefix}.log.txt"
    command = [
        "codex", "exec", "--skip-git-repo-check", "-m", model,
        "-c", f"model_reasoning_effort='{reasoning_effort}'",
        "--disable", "plugins", "--disable", "shell_snapshot",
        "--sandbox", "workspace-write", "--ephemeral",
        "--output-schema", str(schema_path), "-o", str(out_path), "-",
    ]
    for attempt in range(1, retries + 1):
        # A successful exit without fresh output must not accept an older attempt.
        out_path.unlink(missing_ok=True)
        proc = subprocess.run(
            command, input=prompt, text=True, cwd=TOOL_ROOT,
            capture_output=True, check=False,
        )
        log = proc.stdout + "\n\nSTDERR\n" + proc.stderr
        if proc.returncode == 0 and out_path.exists():
            try:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                result = validate(payload)
            except ValueError as exc:
                log += f"\n\nVALIDATION ERROR\n{exc}\n"
            else:
                log_path.write_text(log, encoding="utf-8")
                return result
        log_path.write_text(log, encoding="utf-8")
        if attempt < retries:
            time.sleep(3 * attempt)
    raise RuntimeError(f"translation failed for {prefix}, see {log_path}")
