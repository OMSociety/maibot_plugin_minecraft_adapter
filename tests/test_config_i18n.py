"""配置界面 i18n（en-US / ja-JP）回归测试。

固化两层断言：
1. 覆盖率：SDK 生成的配置 schema 中，凡 base 存在 label/hint/placeholder 的
   字段、凡存在 title/description 的配置节，i18n 必须对 en-US / ja-JP 全覆盖，
   且不得携带 base 不存在的键。
2. 翻译纪律：数字集合与 base 一致；技术标识符（snake_case/驼峰/缩写）不丢失
   （允许复数形态）；ja 译文不得出现「长汉字串且无假名」；ja 与 base 全等的
   文本必须属于纯技术串白名单。

纯离线：仅调用 maibot_sdk.config.generate_plugin_config_schema，不起宿主。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:
    from maibot_sdk.config import generate_plugin_config_schema
except ImportError:  # pragma: no cover - 无 SDK 的环境直接跳过
    pytest.skip("maibot_sdk not available", allow_module_level=True)

from maibot_plugin_minecraft_adapter import plugin as plugin_module
from maibot_plugin_minecraft_adapter.plugin import MinecraftAdapterConfig

LOCALES = ("en-US", "ja-JP")
TEXT_KEYS = ("label", "hint", "placeholder")
# ja 与 base 中文完全相等的合法豁免（纯技术串：数值占位 / 格式示例，本就原样保留）
JA_SAME_BASE_WHITELIST = {"8765", "<{player}> {message}"}

TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*\b")
# 需要逐字保留的 token：snake_case / camelCase / PascalCase / 全大写缩写
TOKEN_KEEP_RE = re.compile(
    r"^(?:[a-z0-9]+(?:_[a-z0-9]+)+"
    r"|[a-z]+(?:[A-Z][a-z0-9]+)+"
    r"|[A-Z][a-z]+(?:[A-Z][a-z0-9]*)+"
    r"|[A-Z]{2,})$"
)
DIGIT_RE = re.compile(r"\d+")
KANA_RE = re.compile(r"[ぁ-ゖァ-ヺー]")
KANJI_RE = re.compile(r"[亜-熙々〆ヶ]")

_ROOT = Path(__file__).resolve().parent.parent


# ---------- schema 构建（模块级，一次生成多次断言） ----------


def _base_keys_of(entry: dict, *, force_hint: bool = False) -> list[str]:
    """base 里实际存在（非空字符串）的展示键。

    hint 存在性口径：字段模型有 description 即视为有 hint（schema 顶层
    description 非空，或 item_fields 场景用 force_hint 显式声明）。
    """
    present: list[str] = []
    for key in TEXT_KEYS:
        value = entry.get(key)
        if key == "hint" and not (isinstance(value, str) and value.strip()):
            value = entry.get("description")
        if isinstance(value, str) and value.strip():
            present.append(key)
        elif key == "hint" and force_hint:
            present.append(key)
    return present


def _collect() -> tuple[list[tuple[str, dict]], list[tuple[str, dict, bool]]]:
    sections = list(
        generate_plugin_config_schema(MinecraftAdapterConfig)["sections"].items()
    )
    fields: list[tuple[str, dict, bool]] = []

    def walk(prefix: str, fields_map: dict, *, is_item: bool) -> None:
        for name, fs in fields_map.items():
            fields.append((f"{prefix}.{name}", fs, is_item))
            item_fields = fs.get("item_fields")
            if isinstance(item_fields, dict):
                walk(f"{prefix}.{name}[]", item_fields, is_item=True)

    for sec_name, sec in sections:
        walk(sec_name, sec["fields"], is_item=False)
    return sections, fields


_SECTIONS, _FIELDS = _collect()
# item_fields 不含 description，从真实模型 FieldInfo 取 base hint（=description）
_ITEM_HINTS = {
    name: (info.description or "")
    for name, info in plugin_module.McServerConfig.model_fields.items()
}


def _iter_pairs():
    for path, sec in _SECTIONS:
        i18n = sec.get("i18n") or {}
        for key in ("title", "description"):
            base = str(sec.get(key) or "")
            if base.strip():
                for loc in LOCALES:
                    yield (
                        f"section:{path}.{key}",
                        base,
                        str(i18n.get(loc, {}).get(key) or ""),
                        loc,
                    )
    for path, fs, is_item in _FIELDS:
        i18n = fs.get("i18n") or {}
        for key in _base_keys_of(fs, force_hint=is_item):
            base = str(fs.get(key) or "")
            if key == "hint" and is_item and not base.strip():
                base = _ITEM_HINTS.get(
                    str(fs.get("name") or path.rsplit(".", 1)[-1]), ""
                )
            for loc in LOCALES:
                yield (
                    f"field:{path}.{key}",
                    base,
                    str(i18n.get(loc, {}).get(key) or ""),
                    loc,
                )


# ---------- 覆盖率 ----------


def test_manifest_declares_supported_locales() -> None:
    manifest = json.loads((_ROOT / "_manifest.json").read_text(encoding="utf-8"))
    i18n = manifest["i18n"]
    assert i18n["default_locale"] == "zh-CN"
    assert i18n["supported_locales"] == ["zh-CN", "en-US", "ja-JP"]


def test_every_field_and_section_fully_covered() -> None:
    assert _FIELDS, "schema 字段收集为空，检查 config_model"
    assert _SECTIONS, "schema 配置节收集为空，检查 config_model"
    missing: list[str] = []
    for where, base, trans, loc in _iter_pairs():
        if base.strip() and not trans.strip():
            missing.append(f"{where}: {loc}")
    assert missing == [], f"i18n 缺失: {missing}"


def test_no_extra_i18n_keys() -> None:
    extra: list[str] = []
    for _, sec in _SECTIONS:
        need = {k for k in ("title", "description") if str(sec.get(k) or "").strip()}
        for loc, entries in (sec.get("i18n") or {}).items():
            extra.extend(
                f"section:{sec['name']}:{loc}:{k}" for k in set(entries) - need
            )
    for path, fs, is_item in _FIELDS:
        need = set(_base_keys_of(fs, force_hint=is_item))
        for loc, entries in (fs.get("i18n") or {}).items():
            extra.extend(f"field:{path}:{loc}:{k}" for k in set(entries) - need)
    assert extra == [], f"i18n 携带 base 不存在的键: {extra}"


# ---------- 翻译纪律 ----------


def _check_discipline(where: str, base: str, trans: str, loc: str) -> list[str]:
    problems: list[str] = []
    # ① 数字集合一致
    if sorted(DIGIT_RE.findall(base)) != sorted(DIGIT_RE.findall(trans)):
        problems.append(f"[digits] {where} {loc}: {base!r} -> {trans!r}")
    # ② 标识符 token 不丢失（复数形态等价）
    b_tokens = {m for m in TOKEN_RE.findall(base) if TOKEN_KEEP_RE.match(m)}
    t_raw = set(TOKEN_RE.findall(trans))
    lost = {
        t
        for t in b_tokens
        if t not in t_raw and f"{t}s" not in t_raw and f"{t}es" not in t_raw
    }
    if lost:
        problems.append(f"[tokens] {where} {loc}: lost {sorted(lost)}")
    # ③ ja 长汉字串且无假名 → 需人工确认，不允许静默通过
    if loc == "ja-JP":
        longest = max(
            (m.group(0) for m in KANJI_RE.finditer(trans)), key=len, default=""
        )
        if len(longest) >= 5 and not KANA_RE.search(trans):
            problems.append(
                f"[kanji] {where}: 无假名的长汉字串 {longest!r} in {trans!r}"
            )
    return problems


def test_discipline_digits_tokens_kana() -> None:
    problems: list[str] = []
    for where, base, trans, loc in _iter_pairs():
        problems.extend(_check_discipline(where, base, trans, loc))
    assert problems == [], "\n".join(problems)


def test_ja_equal_base_only_for_technical_strings() -> None:
    offenders: list[str] = []
    for where, base, trans, loc in _iter_pairs():
        if loc == "ja-JP" and trans == base and trans not in JA_SAME_BASE_WHITELIST:
            offenders.append(f"{where}: {trans!r}")
    assert offenders == [], f"ja 译文与 base 全等且不在白名单: {offenders}"
