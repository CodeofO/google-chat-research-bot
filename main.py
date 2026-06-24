#!/usr/bin/env python3
"""Weekly trend research bot.

Flow
1. Pull each repo under source/
2. Read README and summarize with a local LLM
3. Compare current summaries with previous week snapshot
4. Orchestrate a final report and post to Google Chat
5. Save report + snapshot files
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field


LOGGER_PREFIX = "[research-bot]"
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
CURRENT_LOG_LEVEL = LOG_LEVELS["INFO"]
DEFAULT_REPORT_TITLE = "Document.AI 주요 모델 트렌드 Report 📩"

README_CANDIDATES = (
    "README.md",
    "README_zh.md",
    "README_zh-CN.md",
    "README_ko.md",
    "readme.md",
)


class PatchNote(BaseModel):
    date: str = Field(default="")
    summary: str = Field(default="")


class RepoSummary(BaseModel):
    repo: str = Field(default="unknown")
    release_version: str = Field(default="")
    patch_notes: list[PatchNote] = Field(default_factory=list)
    key_impacts: list[str] = Field(default_factory=list)
    follow_up_points: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    title: str = Field(default=DEFAULT_REPORT_TITLE)
    overall_summary: list[str] = Field(default_factory=list)
    model_briefings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class RepoReadme:
    repo: str
    source: Path
    content: str


@dataclass(frozen=True)
class RepoSummaryPayload:
    repo: str
    summary: RepoSummary
    text: str



def _log(level: str, msg: str) -> None:
    lv = LOG_LEVELS.get(level.upper(), LOG_LEVELS["INFO"])
    if lv < CURRENT_LOG_LEVEL:
        return
    print(f"{LOGGER_PREFIX} {level}: {msg}")


def _set_log_level(level: str) -> None:
    global CURRENT_LOG_LEVEL
    CURRENT_LOG_LEVEL = LOG_LEVELS.get(level.upper(), LOG_LEVELS["INFO"])


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith(("'", '"')) and value.endswith(value[0:1]) and len(value) >= 2:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def _apply_env_aliases() -> None:
    if os.getenv("BASE_URL") and not os.getenv("LOCAL_LLM_BASE_URL"):
        os.environ["LOCAL_LLM_BASE_URL"] = os.getenv("BASE_URL", "")
    if os.getenv("API_KEY") and not os.getenv("LOCAL_LLM_API_KEY"):
        os.environ["LOCAL_LLM_API_KEY"] = os.getenv("API_KEY", "")
    if os.getenv("MODEL_NAME") and not os.getenv("LOCAL_LLM_MODEL"):
        os.environ["LOCAL_LLM_MODEL"] = os.getenv("MODEL_NAME", "")


_load_dotenv(Path(__file__).with_name(".env"))
_apply_env_aliases()



def _run_git_pull(repo_path: Path, timeout: int = 120) -> tuple[bool, str]:
    if not (repo_path / ".git").exists():
        return False, "Not a git repository"

    cmd = ["git", "-C", str(repo_path), "pull", "--ff-only"]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        if completed.returncode != 0:
            return (
                False,
                f"git pull failed ({completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}",
            )
        return True, (completed.stdout or "ok").strip() or "ok"
    except subprocess.TimeoutExpired:
        return False, "git pull timeout"
    except FileNotFoundError:
        return False, "git executable not found"


def _pick_readme(repo_path: Path) -> Path | None:
    for name in README_CANDIDATES:
        candidate = repo_path / name
        if candidate.exists():
            return candidate

    for candidate in sorted(repo_path.glob("README*")):
        if candidate.is_file():
            return candidate
    return None


def _normalize_repo_order(raw: str | None) -> list[str]:
    if not raw:
        return []
    names: list[str] = []
    for token in raw.split(","):
        name = token.strip()
        if name:
            names.append(name)

    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def _parse_repo_order_file(path: Path | None) -> list[str]:
    if not path:
        return []
    if not path.exists() or not path.is_file():
        _log("WARN", f"repo order file not found: {path}")
        return []

    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(line)

    if not lines:
        return []

    values = lines if len(lines) > 1 else lines[0].split(",")
    out: list[str] = []
    seen = set()
    for token in values:
        name = token.strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _sort_repo_entries(entries: list[Path], ordered_names: list[str]) -> list[Path]:
    if not ordered_names:
        return sorted(entries, key=lambda p: p.name)

    rank = {name: i for i, name in enumerate(ordered_names)}
    unknown = len(rank)
    return sorted(entries, key=lambda p: (rank.get(p.name, unknown), p.name))


def _collect_repo_readmes(source_dir: Path, ordered_names: list[str] | None = None) -> list[RepoReadme]:
    if not source_dir.exists():
        raise FileNotFoundError(f"source directory not found: {source_dir}")

    ordered_names = ordered_names or []
    selected = set(ordered_names)

    entries: list[Path] = []
    for entry in source_dir.iterdir():
        if not entry.is_dir():
            continue
        if not (entry / ".git").exists():
            _log("INFO", f"[{entry.name}] not a git repository, skip")
            continue
        if selected and entry.name not in selected:
            _log("DEBUG", f"[{entry.name}] skipped (not in REPO_ORDER)")
            continue
        entries.append(entry)

    entries = _sort_repo_entries(entries, ordered_names)

    if ordered_names:
        available = {e.name for e in entries}
        for name in ordered_names:
            if name not in available:
                _log("WARN", f"repo order contains unknown repo: {name}")

    repos: list[RepoReadme] = []
    for entry in entries:
        ok, msg = _run_git_pull(entry)
        if ok:
            _log("INFO", f"[{entry.name}] git pull: {msg}")
        else:
            _log("WARN", f"[{entry.name}] git pull failed: {msg}")

        readme = _pick_readme(entry)
        if not readme:
            _log("WARN", f"[{entry.name}] README not found")
            continue

        try:
            content = readme.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            _log("WARN", f"[{entry.name}] failed to read README ({readme}): {exc}")
            continue

        if not content.strip():
            _log("WARN", f"[{entry.name}] README is empty")
            continue

        repos.append(RepoReadme(repo=entry.name, source=readme, content=content))

    return repos


def _build_llm(base_url: str, api_key: str, model: str, temperature: float, timeout: int) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key or "local-no-key-required",
        temperature=temperature,
        timeout=timeout,
    )


def _build_structured_chain(llm: ChatOpenAI, schema):
    for method in (None, "json_schema", "json_mode", "function_calling"):
        try:
            if method is None:
                return llm.with_structured_output(schema)
            return llm.with_structured_output(schema, method=method)
        except Exception:
            continue
    return None


def _strip_code_fence(raw: str) -> str:
    if not raw:
        return raw
    text = raw.strip()
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _extract_balanced_json(text: str, start: int) -> tuple[str, int] | None:
    open_char = text[start]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escape = False

    for idx in range(start, len(text)):
        ch = text[idx]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1], idx
    return None


def _coerce_json_from_text(raw: str) -> dict | list | None:
    text = _strip_code_fence(raw).strip()
    if not text:
        return None

    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except Exception:
            pass

    # Find JSON-like chunk in mixed content
    candidates: list[str] = []
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        extracted = _extract_balanced_json(text, idx)
        if extracted:
            payload, _ = extracted
            candidates.append(payload)

    for payload in sorted(candidates, key=len, reverse=True):
        try:
            return json.loads(payload)
        except Exception:
            continue
    return None


def _coerce_to_list(raw: Any, sep: str = "\n") -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, tuple):
        return [str(item).strip() for item in list(raw) if str(item).strip()]
    if isinstance(raw, dict):
        if "0" in raw and len(raw) == 1:
            return [str(raw["0"]).strip()]
        return [str(v).strip() for v in raw.values() if str(v).strip()]
    return [s.strip() for s in str(raw).split(sep) if s.strip()]


def _normalize_patch_note_item(item: Any) -> PatchNote:
    if isinstance(item, PatchNote):
        return item
    if isinstance(item, dict):
        return PatchNote(
            date=str(item.get("date", "")).strip(),
            summary=str(item.get("summary", item.get("title", item.get("text", "")))).strip(),
        )

    if isinstance(item, str):
        line = item.strip()
        if not line:
            return PatchNote(date="", summary="")
        match = re.match(r"\s*(\d{4}[./-]\d{1,2}[./-]\d{1,2})\s*[-: ]\s*(.+)", line)
        if match:
            return PatchNote(date=match.group(1).strip(), summary=match.group(2).strip())
        return PatchNote(date="", summary=line)

    return PatchNote(date="", summary=str(item).strip())


def _normalize_repo_summary_dict(raw: dict) -> RepoSummary:
    parsed = dict(raw)

    if not parsed.get("repo"):
        for key in ("repository", "name", "project"):
            if parsed.get(key):
                parsed["repo"] = parsed[key]
                break

    if not parsed.get("patch_notes"):
        for key in (
            "latest_updates",
            "patch_notes_raw",
            "updates",
            "release_notes",
            "latest_releases",
        ):
            if parsed.get(key):
                parsed["patch_notes"] = parsed[key]
                break

    if not parsed.get("release_version"):
        for key in ("version", "latest_version", "release", "release_tag", "tag"):
            if parsed.get(key):
                parsed["release_version"] = parsed[key]
                break

    if not parsed.get("key_impacts"):
        for key in ("highlights", "insights", "impact", "key_points", "summary"):
            if parsed.get(key):
                parsed["key_impacts"] = parsed[key]
                break

    if not parsed.get("follow_up_points"):
        for key in ("follow_up", "follow_ups", "check_list", "todo", "next_steps"):
            if parsed.get(key):
                parsed["follow_up_points"] = parsed[key]
                break

    notes = parsed.get("patch_notes", [])
    if isinstance(notes, str):
        notes = [line.strip("- ").strip() for line in notes.splitlines() if line.strip()]
    elif isinstance(notes, dict):
        if not notes:
            notes = []
        elif all(isinstance(v, (str, dict)) for v in notes.values()):
            notes = list(notes.values())

    if isinstance(notes, (list, tuple)):
        notes = [_normalize_patch_note_item(item).model_dump() for item in notes[:5]]
    else:
        notes = []

    parsed["patch_notes"] = notes
    parsed["key_impacts"] = _coerce_to_list(parsed.get("key_impacts", []))
    parsed["follow_up_points"] = _coerce_to_list(parsed.get("follow_up_points", []))

    if not parsed.get("repo"):
        parsed["repo"] = "unknown"

    if not parsed.get("key_impacts"):
        parsed["key_impacts"] = ["요약 없음"]

    return RepoSummary.model_validate(parsed)


def _normalize_final_report_dict(raw: dict) -> FinalReport:
    parsed = dict(raw)

    if "전체 핵심 요약" in parsed and "overall_summary" not in parsed:
        parsed["overall_summary"] = parsed["전체 핵심 요약"]
    if "모델 별 상세 브리핑" in parsed and "model_briefings" not in parsed:
        parsed["model_briefings"] = parsed["모델 별 상세 브리핑"]
    if "모델별 상세 브리핑" in parsed and "model_briefings" not in parsed:
        parsed["model_briefings"] = parsed["모델별 상세 브리핑"]

    parsed["title"] = DEFAULT_REPORT_TITLE

    parsed["overall_summary"] = _coerce_to_list(parsed.get("overall_summary", []))
    parsed["model_briefings"] = _coerce_to_list(parsed.get("model_briefings", []))

    if not parsed["overall_summary"]:
        parsed["overall_summary"] = ["해당 없음"]
    if not parsed["model_briefings"]:
        parsed["model_briefings"] = ["레포별 브리핑이 없습니다."]

    return FinalReport.model_validate(parsed)


def _coerce_to_model(schema, value: Any):
    if isinstance(value, schema):
        return value

    parsed: Any = None
    if isinstance(value, dict):
        parsed = value
    elif isinstance(value, str):
        parsed = _coerce_json_from_text(value)
    elif hasattr(value, "dict") and callable(value.dict):
        parsed = value.dict()

    if parsed is None:
        raise ValueError("no parsable structured payload")

    if schema is RepoSummary:
        if isinstance(parsed, list):
            parsed = {
                "repo": "unknown",
                "release_version": "",
                "patch_notes": [],
                "key_impacts": parsed,
                "follow_up_points": ["확인 필요"],
            }
        return _normalize_repo_summary_dict(parsed)

    if schema is FinalReport:
        if isinstance(parsed, list):
            parsed = {
                "title": DEFAULT_REPORT_TITLE,
                "overall_summary": parsed[:5],
                "model_briefings": parsed[5:] if len(parsed) > 5 else [],
            }
        return _normalize_final_report_dict(parsed)

    return schema.model_validate(parsed)


def _to_text_from_repo_summary(summary: RepoSummary, top_notes: int) -> str:
    data = summary.model_dump()
    repo = str(data.get("repo", "") or "(unknown)").strip()
    release_version = str(data.get("release_version", "") or "").strip()

    patch_notes = data.get("patch_notes", []) or []
    if not isinstance(patch_notes, list):
        patch_notes = []

    lines: list[str] = [f"{repo} 📎"]
    if release_version:
        lines.append(f"  🔖 릴리즈 버전: {release_version}")
    lines.append("  📌 최신 패치노트")

    notes: list[str] = []
    for item in patch_notes[:max(1, top_notes)]:
        if isinstance(item, dict):
            date = str(item.get("date", "")).strip()
            summary_text = str(item.get("summary", "")).strip()
        else:
            date = ""
            summary_text = str(getattr(item, "summary", item)).strip()

        if date and summary_text:
            notes.append(f"{date}: {summary_text}")
        elif summary_text:
            notes.append(summary_text)

    if not notes:
        notes = ["해당 없음"]
    lines.extend(notes)

    lines.append("  🔎 핵심 인사이트")
    for item in (data.get("key_impacts") or [])[:2]:
        if str(item).strip():
            lines.append(str(item).strip())

    lines.append("  ✅ 확인 포인트")
    follow = [str(v).strip() for v in (data.get("follow_up_points") or [])[:1] if str(v).strip()]
    if follow:
        lines.extend(follow)
    else:
        lines.append("해당 없음")

    return "\n".join(lines)

def _fallback_from_repo_lines(repo: str, raw: str) -> RepoSummary:
    text = raw.strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first_lines = lines[:3]

    if not first_lines:
        first_lines = ["요약 생성 실패: LLM 응답을 구조화해 정리하지 못했습니다."]

    return RepoSummary(
        repo=repo,
        release_version="",
        patch_notes=[],
        key_impacts=first_lines,
        follow_up_points=["추가 확인 필요"],
    )


def _extract_release_version_hint(text: str) -> str:
    candidates: list[str] = []
    patterns = [
        r"(?im)^\s*(?:latest\s+)?(?:release\s+)?(?:version|ver\.?)\s*[:\-]\s*(v?\d+(?:\.\d+){1,4}(?:[-_][A-Za-z0-9.]+)?)\s*$",
        r"(?im)^\s*(?:release\s+)?(v\d+(?:\.\d+){1,4}(?:[-_][A-Za-z0-9.]+)?)\s*$",
        r"(?im)\b(?:release|version)\b[^\n]{0,60}?(v?\d+(?:\.\d+){1,4}(?:[-_][A-Za-z0-9.]+)?)",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = str(match).strip()
            if value and value not in candidates:
                candidates.append(value)

    return candidates[0] if candidates else ""


def summarize_readme(
    llm: ChatOpenAI,
    repo: str,
    readme: RepoReadme,
    *,
    top_notes: int,
) -> RepoSummaryPayload:
    release_hint = _extract_release_version_hint(readme.content)
    system_prompt = (
        "너는 내부 리서치 분석가다. Google Chat은 텍스트와 줄바꿈 중심으로 출력한다.\n"
        "README.md 전체를 읽고 최신 패치/릴리스 이력을 해석해 정리한다. 룰 기반 추출이 아니라 문맥 이해로 최신 변경점을 추려야 한다.\n"
        "반드시 한국어로만 작성한다. 영어 문장은 한국어로 번역하고, 레포명/제품명/버전 문자열만 예외로 둔다.\n"
        "반드시 순수 JSON 객체만 반환하고 추가 설명은 금지한다.\n"
        "반환 스키마: repo(문자열), release_version(문자열), patch_notes(배열), key_impacts(배열), follow_up_points(배열)\n"
        "README에 릴리즈 버전이 있으면 release_version에 적고, summary 상단에도 드러나게 작성한다.\n"
        "key_impacts는 모델별 상세 브리핑에 그대로 쓰이므로 핵심 변화/영향을 2~3개 작성한다.\n"
        f"최신 항목은 최대 {top_notes}개만 남긴다."
    )

    user_prompt = (
        f"레포지토리: {repo}\n"
        f"최신 패치 항목 개수 N: {top_notes}\n"
        "README 전체 원문:\n"
        "{content}\n"
    )

    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("user", user_prompt)])
    structured_llm = _build_structured_chain(llm, RepoSummary)

    if structured_llm is not None:
        try:
            chain = prompt | structured_llm
            raw = chain.invoke({"repo": repo, "content": readme.content})
            parsed = _coerce_to_model(RepoSummary, raw)
            if release_hint and not str(parsed.release_version).strip():
                parsed = parsed.model_copy(update={"release_version": release_hint})
            return RepoSummaryPayload(
                repo=repo,
                summary=parsed,
                text=_to_text_from_repo_summary(parsed, top_notes),
            )
        except Exception as exc:
            _log("WARN", f"[{repo}] structured summary failed, fallback parsing from raw text: {exc}")

    try:
        chain = prompt | llm | StrOutputParser()
        text = chain.invoke({"repo": repo, "content": readme.content}).strip()
    except Exception as exc:
        _log("WARN", f"[{repo}] LLM call failed in plain mode: {exc}")
        fallback = RepoSummary(
            repo=repo,
            release_version=release_hint,
            patch_notes=[],
            key_impacts=["요약 생성 실패: LLM 응답을 가져오지 못했습니다."],
            follow_up_points=["확인 필요"],
        )
        return RepoSummaryPayload(repo=repo, summary=fallback, text=_to_text_from_repo_summary(fallback, top_notes))

    if not text:
        fallback = RepoSummary(
            repo=repo,
            release_version=release_hint,
            patch_notes=[],
            key_impacts=["요약 생성 결과가 비어 있습니다."],
            follow_up_points=["확인 필요"],
        )
        return RepoSummaryPayload(repo=repo, summary=fallback, text=_to_text_from_repo_summary(fallback, top_notes))

    parsed = _coerce_json_from_text(text)
    if isinstance(parsed, dict):
        try:
            parsed = _coerce_to_model(RepoSummary, parsed)
            if release_hint and not str(parsed.release_version).strip():
                parsed = parsed.model_copy(update={"release_version": release_hint})
            return RepoSummaryPayload(repo=repo, summary=parsed, text=_to_text_from_repo_summary(parsed, top_notes))
        except Exception:
            pass

    fallback = _fallback_from_repo_lines(repo, text)
    if release_hint and not str(fallback.release_version).strip():
        fallback = fallback.model_copy(update={"release_version": release_hint})
    return RepoSummaryPayload(repo=repo, summary=fallback, text=_to_text_from_repo_summary(fallback, top_notes))


def _normalize_text_for_compare(text: str) -> str:
    normalized = text.strip().replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace("*", "")
    normalized = normalized.replace("•", "")
    return normalized.strip().lower()


def _extract_dates_from_summary(summary_text: str) -> set[str]:
    return set(re.findall(r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})", summary_text))


def _compare_with_previous(current: RepoSummaryPayload, previous: str | None) -> tuple[str, str]:
    current_text = _normalize_text_for_compare(current.text)
    if not previous:
        return "변동 있음", "초기 기준 없음"

    prev_text = _normalize_text_for_compare(previous)
    if not prev_text:
        return "변동 있음", "초기 기준 없음"

    ratio = SequenceMatcher(None, current_text, prev_text).ratio()
    if ratio >= 0.985:
        return "변동 없음", "전주 대비 핵심 변경 없음"

    curr_dates = _extract_dates_from_summary(current.text)
    prev_dates = _extract_dates_from_summary(previous)
    added = sorted(curr_dates - prev_dates)
    if added:
        return "변동 있음", f"신규 패치 반영: {added[-1]}"

    return "변동 있음", "브리핑 핵심이 변경됨"


def _clean_bullet_text(text: str) -> str:
    value = str(text).strip()
    value = re.sub(r"^[*\-•]\s*", "", value)
    return value.strip()


def _briefing_points(summary: RepoSummary, limit: int = 3) -> list[str]:
    points: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        value = _clean_bullet_text(raw)
        key = _normalize_text_for_compare(value)
        if not value or key in seen:
            return
        seen.add(key)
        points.append(value)

    for item in summary.key_impacts:
        add(item)
        if len(points) >= limit:
            return points

    for note in summary.patch_notes:
        date = _clean_bullet_text(note.date)
        body = _clean_bullet_text(note.summary)
        if date and body:
            add(f"{date}: {body}")
        elif body:
            add(body)
        if len(points) >= limit:
            return points

    for item in summary.follow_up_points:
        add(item)
        if len(points) >= limit:
            return points

    return points or ["요약 없음"]


def _build_final_briefings(
    repo_summaries: list[RepoSummaryPayload], previous: dict[str, str]
) -> tuple[list[str], list[str], dict[str, str]]:
    briefing_lines: list[str] = []
    plain_overview: list[str] = []
    repo_snapshot: dict[str, str] = {}

    for item in repo_summaries:
        prev_text = previous.get(item.repo)
        status, reason = _compare_with_previous(item, prev_text)
        status_emoji = "✅" if status == "변동 없음" else "⚠️"
        status_label = f"{status_emoji} {status}"
        release_version = str(item.summary.release_version or "").strip()

        briefing_lines.append(f"{item.repo} {status_label}")
        if release_version:
            briefing_lines.append(f"  🔖 릴리즈 버전: {release_version}")
        for point in _briefing_points(item.summary, limit=3):
            briefing_lines.append(f"* {point}")
        briefing_lines.append("")
        plain_overview.append(f"{item.repo} {status_label} {reason}")
        repo_snapshot[item.repo] = item.text

    return briefing_lines, plain_overview, repo_snapshot


def _to_text_from_final_report(report: FinalReport, today: str) -> str:
    data = report.model_dump()
    title = str(data.get("title", DEFAULT_REPORT_TITLE)).strip()
    today = today.strip()

    overall_summary = data.get("overall_summary", []) or []
    model_briefings = data.get("model_briefings", []) or []
    if not isinstance(overall_summary, list):
        overall_summary = [str(overall_summary)]
    if not isinstance(model_briefings, list):
        model_briefings = [str(model_briefings)]

    lines: list[str] = [f"{title}"]
    if today:
        lines.append(f"작성일: {today}")

    lines.append("")
    lines.append("1) 전체 핵심 요약 🔎")
    if not overall_summary:
        lines.append("해당 없음")
    else:
        for row in overall_summary[:8]:
            row = str(row).strip()
            if row:
                if row.startswith(("*", "-", "•")):
                    lines.append(row)
                else:
                    lines.append(f"* {row}")

    lines.append("")
    lines.append("2) 모델 별 상세 브리핑 📚")
    if not model_briefings:
        lines.append("해당 없음")
    else:
        for row in model_briefings:
            row = str(row)
            if not row.strip():
                lines.append("")
                continue
            lines.extend(row.splitlines())

    return "\n".join(lines)


def _snapshot_path(report_dir: Path) -> Path:
    return report_dir / ".latest_snapshot.json"


def _extract_repo_briefing_from_plaintext(text: str) -> dict[str, str]:
    lines = text.splitlines()
    result: dict[str, str] = {}
    collecting = False
    current_repo: str | None = None
    current_detail: list[str] = []

    def _flush_current() -> None:
        nonlocal current_repo, current_detail
        if current_repo is None:
            return
        result[current_repo] = " ".join(s.strip() for s in current_detail).strip()
        current_repo = None
        current_detail = []

    for raw in lines:
        raw = raw.rstrip()
        line = raw.strip()

        if "2) 모델 별 상세 브리핑" in line or "2) 모델별 상세 브리핑" in line:
            collecting = True
            continue
        if not collecting:
            continue

        if not line:
            _flush_current()
            continue

        if line.startswith(("*", "-", "•")):
            content = line[1:].strip()
            if current_repo is not None and content:
                current_detail.append(content)
            continue

        m = re.match(r"^(.+?)\s*(?:✅|⚠️)\s*(변동 없음|변동 있음)\s*[:-]?\s*(.*)$", line)
        if m:
            _flush_current()
            repo = m.group(1).strip()
            detail = m.group(3).strip()
            if not repo:
                continue
            if detail:
                result[repo] = detail
            else:
                current_repo = repo
            continue

        m = re.match(r"^([^\[]+)\s*\[[^\]]+\]\s*(.*)$", line)
        if m:
            _flush_current()
            repo = m.group(1).strip()
            detail = m.group(2).strip()
            if repo:
                result[repo] = detail
            continue

        m = re.match(r"^(.*?)\s*(?:✅|⚠️)?\s*(변동 없음|변동 있음)\s*[:\-–]?(?:\s*)?(.*)$", line)
        if m:
            _flush_current()
            repo = m.group(1).strip()
            detail = m.group(3).strip()
            if repo:
                result[repo] = detail
            continue

        if current_repo is not None:
            current_detail.append(line)

    _flush_current()
    return result


def _load_previous_snapshot(report_dir: Path, current_output: Path | None) -> dict[str, str]:
    snap_path = _snapshot_path(report_dir)
    if snap_path.exists():
        try:
            payload = json.loads(snap_path.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(payload, dict):
                if isinstance(payload.get("report_path"), str) and current_output and str(payload.get("report_path")) == str(current_output.resolve()):
                    # same file is current output; skip to avoid self-compare
                    pass
                else:
                    repo_map = payload.get("repo_briefings")
                    if isinstance(repo_map, dict):
                        return {str(k): str(v) for k, v in repo_map.items()}
        except Exception:
            pass

    files = sorted([p for p in report_dir.glob("*.md") if p.is_file()])
    if not files:
        return {}
    if current_output:
        files = [p for p in files if p.resolve() != current_output.resolve()]
    if not files:
        return {}

    return _extract_repo_briefing_from_plaintext(files[-1].read_text(encoding="utf-8", errors="ignore"))


def _build_final_report(
    llm: ChatOpenAI,
    repo_summaries: list[RepoSummaryPayload],
    previous: dict[str, str],
    *,
    today: str,
) -> tuple[str, dict[str, str]]:
    briefing_lines, plain_overview, repo_snapshot = _build_final_briefings(repo_summaries, previous)
    repo_blocks = "\n\n".join(briefing_lines)
    baseline_lines = "\n".join(plain_overview) if plain_overview else "변동 비교 데이터가 없습니다."

    system_prompt = (
        "너는 최종 오케스트레이터다. Google Chat은 텍스트와 줄바꿈만 사용한다.\n"
        "레포별 1차 요약을 기반으로 아래 JSON 객체만 반환한다.\n"
        "title은 문구를 고정한다: Document.AI 주요 모델 트렌드 Report 📩 (날짜 미포함).\n"
        "모든 문장은 한국어로 작성한다. 영어 문장은 한국어로 번역하고, 레포명/제품명/버전 문자열만 예외로 둔다.\n"
        "JSON 형식: {{\"title\": \"...\", \"overall_summary\": [\"...\"], \"model_briefings\": [\"...\"]}}\n"
        "반드시 2개 섹션을 다룬다: 1) 전체 핵심 요약, 2) 모델 별 상세 브리핑\n"
        "overall_summary는 2~4개 핵심 문장으로 작성한다.\n"
        "model_briefings는 입력된 레포별 1차 요약의 레포명, 변동 상태, bullet을 유지한다. 레포당 bullet은 최대 3개다.\n"
        "JSON 외 다른 텍스트, 코드블록, 코멘트는 출력하지 않는다."
    )
    user_prompt = (
        f"작성일: {today}\n"
        f"변동 기준(전주 대비):\n{baseline_lines}\n\n"
        "레포별 1차 요약:\n"
        f"{repo_blocks}\n"
    )

    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("user", user_prompt)])
    structured_llm = _build_structured_chain(llm, FinalReport)

    if structured_llm is not None:
        try:
            chain = prompt | structured_llm
            raw = chain.invoke({"today": today, "baseline": baseline_lines, "summaries": repo_blocks})
            parsed = _coerce_to_model(FinalReport, raw)
            parsed = parsed.model_copy(update={"model_briefings": briefing_lines})
            return _to_text_from_final_report(parsed, today), repo_snapshot
        except Exception as exc:
            _log("WARN", f"final structured report failed, fallback to parser: {exc}")

    try:
        chain = prompt | llm | StrOutputParser()
        text = chain.invoke({"today": today, "baseline": baseline_lines, "summaries": repo_blocks}).strip()
    except Exception as exc:
        _log("WARN", f"final report failed in plain mode: {exc}")
        text = ""

    if text:
        parsed = _coerce_json_from_text(text)
        if isinstance(parsed, dict):
            try:
                report = _coerce_to_model(FinalReport, parsed)
                report = report.model_copy(update={"model_briefings": briefing_lines})
                return _to_text_from_final_report(report, today), repo_snapshot
            except Exception:
                pass

        if text.lstrip().startswith("{"):
            fallback = _normalize_final_report_dict(
                {
                    "title": DEFAULT_REPORT_TITLE,
                    "overall_summary": ["최종 리포트 구조 파싱 실패"],
                    "model_briefings": [text],
                }
            )
            return _to_text_from_final_report(fallback, today), repo_snapshot

        return text, repo_snapshot

    fallback = _normalize_final_report_dict(
        {
            "title": DEFAULT_REPORT_TITLE,
            "overall_summary": ["최종 리포트 생성 응답이 비어 있습니다."],
            "model_briefings": briefing_lines or ["레포별 브리핑이 없습니다."],
        }
    )
    return _to_text_from_final_report(fallback, today), repo_snapshot


def _save_snapshot(report_dir: Path, report_path: Path, repo_snapshot: dict[str, str]) -> None:
    snap = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "report_path": str(report_path.resolve()),
        "repo_briefings": repo_snapshot,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    _snapshot_path(report_dir).write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")


def send_to_google_chat(webhook_url: str, report: str) -> None:
    if not webhook_url.startswith("http"):
        raise ValueError("invalid webhook url")

    payload = {"text": report}
    response = requests.post(webhook_url, json=payload, timeout=30)
    response.raise_for_status()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weekly trend research bot for Google Chat")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(__file__).with_name("source"),
        help="repositories root directory",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.getenv("GOOGLE_CHAT_WEBHOOK_URL"),
        help="Google Chat Incoming Webhook URL",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("LOCAL_LLM_BASE_URL", os.getenv("BASE_URL", "http://localhost:8000/v1")),
        help="OpenAI-compatible API base URL",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("LOCAL_LLM_MODEL", os.getenv("MODEL_NAME", "n-mix")),
        help="LLM model name",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.getenv("LOCAL_LLM_API_KEY", os.getenv("API_KEY", "local-no-key-required")),
        help="LLM API key",
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--top-patch-notes", type=int, default=2)
    parser.add_argument("--repo-order", default=os.getenv("REPO_ORDER", ""))
    parser.add_argument(
        "--repo-order-file",
        type=Path,
        default=Path(os.getenv("REPO_ORDER_FILE", "")) if os.getenv("REPO_ORDER_FILE") else None,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _resolve_report_path(report_dir: Path, requested: Path | None, today: str) -> Path:
    if requested is not None:
        return requested
    return report_dir / f"{today}.md"


def main() -> None:
    args = parse_args()
    _set_log_level(args.log_level)

    if not args.webhook_url and not args.dry_run:
        raise ValueError("webhook url is required. pass --webhook-url or use --dry-run")

    ordered_names: list[str] = []
    ordered_names.extend(_normalize_repo_order(args.repo_order))
    ordered_names.extend(_parse_repo_order_file(args.repo_order_file))

    repo_readmes = _collect_repo_readmes(args.source_dir, ordered_names=ordered_names)
    if not repo_readmes:
        _log("WARN", "No readable repos/README found.")
        return

    llm = _build_llm(
        base_url=args.llm_base_url,
        api_key=args.llm_api_key,
        model=args.llm_model,
        temperature=args.temperature,
        timeout=args.timeout,
    )

    repo_summaries: list[RepoSummaryPayload] = []
    for repo_readme in repo_readmes:
        _log("INFO", f"summarize README by LLM: {repo_readme.repo}")
        payload = summarize_readme(
            llm,
            repo_readme.repo,
            repo_readme,
            top_notes=args.top_patch_notes,
        )
        repo_summaries.append(payload)

    if not repo_summaries:
        _log("WARN", "No repository summaries generated.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    report_dir = Path(__file__).with_name("reports")
    output = _resolve_report_path(report_dir, args.output, today)
    previous = _load_previous_snapshot(report_dir, output)

    report, repo_snapshot = _build_final_report(
        llm,
        repo_summaries=repo_summaries,
        previous=previous,
        today=today,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    _save_snapshot(report_dir, output, repo_snapshot)
    _log("INFO", f"report saved: {output}")

    if args.dry_run:
        print(report)
        return

    send_to_google_chat(args.webhook_url, report)
    _log("INFO", "report posted to google chat")


if __name__ == "__main__":
    main()
